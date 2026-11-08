"""Run from the extracted release as root. Secrets are read only from the TTY."""
import getpass
import grp
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transport import Telegram, ApiError


def read_settings(path):
    settings = {}
    for line in path.read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if parts and parts[0] == 'export':
            parts = parts[1:]
        if len(parts) == 1 and '=' in parts[0]:
            key, value = parts[0].split('=', 1)
            if key in {'TELEGRAM_PROXY_URL', 'GIGACHAT_CA_BUNDLE'}:
                settings[key] = value
    return settings


def make_directory(path, mode):
    path.mkdir(mode=mode)
    os.chmod(path, mode)


def wait_updates(tg, description, predicate, offset):
    print(description, flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        updates = tg.call('getUpdates', offset=offset, timeout=25,
                          allowed_updates=['message', 'callback_query', 'channel_post'])
        for u in updates:
            offset = u['update_id'] + 1
            value = predicate(u)
            if value is not None:
                return value, offset
    raise ValueError('Время ожидания истекло. Запусти настройку заново.')


def main():
    if os.geteuid() != 0:
        raise ValueError('Запусти от root.')
    os.umask(0o077)
    config_dir = Path('/etc/eat-bot')
    destination = Path('/opt/eat-bot')
    if config_dir.exists() or destination.exists() or Path('/etc/systemd/system/eat-bot.service').exists() or Path('/var/lib/eat-bot').exists():
        raise ValueError('Установка уже существует. Повторная установка не выполнена.')
    try:
        pwd.getpwnam('eat-bot')
    except KeyError:
        pass
    else:
        raise ValueError('Пользователь eat-bot уже существует. Сначала проверь существующую установку.')
    if not shutil.which('tesseract'):
        raise ValueError('Сначала установи Tesseract с rus и eng.')
    token = getpass.getpass('Вставь токен НОВОГО Telegram-бота: ').strip()
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        raise ValueError('Неверный формат токена.')
    settings_input = input('Путь к существующему файлу настроек (Enter — без импорта): ').strip()
    settings_path = Path(settings_input).expanduser().resolve() if settings_input else None
    settings = read_settings(settings_path) if settings_path else {}
    key_input = input('Путь к защищённому файлу ключа GigaChat: ').strip()
    if not key_input:
        raise ValueError('Нужен путь к файлу ключа.')
    old = Path(key_input).expanduser().resolve()
    if not old.is_file():
        raise ValueError('Файл ключа GigaChat не найден.')
    proxy = settings.get('TELEGRAM_PROXY_URL') or None
    if proxy and not proxy.startswith(('http://', 'https://')):
        raise ValueError('Пока поддерживается только HTTP/HTTPS-прокси Telegram.')
    tg = Telegram(token, proxy_url=proxy)
    me = tg.call('getMe')
    print('Подключён бот @' + me['username'])
    if tg.call('getWebhookInfo').get('url'):
        raise ValueError('У бота настроен webhook. Нужен отдельный новый бот.')
    code = secrets.token_hex(6)
    def owner_match(u):
        m = u.get('message', {})
        if m.get('chat', {}).get('type') == 'private' and m.get('text', '').strip() == '/setup ' + code:
            return m['from']['id']
    owner, offset = wait_updates(tg,
        f'Открой @{me["username"]} в своём Telegram и отправь: /setup {code}', owner_match, 0)
    print('Владелец связан. Доступ остальных пользователей будет закрыт.')
    code = secrets.token_hex(6)
    def channel_match(u):
        m = u.get('channel_post', {})
        if m.get('text', '').strip() == '/setup ' + code:
            return m['chat']['id']
    channel, offset = wait_updates(tg,
        'Создай отдельный ТЕСТОВЫЙ канал. Добавь бота администратором с правом публикации.\n'
        f'Опубликуй сам в тестовом канале: /setup {code}\n'
        'Этот служебный пост можно удалить после завершения настройки.', channel_match, offset)
    membership = tg.call('getChatMember', chat_id=channel, user_id=me['id'])
    if membership.get('status') not in {'administrator', 'creator'} or (membership.get('status') == 'administrator' and not membership.get('can_post_messages')):
        raise ValueError('Нет права публикации в тестовый канал.')
    # Read only known keys, never copy the other service environment wholesale.
    if not old.exists():
        raise ValueError('Не найден ранее проверенный ключ GigaChat.')
    context_root = settings_path.parent if settings_path else Path.cwd()
    ca = settings.get('GIGACHAT_CA_BUNDLE')
    ca_data = None
    if ca:
        ca_path = Path(ca)
        if not ca_path.is_absolute():
            ca_path = context_root / ca_path
        ca_data = ca_path.read_bytes()
    # All interactive and remote checks complete before creating the service files.
    subprocess.run(['useradd', '--system', '--user-group', '--home-dir', '/var/lib/eat-bot', '--shell', '/usr/sbin/nologin', 'eat-bot'], check=True)
    group = grp.getgrnam('eat-bot').gr_gid
    uid = pwd.getpwnam('eat-bot').pw_uid
    make_directory(config_dir, 0o750)
    os.chown(config_dir, 0, group)
    data = Path('/var/lib/eat-bot')
    data.mkdir(mode=0o700)
    os.chown(data, uid, group)
    make_directory(destination, 0o755)
    for name in ('bot.py', 'core.py', 'provider.py', 'transport.py'):
        shutil.copyfile(ROOT / name, destination / name)
        os.chmod(destination / name, 0o644)
    shutil.copyfile(ROOT / 'deploy/set_channel.py', destination / 'set_channel.py')
    os.chmod(destination / 'set_channel.py', 0o644)
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(destination / '.venv')], check=True)
    # umask made the environment private to root; only code and runtime need read/execute.
    for directory, folders, files in os.walk(destination / '.venv'):
        os.chmod(directory, 0o755)
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                os.chmod(path, 0o755 if os.access(path, os.X_OK) else 0o644)
    def save(name, content):
        path = config_dir / name
        path.write_bytes(content)
        os.chown(path, 0, group)
        os.chmod(path, 0o640)
    save('gigachat.key', old.read_bytes())
    if ca_data:
        save('ca.pem', ca_data)
    save('post_rules.md', (ROOT / 'config/post_rules.md').read_bytes())
    config = {'telegram_token': token, 'telegram_proxy': proxy, 'owner_id': owner, 'channel': channel,
              'test_channel': channel, 'model': 'GigaChat-3-Ultra',
              'gigachat_key': '/etc/eat-bot/gigachat.key',
              'gigachat_ca': '/etc/eat-bot/ca.pem' if ca_data else None,
              'rules_file': '/etc/eat-bot/post_rules.md'}
    save('config.json', json.dumps(config, ensure_ascii=False, indent=2).encode())
    # Exclude pairing messages from normal draft processing.
    from core import Store
    db = Store(data / 'drafts.sqlite')
    db.meta('offset', offset)
    db.close()
    for path in data.iterdir():
        os.chown(path, uid, group)
        os.chmod(path, 0o600)
    unit = Path('/etc/systemd/system/eat-bot.service')
    if unit.exists():
        raise ValueError('Файл службы уже существует. Он не перезаписан.')
    shutil.copyfile(ROOT / 'deploy/eat-bot.service', unit)
    os.chmod(unit, 0o644)
    print('Файлы установлены. Служба ещё НЕ запущена. Секреты сохранены в /etc/eat-bot с ограничением доступа.')
    print('Следующий шаг: проверка unit-файла и запуск только eat-bot.service.')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('\nНастройка прервана.')
    except ApiError as error:
        print('Ошибка API. Код:', error.code, '(секреты скрыты)')
        sys.exit(1)
    except Exception as error:
        # Validation messages are ours; arbitrary exception messages may contain sensitive data.
        print(str(error) if isinstance(error, ValueError) else 'Ошибка настройки: ' + type(error).__name__)
        sys.exit(1)
