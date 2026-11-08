import os
from pathlib import Path
import tempfile
import unittest
from deploy import setup

class SetupTests(unittest.TestCase):
    def test_directory_permissions_are_correct_even_with_private_umask(self):
        helper = getattr(setup, 'make_directory', None)
        self.assertIsNotNone(helper, 'Создание каталогов с явными правами ещё не реализовано')
        with tempfile.TemporaryDirectory() as root:
            old = os.umask(0o077)
            try:
                path = Path(root) / 'app'
                helper(path, 0o755)
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            finally:
                os.umask(old)
