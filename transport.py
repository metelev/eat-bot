"""HTTPS transports. Exception text never includes tokens, URLs or response bodies."""
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path


class ApiError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('API request failed')


def request(url, data=None, headers=None, context=None, timeout=60, opener=None):
    try:
        req = urllib.request.Request(url, data=data, headers=headers or {})
        connection = opener.open(req, timeout=timeout) if opener is not None else urllib.request.urlopen(req, context=context, timeout=timeout)
        with connection as response:
            return response.read(32_000_001)
    except urllib.error.HTTPError as error:
        raise ApiError(error.code) from None
    except Exception:
        raise ApiError(0) from None


class Telegram:
    def __init__(self, token, proxy_url=None):
        self.base = 'https://api.telegram.org/bot' + token + '/'
        self.files = 'https://api.telegram.org/file/bot' + token + '/'
        self.context = ssl.create_default_context()
        proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else {}
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(proxies),
            urllib.request.HTTPSHandler(context=self.context),
        )

    def call(self, method, **kwargs):
        raw = request(self.base + method, json.dumps(kwargs).encode(),
                      {'Content-Type': 'application/json'}, self.context, 60, opener=self.opener)
        body = json.loads(raw)
        if not body.get('ok'):
            raise ApiError(body.get('error_code', 0))
        return body['result']

    def download(self, file_id, path):
        info = self.call('getFile', file_id=file_id)
        if info.get('file_size', 0) > 15_000_000:
            raise ApiError(413)
        raw = request(self.files + info['file_path'], context=self.context, opener=self.opener)
        if len(raw) > 15_000_000:
            raise ApiError(413)
        temp = Path(str(path) + '.tmp')
        temp.write_bytes(raw)
        temp.replace(path)
