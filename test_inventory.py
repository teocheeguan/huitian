import base64
import io
import json
import secrets
import sqlite3
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import app
import inventory as inv
import reports
from importing import load_opening


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'test.sqlite'
        inv.initialize(self.path)
        app.create_admin(self.path,'testadmin','总部测试','test-account-only-42')
        with closing(inv.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            self.hq = inv.row(db,'SELECT id,name,role,store_id FROM users')
            self.a = inv.save_store(db,self.hq,{'code':'A','name':'A店'})
            self.b = inv.save_store(db,self.hq,{'code':'B','name':'B店'})
            self.staff = {'name':'门店测试','role':'store','store_id':self.a}
            self.staff['id'] = db.execute("""INSERT INTO users(login,name,password,role,store_id)
                VALUES('storea','门店测试',?,'store',?)""",(app.hash_password('test-branch-only-42'),self.a)).lastrowid
            self.staff_b = {'name':'B店测试','role':'store','store_id':self.b}
            self.staff_b['id'] = db.execute("""INSERT INTO users(login,name,password,role,store_id)
                VALUES('storeb','B店测试','', 'store',?)""",(self.b,)).lastrowid
            self.water = self.product(db,'W1','惠田','18.9 L')
            self.other_brand = self.product(db,'W2','山泉','18.9 L')
            self.other_size = self.product(db,'W3','惠田','15 L')
            self.empty,self.other_empty,self.other_size_empty = [inv.scalar(
                db,'SELECT bucket_id FROM products WHERE id=?',(i,)) for i in
                (self.water,self.other_brand,self.other_size)]
            self.phone = '13800000001'
            self.customer = inv.save_customer(db,self.staff,{'name':'测试客户','phone':self.phone})

    def product(self,db,sku,brand,spec):
        return inv.save_product(db,self.hq,dict(sku=sku, name=brand+'纯净水', brand=brand,
                                spec=spec, category='桶装水', kind='water'))

    def make(self,kind,store,product=None,qty=0,**fields):
        result = dict(kind=kind,store_id=store,request_key=secrets.token_hex(16),**fields)
        if product is not None:
            result['items'] = [dict(product_id=product,qty=qty)]
        if kind in ('SALE','OPEN_BUCKET','BUCKET_RETURN','DEPOSIT','REFUND'):
            result.update(customer_id=self.customer,customer_phone=self.phone)
        return result

    def post(self,data,operator=None):
        with closing(inv.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            return inv.Posting(db,operator or self.hq,data).write()

    def stock(self,store,product):
        with closing(inv.connect(self.path)) as db:
            return inv.scalar(db,'SELECT qty FROM stock WHERE store_id=? AND product_id=?',(store,product)) or 0

    def test_sales_no_double_deduction(self):
        self.post(self.make('IN',self.a,self.water,5))
        order=self.make('SALE',self.a,self.water,3,source='小程序',external_number='MINI-1')
        posted=self.post(order,self.staff)
        self.assertEqual(self.post(order,self.staff),(posted[0],True))
        self.assertEqual(self.stock(self.a,self.water),2)
        with self.assertRaisesRegex(inv.Problem,'已登记'):
            self.post(dict(order,request_key=secrets.token_hex(16)),self.staff)
        other=dict(order,request_key=secrets.token_hex(16),external_number='MINI-2')
        with self.assertRaisesRegex(inv.Problem,'库存不足'):
            self.post(other,self.staff)
        self.assertEqual(self.stock(self.a,self.water),2)
        with closing(inv.connect(self.path)) as db:
            self.assertEqual(inv.scalar(db,'SELECT SUM(qty) FROM sales_log'),3)
            self.assertEqual(inv.scalar(db,'SELECT COUNT(*) FROM external_orders'),1)
            self.assertEqual(inv.scalar(db,'SELECT SUM(sent) FROM bucket_log'),3)
            detail=reports.document_detail(db,self.staff,posted[0])
            self.assertEqual((detail['stock'][0]['before_qty'],detail['stock'][0]['after_qty']),(5,2))

    def test_concurrent_transfers_and_boundaries(self):
        self.post(self.make('IN',self.a,self.water,10))
        data1=self.make('TRANSFER',self.a,self.water,8,target_store=self.b)
        data2=self.make('TRANSFER',self.a,self.water,5,target_store=self.b)
        def go(payload):
            try:
                return self.post(payload,self.staff)
            except inv.Problem as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(go,[data1,data2]))
        self.assertEqual(outcomes.count(409),1)
        self.assertEqual(self.stock(self.a,self.water)+self.stock(self.b,self.water),10)
        self.assertIn(self.stock(self.b,self.water),(5,8))
        with self.assertRaisesRegex(inv.Problem,'只能操作本店'):
            self.post(self.make('IN',self.b,self.water,1),self.staff)
        with closing(inv.connect(self.path)) as db:
            self.assertEqual({entry['store_id'] for entry in reports.stock_rows(db,self.staff,{})},{self.a})
            detail=reports.document_detail(db,self.staff_b,next(v[0] for v in outcomes if isinstance(v,tuple)))
            self.assertEqual({entry['store_id'] for entry in detail['stock']},{self.b})
            with self.assertRaisesRegex(inv.Problem,'只能操作本店'):
                reports.history(db,self.staff,{'store':[str(self.b)]})
            with self.assertRaisesRegex(inv.Problem,'无权'):
                reports.document_detail(db,self.staff_b,1)

    def test_cross_store_identical_empty_only_and_no_double_count(self):
        self.post(self.make('IN',self.a,self.water,6))
        self.post(self.make('DEPOSIT',self.a,self.empty,2),self.staff)
        self.post(self.make('SALE',self.a,self.water,2,source='电话'),self.staff)
        for incompatible in (self.other_empty,self.other_size_empty):
            with self.assertRaisesRegex(inv.Problem,'未归还数不足'):
                self.post(self.make('BUCKET_RETURN',self.b,incompatible,1),self.staff_b)
            self.assertEqual(self.stock(self.b,incompatible),0)
        self.post(self.make('BUCKET_RETURN',self.b,self.empty,1),self.staff_b)
        with self.assertRaisesRegex(inv.Problem,'原押桶门店'):
            self.post(self.make('REFUND',self.b,self.empty,1),self.staff_b)
        self.assertEqual(self.stock(self.a,self.empty),0)
        self.assertEqual(self.stock(self.b,self.empty),1)
        with closing(inv.connect(self.path)) as db:
            dash=reports.dashboard(db,self.hq,{})
            self.assertEqual(dash['totals'],dict(customers=1,water=4,empty=1,sold=2,deposits=2,sent=2,returned=1,held=1))
            self.assertEqual([store['customers'] for store in dash['stores']],[1,1])
            balances=reports.customer_balances(db,self.staff_b,self.customer,{})
            self.assertEqual((balances[0]['held'],balances[0]['deposits']),(1,2))

    def test_actual_dispatch_partial_return_reversal(self):
        self.post(self.make('IN',self.a,self.water,10))
        sale,_=self.post(self.make('SALE',self.a,self.water,5,source='微信',dispatch_now=False))
        self.assertEqual(self.stock(self.a,self.water),5)
        with self.assertRaisesRegex(inv.Problem,'超过'):
            self.post(self.make('RETURN',self.a,self.water,2,linked_id=sale))
        self.post(self.make('SEND',self.a,self.water,3,linked_id=sale),self.staff)
        ret=self.make('RETURN',self.a,self.water,2,linked_id=sale)
        ret['items'][0]['restock']=1
        reversal,_=self.post(ret,self.staff)
        self.assertEqual(self.stock(self.a,self.water),6)
        with closing(inv.connect(self.path)) as db:
            totals=reports.dashboard(db,self.hq,{})['totals']
            self.assertEqual((totals['held'],totals['sold'],totals['sent'],totals['returned']),(1,3,3,0))
        with self.assertRaisesRegex(inv.Problem,'关联'):
            self.post(self.make('REVERSE',self.a,linked_id=sale,note='测试'))
        self.post(self.make('REVERSE',self.a,linked_id=reversal,note='登记错误'),self.staff)
        self.assertEqual(self.stock(self.a,self.water),5)
        with self.assertRaisesRegex(inv.Problem,'已冲销'):
            self.post(self.make('REVERSE',self.a,linked_id=reversal,note='重复'))

    def test_count_audit_and_immutable_history(self):
        self.post(self.make('OPEN_STOCK',self.a,self.water,7))
        for value in (10,4,4):
            self.post(self.make('COUNT',self.a,self.water,value),self.staff)
        self.assertEqual(self.stock(self.a,self.water),4)
        with closing(inv.connect(self.path)) as db:
            self.assertEqual([r['delta'] for r in reports.history(db,self.staff,{})],[0,-6,3,7])
            for statement in ('DELETE FROM stock_log','UPDATE documents SET kind=\'OUT\''):
                with self.assertRaisesRegex(sqlite3.IntegrityError,'immutable'):
                    db.execute(statement)
            self.assertEqual(inv.scalar(db,'PRAGMA integrity_check'),'ok')

    def test_opening_import_atomic_and_repeatable(self):
        def run(filename,binary,key='import-id-00001'):
            payload={'request_key':key,'filename':filename,'kind':'stock','content':base64.b64encode(binary).decode()}
            with closing(inv.connect(self.path)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                return load_opening(db,self.hq,payload)
        bad='门店编码,商品编码,期初库存\nA,W1,15\nB,missing,3\n'
        with self.assertRaisesRegex(inv.Problem,'第 3 行'):
            run('open.csv',bad.encode())
        self.assertEqual(self.stock(self.a,self.water),0)
        good='门店编码,商品编码,期初库存\nA,W1,15\nB,W1,4\n'
        self.assertEqual(run('open.csv',good.encode()),2)
        self.assertEqual(run('open.csv',good.encode()),2)
        self.assertEqual(self.stock(self.a,self.water),15)
        with self.assertRaisesRegex(inv.Problem,'另一份'):
            run('open2.csv',good.encode())
        target=io.BytesIO()
        with zipfile.ZipFile(target,'w') as z:
            z.writestr('xl/workbook.xml','''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet r:id="rId1"/></sheets></workbook>''')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr('xl/worksheets/sheet1.xml','''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>门店编码</t></is></c>
                <c r="B1" t="inlineStr"><is><t>商品编码</t></is></c><c r="C1" t="inlineStr"><is><t>期初库存</t></is></c></row>
                <row r="2"><c r="A2" t="inlineStr"><is><t>A</t></is></c><c r="B2" t="inlineStr"><is><t>W2</t></is></c>
                <c r="C2"><v>9</v></c></row></sheetData></worksheet>''')
        self.assertEqual(run('open.xlsx',target.getvalue(),'import-id-00002'),1)
        self.assertEqual(self.stock(self.a,self.other_brand),9)

    def test_report_dates_and_opening_customer_counts(self):
        opened=self.make('OPEN_BUCKET',self.a,self.empty,2)
        opened['items'][0]['held']=1
        self.post(opened)
        with closing(inv.connect(self.path)) as db:
            today=reports.dashboard(db,self.hq,{})
            self.assertEqual(today['totals']['customers'],1)
            self.assertEqual(today['totals']['held'],1)
            self.assertEqual(today['totals']['deposits'],2)
            self.assertEqual(today['totals']['sent'],0)
            previous=reports.dashboard(db,self.hq,{'start':['2000-01-01'],'end':['2000-01-01']})
            self.assertEqual(previous['totals']['customers'],0)
            self.assertEqual(previous['totals']['deposits'],0)

    def http_requests(self, base_path='', public_origin=''):
        server=SimpleNamespace(database=self.path,secure=bool(public_origin),base_path=base_path,
                               public_origin=public_origin,rate_limiter=app.RateLimiter())
        def request(route,data=None,cookie='',origin=''):
            body=b'' if data is None else json.dumps(data).encode()
            method='POST' if data is not None else 'GET'
            raw=(f'{method} {route} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n'
                 f'Cookie: {cookie}\r\nOrigin: {origin}\r\nContent-Type: application/json\r\n'
                 f'Content-Length: {len(body)}\r\n\r\n').encode()+body
            class MemorySocket:
                def settimeout(self,value): pass
                def makefile(self,*args): return io.BytesIO(raw)
                def sendall(self,value): output.extend(value)
            output=bytearray()
            app.Handler(MemorySocket(),('127.0.0.1',5555),server)
            return bytes(output).decode('utf-8')
        return request

    def test_http_dispatch_auth_cookie_and_permissions(self):
        request=self.http_requests()
        self.assertIn('200 OK',request('/'))
        self.assertIn('401 Unauthorized',request('/api/bootstrap'))
        result=request('/api/login',{'login':'storea','password':'test-branch-only-42'})
        self.assertIn('200 OK',result)
        self.assertIn('HttpOnly; SameSite=Strict',result)
        cookie=next(l for l in result.splitlines() if l.startswith('Set-Cookie: ')).split(' ',1)[1].split(';',1)[0]
        self.assertIn('200 OK',request('/api/dashboard',cookie=cookie))
        self.assertIn('403 Forbidden',request('/api/users',cookie=cookie))
        self.assertIn('200 OK',request('/api/post',self.make('IN',self.a,self.water,5),cookie))
        self.assertIn('200 OK',request('/api/logout',{},cookie))
        self.assertIn('401 Unauthorized',request('/api/stock',cookie=cookie))

    def test_https_subpath_isolates_assets_api_and_cookie(self):
        origin='https://inventory.example.com'
        request=self.http_requests('/huitian',origin)
        self.assertIn('404 Not Found',request('/'))
        self.assertIn('404 Not Found',request('/huitian-other/'))
        self.assertIn('Location: /huitian/',request('/huitian'))
        self.assertIn('src="./app.js"',request('/huitian/'))
        self.assertIn('200 OK',request('/huitian/styles.css'))
        self.assertIn('200 OK',request('/huitian/app.js'))
        data={'login':'testadmin','password':'test-account-only-42'}
        self.assertIn('403 Forbidden',request('/huitian/api/login',data,origin='https://other.example'))
        result=request('/huitian/api/login',data,origin=origin)
        self.assertIn('200 OK',result)
        self.assertIn('Path=/huitian/; HttpOnly; SameSite=Strict',result)
        self.assertIn('; Secure',result)
        cookie=next(l for l in result.splitlines() if l.startswith('Set-Cookie: ')).split(' ',1)[1].split(';',1)[0]
        self.assertIn('200 OK',request('/huitian/api/bootstrap',cookie=cookie))
        self.assertIn('Content-Disposition: attachment',request('/huitian/api/templates/stock',cookie=cookie))
        self.assertIn('404 Not Found',request('/api/bootstrap',cookie=cookie))
        self.assertIn('Path=/huitian/; HttpOnly',request('/huitian/api/logout',{},cookie,origin))


if __name__ == '__main__':
    unittest.main()
