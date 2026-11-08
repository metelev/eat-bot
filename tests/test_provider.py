import unittest
from test_core import PRODUCT
try:
    from provider import normalize, parse_json
except ImportError:
    normalize = parse_json = None

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(normalize, 'Проверки модели ещё не реализованы')
        self.sources = [{'source': 2, 'kind': 'label'}, {'source': 4, 'kind': 'receipt', 'ocr_text': 'Скидка Плюса 1393'}]
    def test_receipt_only_items_are_removed(self):
        result = normalize({'products': [PRODUCT, {**PRODUCT, 'name': 'Сок', 'photo_sources': [4]}]}, self.sources, [])
        self.assertEqual(len(result['products']), 1)
    def test_unallocated_discount_blocks_price_without_owner_answer(self):
        result = normalize({'products': [PRODUCT]}, self.sources, [])
        self.assertIsNone(result['products'][0]['paid_price'])
        self.assertFalse(result['products'][0]['price_confirmed'])
    def test_explicit_owner_zero_price_is_preserved(self):
        result = normalize({'products': [PRODUCT]}, self.sources, ['0 ₽'])
        self.assertEqual(result['products'][0]['paid_price'], '0')
        self.assertTrue(result['products'][0]['price_confirmed'])
    def test_owner_original_and_paid_price(self):
        result = normalize({'products': [PRODUCT]}, self.sources, ['949 ₽ до скидки\n854 ₽ после скидки'])
        self.assertEqual(result['products'][0]['original_price'], '949')
        self.assertEqual(result['products'][0]['paid_price'], '854')
    def test_unknown_hashtags_not_published(self):
        result = normalize({'products': [{**PRODUCT, 'tags': ['#шавуха', '#шаурма']}]}, self.sources, ['854 ₽'])
        self.assertEqual(result['products'][0]['tags'], ['#шавуха'])
    def test_default_nutrition_off(self):
        result = normalize({'products': [{**PRODUCT, 'nutrition': 'КБЖУ: 500 ккал'}]}, self.sources, [])
        self.assertNotIn('nutrition', result['products'][0])
    def test_json_fence_and_invalid_response(self):
        self.assertEqual(parse_json('```json\n{"a":1}\n```'), {'a': 1})
        with self.assertRaises(ValueError):
            parse_json('not json')

    def test_later_natural_price_correction_replaces_zero(self):
        result = normalize({'products': [PRODUCT]}, self.sources, ['0 ₽', 'измени цену на 854 ₽'])
        self.assertEqual(result['products'][0]['paid_price'], '854')

    def test_nutrition_can_be_disabled_again(self):
        result = normalize({'products': [{**PRODUCT, 'nutrition': 'КБЖУ: 500 ккал'}]}, self.sources, ['добавь КБЖУ', 'убери КБЖУ'])
        self.assertNotIn('nutrition', result['products'][0])
