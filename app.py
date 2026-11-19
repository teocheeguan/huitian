import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import webbrowser
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import reports
from inventory import (CATEGORIES, CHINA, ROOT, TITLES, Posting, Problem, connect, delete_product, hq,
                       ident, initialize, row, rows, save_customer, save_product, save_store, scalar, store_for, txt)

ITERATIONS = 600_000
SESSION_AGE = 12 * 60 * 60
MAX_REQUEST = 3_530_000
ASSETS = {
    '/': ('index.html','text/html; charset=utf-8'),
    '/styles.css': ('styles.css','text/css; charset=utf-8'),
    '/app.js': ('app.js','text/javascript; charset=utf-8'),
}
FAKE_PASSWORD = '00' * 16 + ':' + '00' * 32
LOGIN_ERRORS = '账号或密码不正确'


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    encoded = hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),ITERATIONS).hex()
    return f'{salt}:{encoded}'


def login_name(value):
    result = txt(value,'登录账号',40)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,40}', result):
        raise Problem('账号须为 3–40 位英文字母、数字、下划线、点或连字符')
    return result


def validated_password(password):
    password = txt(password,'密码',128)
    if len(password) < 12:
        raise Problem('密码至少 12 个字符')
    return password


def save_user(db, operator, data):
    hq(operator)
    username = login_name(data.get('login'))
    name = txt(data.get('name'),'显示姓名',70)
    role = data.get('role')
    if role not in ('hq','store'):
        raise Problem('请选择账号角色')
    store = store_for(db,operator,data.get('store_id')) if role == 'store' else None
    enabled = data.get('active',True)
    if type(enabled) is not bool:
        raise Problem('账号状态有误')
    password = data.get('password')
    password_hash = hash_password(validated_password(password)) if password else None
    if data.get('id') is None:
        if not password_hash:
            raise Problem('请为新账号设置密码')
        return db.execute('INSERT INTO users(login,name,password,role,store_id,active) VALUES(?,?,?,?,?,?)',
                          (username,name,password_hash,role,store,int(enabled))).lastrowid
    uid = ident(data['id'],'账号编号')
    previous = row(db,'SELECT * FROM users WHERE id=?',(uid,))
    if not previous:
        raise Problem('找不到账号')
    if uid == operator['id'] and (role != 'hq' or not enabled):
        raise Problem('不能移除当前账号的总部权限')
    db.execute('''UPDATE users SET login=?,name=?,password=COALESCE(?,password),role=?,store_id=?,active=? WHERE id=?''',
               (username,name,password_hash,role,store,int(enabled),uid))
    if password or not enabled or role != previous['role'] or store != previous['store_id']:
        db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
    return uid


