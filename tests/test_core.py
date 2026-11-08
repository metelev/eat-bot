import tempfile
import unittest
from pathlib import Path
try:
    from core import Store, render, validate
except ImportError:
    Store = render = validate = None

PRODUCT = {'name': 'Жимолость в молочном шоколаде Frambini', 'portion': '130 г',
           'organization': 'ООО «Фрамбини»', 'country': 'RU', 'tags': [],
           'original_price': '949', 'paid_price': '854', 'currency': 'RUB',
           'photo_sources': [2], 'price_confirmed': True}

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Store, 'Хранилище ещё не реализовано')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'db.sqlite'
        self.db = Store(self.path)
        self.addCleanup(self.db.close)

    def test_photos_persist_first_is_main_and_duplicates_ignored(self):
        d = self.db.active(42)
        self.db.add_photo(d['id'], 11, 'file1', 'a.jpg', 'album')
        self.db.add_photo(d['id'], 11, 'file1', 'a.jpg', 'album')
        self.db.add_photo(d['id'], 12, 'file2', 'b.jpg', None)
        other = Store(self.path)
        self.addCleanup(other.close)
        self.assertEqual(len(other.photos(d['id'])), 2)
        self.assertEqual(other.get(d['id'])['main_photo'], 11)

    def test_owner_isolation(self):
        self.assertNotEqual(self.db.active(42)['id'], self.db.active(43)['id'])

    def test_revision_and_only_one_publish_claim(self):
        d = self.db.active(42)
        self.db.add_photo(d['id'], 11, 'file1', 'a.jpg', None)
        d = self.db.save_result(d['id'], {'products': [PRODUCT], 'questions': []})
        self.assertFalse(self.db.claim(d['id'], d['revision'] - 1))
        self.assertTrue(self.db.claim(d['id'], d['revision']))
        self.assertFalse(self.db.claim(d['id'], d['revision']))
        self.db.finish(d['id'], 'uncertain')
        self.assertFalse(self.db.claim(d['id'], d['revision']))

    def test_restart_does_not_retry_inflight_publication(self):
        d = self.db.active(42)
        self.db.add_photo(d['id'], 11, 'file', 'x', None)
        d = self.db.save_result(d['id'], {'products': [PRODUCT], 'questions': []})
        self.assertTrue(self.db.claim(d['id'], d['revision']))
        self.db.recover()
        self.assertEqual(self.db.get(d['id'])['status'], 'uncertain')

    def test_zero_price_and_html_discount(self):
        p = {**PRODUCT, 'paid_price': '0'}
        self.assertEqual(render([p]), 'Жимолость в молочном шоколаде Frambini, 130 г\nООО «Фрамбини»\n<s>949 ₽</s> 0 ₽')

    def test_foreign_currency_tags_and_escape(self):
        p = {**PRODUCT, 'name': 'Донер <Чикен>', 'tags': ['#шавуха'],
             'original_price': None, 'paid_price': '9.40', 'currency': 'BYN', 'country': None}
        self.assertTrue(render([p]).startswith('#шавуха Донер &lt;Чикен&gt;, 130 г'))
        self.assertTrue(render([p]).endswith('9,40 BYN'))

    def test_unconfirmed_price_and_receipt_only_items_blocked(self):
        self.assertTrue(validate({'products': [{**PRODUCT, 'price_confirmed': False}], 'questions': []}, {2}))
        self.assertTrue(validate({'products': [PRODUCT], 'questions': []}, {1}))

    def test_oversized_caption_is_blocked(self):
        p = {**PRODUCT, 'name': 'я' * 1100}
        self.assertTrue(validate({'products': [p], 'questions': []}, {2}))

    def test_select_main_does_not_remove_sources(self):
        d = self.db.active(42)
        self.db.add_photo(d['id'], 11, 'f1', 'a', None)
        self.db.add_photo(d['id'], 12, 'f2', 'b', None)
        self.db.choose(d['id'], 12)
        self.assertEqual(self.db.get(d['id'])['main_photo'], 12)
        self.assertEqual(len(self.db.photos(d['id'])), 2)

if __name__ == '__main__':
    unittest.main()
