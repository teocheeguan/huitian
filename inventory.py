import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHINA = timezone(timedelta(hours=8))
MAX_QTY = 1_000_000
SOURCES = {'小程序', '微信', '电话', '到店', '其他'}
CATEGORIES = {'water': '桶装水', 'empty': '空桶系列', 'bottle': '瓶装水', 'other': '其他'}
TITLES = {
    'OPEN_STOCK': '期初库存', 'OPEN_BUCKET': '期初桶账', 'IN': '进货入库',
    'OUT': '出库', 'COUNT': '盘点', 'TRANSFER': '调拨', 'SALE': '销售',
    'SEND': '补记送出', 'BUCKET_RETURN': '回空桶', 'DEPOSIT': '押桶',
    'REFUND': '退押', 'RETURN': '销售退货', 'REVERSE': '冲销',
}


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def connect(path):
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def initialize(path):
    with closing(connect(path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        if scalar(db, 'PRAGMA user_version') == 0 and scalar(db, "SELECT 1 FROM sqlite_master WHERE name='products'"):
            upgrade_products(db)
        db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        if scalar(db, 'PRAGMA user_version') == 0:
            db.execute('PRAGMA user_version=1')


def upgrade_products(db):
    # Preserve product IDs and the foreign keys held by existing stock and documents.
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            if scalar(db, 'PRAGMA user_version') != 0:
                return
            db.execute('''CREATE TABLE products_v1 (
                id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
                brand TEXT NOT NULL, spec TEXT NOT NULL, category TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('water','empty','bottle','other')),
                bucket_id INTEGER REFERENCES products(id), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                CHECK((kind='water' AND bucket_id IS NOT NULL) OR (kind<>'water' AND bucket_id IS NULL)))''')
            db.execute('''INSERT INTO products_v1 SELECT id,sku,name,brand,spec,category,
                kind,bucket_id,active FROM products''')
            db.execute('DROP TABLE products')
            db.execute('ALTER TABLE products_v1 RENAME TO products')
            db.execute("CREATE UNIQUE INDEX empty_brand_spec ON products(brand COLLATE NOCASE,spec COLLATE NOCASE) WHERE kind='empty'")
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise Problem('商品升级遇到异常关联，原库已保留')
            db.execute('PRAGMA user_version=1')
    finally:
        db.execute('PRAGMA foreign_keys=ON')


def rows(db, sql, args=()):
    return [dict(row) for row in db.execute(sql, args)]


def row(db, sql, args=()):
    result = db.execute(sql, args).fetchone()
    return dict(result) if result is not None else None


def scalar(db, sql, args=()):
    result = db.execute(sql, args).fetchone()
    return result[0] if result is not None else None


def txt(value, title, max_length, optional=False):
    if value is None and optional:
        return ''
    if not isinstance(value, str):
        raise Problem(f'请输入{title}')
    value = value.strip()
    if len(value) > max_length or (not value and not optional):
        raise Problem(f'{title}须为 {"0" if optional else "1"}–{max_length} 个字符')
    return value


def integer(value, title, minimum=1, maximum=MAX_QTY):
    if type(value) is not int or not minimum <= value <= maximum:
        raise Problem(f'{title}须为 {minimum}–{maximum} 之间的整数')
    return value


def ident(value, title='编号'):
    return integer(value, title, 1, 2_147_483_647)


def phone_num(value):
    value = re.sub(r'[\s()\-]', '', txt(value, '手机号', 40))
    if re.fullmatch(r'\+?86(1\d{10})', value):
        value = value[-11:]
    if not re.fullmatch(r'\+?\d{5,20}', value):
        raise Problem('请输入有效的联系电话')
    return value


def hq(user):
    if user['role'] != 'hq':
        raise Problem('此操作仅限总部账号', 403)


def store_for(db, user, value, active=True):
    code = ident(value, '门店编号')
    if user['role'] == 'store' and user['store_id'] != code:
        raise Problem('当前账号只能操作本店', 403)
    found = row(db, 'SELECT * FROM stores WHERE id=?', (code,))
    if not found or (active and not found['active']):
        raise Problem('所选门店不可用')
    return code


def save_store(db, user, data):
    hq(user)
    code = txt(data.get('code'), '门店编码', 40)
    name = txt(data.get('name'), '门店名称', 80)
    if data.get('id') is None:
        return db.execute('INSERT INTO stores(code,name) VALUES(?,?)', (code, name)).lastrowid
    store = store_for(db, user, data['id'])
    db.execute('UPDATE stores SET code=?,name=? WHERE id=?', (code, name, store))
    return store


def get_product(db, product_id, active=True):
    found = row(db, 'SELECT * FROM products WHERE id=?', (ident(product_id, '商品编号'),))
    if not found or (active and not found['active']):
        raise Problem('所选商品不可用')
    return found


def save_product(db, user, data, *, catalog=False):
    hq(user)
    sku = txt(data.get('sku'), '商品编码', 70)
    name = txt(data.get('name'), '商品名称', 120)
    brand = txt(data.get('brand'), '品牌', 60)
    spec = txt(data.get('spec'), '规格', 40)
    category = txt(data.get('category'), '商品分类', 60)
    kind = next((key for key, label in CATEGORIES.items() if label == category), None) if catalog else data.get('kind')
    if kind not in CATEGORIES:
        raise Problem('请选择有效的商品分类')
    current = None
    if data.get('id') is not None:
        current = get_product(db, data['id'], active=catalog)
    elif catalog:
        current = row(db, 'SELECT * FROM products WHERE sku=? AND active=0', (sku,))
    if current:
        if (brand, spec) != (current['brand'], current['spec']) or (
            kind != current['kind'] and {kind, current['kind']} != {'bottle','other'}
        ):
            raise Problem('请为新的品牌、规格或桶型建立新商品')
    bucket_id = None
    if kind == 'water':
        empty = row(db, """SELECT * FROM products WHERE kind='empty'
             AND brand=? COLLATE NOCASE AND spec=? COLLATE NOCASE""", (brand, spec))
        if empty is None:
            import secrets
            bucket_id = db.execute('''INSERT INTO products(sku,name,brand,spec,category,kind)
                VALUES(?,?,?,?,?,'empty')''',
                ('KT-' + secrets.token_hex(5).upper(), f'{brand} {spec} 空桶', brand, spec, '空桶系列')).lastrowid
        else:
            bucket_id = empty['id']
            if not empty['active'] and catalog:
                db.execute('UPDATE products SET active=1 WHERE id=?', (bucket_id,))
            elif not empty['active'] and data.get('active', True):
                raise Problem('请先启用对应品牌和规格的空桶')
    active = True if catalog else data.get('active', True)
    if type(active) is not bool:
        raise Problem('商品状态无效')
    if not active and kind == 'empty' and current:
        if scalar(db, 'SELECT 1 FROM products WHERE bucket_id=? AND active=1 LIMIT 1', (current['id'],)):
            raise Problem('请先停用仍使用此空桶的满水商品')
    if current:
        db.execute('UPDATE products SET sku=?,name=?,category=?,kind=?,active=? WHERE id=?',
                   (sku, name, category, kind, int(active), current['id']))
        return current['id']
    if catalog and kind == 'empty':
        retired = scalar(db, """SELECT id FROM products WHERE kind='empty' AND active=0
            AND brand=? COLLATE NOCASE AND spec=? COLLATE NOCASE""", (brand, spec))
        if retired is not None:
            db.execute('UPDATE products SET sku=?,name=?,category=?,active=1 WHERE id=?', (sku, name, category, retired))
            return retired
    return db.execute('''INSERT INTO products(sku,name,brand,spec,category,kind,bucket_id,active)
        VALUES(?,?,?,?,?,?,?,?)''', (sku, name, brand, spec, category, kind, bucket_id, int(active))).lastrowid


def delete_product(db, user, data):
    hq(user)
    product = get_product(db, data.get('id'), active=False)
    product_id = product['id']
    if scalar(db, 'SELECT 1 FROM stock WHERE product_id=? AND qty>0 LIMIT 1', (product_id,)):
        raise Problem('此商品仍有门店库存，请先核对并处理结存', 409)
    if scalar(db, 'SELECT 1 FROM products WHERE bucket_id=? AND active=1 LIMIT 1', (product_id,)):
        raise Problem('此空桶仍关联在售的桶装水，请先处理对应商品', 409)
    db.execute('UPDATE products SET active=0 WHERE id=?', (product_id,))
    return product_id


def customer_for(db, user, customer_id):
    found = row(db, 'SELECT * FROM customers WHERE id=?', (ident(customer_id, '客户编号'),))
    if found is None:
        raise Problem('所选客户不存在')
    return found


def save_customer(db, user, data):
    name = txt(data.get('name'), '客户名称', 100)
    phone = phone_num(data.get('phone'))
    address = txt(data.get('address'), '地址', 220, optional=True)
    if data.get('id') is not None:
        customer = customer_for(db, user, data['id'])
        if user['role'] != 'hq' and customer['creator_store'] != user['store_id']:
            raise Problem('全公司共享客户档案，请联系总部修改', 403)
        db.execute('UPDATE customers SET name=?,phone=?,address=? WHERE id=?',
                   (name, phone, address, customer['id']))
        return customer['id']
    if scalar(db, 'SELECT 1 FROM customers WHERE phone=?', (phone,)):
        raise Problem('此联系电话已有客户，请查找后使用原档案', 409)
    return db.execute('INSERT INTO customers(name,phone,address,creator_store,created_day) VALUES(?,?,?,?,?)',
                      (name, phone, address, user.get('store_id'), datetime.now(CHINA).date().isoformat())).lastrowid


class Posting:
    def __init__(self, db, user, data, *, order=False):
        self.db = db
        self.user = user
        self.data = data
        self.kind = data.get('kind')
        self.order = order
        self.stock = defaultdict(int)
        self.buckets = defaultdict(lambda: [0, 0, 0, 0, 0])
        self.sales = defaultdict(int)
        self.dispatch = defaultdict(int)
        self.order_buckets = defaultdict(lambda: [0, 0, 0])
        self.items = {}
        self.customer = None
        self.target = None
        self.link = None
        self.source = None
        self.external = None
        self.external_replaces = None

    def product(self, product_id, kind=None, active=True):
        found = get_product(self.db, product_id, active)
        if kind and found['kind'] != kind:
            raise Problem('请选择满水商品' if kind == 'water' else '请选择对应品牌和规格的空桶')
        return found

    def line_entries(self, product_kind=None, zero=False, active=True):
        lines = self.data.get('items')
        if not isinstance(lines, list) or not 1 <= len(lines) <= 100:
            raise Problem('请填写 1–100 条商品明细')
        for line in lines:
            if not isinstance(line, dict):
                raise Problem('商品明细格式错误')
            product = self.product(line.get('product_id'), product_kind, active)
            number = integer(line.get('qty'), '数量', 0 if zero else 1)
            if product['id'] in self.items:
                raise Problem('同一单据请合并相同商品')
            self.items[product['id']] = (product, number, 0)
            yield product, number, line

    def bucket(self, bucket_id, deposit=0, held=0, sent=0, returned=0, full_returned=0):
        for index, number in enumerate((deposit, held, sent, returned, full_returned)):
            self.buckets[bucket_id][index] += number

    def require_customer(self):
        self.customer = customer_for(self.db, self.user, self.data.get('customer_id'))['id']
        if self.user['role'] == 'store':
            known = scalar(self.db, '''SELECT 1 FROM customers c WHERE c.id=? AND
                (c.creator_store=? OR EXISTS(SELECT 1 FROM documents d WHERE d.customer_id=c.id AND d.store_id=?))''',
                (self.customer, self.store, self.store))
            if not known:
                supplied = phone_num(self.data.get('customer_phone'))
                if not scalar(self.db, 'SELECT 1 FROM customers WHERE id=? AND phone=?', (self.customer, supplied)):
                    raise Problem('请用客户完整联系电话核对共享档案', 403)

    def base_document(self, expected=None, active=True):
        self.link = ident(self.data.get('linked_id'), '关联单据')
        base = row(self.db, 'SELECT * FROM documents WHERE id=?', (self.link,))
        if not base or base['store_id'] != self.store or (expected and base['kind'] != expected):
            raise Problem('找不到此门店的关联单据', 404)
        if active and scalar(self.db, "SELECT 1 FROM documents WHERE kind='REVERSE' AND linked_id=?", (self.link,)):
            raise Problem('原单据已冲销', 409)
        self.customer = base['customer_id']
        return base

    def counts(self):
        for product, number, _ in self.line_entries(zero=self.kind in ('COUNT','OPEN_STOCK'), active=False):
            product_id = product['id']
            current = scalar(self.db, 'SELECT qty FROM stock WHERE store_id=? AND product_id=?', (self.store, product_id)) or 0
            if not product['active'] and self.kind not in ('OUT','COUNT'):
                raise Problem(f'{product["name"]}已停用')
            if self.kind == 'OPEN_STOCK' and scalar(self.db, """SELECT 1 FROM stock_log s
                JOIN documents d ON d.id=s.doc_id WHERE s.store_id=? AND s.product_id=?
                AND NOT (d.kind='OPEN_STOCK' AND EXISTS(SELECT 1 FROM documents r WHERE r.kind='REVERSE' AND r.linked_id=d.id))
                AND NOT (d.kind='REVERSE' AND EXISTS(SELECT 1 FROM documents orig WHERE orig.id=d.linked_id AND orig.kind='OPEN_STOCK'))
                LIMIT 1""", (self.store, product_id)):
                raise Problem(f'{product["name"]}已开账或已有出入库，请使用盘点')
            number = number - current if self.kind in ('COUNT','OPEN_STOCK') else number
            self.stock[(self.store, product_id)] = -number if self.kind == 'OUT' else number

    def transfer(self):
        self.target = ident(self.data.get('target_store'), '调入门店')
        if self.target == self.store:
            raise Problem('调出和调入门店必须不同')
        if not scalar(self.db, 'SELECT 1 FROM stores WHERE id=? AND active=1', (self.target,)):
            raise Problem('调入门店不可用')
        for product, qty, _ in self.line_entries():
            self.stock[(self.store, product['id'])] -= qty
            self.stock[(self.target, product['id'])] += qty

    def sale(self):
        self.require_customer()
        self.source = txt(self.data.get('source'), '订单来源', 20)
        if self.source not in SOURCES:
            raise Problem('订单来源无效')
        self.external = txt(self.data.get('external_number'), '原订单号', 90, optional=True)
        if self.source == '小程序' and not self.external:
            raise Problem('小程序订单必须填写原订单号')
        if self.external:
            claim = row(self.db, '''SELECT o.doc_id,d.store_id,
                EXISTS(SELECT 1 FROM documents r WHERE r.kind='REVERSE' AND r.linked_id=d.id) AS reversed
                FROM external_orders o JOIN documents d ON d.id=o.doc_id WHERE o.source=? AND o.number=?''',
                (self.source, self.external))
            if claim and (not claim['reversed'] or claim['store_id'] != self.store):
                raise Problem('此来源及原订单号已登记，请核对原单', 409)
            if claim:
                self.external_replaces = claim['doc_id']
        sent = self.data.get('dispatch_now', True)
        if type(sent) is not bool:
            raise Problem('请选择是否已经送出')
        for product, qty, _ in self.line_entries('water'):
            product_id = product['id']
            self.stock[(self.store, product_id)] -= qty
            self.sales[product_id] += qty
            if sent:
                self.dispatch[(None, product_id)] += qty
                self.bucket(product['bucket_id'], held=qty, sent=qty)

    def store_order(self):
        physical = self.data.get('empty_returns')
        for product, qty, _ in self.line_entries():
            product_id = product['id']
            self.stock[(self.store, product_id)] -= qty
            self.dispatch[(None, product_id)] += qty
            if product['kind'] == 'water':
                self.order_buckets[product['bucket_id']][0] += qty
        if physical is None and not self.order_buckets:
            physical = []
        if not isinstance(physical, list) or len(physical) > 100:
            raise Problem('请确认实收空桶，明细最多 100 条')
        if physical and not self.order_buckets:
            raise Problem('仅含桶装水的订单可登记实收空桶')
        seen = set()
        for entry in physical:
            if not isinstance(entry, dict):
                raise Problem('空桶明细格式错误')
            bucket = self.product(entry.get('product_id'), 'empty')['id']
            qty = integer(entry.get('qty'), '实收空桶数量')
            if bucket in seen:
                raise Problem('请合并相同品牌和规格的实收空桶')
            seen.add(bucket)
            self.order_buckets[bucket][1] += qty
            self.stock[(self.store, bucket)] += qty

    def change_bucket(self):
        self.require_customer()
        for product, qty, line in self.line_entries('empty', self.kind == 'OPEN_BUCKET', active=False):
            bucket = product['id']
            if self.kind == 'OPEN_BUCKET':
                if not product['active']:
                    raise Problem(f'{product["name"]}已停用')
                held = integer(line.get('held', 0), '期初未归还数', 0)
                previous = scalar(self.db, """SELECT 1 FROM bucket_log b JOIN documents d ON d.id=b.doc_id
                    WHERE b.customer_id=? AND b.bucket_id=? AND
                    (d.kind NOT IN ('OPEN_BUCKET','REVERSE') OR (d.kind='OPEN_BUCKET' AND b.store_id=?))
                    AND NOT EXISTS(SELECT 1 FROM documents r WHERE r.kind='REVERSE' AND r.linked_id=d.id)
                    LIMIT 1""", (self.customer, bucket, self.store))
                if previous:
                    raise Problem(f'{product["name"]}已开账或已有桶账，请使用正常业务记录')
                self.bucket(bucket, deposit=qty, held=held)
            elif self.kind == 'DEPOSIT':
                if not product['active']:
                    raise Problem(f'{product["name"]}已停用')
                self.bucket(bucket, deposit=qty)
            elif self.kind == 'REFUND':
                local_deposits = scalar(self.db, '''SELECT COALESCE(SUM(deposit_delta),0)
                    FROM bucket_log WHERE customer_id=? AND bucket_id=? AND store_id=?''',
                    (self.customer, bucket, self.store))
                if qty > local_deposits:
                    raise Problem(f'{product["name"]}在本店已押 {local_deposits} 个，请在原押桶门店办理退押', 409)
                self.bucket(bucket, deposit=-qty)
            else:
                self.bucket(bucket, held=-qty, returned=qty)
                self.stock[(self.store, bucket)] += qty

    def return_or_send(self):
        sale = self.base_document('SALE')
        anonymous = sale['customer_id'] is None
        if anonymous and self.kind == 'SEND':
            raise Problem('门店订单确认时已登记送出')
        for product, qty, entry in self.line_entries(None if anonymous else 'water', active=False):
            product_id = product['id']
            sale_qty = scalar(self.db, 'SELECT qty FROM document_items WHERE doc_id=? AND product_id=?',
                              (self.link, product_id)) or 0
            sent_qty = scalar(self.db, 'SELECT SUM(qty) FROM dispatch_log WHERE sale_id=? AND product_id=?',
                              (self.link, product_id)) or 0
            if self.kind == 'SEND':
                if qty > sale_qty - sent_qty:
                    raise Problem(f'{product["name"]}超过原订单待送出数量')
                self.dispatch[(self.link, product_id)] += qty
                self.bucket(product['bucket_id'], held=qty, sent=qty)
                continue
            returned = scalar(self.db, """SELECT SUM(i.qty) FROM documents d JOIN document_items i ON i.doc_id=d.id
                WHERE d.kind='RETURN' AND d.linked_id=? AND i.product_id=?
                AND NOT EXISTS(SELECT 1 FROM documents rev WHERE rev.kind='REVERSE' AND rev.linked_id=d.id)""",
                (self.link, product_id)) or 0
            if qty > sent_qty - returned:
                raise Problem(f'{product["name"]}超过原订单已送出且未退数量')
            restock = integer(entry.get('restock', qty), '可再次销售数量', 0, qty)
            self.items[product_id] = (product, qty, restock)
            if anonymous and product['kind'] == 'water':
                self.order_buckets[product['bucket_id']][2] += qty
            if not anonymous:
                self.sales[product_id] -= qty
                self.bucket(product['bucket_id'], held=-qty, full_returned=qty)
            if restock:
                self.stock[(self.store, product_id)] += restock

    def reverse(self):
        original = self.base_document()
        if original['kind'] == 'REVERSE':
            raise Problem('冲销单不可再次冲销，请按实际情况登记新单')
        if original['kind'] in ('OPEN_STOCK','OPEN_BUCKET'):
            hq(self.user)
        if not self.note:
            raise Problem('请填写冲销原因')
        if original['kind'] in ('SALE','SEND'):
            sale_id = original['id'] if original['kind'] == 'SALE' else original['linked_id']
            child = scalar(self.db, """SELECT 1 FROM documents d WHERE d.linked_id=? AND
                (d.kind='RETURN' OR (d.kind='SEND' AND ?='SALE'))
                AND NOT EXISTS(SELECT 1 FROM documents r WHERE r.kind='REVERSE' AND r.linked_id=d.id)
                LIMIT 1""", (sale_id, original['kind']))
            if child:
                raise Problem('请先核对并处理关联的补送或退货单')
        self.target = original['target_store']
        for move in self.db.execute('SELECT * FROM stock_log WHERE doc_id=?', (self.link,)):
            self.stock[(move['store_id'], move['product_id'])] -= move['delta']
        for move in self.db.execute('SELECT * FROM bucket_log WHERE doc_id=?', (self.link,)):
            self.buckets[move['bucket_id']] = [-move[key] for key in
                ('deposit_delta','held_delta','sent','returned','full_returned')]
        for move in self.db.execute('SELECT * FROM order_bucket_log WHERE doc_id=?', (self.link,)):
            self.order_buckets[move['bucket_id']] = [-move[key] for key in ('sent','received','full_returned')]
        for move in self.db.execute('SELECT * FROM sales_log WHERE doc_id=?', (self.link,)):
            self.sales[move['product_id']] -= move['qty']
        for move in self.db.execute('SELECT * FROM dispatch_log WHERE doc_id=?', (self.link,)):
            self.dispatch[(move['sale_id'], move['product_id'])] -= move['qty']
        for move in self.db.execute('SELECT * FROM document_items WHERE doc_id=?', (self.link,)):
            self.items[move['product_id']] = (move, move['qty'], move['restock'])

    def write(self):
        db = self.db
        data = self.data
        key = txt(data.get('request_key'), '请求编号', 75)
        if not re.fullmatch(r'[A-Za-z0-9_-]{12,75}', key):
            raise Problem('提交编号有误，请刷新后重试')
        fingerprint_data = {**data, '_order': True} if self.order else data
        fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        existing = row(db, 'SELECT * FROM documents WHERE request_key=?', (key,))
        if existing:
            if existing['operator'] != self.user['id'] or existing['fingerprint'] != fingerprint:
                raise Problem('提交编号已用于另一笔业务，请刷新后重试', 409)
            return existing['id'], True
        self.store = store_for(db, self.user, data.get('store_id'))
        self.note = txt(data.get('note'), '备注', 300, optional=True)
        if self.kind in ('OPEN_STOCK','OPEN_BUCKET'):
            hq(self.user)
        if self.kind in ('IN','OUT','COUNT','OPEN_STOCK'):
            self.counts()
        elif self.kind == 'TRANSFER':
            self.transfer()
        elif self.kind == 'SALE':
            if self.order:
                self.store_order()
            else:
                self.sale()
        elif self.kind in ('BUCKET_RETURN','DEPOSIT','REFUND','OPEN_BUCKET'):
            self.change_bucket()
        elif self.kind in ('SEND','RETURN'):
            self.return_or_send()
        elif self.kind == 'REVERSE':
            self.reverse()
        else:
            raise Problem('请选择有效的单据类型')
        now = datetime.now(timezone.utc)
        doc = db.execute('''INSERT INTO documents(request_key,fingerprint,kind,store_id,target_store,customer_id,
            linked_id,source,external_number,business_day,timestamp,operator,operator_name,note)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (key, fingerprint, self.kind, self.store,
            self.target, self.customer, self.link, self.source, self.external, now.astimezone(CHINA).date().isoformat(),
            now.isoformat(timespec='seconds'), self.user['id'], self.user['name'], self.note)).lastrowid
        if self.external:
            if self.external_replaces:
                db.execute('UPDATE external_orders SET doc_id=? WHERE source=? AND number=?', (doc, self.source, self.external))
            else:
                db.execute('INSERT INTO external_orders VALUES(?,?,?)', (self.source, self.external, doc))
        for product_id, (product, qty, restock) in self.items.items():
            db.execute('INSERT INTO document_items VALUES(?,?,?,?,?,?,?,?)',
                       (doc, product_id, qty, restock, product['sku'], product['name'], product['brand'], product['spec']))
        for (store, product_id), delta in self.stock.items():
            previous = scalar(db, 'SELECT qty FROM stock WHERE store_id=? AND product_id=?', (store, product_id)) or 0
            after = previous + delta
            product = self.items[product_id][0] if product_id in self.items else self.product(product_id, active=False)
            if after < 0:
                raise Problem(f'{product["name"]}库存不足，当前库存 {previous}，本次需要 {-delta}', 409)
            if after > 1_000_000_000:
                raise Problem('库存数量超过上限')
            db.execute('''INSERT INTO stock VALUES(?,?,?) ON CONFLICT(store_id,product_id)
               DO UPDATE SET qty=excluded.qty''', (store, product_id, after))
            db.execute('''INSERT INTO stock_log(doc_id,store_id,product_id,sku,name,brand,spec,delta,before_qty,after_qty)
                VALUES(?,?,?,?,?,?,?,?,?,?)''', (doc, store, product_id, product['sku'], product['name'],
                product['brand'], product['spec'], delta, previous, after))
        for bucket_id, moves in self.buckets.items():
            balances = row(db, '''SELECT COALESCE(SUM(deposit_delta),0) AS deposits,
                COALESCE(SUM(held_delta),0) AS held FROM bucket_log WHERE customer_id=? AND bucket_id=?''',
                (self.customer, bucket_id))
            product = self.product(bucket_id, 'empty', active=False)
            final_deposits, final_held = balances['deposits'] + moves[0], balances['held'] + moves[1]
            original_deposit = scalar(db, '''SELECT COALESCE(SUM(deposit_delta),0) FROM bucket_log
                WHERE customer_id=? AND bucket_id=? AND store_id=?''', (self.customer,bucket_id,self.store))
            if original_deposit + moves[0] < 0:
                raise Problem(f'{product["brand"]} {product["spec"]} 本店押桶余额不足', 409)
            if final_deposits < 0:
                raise Problem(f'{product["brand"]} {product["spec"]} 押桶余额不足，目前已押 {balances["deposits"]} 个', 409)
            if final_held < 0:
                raise Problem(f'{product["brand"]} {product["spec"]} 未归还数不足，目前未归还 {balances["held"]} 个，请核对品牌、规格和旧账', 409)
            db.execute('INSERT INTO bucket_log VALUES(?,?,?,?,?,?,?,?,?)',
                       (doc, self.store, self.customer, bucket_id, *moves))
        for product_id, qty in self.sales.items():
            db.execute('INSERT INTO sales_log VALUES(?,?,?,?,?)', (doc, self.store, self.customer, product_id, qty))
        for bucket_id, moves in self.order_buckets.items():
            db.execute('INSERT INTO order_bucket_log VALUES(?,?,?,?,?)', (doc, bucket_id, *moves))
        for (sale_id, product_id), qty in self.dispatch.items():
            db.execute('INSERT INTO dispatch_log VALUES(?,?,?,?)', (doc, sale_id or doc, product_id, qty))
        return doc, False