class RateLimiter:
    def __init__(self):
        self.attempts = defaultdict(list)
        self.lock = threading.Lock()

    def add(self,key):
        now = time.monotonic()
        with self.lock:
            for item,times in list(self.attempts.items()):
                alive = [value for value in times if value > now-300]
                if alive:
                    self.attempts[item] = alive
                else:
                    del self.attempts[item]
            recent = self.attempts[key]
            if len(recent)>=25 or len(self.attempts)>5000:
                raise Problem('登录尝试过多，请在 5 分钟后重试', 429)
            recent.append(now)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 40

    def __init__(self, address, database, secure=False, base_path='', public_origin=''):
        self.database = database
        self.secure = secure
        self.base_path = base_path
        self.public_origin = public_origin
        self.rate_limiter = RateLimiter()
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Huitian/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, message, *args):
        if '/api/' not in self.path or '/api/login' in self.path:
            super().log_message(message,*args)

    def reply(self,status,content,content_type='application/json; charset=utf-8',cookie=None,filename=None,location=None):
        if not isinstance(content,bytes):
            content = json.dumps(content,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Robots-Tag','noindex, nofollow')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if location:
            self.send_header('Location',location)
        if cookie is not None:
            extra = '; Secure' if self.server.secure else ''
            max_age = SESSION_AGE if cookie else 0
            scope = self.server.base_path + '/'
            self.send_header('Set-Cookie',f'huitian_session={cookie}; Path={scope}; HttpOnly; SameSite=Strict; Max-Age={max_age}{extra}')
        if filename:
            self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def read_json(self):
        if self.headers.get_content_type() != 'application/json':
            raise Problem('请求必须使用 JSON',415)
        try:
            size = int(self.headers.get('Content-Length','0'))
        except ValueError as error:
            raise Problem('请求长度无效') from error
        if not 0 < size <= MAX_REQUEST:
            raise Problem('请求内容为空或过大',413)
        try:
            data = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise Problem('请求格式无效') from error
        if not isinstance(data,dict):
            raise Problem('请求数据格式无效')
        return data

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie',''))
        except ValueError:
            return ''
        session = cookie.get('huitian_session')
        if not session or not re.fullmatch(r'[a-f0-9]{64}',session.value):
            return ''
        return hashlib.sha256(session.value.encode()).hexdigest()

    def current_user(self,db):
        return row(db,'''SELECT u.id,u.login,u.name,u.role,u.store_id,t.name AS store
            FROM sessions s JOIN users u ON u.id=s.user_id LEFT JOIN stores t ON t.id=u.store_id
            WHERE s.token=? AND s.expires>? AND u.active=1 AND (t.id IS NULL OR t.active=1)''',
            (self.token(),int(time.time())))

    def login(self,db,data):
        username = login_name(data.get('login'))
        self.server.rate_limiter.add((self.client_address[0],username.casefold()))
        password = txt(data.get('password'),'密码',128)
        user = row(db,'''SELECT u.* FROM users u LEFT JOIN stores t ON t.id=u.store_id WHERE
            u.login=? AND u.active=1 AND (t.id IS NULL OR t.active=1)''',(username,))
        stored = user['password'] if user else FAKE_PASSWORD
        if not hmac.compare_digest(hash_password(password,stored.split(':',1)[0]),stored) or not user:
            raise Problem(LOGIN_ERRORS,401)
        token = secrets.token_hex(32)
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM sessions WHERE expires<=?',(int(time.time()),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(),user['id'],int(time.time())+SESSION_AGE))
        self.reply(200,{'ok':True},cookie=token)

    def get_api(self,db,user,path,args):
        if path == '/api/bootstrap':
            return {
                'user':user, 'today':datetime.now(CHINA).date().isoformat(), 'titles':TITLES,
                'stores': rows(db,'SELECT * FROM stores WHERE active=1 ORDER BY code'),
                'products': [dict(p, category=CATEGORIES[p['kind']]) for p in rows(db,
                    'SELECT * FROM products WHERE active=1 ORDER BY kind DESC,brand,spec,name')],
                'stock_adjustments': rows(db,'''SELECT p.* FROM products p WHERE p.active=0 AND EXISTS (
                    SELECT 1 FROM stock s WHERE s.product_id=p.id AND s.qty>0 AND (?1 IS NULL OR s.store_id=?1))''',
                    (user['store_id'],)),
                'categories': CATEGORIES,
            }
        readers = {
            '/api/dashboard':reports.dashboard,'/api/stock':reports.stock_rows,'/api/history':reports.history,
            '/api/documents':reports.documents,'/api/stock-summary':reports.stock_summary,
        }
        if path in readers:
            return readers[path](db,user,args)
        match = re.fullmatch('/api/stock/([0-9]{1,10})',path)
        if match:
            return reports.stock_rows(db,user,args,int(match[1]))
        match = re.fullmatch('/api/documents/([0-9]{1,10})',path)
        if match:
            detail = reports.document_detail(db,user,int(match[1]))
            for field in ('customer','customer_id','source','external_number','buckets'):
                detail.pop(field, None)
            return detail
        if path == '/api/users':
            hq(user)
            return rows(db,'''SELECT u.id,u.login,u.name,u.role,u.store_id,u.active,s.name AS store
                FROM users u LEFT JOIN stores s ON s.id=u.store_id ORDER BY role,u.login''')
        raise Problem('页面不存在',404)

    def post_api(self,db,user,path,data):
        if path == '/api/dashboard/products':
            return reports.update_dashboard_product(db,user,data)
        writers = {'/api/stores': save_store, '/api/products/delete':delete_product, '/api/users':save_user}
        if path in writers:
            return {'id':writers[path](db,user,data)}
        if path == '/api/products':
            return {'id':save_product(db,user,data,catalog=True)}
        if path == '/api/orders':
            fields = ('store_id', 'items', 'empty_returns', 'request_key', 'note')
            doc, duplicate = Posting(db,user,dict(kind='SALE', **{k:data[k] for k in fields if k in data}),
                                     order=True).write()
            return {'id':doc,'duplicate':duplicate}
        if path == '/api/post' and data.get('kind') in ('IN','OUT','COUNT','TRANSFER','SEND','RETURN','REVERSE'):
            doc, duplicate = Posting(db,user,data).write()
            return {'id':doc,'duplicate':duplicate}
        raise Problem('页面不存在',404)

    def process(self):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            args = parse_qs(parsed.query,max_num_fields=35)
            prefix = self.server.base_path
            if prefix:
                if path==prefix and self.command in ('GET','HEAD'):
                    self.reply(308,b'',location=prefix+'/')
                    return
                if not path.startswith(prefix+'/'):
                    raise Problem('页面不存在',404)
                path = path[len(prefix):]
            if self.command in ('GET','HEAD') and path in ASSETS:
                filename,mime = ASSETS[path]
                self.reply(200,(ROOT/'web'/filename).read_bytes(),mime)
                return
            if not path.startswith('/api/'):
                raise Problem('页面不存在',404)
            if self.command not in ('GET','POST'):
                raise Problem('不支持此请求方式',405)
            with closing(connect(self.server.database)) as db:
                if self.command == 'POST':
                    if self.server.public_origin and self.headers.get('Origin') != self.server.public_origin:
                        raise Problem('请从库存系统的正式 HTTPS 地址操作',403)
                    data = self.read_json()
                    if path == '/api/login':
                        self.login(db,data)
                        return
                    if path == '/api/logout':
                        db.execute('DELETE FROM sessions WHERE token=?',(self.token(),))
                        self.reply(200,{'ok':True},cookie='')
                        return
                user = self.current_user(db)
                if not user:
                    raise Problem('请先登录系统',401)
                if self.command == 'GET':
                    with db:
                        db.execute('BEGIN')
                        result = self.get_api(db,user,path,args)
                else:
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        result = self.post_api(db,user,path,data)
                self.reply(200,result)
        except Problem as error:
            self.reply(error.status,{'error':str(error)})
        except sqlite3.IntegrityError:
            self.reply(409,{'error':'编码、账号或商品已存在，请检查后重试'})
        except sqlite3.OperationalError as error:
            if 'locked' in str(error):
                self.reply(503,{'error':'系统正在处理其他单据，请原样重试'})
            else:
                self.log_error('Database operation failed: %s',error)
                self.reply(500,{'error':'数据库操作失败，请联系管理员'})
        except (BrokenPipeError,ConnectionResetError,TimeoutError):
            self.close_connection = True
        except Exception as error:
            self.log_error('Unhandled %s: %s',type(error).__name__,error)
            self.reply(500,{'error':'系统请求失败，请联系管理员'})

    do_GET = do_HEAD = do_POST = process


def create_admin(database, username, name, password):
    username = login_name(username)
    name = txt(name,'显示姓名',70)
    validated_password(password)
    with closing(connect(database)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if scalar(db,'SELECT 1 FROM users LIMIT 1'):
            raise Problem('系统已经初始化，请在总部设置管理账号')
        db.execute("INSERT INTO users(login,name,password,role) VALUES(?,?,?,'hq')",
                   (username,name,hash_password(password)))


def seed_demo(database, password):
    create_admin(database,'demo','总部演示账号',password)
    conn = connect(database)
    day = datetime.now(CHINA).date().isoformat()
    with closing(conn) as db, db:
        db.execute('BEGIN IMMEDIATE')
        user = row(db,'SELECT id,role,store_id,name FROM users LIMIT 1')
        stores = [save_store(db,user,{'code':code,'name':name}) for code,name in
                  [('HT01','江北店'),('HT02','东平店'),('HT03','河南岸店')]]
        products = [save_product(db,user,{'sku':sku,'name':name,'brand':brand,'spec':spec,
                    'category':'桶装水','kind':'water'}) for sku,name,brand,spec in
                    [('W-001','惠田纯净水','惠田','18.9 L'),('W-002','惠田天然泉水','惠田','15 L'),
                     ('W-003','山泉饮用水','山泉','18.9 L')]]
        def post(kind, store, items, **extra):
            return Posting(db,user,dict(kind=kind,store_id=store,items=items,
                                        request_key=secrets.token_hex(18),**extra)).write()[0]
        for index,store in enumerate(stores):
            post('OPEN_STOCK',store,[{'product_id':pid,'qty':110+17*index+21*j}
                 for j,pid in enumerate(products)])
            post('OPEN_STOCK',store,[{'product_id':scalar(db,'SELECT bucket_id FROM products WHERE id=?',(pid,)),
                 'qty':20+7*index+5*j} for j,pid in enumerate(products)])
            for j,name in enumerate(['示例 · 周先生','示例 · 林女士','示例 · 办公室']):
                customer = save_customer(db,user,{'name':name,'phone':f'000000{index}{j}'})
                pid = products[j]
                bucket = scalar(db,'SELECT bucket_id FROM products WHERE id=?',(pid,))
                post('DEPOSIT',store,[{'product_id':bucket,'qty':3+j}],customer_id=customer)
                post('SALE',store,[{'product_id':pid,'qty':3+2*j+index}],customer_id=customer,source='微信')
                post('BUCKET_RETURN',store,[{'product_id':bucket,'qty':j+1}],customer_id=customer)
        post('TRANSFER',stores[0],[{'product_id':products[0],'qty':8}],target_store=stores[1])
    print(f'演示数据已创建（{day}）。账号 demo，密码为刚才输入的密码。')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='惠田水超市库存管理')
    parser.add_argument('--db',default=str(ROOT/'data'/'huitian.sqlite3'))
    subs = parser.add_subparsers(dest='action',required=True)
    admin = subs.add_parser('init-admin')
    admin.add_argument('--login',default='admin')
    admin.add_argument('--name',default='总部管理员')
    subs.add_parser('demo')
    for command in ('serve','start'):
        serve = subs.add_parser(command)
        serve.add_argument('--host',default='127.0.0.1')
        serve.add_argument('--port',type=int,default=8765)
        serve.add_argument('--secure-cookie',action='store_true',help='站点通过 HTTPS 反向代理访问时启用')
        serve.add_argument('--base-path',default='',help='应用路径前缀，例如 /huitian')
        serve.add_argument('--public-origin',default='',help='正式 HTTPS 来源，例如 https://example.com')
    bak = subs.add_parser('backup')
    bak.add_argument('destination')
    args = parser.parse_args()
    database = Path(args.db).expanduser().resolve()
    database.parent.mkdir(parents=True,exist_ok=True)
    if args.action == 'backup' and not database.is_file():
        parser.exit(1,'待备份数据库不存在\n')
    def choose_password():
        password = getpass.getpass('设置登录密码（至少 12 个字符）：')
        if password != getpass.getpass('再次输入密码：'):
            raise Problem('两次输入的密码不一致')
        return password

    try:
        if args.action != 'backup':
            initialize(database)
        if args.action in ('init-admin','demo'):
            password = choose_password()
            if args.action == 'init-admin':
                create_admin(database,args.login,args.name,password)
                print('总部账号已创建。')
            else:
                seed_demo(database,password)
        elif args.action == 'backup':
            destination = Path(args.destination).expanduser().resolve()
            if destination.exists():
                raise Problem('目标备份文件已经存在，请换一个文件名')
            destination.parent.mkdir(parents=True,exist_ok=True)
            with closing(connect(database)) as source, closing(sqlite3.connect(destination)) as copy:
                source.backup(copy)
            print(f'备份完成：{destination}')
        else:
            if args.base_path and not re.fullmatch(r'(/[A-Za-z0-9_-]+)+',args.base_path):
                raise Problem('应用路径应类似 /huitian，末尾不加斜杠')
            if args.public_origin and not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?',args.public_origin):
                raise Problem('正式来源须为 HTTPS 域名，末尾不加路径或斜杠')
            if args.public_origin and not args.secure_cookie:
                raise Problem('正式 HTTPS 来源需要同时启用 --secure-cookie')
            if args.host not in ('127.0.0.1','localhost','::1') and not args.secure_cookie:
                raise Problem('请通过 HTTPS 反向代理提供非本机访问，并启用 --secure-cookie')
            with closing(connect(database)) as db:
                initialized = scalar(db,"SELECT 1 FROM users WHERE role='hq' AND active=1 LIMIT 1")
            if not initialized:
                if args.action != 'start':
                    raise Problem('请先创建总部账号，或使用独立演示数据库')
                print('首次启动，请设置总部管理员密码。账号为 admin。')
                create_admin(database,'admin','总部管理员',choose_password())
            try:
                server = Server((args.host,args.port),database,args.secure_cookie,args.base_path,args.public_origin)
            except OSError as error:
                raise Problem(f'无法在 {args.host}:{args.port} 启动网页服务，请确认端口可用或更换端口') from error
            with server:
                address = f'http://{args.host}:{server.server_port}{args.base_path}/'
                print(f'惠田水超市库存管理已启动：{address}',flush=True)
                if args.action == 'start' and not args.secure_cookie:
                    webbrowser.open(address)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
    except Problem as error:
        parser.exit(1,f'{error}\n')


if __name__ == '__main__':
    main()
