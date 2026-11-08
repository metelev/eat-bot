"""Persistent drafts and deterministic Telegram captions; no network access."""
import html
import json
import re
import sqlite3
import uuid
from decimal import Decimal, InvalidOperation

TAGS = set('#кофе #шавуха #паста #пицца #кола #роллы #бургер #чай #чипсы #цезарь #окрошка #мимоза #плов #оливье #борщ #щи #винегрет #рамен #блины #хотдог #драники #греческий #квас #вода #эчпочмак #чакчак #хачапури #поке #фанта #солянка #пельмени #вареники'.split())


def money(value):
    number = Decimal(str(value).replace(',', '.'))
    if not number.is_finite() or number < 0 or number.as_tuple().exponent < -2:
        raise ValueError('Некорректная цена')
    return (format(number, '.2f') if number % 1 else format(number, '.0f')).replace('.', ',')


def render(products):
    blocks = []
    for p in products:
        tags = ' '.join(dict.fromkeys(t for t in p.get('tags', []) if t in TAGS))
        country = p.get('country')
        flag = ''
        if country and country != 'RU' and re.fullmatch('[A-Z]{2}', country):
            flag = ''.join(chr(127397 + ord(c)) for c in country) + ' '
        title = (tags + ' ' if tags else '') + flag + p['name']
        if p.get('portion'):
            title += ', ' + p['portion']
        lines = [html.escape(title)]
        if p.get('organization'):
            lines.append(html.escape(p['organization']))
        symbol = {'RUB': '₽', 'BYN': 'BYN'}.get(p['currency'], p['currency'])
        price = money(p['paid_price']) + ' ' + html.escape(symbol)
        old = p.get('original_price')
        if old is not None and Decimal(str(old).replace(',', '.')) > Decimal(str(p['paid_price']).replace(',', '.')):
            price = '<s>' + money(old) + ' ' + html.escape(symbol) + '</s> ' + price
        lines.append(price)
        if p.get('nutrition'):
            lines.append(html.escape(p['nutrition']))
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks)


