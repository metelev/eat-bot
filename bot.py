"""Private Telegram UI and polling. No incoming sockets or automatic publication."""
import fcntl
import json
import os
from pathlib import Path
import time
from core import Store, render, validate


class App:
    def __init__(self, config, store, telegram, provider, data):
        self.config, self.store, self.tg, self.provider = config, store, telegram, provider
        self.owner = int(config['owner_id'])
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.pending = None

    def say(self, text, keyboard=None):
        kwargs = {'chat_id': self.owner, 'text': text[:4000]}
        if keyboard:
            kwargs['reply_markup'] = {'inline_keyboard': keyboard}
        return self.tg.call('sendMessage', **kwargs)

    def buttons(self, d):
        def button(label, action):
            return {'text': label, 'callback_data': f'{action}:{d["id"]}:{d["revision"]}'}
        if d['status'] == 'uncertain':
            return [[button('Проверил канал: пост есть', 'sent')],
                    [button('Проверил канал: поста нет', 'absent')]]
        rows = []
        if d['status'] == 'ready':
            rows.append([button('Опубликовать', 'pub'), button('Исправить', 'edit')])
        else:
            rows.append([button('Собрать пост', 'build')])
        rows += [[button('Выбрать главное фото', 'main')], [button('Отменить', 'cancel')]]
        return rows

    def show(self, d):
        if d['status'] == 'uncertain':
            self.say('Неизвестно, дошла ли предыдущая отправка. Сначала проверь канал. Автоматического повтора не будет.', self.buttons(d))
            return
        if d['result'] and d['status'] in {'ready', 'question'}:
            result = json.loads(d['result'])
            if result.get('questions'):
                self.say('\n'.join(result['questions'][:3]) + '\nОтветь обычным сообщением.', self.buttons(d))
                return
            photo = next(p for p in self.store.photos(d['id']) if p['message_id'] == d['main_photo'])
            self.tg.call('sendPhoto', chat_id=self.owner, photo=photo['file_id'],
                         caption=render(result['products']), parse_mode='HTML',
                         reply_markup={'inline_keyboard': self.buttons(d)})
        else:
            self.say(f'Материалов: {len(self.store.photos(d["id"]))}. Добавь фото или нажми «Собрать пост».', self.buttons(d))

    def build(self, ident):
        d = self.store.get(ident)
        if not d or d['status'] in {'sending', 'uncertain', 'published', 'cancelled'}:
            return
        photos = self.store.photos(ident)
        if not photos:
            self.say('Сначала пришли фотографию продукта.')
            return
        self.store.status(ident, 'processing')
        self.say('Читаю материалы. Это может занять несколько минут. Публикации не будет до твоего нажатия кнопки.')
        try:
            result = self.provider.prepare(self.store.get(ident), photos, self.store)
            sources = {p['message_id'] for p in photos}
            result['questions'] = validate(result, sources)
            d = self.store.save_result(ident, result)
        except Exception as error:
            self.store.status(ident, 'collecting')
            print('prepare failed:', type(error).__name__, flush=True)
            self.say('Не удалось завершить распознавание. Материалы сохранены. Повтори «Собрать пост».', self.buttons(self.store.get(ident)))
            return
        self.show(d)

    def handle(self, update):
        cb = update.get('callback_query')
        message = update.get('message')
        actor = cb or message or {}
        if actor.get('from', {}).get('id') != self.owner:
            return
        chat = (cb.get('message', {}) if cb else message or {}).get('chat', {})
        if chat.get('type') != 'private' or chat.get('id') != self.owner:
            return
        if cb:
            try:
                self.tg.call('answerCallbackQuery', callback_query_id=cb['id'])
            except Exception:
                pass
            parts = cb.get('data', '').split(':')
            if len(parts) < 3:
                return
            action, ident, revision = parts[:3]
            d = self.store.get(ident)
            if not d or d['owner'] != self.owner:
                return
            if (str(d['revision']) != revision and not (action == 'build' and d['status'] == 'collecting')) or d['status'] in {'published', 'cancelled', 'sending'}:
                self.say('Эта кнопка устарела. Отправь /start для текущего черновика.')
                return
            if d['status'] == 'uncertain':
                if action == 'sent':
                    self.store.finish(ident, 'published')
                    self.say('Отметил публикацию. Повторной отправки не будет.')
                elif action == 'absent':
                    self.say('Ты проверил канал и уверен, что пост отсутствует? Повтор разрешится только после подтверждения.', [[{'text': 'Да, разрешить новую попытку', 'callback_data': f'retry:{ident}:{revision}'}]])
                elif action == 'retry':
                    self.store.status(ident, 'ready')
                    self.show(self.store.get(ident))
                return
            if action == 'pub':
                if d['status'] != 'ready':
                    return
                result = json.loads(d['result'])
                if validate(result, {p['message_id'] for p in self.store.photos(ident)}):
                    self.say('Черновик требует уточнения. Нажми «Собрать пост».')
                    return
                target = self.config['channel']
                if not self.store.claim(ident, int(revision), target):
                    return
                photo = next(p for p in self.store.photos(ident) if p['message_id'] == d['main_photo'])
                try:
                    sent = self.tg.call('sendPhoto', chat_id=target, photo=photo['file_id'], caption=render(result['products']), parse_mode='HTML')
                    self.store.finish(ident, 'published', sent['message_id'])
                except Exception as error:
                    self.store.finish(ident, 'uncertain')
                    print('publication uncertain:', type(error).__name__, flush=True)
                    self.show(self.store.get(ident))
                    return
                self.say('Опубликовано. Для следующего поста пришли новые фотографии.')
            elif action == 'build':
                self.pending = (ident, time.monotonic() + 2)
                self.say('Завершаю приём альбома…')
            elif action == 'edit':
                self.say('Напиши правку обычным сообщением: например, «0 ₽», «измени массу на 150 г» или «убери второй продукт».')
            elif action == 'cancel':
                self.store.status(ident, 'cancelled')
                self.pending = None
                self.say('Черновик отменён.')
            elif action == 'main':
                for n, p in enumerate(self.store.photos(ident), 1):
                    self.tg.call('sendPhoto', chat_id=self.owner, photo=p['file_id'], caption=f'Фото {n}', reply_markup={'inline_keyboard': [[{'text': 'Сделать главным', 'callback_data': f'choose:{ident}:{revision}:{p["message_id"]}'}]]})
            elif action == 'choose' and len(parts) == 4:
                self.store.choose(ident, int(parts[3]))
                self.show(self.store.get(ident))
            return
        if not message:
            return
        text = message.get('text', '').strip()
        if text.startswith('/start') or text == '/status':
            self.show(self.store.active(self.owner))
            return
        d = self.store.active(self.owner)
        if d['status'] == 'uncertain':
            self.show(d)
            return
        if text == '/cancel':
            self.store.status(d['id'], 'cancelled')
            self.pending = None
            self.say('Черновик отменён.')
            return
        photo = None
        if message.get('photo'):
            photo = message['photo'][-1]
        elif message.get('document', {}).get('mime_type') in {'image/jpeg', 'image/png'}:
            photo = message['document']
        if photo:
            if any(p['message_id'] == message['message_id'] for p in self.store.photos(d['id'])):
                return
            if len(self.store.photos(d['id'])) >= 20 or photo.get('file_size', 0) > 15_000_000:
                self.say('Лимит: 20 материалов на пост, до 15 МБ на изображение.')
                return
            folder = self.data / d['id']
            folder.mkdir(mode=0o700, exist_ok=True)
            path = folder / f'{message["message_id"]}.image'
            self.tg.download(photo['file_id'], path)
            self.store.add_photo(d['id'], message['message_id'], photo['file_id'], str(path), message.get('media_group_id'))
            if message.get('caption'):
                self.store.correction(d['id'], message['caption'][:3000])
                self.store.status(d['id'], 'collecting')
            if self.pending:
                self.pending = (d['id'], time.monotonic() + 2)
            # One progress response per album (first arrival), individual photos each get a response.
            album = message.get('media_group_id')
            count = sum(p['album'] == album for p in self.store.photos(d['id'])) if album else 1
            if count == 1:
                self.say('Фото сохранено. Первое фото — главное; остальные — источники. Добавь материалы и нажми «Собрать пост».', self.buttons(self.store.get(d['id'])))
            return
        if text:
            if text.startswith('/'):
                self.say('Команды: /start — черновик, /cancel — отменить.')
                return
            if len(text) > 3000:
                self.say('Сократи правку до 3000 символов.')
                return
            self.store.correction(d['id'], text)
            self.build(d['id'])
        else:
            self.say('Пришли фото, изображение файлом или текст правки.')


