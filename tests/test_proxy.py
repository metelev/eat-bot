import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from transport import Telegram
from deploy import setup

class ProxyTests(unittest.TestCase):
    def telegram(self):
        try:
            return Telegram('123:secret', proxy_url='http://127.0.0.1:18080')
        except TypeError:
            self.fail('Изолированный прокси Telegram ещё не поддерживается')

    def test_proxy_is_attached_to_api_and_file_download(self):
        tg = self.telegram()
        proxy_handlers = [h for h in tg.opener.handlers if hasattr(h, 'proxies')]
        self.assertEqual(proxy_handlers[0].proxies['https'], 'http://127.0.0.1:18080')
        replies = [json.dumps({'ok': True, 'result': {'file_path': 'photos/x.jpg', 'file_size': 3}}).encode(), b'jpg']
        with tempfile.TemporaryDirectory() as root, patch('transport.request', side_effect=replies) as request:
            tg.download('file', Path(root) / 'photo')
            self.assertEqual((Path(root) / 'photo').read_bytes(), b'jpg')
            for call in request.call_args_list:
                self.assertIs(call.kwargs.get('opener'), tg.opener)

    def test_proxy_setup_reads_only_selected_value(self):
        reader = getattr(setup, 'read_settings', None)
        self.assertIsNotNone(reader, 'Чтение настройки прокси ещё не реализовано')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '.env'
            path.write_text('OTHER_SECRET=hidden\nTELEGRAM_PROXY_URL=http://127.0.0.1:18080\n')
            settings = reader(path)
            self.assertEqual(settings.get('TELEGRAM_PROXY_URL'), 'http://127.0.0.1:18080')
            self.assertNotIn('OTHER_SECRET', settings)
