from collections import defaultdict
from datetime import datetime

from inventory import CHINA, Problem, get_product, phone_num, row, rows, scalar, store_for


def query(args, name, default=''):
    return args.get(name, [default])[0]


def date_value(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d').date().isoformat()
        if parsed != value:
            raise ValueError
        return parsed
    except ValueError as error:
        raise Problem('日期格式应为 YYYY-MM-DD') from error


def scope(db, user, args):
    choice = query(args, 'store')
    if choice:
        if not choice.isascii() or not choice.isdecimal() or len(choice) > 10:
            raise Problem('门店编号无效')
        return store_for(db, user, int(choice), active=False)
    return user['store_id']


def range_values(args, default_today=True):
    today = datetime.now(CHINA).date().isoformat()
    end = date_value(query(args, 'end', today) or today)
    start = date_value(query(args, 'start', today if default_today else '2000-01-01') or '2000-01-01')
    if start > end:
        raise Problem('开始日期须早于或等于结束日期')
    return start, end


def dashboard(db, user, args):
    store = scope(db, user, args)
    day = date_value(query(args, 'day') or query(args, 'end') or datetime.now(CHINA).date().isoformat())
    stores = rows(db, """SELECT s.id,s.name,COUNT(d.id) AS orders
        FROM stores s LEFT JOIN documents d ON d.store_id=s.id AND d.kind='SALE' AND d.business_day<=?2
          AND NOT EXISTS(SELECT 1 FROM documents rev WHERE rev.kind='REVERSE' AND rev.linked_id=d.id
                         AND rev.business_day<=?2)
        WHERE s.active=1 AND (?1 IS NULL OR s.id=?1) GROUP BY s.id ORDER BY s.code""", (store, day))
    stock = rows(db, """SELECT s.id AS store_id,p.id AS product_id,p.brand,p.kind,SUM(l.delta) AS qty
        FROM stock_log l JOIN documents d ON d.id=l.doc_id JOIN stores s ON s.id=l.store_id
        JOIN products p ON p.id=l.product_id
        WHERE (?1 IS NULL OR s.id=?1) AND s.active=1 AND d.business_day<=?2 AND p.kind IN ('water','empty')
        GROUP BY s.id,p.id HAVING SUM(l.delta)<>0""", (store, day))
    brand_totals = defaultdict(int)
    sku_totals = defaultdict(int)
    for item in stock:
        brand_totals[item['store_id'],item['brand'],item['kind']] += item['qty']
        sku_totals[item['product_id']] += item['qty']
    brand_stock = [dict(store_id=store_id,brand=brand,kind=kind,qty=qty)
                   for (store_id,brand,kind), qty in brand_totals.items()]
    sku_stock = rows(db, """SELECT p.id,p.sku,p.name,p.brand,p.kind,(d.product_id IS NOT NULL) AS selected
        FROM products p LEFT JOIN dashboard_products d ON d.product_id=p.id AND d.user_id=?
        WHERE p.active=1 AND p.kind IN ('water','empty') ORDER BY p.sku""", (user['id'],))
    for item in sku_stock:
        item['qty'] = sku_totals[item['id']]
    brands = sorted({item['brand'] for item in sku_stock})
    bucket_types = rows(db, """WITH daily AS (
        SELECT doc_id,bucket_id,sent,returned AS received,full_returned FROM bucket_log
        UNION ALL
        SELECT doc_id,bucket_id,sent,received,full_returned FROM order_bucket_log
    )
    SELECT s.name AS store,p.brand,p.spec,SUM(q.sent) AS sent,SUM(q.received) AS received,
        SUM(q.full_returned) AS full_returned,
        MAX(0,SUM(q.sent-q.received-q.full_returned)) AS missing
    FROM daily q JOIN documents d ON d.id=q.doc_id JOIN stores s ON s.id=d.store_id
    JOIN products p ON p.id=q.bucket_id
    LEFT JOIN documents original ON d.kind='REVERSE' AND original.id=d.linked_id
    WHERE (?1 IS NULL OR s.id=?1) AND COALESCE(original.business_day,d.business_day)=?2
    GROUP BY s.id,p.id HAVING SUM(q.sent)<>0 OR SUM(q.received)<>0 OR SUM(q.full_returned)<>0
    ORDER BY s.code,p.brand,p.spec""", (store, day))
    totals = {key: sum(r[key] for r in bucket_types) for key in ('sent','received','missing')}
    totals['orders'] = sum(s['orders'] for s in stores)
    return dict(day=day, today=datetime.now(CHINA).date().isoformat(), totals=totals,
                stores=stores, brands=brands, stock=brand_stock, sku_stock=sku_stock, bucket_types=bucket_types)


def update_dashboard_product(db, user, data):
    selected = data.get('selected')
    if type(selected) is not bool:
        raise Problem('请选择添加或移除商品编码')
    product = get_product(db, data.get('product_id'), active=selected)
    if product['kind'] not in ('water','empty'):
        raise Problem('请选择桶装水或空桶系列的商品编码')
    link = (user['id'],product['id'])
    if selected:
        db.execute('INSERT INTO dashboard_products VALUES (?,?) ON CONFLICT DO NOTHING', link)
    else:
        db.execute('DELETE FROM dashboard_products WHERE user_id=? AND product_id=?', link)
    return {'ok': True}


def stock_summary(db, user, args):
    store = scope(db, user, args)
    return rows(db, '''SELECT p.id,p.sku,p.name,p.brand,p.spec,p.kind,COALESCE(SUM(i.qty),0) AS qty
        FROM products p LEFT JOIN stock i ON i.product_id=p.id AND (?1 IS NULL OR i.store_id=?1)
          AND EXISTS(SELECT 1 FROM stores s WHERE s.id=i.store_id AND s.active=1)
        GROUP BY p.id HAVING p.active=1 OR COALESCE(SUM(i.qty),0)>0
        ORDER BY p.kind DESC,p.name,p.sku''', (store,))


def stock_rows(db, user, args, product=None):
    store = scope(db, user, args)
    return rows(db, '''SELECT s.id AS store_id,s.name AS store,p.id,p.sku,p.name,p.brand,p.spec,p.kind,p.active,
        COALESCE(i.qty,0) AS qty FROM stores s CROSS JOIN products p
        LEFT JOIN stock i ON i.store_id=s.id AND i.product_id=p.id
        WHERE s.active=1 AND (?1 IS NULL OR s.id=?1) AND (p.active=1 OR i.qty>0)
          AND (?2 IS NULL OR p.id=?2)
        ORDER BY s.code,p.kind DESC,p.name,p.sku''', (store,product))


def history(db, user, args):
    store = scope(db, user, args)
    start, end = range_values(args, default_today=False)
    return rows(db, '''SELECT l.*,d.kind,d.timestamp,d.business_day,d.operator_name,d.note,d.linked_id,
           s.name AS store,rev.id AS reversal_id
           FROM stock_log l JOIN stores s ON s.id=l.store_id JOIN documents d ON d.id=l.doc_id
           LEFT JOIN documents rev ON rev.kind='REVERSE' AND rev.linked_id=d.id
           WHERE (?1 IS NULL OR l.store_id=?1) AND d.business_day BETWEEN ?2 AND ?3 ORDER BY l.id DESC''',
           (store,start,end))


def documents(db, user, args):
    store = scope(db, user, args)
    start, end = range_values(args, default_today=False)
    group = query(args, 'group')
    kinds = {
        'sales': " AND (d.kind IN ('SALE','SEND','RETURN') OR o.kind IN ('SALE','SEND','RETURN'))",
        'bucket': " AND (d.kind IN ('OPEN_BUCKET','BUCKET_RETURN','DEPOSIT','REFUND','SEND') OR o.kind IN ('OPEN_BUCKET','BUCKET_RETURN','DEPOSIT','REFUND','SEND'))",
        'transfer': " AND (d.kind='TRANSFER' OR o.kind='TRANSFER')",
        'purchases': " AND (d.kind='IN' OR o.kind='IN')",
        'all': '',
    }
    if group not in kinds:
        raise Problem('单据类型无效')
    return rows(db, '''SELECT d.id,d.kind,d.store_id,d.target_store,d.business_day,d.timestamp,d.operator_name,d.note,
        d.linked_id,rev.id AS reversal_id,
        f.name AS store,t.name AS target,GROUP_CONCAT(i.name || ' × ' || i.qty,'、') AS detail,
        (SELECT COALESCE(SUM(i2.qty),0) FROM document_items i2 WHERE i2.doc_id=d.id) AS total_qty
        FROM documents d JOIN stores f ON f.id=d.store_id LEFT JOIN stores t ON t.id=d.target_store
        LEFT JOIN document_items i ON i.doc_id=d.id
        LEFT JOIN documents rev ON rev.kind='REVERSE' AND rev.linked_id=d.id
        LEFT JOIN documents o ON o.id=d.linked_id
        WHERE (?1 IS NULL OR d.store_id=?1 OR d.target_store=?1) AND d.business_day BETWEEN ?2 AND ?3'''
        + kinds[group] + ' GROUP BY d.id ORDER BY d.id DESC', (store,start,end))


def document_detail(db, user, document_id):
    doc = row(db, '''SELECT d.*,f.name AS store,t.name AS target,c.name AS customer, rev.id AS reversal_id
        FROM documents d JOIN stores f ON f.id=d.store_id LEFT JOIN stores t ON t.id=d.target_store
        LEFT JOIN customers c ON c.id=d.customer_id
        LEFT JOIN documents rev ON rev.kind='REVERSE' AND rev.linked_id=d.id WHERE d.id=?''', (document_id,))
    if not doc or (user['role']=='store' and user['store_id'] not in (doc['store_id'],doc['target_store'])):
        raise Problem('单据不存在或当前账号无权查看', 404)
    del doc['fingerprint'], doc['request_key']
    doc['items'] = rows(db, '''SELECT i.*,COALESCE((SELECT SUM(qty) FROM dispatch_log q
        WHERE q.sale_id=i.doc_id AND q.product_id=i.product_id),0) AS sent,
        COALESCE((SELECT SUM(x.qty) FROM documents r JOIN document_items x ON x.doc_id=r.id
        WHERE r.linked_id=i.doc_id AND r.kind='RETURN' AND x.product_id=i.product_id
          AND NOT EXISTS(SELECT 1 FROM documents v WHERE v.kind='REVERSE' AND v.linked_id=r.id)),0) AS returned
        FROM document_items i WHERE i.doc_id=? ORDER BY i.name''', (document_id,))
    doc['stock'] = rows(db, '''SELECT l.*,s.name AS store FROM stock_log l JOIN stores s ON s.id=l.store_id
        WHERE l.doc_id=? AND (? IS NULL OR l.store_id=?) ORDER BY l.id''',
        (document_id,user['store_id'],user['store_id']))
    doc['buckets'] = rows(db, '''SELECT b.*,p.name,p.brand,p.spec FROM bucket_log b JOIN products p ON p.id=b.bucket_id
        WHERE b.doc_id=? ORDER BY p.name''', (document_id,))
    doc['order_buckets'] = rows(db, '''SELECT b.*,p.name,p.brand,p.spec FROM order_bucket_log b
        JOIN products p ON p.id=b.bucket_id WHERE b.doc_id=? ORDER BY p.brand,p.spec''', (document_id,))
    return doc


def customers(db, user, args):
    choice = query(args, 'phone')
    if choice:
        phone = phone_num(choice)
        contacts = rows(db, '''SELECT c.id,c.name,c.phone,
            CASE WHEN ?2 IS NULL OR c.creator_store=?2 OR EXISTS(
                SELECT 1 FROM documents d WHERE d.customer_id=c.id AND d.store_id=?2)
            THEN c.address ELSE '' END AS address,c.creator_store
            FROM customers c WHERE c.phone=?1''', (phone,user['store_id']))
    else:
        store = scope(db,user,args)
        contacts = rows(db, '''SELECT c.*,c.address FROM customers c
            WHERE ?1 IS NULL OR c.creator_store=?1 OR EXISTS(
            SELECT 1 FROM documents d WHERE d.customer_id=c.id AND d.store_id=?1)
            ORDER BY c.name,c.id''', (store,))
    return contacts


def customer_balances(db, user, customer_id, args):
    contacts = customers(db, user, args)
    if not any(entry['id']==customer_id for entry in contacts):
        raise Problem('客户不存在或请用完整联系电话查找', 404)
    return rows(db, '''SELECT p.id,p.sku,p.name,p.brand,p.spec,
           SUM(b.deposit_delta) AS deposits,SUM(b.held_delta) AS held
        FROM bucket_log b JOIN products p ON p.id=b.bucket_id WHERE b.customer_id=?
        GROUP BY b.bucket_id HAVING SUM(b.deposit_delta)<>0 OR SUM(b.held_delta)<>0 ORDER BY p.brand,p.spec''',
        (customer_id,))


def bucket_rows(db, user, args):
    store = scope(db,user,args)
    start,end = range_values(args,default_today=False)
    return rows(db, '''SELECT c.name AS customer,c.id AS customer_id,s.name AS store,p.name,p.brand,p.spec,p.sku,
        SUM(b.deposit_delta) AS deposits,SUM(b.held_delta) AS held,
        SUM(CASE WHEN d.business_day>=?3 THEN b.sent ELSE 0 END) AS sent,
        SUM(CASE WHEN d.business_day>=?3 THEN b.returned ELSE 0 END) AS returned,
        SUM(CASE WHEN d.business_day>=?3 THEN b.full_returned ELSE 0 END) AS full_returned
        FROM bucket_log b JOIN documents d ON d.id=b.doc_id JOIN products p ON p.id=b.bucket_id
        JOIN stores s ON s.id=b.store_id JOIN customers c ON c.id=b.customer_id
        WHERE (?1 IS NULL OR b.store_id=?1) AND d.business_day<=?2
        GROUP BY b.store_id,b.customer_id,b.bucket_id ORDER BY s.code,c.name,p.brand,p.spec''',
        (store,end,start))