def main():
    from transport import Telegram
    from provider import Provider
    os.umask(0o077)
    config = json.loads(Path('/etc/eat-bot/config.json').read_text())
    data = Path('/var/lib/eat-bot')
    lock = (data / 'instance.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    store = Store(data / 'drafts.sqlite')
    store.recover()
    tg = Telegram(config['telegram_token'], proxy_url=config.get('telegram_proxy'))
    webhook = tg.call('getWebhookInfo')
    if webhook.get('url'):
        raise RuntimeError('У этого токена настроен webhook. Используй отдельного бота.')
    app = App(config, store, tg, Provider(config), data)
    print('eat-bot started; long polling', flush=True)
    while True:
        try:
            offset = int(store.meta('offset') or 0)
            updates = tg.call('getUpdates', offset=offset, timeout=1 if app.pending else 25,
                              allowed_updates=['message', 'callback_query'])
            for update in updates:
                try:
                    app.handle(update)
                except Exception as error:
                    # Offset is not advanced on failure: photos are deduplicated and sending is claimed.
                    print('update failed:', type(error).__name__, flush=True)
                    time.sleep(3)
                    break
                store.meta('offset', update['update_id'] + 1)
            else:
                if app.pending and time.monotonic() >= app.pending[1]:
                    ident, _ = app.pending
                    app.pending = None
                    app.build(ident)
        except Exception as error:
            print('poll failed:', type(error).__name__, flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
