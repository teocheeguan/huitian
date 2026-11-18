import base64
import binascii
import csv
import hashlib
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from inventory import Posting, Problem, hq, phone_num, row, scalar, save_customer, txt

HEADERS = {
    'stock': ['门店编码','商品编码','期初库存'],
    'bucket': ['门店编码','空桶编码','客户名称','联系电话','已押桶数','未归还数'],
}
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def parse_xlsx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.infolist()) > 2000 or sum(x.file_size for x in archive.infolist()) > 25_000_000:
            raise Problem('Excel 展开后文件过大，请拆分后导入')
        def xml(name):
            source = archive.read(name)
            if b'<!DOCTYPE' in source or b'<!ENTITY' in source:
                raise Problem('Excel 文件包含不支持的 XML')
            return ET.fromstring(source)
        workbook = xml('xl/workbook.xml')
        sheet = workbook.find('s:sheets/s:sheet', NS)
        if sheet is None:
            raise Problem('Excel 没有工作表')
        relation = sheet.get(f'{{{REL}}}id')
        rels = xml('xl/_rels/workbook.xml.rels')
        target = next((item.get('Target') for item in rels if item.get('Id') == relation), None)
        if not target:
            raise Problem('Excel 找不到第一张工作表')
        sheet_path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl',target))
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(node.text or '' for node in item.findall('.//s:t', NS))
                       for item in xml('xl/sharedStrings.xml').findall('s:si', NS)]
        sheet_data = xml(sheet_path).findall('s:sheetData/s:row', NS)
        for record in sheet_data:
            columns = [''] * 16
            for cell in record.findall('s:c', NS):
                match = re.match(r'([A-Z]+)', cell.get('r',''))
                if not match or len(match[1]) != 1 or not 'A' <= match[1] <= 'P':
                    continue
                index = ord(match[1])-ord('A')
                kind = cell.get('t')
                value = cell.findtext('s:v','',NS)
                if kind == 'inlineStr':
                    value = ''.join(node.text or '' for node in cell.findall('.//s:t',NS))
                elif kind == 's':
                    value = strings[int(value)]
                if cell.find('s:f', NS) is not None and not value:
                    raise Problem('Excel 公式需要有已保存的结果，建议粘贴为数值后再导入')
                columns[index] = value
            yield int(record.get('r','0')), columns


def file_rows(filename, content):
    try:
        binary = base64.b64decode(content, validate=True)
        if len(binary) > 2_600_000:
            raise Problem('每次导入的文件须小于 2.6 MB')
        if filename.lower().endswith('.xlsx'):
            return list(parse_xlsx(binary))
        if filename.lower().endswith('.csv'):
            return list(enumerate(csv.reader(io.StringIO(binary.decode('utf-8-sig'))),1))
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError, IndexError,
            UnicodeError, csv.Error, binascii.Error) as error:
        raise Problem('文件读取失败，请使用标准 .xlsx 或 UTF-8 CSV') from error
    raise Problem('请选择 .xlsx 或 UTF-8 CSV 文件')


def load_opening(db, user, data):
    hq(user)
    kind = data.get('kind')
    if kind not in HEADERS:
        raise Problem('请选择期初导入类型')
    filename = txt(data.get('filename'),'文件名',220)
    content = txt(data.get('content'),'文件内容',3_500_000)
    request = txt(data.get('request_key'),'导入编号',55)
    if not re.fullmatch(r'[A-Za-z0-9_-]{12,55}', request):
        raise Problem('导入编号无效')
    fingerprint = hashlib.sha256((kind+'|'+filename+'|'+content).encode()).hexdigest()
    existing = row(db,'SELECT * FROM opening_imports WHERE request_key=?',(request,))
    if existing:
        if existing['operator'] != user['id'] or existing['fingerprint'] != fingerprint:
            raise Problem('该导入编号已用于另一份表格，请重新打开导入窗口',409)
        return existing['line_count']
    table = [(line, [value.strip() for value in fields])
             for line, fields in file_rows(filename,content) if any(x.strip() for x in fields)]
    headers = HEADERS[kind]
    if not 2 <= len(table) <= 5001 or table[0][1][:len(headers)] != headers:
        raise Problem(f'请使用对应模板，每次填写 1–5000 行。列顺序为 {"、".join(headers)}')
    count = 0
    seen = set()
    for line, fields in table[1:]:
        try:
            if len(fields) < len(headers) or any(fields[len(headers):]):
                raise Problem('列数与模板不符')
            store = scalar(db,'SELECT id FROM stores WHERE code=? AND active=1',(fields[0],))
            product = scalar(db,'SELECT id FROM products WHERE sku=? AND active=1',(fields[1],))
            if not store or not product:
                raise Problem('未找到可用的门店编码或商品编码')
            entry = {'request_key': f'open_{request}_{line}', 'store_id': store, 'note': '期初表格导入'}
            if kind == 'stock':
                entry.update(kind='OPEN_STOCK', items=[{'product_id':product,'qty':int(fields[2])}])
            else:
                phone = phone_num(fields[3])
                customer_id = scalar(db,'SELECT id FROM customers WHERE phone=?',(phone,))
                if customer_id is None:
                    customer_id = save_customer(db,user,{'phone':phone,'name':fields[2]})
                entry.update(kind='OPEN_BUCKET', customer_id=customer_id,
                             items=[{'product_id':product,'qty':int(fields[4]),'held':int(fields[5])}])
            identity = store, product, entry.get('customer_id')
            if identity in seen:
                raise Problem('同一门店、商品及客户出现重复行')
            seen.add(identity)
            Posting(db,user,entry).write()
            count += 1
        except ValueError as error:
            raise Problem(f'第 {line} 行，数量必须为非负整数') from error
        except Problem as error:
            raise Problem(f'第 {line} 行，{error}') from error
    db.execute('INSERT INTO opening_imports VALUES(?,?,?,?)',(request,user['id'],fingerprint,count))
    return count
