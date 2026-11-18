PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS stores (
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, login TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
  password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('hq','store')),
  store_id INTEGER REFERENCES stores(id), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  CHECK((role='hq' AND store_id IS NULL) OR (role='store' AND store_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY, sku TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT NOT NULL,
  brand TEXT NOT NULL, spec TEXT NOT NULL, category TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('water','empty')), bucket_id INTEGER REFERENCES products(id),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  CHECK((kind='water' AND bucket_id IS NOT NULL) OR (kind='empty' AND bucket_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS empty_brand_spec ON products(brand COLLATE NOCASE,spec COLLATE NOCASE) WHERE kind='empty';
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL UNIQUE, address TEXT NOT NULL DEFAULT '',
  creator_store INTEGER REFERENCES stores(id), created_day TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('OPEN_STOCK','OPEN_BUCKET','IN','OUT','COUNT','TRANSFER','SALE','SEND','BUCKET_RETURN','DEPOSIT','REFUND','RETURN','REVERSE')),
  store_id INTEGER NOT NULL REFERENCES stores(id), target_store INTEGER REFERENCES stores(id),
  customer_id INTEGER REFERENCES customers(id), linked_id INTEGER REFERENCES documents(id),
  source TEXT, external_number TEXT COLLATE NOCASE,
  business_day TEXT NOT NULL, timestamp TEXT NOT NULL,
  operator INTEGER NOT NULL REFERENCES users(id), operator_name TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS one_reversal ON documents(linked_id) WHERE kind='REVERSE';
CREATE INDEX IF NOT EXISTS doc_day ON documents(store_id,business_day,id);
CREATE INDEX IF NOT EXISTS doc_link ON documents(linked_id,kind);
CREATE INDEX IF NOT EXISTS doc_customer ON documents(customer_id,store_id,business_day);
CREATE TABLE IF NOT EXISTS opening_imports (
  request_key TEXT PRIMARY KEY, operator INTEGER NOT NULL REFERENCES users(id), fingerprint TEXT NOT NULL,
  line_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS external_orders (
  source TEXT NOT NULL, number TEXT NOT NULL COLLATE NOCASE,
  doc_id INTEGER NOT NULL UNIQUE REFERENCES documents(id), PRIMARY KEY(source,number)
);
CREATE TABLE IF NOT EXISTS document_items (
  doc_id INTEGER NOT NULL REFERENCES documents(id), product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL CHECK(qty>=0), restock INTEGER NOT NULL DEFAULT 0 CHECK(restock>=0),
  sku TEXT NOT NULL, name TEXT NOT NULL, brand TEXT NOT NULL, spec TEXT NOT NULL,
  PRIMARY KEY(doc_id,product_id)
);
CREATE TABLE IF NOT EXISTS stock (
  store_id INTEGER NOT NULL REFERENCES stores(id), product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL CHECK(qty BETWEEN 0 AND 1000000000), PRIMARY KEY(store_id,product_id)
);
CREATE TABLE IF NOT EXISTS stock_log (
  id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL REFERENCES documents(id),
  store_id INTEGER NOT NULL REFERENCES stores(id), product_id INTEGER NOT NULL REFERENCES products(id),
  sku TEXT NOT NULL, name TEXT NOT NULL, brand TEXT NOT NULL, spec TEXT NOT NULL,
  delta INTEGER NOT NULL, before_qty INTEGER NOT NULL CHECK(before_qty>=0),
  after_qty INTEGER NOT NULL CHECK(after_qty>=0 AND after_qty=before_qty+delta),
  UNIQUE(doc_id,store_id,product_id)
);
CREATE INDEX IF NOT EXISTS stock_log_store ON stock_log(store_id,product_id,doc_id);
CREATE TABLE IF NOT EXISTS bucket_log (
  doc_id INTEGER NOT NULL REFERENCES documents(id), store_id INTEGER NOT NULL REFERENCES stores(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id), bucket_id INTEGER NOT NULL REFERENCES products(id),
  deposit_delta INTEGER NOT NULL, held_delta INTEGER NOT NULL,
  sent INTEGER NOT NULL, returned INTEGER NOT NULL, full_returned INTEGER NOT NULL,
  PRIMARY KEY(doc_id,bucket_id)
);
CREATE INDEX IF NOT EXISTS bucket_customer ON bucket_log(customer_id,bucket_id,store_id,doc_id);
CREATE INDEX IF NOT EXISTS bucket_store ON bucket_log(store_id,doc_id);
CREATE TABLE IF NOT EXISTS sales_log (
  doc_id INTEGER NOT NULL REFERENCES documents(id), store_id INTEGER NOT NULL REFERENCES stores(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id), product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL, PRIMARY KEY(doc_id,product_id)
);
CREATE INDEX IF NOT EXISTS sales_store ON sales_log(store_id,doc_id);
CREATE TABLE IF NOT EXISTS dispatch_log (
  doc_id INTEGER NOT NULL REFERENCES documents(id), sale_id INTEGER NOT NULL REFERENCES documents(id),
  product_id INTEGER NOT NULL REFERENCES products(id), qty INTEGER NOT NULL,
  PRIMARY KEY(doc_id,product_id)
);
CREATE INDEX IF NOT EXISTS dispatch_sale ON dispatch_log(sale_id,product_id);
CREATE TRIGGER IF NOT EXISTS documents_immutable_update BEFORE UPDATE ON documents BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS documents_immutable_delete BEFORE DELETE ON documents BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS document_items_immutable_update BEFORE UPDATE ON document_items BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS document_items_immutable_delete BEFORE DELETE ON document_items BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS stock_log_immutable_update BEFORE UPDATE ON stock_log BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS stock_log_immutable_delete BEFORE DELETE ON stock_log BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS bucket_log_immutable_update BEFORE UPDATE ON bucket_log BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS bucket_log_immutable_delete BEFORE DELETE ON bucket_log BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS sales_log_immutable_update BEFORE UPDATE ON sales_log BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS sales_log_immutable_delete BEFORE DELETE ON sales_log BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS dispatch_log_immutable_update BEFORE UPDATE ON dispatch_log BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
CREATE TRIGGER IF NOT EXISTS dispatch_log_immutable_delete BEFORE DELETE ON dispatch_log BEGIN SELECT RAISE(ABORT, 'immutable history'); END;
