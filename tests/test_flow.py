import json
import tempfile
import unittest
from pathlib import Path
from test_core import PRODUCT
try:
    from bot import App
except ImportError:
    App = None
from core import Store

class Telegram:
    def __init__(self):
        self.calls = []
        self.fail_publish = False
    def call(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if method == 'sendPhoto' and kwargs['chat_id'] == '@test' and self.fail_publish:
            raise TimeoutError()
        return {'message_id': len(self.calls)}
    def download(self, file_id, path):
        Path(path).write_bytes(b'image')

class Provider:
    def prepare(self, draft, photos, store):
        return {'products': [{**PRODUCT, 'photo_sources': [photos[0]['message_id']]}], 'questions': []}

class FlowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(App, 'Обработчик ещё не реализован')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = Store(self.root / 'db')
        self.addCleanup(self.db.close)
        self.tg = Telegram()
        self.app = App({'owner_id': 42, 'channel': '@test'}, self.db, self.tg, Provider(), self.root)
    def photo(self, mid, uid=42, album=None):
        return {'update_id': mid, 'message': {'message_id': mid, 'chat': {'id': uid, 'type': 'private'}, 'from': {'id': uid}, 'photo': [{'file_id': str(mid), 'file_size': 100}], 'media_group_id': album}}
    def callback(self, action, draft):
        return {'update_id': 100 + len(self.tg.calls), 'callback_query': {'id': 'cb', 'from': {'id': 42}, 'message': {'chat': {'id': 42, 'type': 'private'}}, 'data': f'{action}:{draft["id"]}:{draft["revision"]}'}}
    def test_unauthorized_photos_ignored(self):
        self.app.handle(self.photo(1, 666))
        self.assertIsNone(self.db.active(666, create=False))
        self.assertEqual(self.tg.calls, [])
    def test_album_and_separate_photos_saved_then_preview_only(self):
        self.app.handle(self.photo(1, album='group'))
        self.app.handle(self.photo(2, album='group'))
        self.app.handle(self.photo(3))
        d = self.db.active(42)
        self.app.build(d['id'])
        self.assertEqual(len(self.db.photos(d['id'])), 3)
        sends = [k for m, k in self.tg.calls if m == 'sendPhoto']
        self.assertEqual(sends[-1]['chat_id'], 42)
        self.assertEqual(sends[-1]['photo'], '1')
    def test_double_publish_only_sends_once(self):
        self.app.handle(self.photo(1))
        d = self.db.active(42)
        self.app.build(d['id'])
        cb = self.callback('pub', self.db.get(d['id']))
        self.app.handle(cb)
        self.app.handle(cb)
        self.assertEqual(sum(m == 'sendPhoto' and k['chat_id'] == '@test' for m, k in self.tg.calls), 1)
    def test_timeout_blocks_automatic_retry(self):
        self.app.handle(self.photo(1))
        d = self.db.active(42)
        self.app.build(d['id'])
        cb = self.callback('pub', self.db.get(d['id']))
        self.tg.fail_publish = True
        self.app.handle(cb)
        self.app.handle(cb)
        self.assertEqual(self.db.get(d['id'])['status'], 'uncertain')
        self.assertEqual(sum(m == 'sendPhoto' and k['chat_id'] == '@test' for m, k in self.tg.calls), 1)
    def test_new_photo_invalidates_old_publish_button(self):
        self.app.handle(self.photo(1))
        d = self.db.active(42)
        self.app.build(d['id'])
        cb = self.callback('pub', self.db.get(d['id']))
        self.app.handle(self.photo(2))
        self.app.handle(cb)
        self.assertFalse(any(k.get('chat_id') == '@test' for _, k in self.tg.calls))

    def test_album_first_collect_button_still_finishes_collection(self):
        self.app.handle(self.photo(1, album='group'))
        d = self.db.active(42)
        button = self.callback('build', d)
        self.app.handle(self.photo(2, album='group'))
        self.app.handle(button)
        self.assertIsNotNone(self.app.pending)

    def test_text_correction_persists_and_preview_updates(self):
        self.app.handle(self.photo(1))
        d = self.db.active(42)
        self.app.build(d['id'])
        self.app.handle({'update_id': 99, 'message': {'message_id': 99, 'from': {'id': 42}, 'chat': {'id': 42, 'type': 'private'}, 'text': '0 ₽'}})
        current = self.db.get(d['id'])
        self.assertEqual(json.loads(current['corrections']), ['0 ₽'])
        self.assertEqual(current['status'], 'ready')
        self.assertGreater(current['revision'], d['revision'])

    def test_cancelled_draft_does_not_publish(self):
        self.app.handle(self.photo(1))
        d = self.db.active(42)
        self.app.build(d['id'])
        d = self.db.get(d['id'])
        old = self.callback('pub', d)
        self.app.handle(self.callback('cancel', d))
        self.app.handle(old)
        self.assertEqual(self.db.get(d['id'])['status'], 'cancelled')
        self.assertFalse(any(k.get('chat_id') == '@test' for _, k in self.tg.calls))
