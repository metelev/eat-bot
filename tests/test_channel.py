import json
from pathlib import Path
import tempfile
import unittest
try:
    from deploy.set_channel import change_channel
except ImportError:
    change_channel = None

class ChannelTests(unittest.TestCase):
    def test_only_verified_channel_is_saved_without_losing_secrets(self):
        self.assertIsNotNone(change_channel, 'Смена канала ещё не реализована')
        class T:
            def call(self, method, **kwargs):
                if method == 'getMe':
                    return {'id': 1}
                if method == 'getChat':
                    return {'id': -100123, 'type': 'channel', 'title': 'Тест'}
                if method == 'getChatMember':
                    return {'status': 'administrator', 'can_post_messages': True}
                raise AssertionError('Неожиданный вызов API')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text(json.dumps({'telegram_token': 'secret', 'channel': -100001}))
            path.chmod(0o640)
            change_channel(path, '@tests', T())
            saved = json.loads(path.read_text())
            self.assertEqual(saved['channel'], -100123)
            self.assertEqual(saved['telegram_token'], 'secret')
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
