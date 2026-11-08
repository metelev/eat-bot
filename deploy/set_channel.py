"""Change only the destination channel after checking Telegram permissions."""
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transport import Telegram, ApiError


def change_channel(path, target, tg):
    if not re.fullmatch(r'@[A-Za-z0-9_]{5,32}|-100\d+', str(target)):
        raise ValueError('Нужен @username канала или его ID вида -100…')
    target = int(target) if str(target).startswith('-') else target
    chat = tg.call('getChat', chat_id=target)
    if chat.get('type') != 'channel':
        raise ValueError('Это не канал.')
    member = tg.call('getChatMember', chat_id=chat['id'], user_id=tg.call('getMe')['id'])
    if member.get('status') not in {'administrator', 'creator'} or (member.get('status') == 'administrator' and not member.get('can_post_messages')):
        raise ValueError('Боту нужно право публикации в канале.')
    config = json.loads(path.read_text())
    config['channel'] = chat['id']
    temp = path.with_suffix('.new')
    metadata = path.stat()
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temp, metadata.st_uid, metadata.st_gid)
    os.chmod(temp, metadata.st_mode & 0o777)
    temp.replace(path)
    return chat.get('title', str(chat['id']))


if __name__ == '__main__':
    try:
        os.umask(0o077)
        path = Path('/etc/eat-bot/config.json')
        config = json.loads(path.read_text())
        target = input('Основной канал после успешного теста (@username или -100…): ').strip()
        title = change_channel(path, target, Telegram(config['telegram_token'], proxy_url=config.get('telegram_proxy')))
        print('Канал сохранён:', title)
        print('Перезапусти только eat-bot.service. Автоматической публикации не будет.')
    except ApiError as error:
        print('Ошибка API:', error.code)
        sys.exit(1)
    except Exception as error:
        print(str(error) if isinstance(error, ValueError) else 'Ошибка: ' + type(error).__name__)
        sys.exit(1)