def validate(result, food_sources):
    errors = list(result.get('questions') or [])
    products = result.get('products')
    if not isinstance(products, list) or not products:
        return errors + ['Какой продукт нужно включить в пост?']
    for p in products:
        if not isinstance(p, dict):
            errors.append('Не удалось разобрать товар. Повтори сборку.')
            continue
        name = p.get('name') or 'продукта'
        if not p.get('photo_sources') or not set(p['photo_sources']).intersection(food_sources):
            errors.append(f'Подтверди фотографию для «{name}» или убери этот товар.')
        if not p.get('name') or not p.get('portion'):
            errors.append(f'Уточни название и массу/объём для «{name}».')
        if not p.get('organization') and not p.get('organization_omitted'):
            errors.append(f'Какое юрлицо у «{name}»? Можно ответить «без производителя».')
        if not p.get('price_confirmed') or p.get('paid_price') is None:
            errors.append(f'Сколько фактически уплачено за «{name}» с учётом скидок?')
        try:
            money(p.get('paid_price'))
            if p.get('original_price') is not None:
                money(p['original_price'])
            if not re.fullmatch('[A-Z]{3}', p.get('currency') or ''):
                raise ValueError()
        except (ValueError, InvalidOperation, TypeError):
            errors.append(f'Уточни цену и валюту для «{name}».')
        if any(t not in TAGS for t in p.get('tags', [])):
            errors.append('Модель предложила неизвестный хештег. Попроси убрать его.')
    if not errors:
        try:
            plain = html.unescape(re.sub(r'</?s>', '', render(products)))
            if len(plain.encode('utf-16-le')) // 2 > 1024:
                errors.append('Подпись длиннее 1024 символов. Попроси сократить название или убрать товары.')
        except (KeyError, TypeError, ValueError, InvalidOperation):
            errors.append('Данные неполные. Уточни товар или повтори сборку.')
    return list(dict.fromkeys(errors))


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS drafts(
                id TEXT PRIMARY KEY, owner INTEGER, status TEXT DEFAULT 'collecting',
                revision INTEGER DEFAULT 0, main_photo INTEGER, result TEXT,
                corrections TEXT DEFAULT '[]', published_id INTEGER, target TEXT);
            CREATE TABLE IF NOT EXISTS photos(
                draft TEXT, message_id INTEGER, file_id TEXT, path TEXT, album TEXT,
                extraction TEXT, PRIMARY KEY(draft,message_id));
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        ''')

    def close(self):
        self.db.close()

    def get(self, ident):
        row = self.db.execute('SELECT * FROM drafts WHERE id=?', (ident,)).fetchone()
        return dict(row) if row else None

    def active(self, owner, create=True):
        row = self.db.execute("SELECT * FROM drafts WHERE owner=? AND status NOT IN ('published','cancelled') ORDER BY rowid DESC LIMIT 1", (owner,)).fetchone()
        if row:
            return dict(row)
        if not create:
            return None
        ident = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute('INSERT INTO drafts(id,owner) VALUES(?,?)', (ident, owner))
        return self.get(ident)

    def photos(self, ident):
        return [dict(r) for r in self.db.execute('SELECT * FROM photos WHERE draft=? ORDER BY message_id', (ident,))]

    def add_photo(self, ident, message_id, file_id, path, album):
        d = self.get(ident)
        if d['status'] in {'sending', 'uncertain', 'published', 'cancelled'}:
            return False
        with self.db:
            cursor = self.db.execute('INSERT OR IGNORE INTO photos(draft,message_id,file_id,path,album) VALUES(?,?,?,?,?)', (ident, message_id, file_id, path, album))
            if cursor.rowcount:
                self.db.execute("UPDATE drafts SET main_photo=COALESCE(main_photo,?),status='collecting',revision=revision+1 WHERE id=?", (message_id, ident))
        return bool(cursor.rowcount)

    def choose(self, ident, message_id):
        if not any(p['message_id'] == message_id for p in self.photos(ident)):
            raise ValueError('Фото не найдено')
        with self.db:
            self.db.execute("UPDATE drafts SET main_photo=?,revision=revision+1 WHERE id=? AND status NOT IN ('sending','uncertain','published','cancelled')", (message_id, ident))

    def save_result(self, ident, result):
        with self.db:
            self.db.execute("UPDATE drafts SET result=?,status=?,revision=revision+1 WHERE id=? AND status NOT IN ('sending','uncertain','published','cancelled')", (json.dumps(result, ensure_ascii=False), 'question' if result.get('questions') else 'ready', ident))
        return self.get(ident)

    def correction(self, ident, text):
        history = json.loads(self.get(ident)['corrections'])
        history.append(text)
        with self.db:
            self.db.execute("UPDATE drafts SET corrections=?,status='processing',revision=revision+1 WHERE id=?", (json.dumps(history, ensure_ascii=False), ident))

    def extraction(self, ident, message_id, data):
        with self.db:
            self.db.execute('UPDATE photos SET extraction=? WHERE draft=? AND message_id=?', (json.dumps(data, ensure_ascii=False), ident, message_id))

    def status(self, ident, status):
        with self.db:
            self.db.execute('UPDATE drafts SET status=?,revision=revision+1 WHERE id=?', (status, ident))

    def claim(self, ident, revision, target=None):
        with self.db:
            row = self.db.execute("UPDATE drafts SET status='sending',target=? WHERE id=? AND revision=? AND status='ready' AND main_photo IS NOT NULL", (target, ident, revision))
        return row.rowcount == 1

    def finish(self, ident, status, message_id=None):
        with self.db:
            self.db.execute('UPDATE drafts SET status=?,published_id=?,revision=revision+1 WHERE id=?', (status, message_id, ident))

    def recover(self):
        with self.db:
            self.db.execute("UPDATE drafts SET status='uncertain' WHERE status='sending'")
            self.db.execute("UPDATE drafts SET status='collecting' WHERE status='processing'")

    def meta(self, key, value=None):
        if value is not None:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None
