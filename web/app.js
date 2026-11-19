'use strict';

const app = document.querySelector('#app');
const modal = document.querySelector('#dialog');
const toast = document.querySelector('#toast');
const nf = new Intl.NumberFormat('zh-CN');
const pages = {
  dashboard: ['▦','运营看板'], orders: ['▧','订单管理'], products: ['◇','商品管理'], inventory: ['▤','库存管理'],
  purchases: ['◎','进销管理'], transfers: ['⇄','调货管理'], settings: ['⚙','基础设置'],
};
const tabs = {
  inventory: [['stock','实时库存'],['history','库存流水'],['sales','销售单据']],
  settings: [['stores','门店'],['users','账号']],
};
const state = { page:'dashboard', view:{}, store:'', filters:{}, data:null, loaded:0, ymd:'' };
let toastTimer;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const num = value => nf.format(value ?? 0);
const signed = value => `<span class="num ${value < 0 ? 'neg' : value > 0 ? 'pos' : ''}">${value > 0 ? '+' : ''}${num(value)}</span>`;
const clock = value => new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)).replaceAll('/','-');
const skuTag = value => `<span class="tag">${esc(value)}</span>`;
const code = id => 'HT-' + String(id).padStart(6, '0');
const docLink = id => `<button class="linkbtn" data-action="detail" data-id="${id}">${code(id)}</button>`;
const act = (label, action, value='', classes='') => `<button type="button" class="btn ${classes}" data-action="${action}" data-value="${esc(value)}">${esc(label)}</button>`;
const isHQ = () => state.boot.user.role === 'hq';
const titles = key => state.boot.titles[key] || key;
const shift = (day, diff) => {
  const date = new Date(day + 'T00:00:00Z');
  date.setUTCDate(date.getUTCDate() + diff);
  return date.toISOString().slice(0,10);
};

function alertToast(message) {
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 4200);
}

async function api(path, data) {
  const init = data === undefined ? {} : {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(data),
  };
  const response = await fetch('.' + path, init);
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || '操作暂时失败');
    error.status = response.status;
    throw error;
  }
  return result;
}

const brand = () => '<div class="brand"><div class="mark" aria-hidden="true">水</div><div class="brand-copy"><strong>惠田水超市</strong><small>多门店库存管理</small></div></div>';

function login(message='') {
  state.boot = null;
  if (modal.open) modal.close();
  app.innerHTML = `<main class="login-stage"><section class="card login-card">
    ${brand()}<h1>登录库存系统</h1><p class="sub">请输入总部或门店账号</p>
    <form id="login"><label class="label">账号<input name="login" autocomplete="username" required maxlength="40"></label>
    <label class="label">密码<input name="password" type="password" autocomplete="current-password" required maxlength="128"></label>
    <div class="form-error" role="alert">${esc(message)}</div><button class="btn primary" type="submit">登录</button></form>
    </section></main>`;
}

async function boot() {
  try {
    state.boot = await api('/api/bootstrap');
    state.ymd = state.boot.today;
    if (!isHQ() && state.page === 'settings') state.page = 'dashboard';
    if (!isHQ()) state.store = String(state.boot.user.store_id);
    await loadPage();
  } catch (error) {
    login(error.status === 401 ? '' : error.message);
  }
}

function renderShell(content) {
  const user = state.boot.user;
  const nav = (key) => {
    const [icon, label] = pages[key];
    return `<button class="nav" data-action="nav" data-value="${key}" aria-label="${label}" ${key===state.page?'aria-current="page"':''}>
      <span class="icon" aria-hidden="true">${icon}</span><span>${label}</span></button>`;
  };
  app.innerHTML = `<div class="shell">
    <aside class="side">${brand()}<nav aria-label="系统功能">${Object.keys(pages).filter(k=>k!=='settings').map(nav).join('')}</nav>
      <div class="side-bottom">${isHQ()?nav('settings'):''}</div></aside>
    <div class="main"><header class="top"><div class="trail"><span>库存管理系统 /</span>${pages[state.page][1]}</div>
      <div class="account"><span class="avatar" aria-hidden="true">${esc(user.name.slice(0,1))}</span>
        <strong>${esc(user.name)}</strong><small>${esc(isHQ()?'总部':user.store)}</small>${act('退出','logout','','ghost slim')}</div></header>
      <main class="page">${content}</main></div></div>`;
}

function pageHead(title, subtitle, buttons='') {
  return `<div class="page-head"><div><h1>${esc(title)}</h1><p class="sub">${esc(subtitle)}</p></div><div class="actions">${buttons}</div></div>`;
}

function view(page=state.page) { return state.view[page] || tabs[page]?.[0]?.[0] || ''; }

function tabsHtml() {
  return `<div class="tabs" role="tablist">${tabs[state.page].map(([key,label]) =>
    `<button class="tab" role="tab" aria-selected="${key===view()}" data-action="tab" data-value="${key}">${label}</button>`).join('')}</div>`;
}

function filt() {
  const key = state.page + '.' + view();
  if (!state.filters[key]) {
    state.filters[key] = { q:'', type:'', brand:'', spec:'', page:1, day:'', start:shift(state.ymd,-13), end:state.ymd };
  }
  return state.filters[key];
}

function selectors(options, selected='', placeholder='请选择') {
  const base = placeholder===null ? '' : `<option value="">${esc(placeholder)}</option>`;
  return base + options.map(([val,label]) => `<option value="${esc(val)}" ${String(val)===String(selected)?'selected':''}>${esc(label)}</option>`).join('');
}
function storeEntries() { return state.boot.stores.map(store=>[store.id, store.name]); }

function outerFilters(dates=false) {
  const f = filt();
  return `<form id="filters" class="filters card">
    <label>门店<select name="store" ${isHQ()?'':'disabled'} aria-label="门店">${selectors(storeEntries(),state.store,isHQ()?'全部门店':null)}</select></label>
    ${dates ? `<label>起始日期<input name="start" type="date" value="${esc(f.start)}"></label><span class="sep">—</span>
      <label>截止日期<input name="end" type="date" value="${esc(f.end)}"></label>
      <button class="btn slim" type="submit">查询</button>${act('今天','today','','ghost slim')}` : ''}
    </form>`;
}

function searchBar(options={}) {
  const f = filt();
  const opts = [
    ['type', options.types, '全部分类'],
    ['brand', options.brands, '全部品牌'],
    ['spec', options.specs, '全部规格'],
  ];
  return `<form id="search" class="search-row"><input type="search" name="q" aria-label="搜索" value="${esc(f.q)}" placeholder="${esc(options.placeholder||'搜索名称、编码或单据号')}">
    ${opts.filter(([, list])=>list?.length).map(([key,list,label]) =>
      `<select name="${key}" aria-label="${label}">${selectors(list,f[key],label)}</select>`
    ).join('')}<button class="btn slim" type="submit">搜索</button>${f.q||f.type||f.brand||f.spec?act('清除','clear-search','','ghost slim'):''}</form>`;
}

function column(label, get, numeric=false) { return {label,get,numeric}; }
const plain = (label,key) => column(label,entry=>esc(entry[key] ?? '—'));
const count = (label,key) => column(label,entry=>`<span class="num">${num(entry[key])}</span>`,true);
const storeColumn = plain('门店','store');
const typeColumn = column('分类',e=>skuTag(state.boot.categories[e.kind]));
const brandSpec = [plain('品牌','brand'),plain('规格','spec')];

function makeTable(columns, rows, empty='暂无记录') {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  return `<div class="scroll-table"><table><thead><tr>${columns.map(x=>`<th scope="col" class="${x.numeric?'n':''}">${esc(x.label)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(entry=>`<tr>${columns.map(x=>`<td class="${x.numeric?'n':''}">${x.get(entry)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function table(columns, records, options={}) {
  const f = filt();
  const filtered = records.filter(e => {
    const matchesText = Object.values(e).some(value => String(value??'').toLowerCase().includes(f.q.toLowerCase())) ||
      (e.id && code(e.id).toLowerCase().includes(f.q.toLowerCase())) ||
      (e.doc_id && code(e.doc_id).toLowerCase().includes(f.q.toLowerCase()));
    return (!f.q || matchesText) && (!f.type || f.type===e.kind) && (!f.brand || f.brand===e.brand) && (!f.spec || f.spec===e.spec);
  });
  const size = 15;
  const max = Math.max(1,Math.ceil(filtered.length/size));
  f.page = Math.min(f.page,max);
  return makeTable(columns,filtered.slice((f.page-1)*size,f.page*size),options.empty) +
    `<div class="pagination"><span class="minor">共 ${num(filtered.length)} 条</span><div>
      <button type="button" class="btn slim" data-action="page-step" data-value="-1" ${f.page===1?'disabled':''}>上一页</button>
      <span class="minor">${f.page} / ${max}</span>
      <button type="button" class="btn slim" data-action="page-step" data-value="1" ${f.page===max?'disabled':''}>下一页</button></div></div>`;
}

function heading(title, note='') {
  return `<div class="panel-head"><div><h2>${title}</h2>${note?`<p>${note}</p>`:''}</div></div>`;
}
const featuredBrands = ['景田','长龙','奥绿','鼎湖山泉大氧吧'];

function overview(data) {
  const brands = Array.from(new Set([...featuredBrands,...data.brands,...data.stock.map(e=>e.brand)]));
  const locationStock = new Map();
  const brandStock = new Map();
  for (const entry of data.stock) {
    locationStock.set(JSON.stringify([entry.store_id,entry.brand,entry.kind]),entry.qty);
    const key = JSON.stringify([entry.brand,entry.kind]);
    brandStock.set(key,(brandStock.get(key)||0)+entry.qty);
  }
  const amount = (value) => `<span class="num">${num(value)}</span>`;
  const brandRows = brands.map(brand=>({brand,water:brandStock.get(JSON.stringify([brand,'water'])),
    empty:brandStock.get(JSON.stringify([brand,'empty']))}));
  const storeCols = [plain('门店','name'),count('订单数','orders'),
    ...brands.flatMap(brand=>[
      column(brand+'库存',store=>amount(locationStock.get(JSON.stringify([store.id,brand,'water']))),true),
      column(brand+'空桶库存',store=>amount(locationStock.get(JSON.stringify([store.id,brand,'empty']))),true),
    ])];
  const bucketCols = [storeColumn,...brandSpec,count('送出桶','sent'),count('回空桶','received'),count('差回空桶','missing')];
  return pageHead(isHQ()?'总部运营看板':'门店运营看板','按品牌看库存，按北京时间查询每日桶账。',act('刷新','reload','','ghost')) +
    `<form id="filters" class="filters card">
      <label>门店<select name="store" ${isHQ()?'':'disabled'}>${selectors(storeEntries(),state.store,isHQ()?'全部门店':null)}</select></label>
      <label>统计日期<input name="day" type="date" value="${esc(data.day)}" max="${esc(data.today)}" required></label>
      <button class="btn slim" type="submit">查询</button>${act('今天','today','','ghost slim')}
    </form>
    <section class="card gap">${heading('运营指标','库存与订单总数按统计日截止。')}
      <div class="pad"><strong>订单总数　${num(data.totals.orders)}</strong></div>
      ${makeTable([plain('品牌','brand'),count('桶装水库存','water'),count('空桶库存','empty')],brandRows)}
    </section>
    <section class="card gap">${heading('桶账记录 · '+esc(data.day),'每天从零统计，往日记录可通过统计日期查询。')}
      <div class="pad bucket-overview">
        <div><span>送出桶（应回桶）</span><strong>${num(data.totals.sent)}</strong></div>
        <div><span>回空桶（实回桶）</span><strong>${num(data.totals.received)}</strong></div>
        <div><span>差回空桶</span><strong>${num(data.totals.missing)}</strong></div>
      </div>
      <details class="pad"><summary>按门店、品牌和规格查看</summary>
        <p class="minor">差回按本店同品牌同规格的应回、实回和退回满水计算。</p>
        ${makeTable(bucketCols,data.bucket_types,'所选日期暂无桶账记录')}
      </details>
    </section>
    <section class="card">${heading('门店经营概况')}${makeTable(storeCols,data.stores,'先在基础设置新增门店')}</section>`;
}

function ordersPage(stores) {
  const cols = [plain('门店编码','code'),plain('门店名称','name'),
    column('操作',e=>act('下单','order',e.id,'primary slim'))];
  return pageHead('订单管理','选择门店后下单，订单只计入所选门店。') +
    `<section class="card">${makeTable(cols,stores,'先在基础设置新增门店')}</section>`;
}

const docCols = [
  column('单据号',e=>docLink(e.id)), column('业务',e=>esc(titles(e.kind))),
  storeColumn, column('商品明细',e=>`<span title="${esc(e.detail||'')}">${esc((e.detail||'—').slice(0,48))}${(e.detail||'').length>48?'…':''}</span>`),
  column('状态',e=>e.reversal_id?'<span class="tag gray">已冲销</span>':'<span class="tag good">已记账</span>'),
  column('时间',e=>esc(clock(e.timestamp))),
];

function filtersFor(data) {
  const distinct = key=>Array.from(new Set(data.map(e=>e[key]).filter(Boolean))).sort().map(e=>[e,e]);
  return {types:Object.entries(state.boot.categories),brands:distinct('brand'),specs:distinct('spec')};
}

function inventory(data) {
  const head = pageHead('库存管理','查看库存、流水和销售单据。',act('出库','post','OUT')+act('盘点','post','COUNT'));
  const cols = {
    stock: [storeColumn,plain('商品编码','sku'),plain('商品名称','name'),...brandSpec,typeColumn,count('结存','qty')],
    history: [column('单据号',e=>docLink(e.doc_id)),storeColumn,column('业务',e=>esc(titles(e.kind))+
      (e.kind==='COUNT'&&e.delta!==0?` <span class="tag">${e.delta>0?'盘盈':'盘亏'}</span>`:'')),
      plain('商品编码','sku'),plain('商品','name'),...brandSpec,column('变动',e=>signed(e.delta),true),
      count('变动前','before_qty'),count('变动后','after_qty'),plain('操作人','operator_name'),column('时间',e=>esc(clock(e.timestamp)))],
    sales: docCols,
  };
  const options = view()==='stock'?filtersFor(data):view()==='history'?{...filtersFor(data),types:[]}: {};
  return head + outerFilters(view()!=='stock') + `<section class="card">${tabsHtml()}${searchBar(options)}${table(cols[view()],data)}</section>`;
}

function productPage(data) {
  const cols = [
    plain('商品编码','sku'),plain('商品名称','name'),...brandSpec,typeColumn,
    ...(isHQ()?[column('操作',e=>act('编辑','product',e.id,'ghost slim')+
      act('删除','delete-product',e.id,'danger slim'))]:[]),
  ];
  return pageHead('商品管理','维护商品及空桶的品牌、规格和分类。',isHQ()?act('+ 新增商品','product','','primary'):'')+
    `<section class="card">${searchBar(filtersFor(data))}${table(cols,data,'暂无商品')}</section>`;
}

function purchasesPage(data) {
  const cols = [column('进货单',e=>docLink(e.id)),plain('日期','business_day'),storeColumn,
    plain('进货商品','detail'),count('数量','total_qty'),plain('操作人','operator_name'),
    column('状态',e=>e.kind==='REVERSE'?skuTag('冲销单'):e.reversal_id?skuTag('已冲销'):skuTag('已入库'))];
  return pageHead('进销管理','进货确认后计入所选门店库存。',act('+ 添加进货商品','post','IN','primary'))+
    outerFilters(true)+`<section class="card">${searchBar()}${table(cols,data,'暂无进货记录')}</section>`;
}

function transfersPage(data) {
  const cols = [column('调拨单',e=>docLink(e.id)),column('业务',e=>esc(titles(e.kind))),plain('调出门店','store'),
    plain('调入门店','target'),plain('商品与数量','detail'),plain('操作人','operator_name'),
    column('状态', e=>e.reversal_id?'<span class="tag gray">已冲销</span>':'<span class="tag good">已记账</span>'),
    column('确认时间',e=>esc(clock(e.timestamp)))];
  return pageHead('调货管理','同一笔调拨同步记入调出店和调入店。',act('+ 新建调拨','post','TRANSFER','primary')) +
    outerFilters(true) + `<section class="card">${searchBar()}${table(cols,data,'暂无调拨记录')}</section>`;
}
function settings(data) {
  let contents;
  if (view()==='stores') {
    contents = heading('门店列表') + table([plain('门店编码','code'),plain('门店名称','name'),
      column('操作',e=>act('编辑','store',e.id,'ghost slim'))],data);
  } else {
    contents = heading('账号列表') + table([plain('登录账号','login'),plain('显示姓名','name'),
      column('角色',e=>e.role==='hq'?'总部':'门店'),plain('门店','store'),
      column('状态',e=>e.active?'<span class="tag good">启用</span>':'<span class="tag gray">停用</span>'),
      column('操作',e=>act('编辑','user',e.id,'ghost slim'))],data);
  }
  const buttons = view()==='stores'?act('+ 新增门店','store','','primary'):
    view()==='users'?act('+ 新增账号','user','','primary'):'';
  return pageHead('基础设置','维护门店和登录账号。',buttons)+`<section class="card">${tabsHtml()}${contents}</section>`;
}

const renderers = {dashboard:overview,orders:ordersPage,products:productPage,inventory,purchases:purchasesPage,transfers:transfersPage,settings};
function paint() {
  if (state.data !== null) renderShell(renderers[state.page](state.data));
}
function params(extra={}) {
  const f = filt();
  return new URLSearchParams(Object.fromEntries(Object.entries({
    store:state.store,start:f.start,end:f.end,...extra,
  }).filter(([, value])=>value!==''))).toString();
}
async function loadPage() {
  const token = ++state.loaded;
  const page = state.page;
  const current = view();
  let value;
  const filter = params();
  try {
    if (page==='dashboard') value = await api('/api/dashboard?'+new URLSearchParams({store:state.store,day:filt().day}));
    if (page==='orders') value = state.boot.stores.filter(e=>isHQ()||e.id===state.boot.user.store_id);
    if (page==='products') value = state.boot.products;
    if (page==='inventory') {
      if (current==='stock') value = await api('/api/stock?'+filter);
      if (current==='history') value = await api('/api/history?'+filter);
      if (current==='sales') value = await api('/api/documents?'+params({group:'sales'}));
    }
    if (page==='purchases') value = await api('/api/documents?'+params({group:'purchases'}));
    if (page==='transfers') value = await api('/api/documents?'+params({group:'transfer'}));
    if (page==='settings') value = current==='users'?await api('/api/users'):state.boot.stores;
    if (token!==state.loaded) return;
    state.data = value;
    paint();
  } catch (error) {
    if (token!==state.loaded) return;
    if (error.status===401) login('登录已过期，请重新登录。');
    else renderShell(`<section class="card pad"><p class="form-error" role="alert">${esc(error.message)}</p>
      <p>${act('重试','reload','','primary')} ${act('重置筛选','reset')}</p></section>`);
  }
}

function openModal(title, body) {
  modal.innerHTML = `<div class="d-head"><h2>${esc(title)}</h2>${act('关闭','close','','ghost slim')}</div><div class="d-body">${body}</div>`;
  if (!modal.open) modal.showModal();
}

function wrapForm(contents, label='保存', action='save') {
  return `<form id="edit" data-save="${action}">${contents}<div class="form-error" role="alert"></div>
    <div class="d-footer">${act('取消','close')}<button class="btn primary" type="submit">${label}</button></div></form>`;
}
const field = (label, inner, wide=false) => `<label class="label ${wide?'field-wide':''}">${label}${inner}</label>`;
function fieldInput(name,value='', {type='text',required=true,max=120,extra=''}={}) {
  return `<input name="${name}" value="${esc(value)}" type="${type}" ${required?'required':''} maxlength="${max}" ${extra}>`;
}
const sel = (name,options,choice='',placeholder='请选择',required=true) =>
  `<select name="${name}" ${required?'required':''}>${selectors(options,choice,placeholder)}</select>`;
const hidden = (name,value) => `<input type="hidden" name="${name}" value="${esc(value)}">`;
const area = (name,required=false) => `<textarea name="${name}" rows="2" maxlength="300" ${required?'required':''}></textarea>`;
const check = (name, text, checked=true) => `<label class="checkbox-row"><input name="${name}" type="checkbox" ${checked?'checked':''}>${text}</label>`;
const uuid = () => Array.from(crypto.getRandomValues(new Uint8Array(18)), x=>x.toString(16).padStart(2,'0')).join('');

function editProduct(id) {
  const item = state.boot.products.find(e=>e.id===id) || {};
  const frozen = id ? 'readonly' : '';
  const categories = state.boot.categories;
  const chosen = categories[item.kind] || categories.water;
  const labels = Object.values(categories);
  const choices = item.kind==='water'||item.kind==='empty' ? [chosen] :
    id ? [categories.bottle,categories.other] : labels;
  openModal(id?'编辑商品':'新增商品',wrapForm(`<div class="fields">
    ${id?hidden('id',id):''}${field('商品编码',fieldInput('sku',item.sku,{max:70}))}
    ${field('商品名称',fieldInput('name',item.name))}
    ${field('品牌',fieldInput('brand',item.brand,{max:60,extra:frozen}))}
    ${field('规格',fieldInput('spec',item.spec,{max:40,extra:frozen}))}
    ${field('商品分类',sel('category',choices.map(label=>[label,label]),chosen,null))}</div>
    <p class="notice">桶装水自动关联同品牌、同规格的空桶。</p>`,'保存商品','products'));
}

function confirmProductDelete(id) {
  const item = state.boot.products.find(e=>e.id===id);
  if (!item) return;
  openModal('删除商品',wrapForm(`${hidden('id',id)}<p>确认删除「${esc(item.name)}」？已有业务单据和流水仍可查询。</p>`,
    '确认删除','products/delete'));
}

function editStore(id) {
  const item = state.boot.stores.find(e=>e.id===id) || {};
  openModal(id?'编辑门店':'新增门店',wrapForm(`<div class="fields">
    ${id?hidden('id',id):''}${field('门店编码',fieldInput('code',item.code,{max:40}))}
    ${field('门店名称',fieldInput('name',item.name,{max:80}))}</div>`,'保存门店','stores'));
}

function editUser(id) {
  const item = state.data.find(e=>e.id===id) || {};
  const defaultRole = item.role || 'store';
  openModal(id?'编辑账号':'新增账号',wrapForm(`<div class="fields">
    ${id?hidden('id',id):''}${field('登录账号',fieldInput('login',item.login,{max:40,extra:'autocomplete="off"'}))}
    ${field('显示姓名',fieldInput('name',item.name,{max:70}))}
    ${field('账号角色',sel('role',[['store','门店'],['hq','总部']],defaultRole,null))}
    ${field('所属门店',sel('store_id',storeEntries(),item.store_id,'请选择门店',defaultRole==='store'))}
    ${field(id?'新密码（留空沿用）':'初始密码（至少 12 位）',fieldInput('password','',
     {type:'password',required:!id,max:128,extra:'autocomplete="new-password" minlength="12"'}),true)}
    </div>${check('active','启用账号',item.active!==0)}`,'保存账号','users'));
  syncRole(modal.querySelector('form'));
}

function syncRole(form) {
  if (!form.elements.role) return;
  const store = form.elements.store_id;
  store.disabled = form.elements.role.value !== 'store';
  store.required = !store.disabled;
}

const orderHints = {
  ORDER:'确认后扣减本店商品库存，实收空桶同时进入本店库存。',
  COUNT:'按实物盘点填写实盘数，系统记录盘盈或盘亏。',
  TRANSFER:'调出与调入门店在同一笔单据中记账。',
  RETURN:'填实退数量和可重新销售的数量。',
  SEND:'按实际交付数量补记原单的送出记录。',
  REVERSE:'填写原因，系统另记一笔反向流水并保留原单。',
};

function postOptions(kind, detail) {
  if (kind==='RETURN'||kind==='SEND') {
    return detail.items.map(p=>{
      const available = kind==='RETURN'?p.sent-p.returned:p.qty-p.sent;
      return { id:p.product_id, label: p.sku+' · '+p.name+'（最多 '+available+'）', available };
    }).filter(p=>p.available>0);
  }
  const products = ['OUT','COUNT'].includes(kind) ?
    [...state.boot.products,...state.boot.stock_adjustments] : state.boot.products;
  return products.map(p=>({id:p.id,kind:p.kind,label:p.sku+' · '+p.name+' · '+p.spec}));
}

function lineHtml(kind, options) {
  const special = kind==='RETURN';
  const quantity = kind==='COUNT'?'实盘数':kind==='RECEIVED'?'实收空桶数量':'数量';
  const min = kind==='COUNT'?0:1;
  const qtyOptions = {type:'number',extra:`min="${min}" max="1000000" step="1" inputmode="numeric"`};
  const item = sel('product_id',options.map(p=>[p.id,p.label]));
  return `<div class="line-grid ${special?'four':''}">
    ${field(kind==='RECEIVED'?'空桶品牌及规格':'商品',item)}${field(quantity,fieldInput('qty','',qtyOptions))}
    ${special?field('可再售',fieldInput('restock','0',{type:'number',extra:'min="0" max="1000000" step="1"'})):''}
    <div>${act('移除','remove-line','','ghost slim')}</div>
    </div>`;
}

function syncOrderReturns(form) {
  const goods = form.querySelector('#post-lines');
  const received = form.querySelector('#order-empty');
  const waterIds = new Set(state.boot.products.filter(p=>p.kind==='water').map(p=>String(p.id)));
  const hasWater = Array.from(goods.querySelectorAll('select')).some(el=>waterIds.has(el.value));
  received.hidden = !hasWater;
  received.disabled = !hasWater;
  const returned = received.querySelector('#empty-lines');
  const noReturns = form.elements.no_returns;
  if (hasWater) {
    noReturns.required = returned.childElementCount===0;
    noReturns.disabled = returned.childElementCount>0;
    if (noReturns.disabled) noReturns.checked = false;
    received.querySelector('[data-action="add-empty"]').disabled = noReturns.checked;
  }
}

function openPosting(kind, detail=null, chosenStore=null) {
  if (!detail && !state.boot.stores.length) {
    alertToast('请先在基础设置新增门店。');
    return;
  }
  if (kind==='TRANSFER' && state.boot.stores.length<2) {
    alertToast('至少需要两家门店才能调货。');
    return;
  }
  const choices = kind==='REVERSE'?[]:postOptions(kind,detail);
  if (kind!=='REVERSE' && !choices.length) {
    alertToast(detail?'当前没有可处理的商品。':'请先维护商品资料。');
    return;
  }
  const store = chosenStore ?? detail?.store_id ?? (isHQ()?state.store:state.boot.user.store_id);
  const fixed = chosenStore!==null || detail || !isHQ();
  const stores = storeEntries();
  const storeName = state.boot.stores.find(e=>e.id===Number(store))?.name || detail?.store;
  const selectStore = fixed?hidden('store_id',store)+`<input value="${esc(storeName)}" readonly>`:
    sel('store_id',stores,store||(stores.length===1?stores[0][0]:''),'请选择门店');
  const order = kind==='ORDER';
  const emptyChoices = order?state.boot.products.filter(p=>p.kind==='empty').map(
    p=>({id:p.id,label:p.brand+' · '+p.spec+' · '+p.sku})):[];
  const emptyReturns = order?`<fieldset id="order-empty" class="order-empty" hidden disabled>
    <legend>实收空桶</legend>
    <p class="minor">按实际品牌及规格登记。不同品牌或规格的空桶单独计入库存。</p>
    <div id="empty-lines" class="lines"></div>
    ${act('+ 添加实收空桶','add-empty','','slim')}${check('no_returns','本单实收空桶为 0',false)}
    </fieldset>`:'';
  const title = order?'门店下单':kind==='IN'?'添加进货商品':titles(kind);
  openModal(detail?title+' · '+code(detail.id):title,wrapForm(`
    ${hidden('kind',kind)}${hidden('request_key',uuid())}${detail?hidden('linked_id',detail.id):''}
    <div class="fields">${field(kind==='TRANSFER'?'调出门店':kind==='IN'?'分配门店':'所属门店',selectStore)}
    ${kind==='TRANSFER'?field('调入门店',sel('target_store',stores.filter(([id])=>String(id)!==String(store)),'','请选择调入门店')):''}</div>
    ${kind==='REVERSE'?'':`<div class="lines" id="post-lines">${lineHtml(kind,choices)}</div>
    <div>${act('+ 添加商品','add-line','','slim')}</div>`}
    ${emptyReturns}
    ${orderHints[kind]?`<p class="notice">${orderHints[kind]}</p>`:''}
    ${field(kind==='REVERSE'?'冲销原因':'备注（选填）',area('note',kind==='REVERSE'))}
    `,kind==='REVERSE'?'确认冲销':order?'确认下单':kind==='IN'?'确认进货':'确认记账','post'));
  modal.choices = choices;
  modal.emptyChoices = emptyChoices;
  if (detail && choices.length===1) modal.querySelector('[name=product_id]').value = String(choices[0].id);
}

async function showDetail(id) {
  const detail = await api('/api/documents/'+id);
  const original = !detail.reversal_id && detail.kind!=='REVERSE';
  const own = isHQ() || detail.store_id===state.boot.user.store_id;
  const canSend = original&&own&&detail.kind==='SALE'&&detail.items.some(e=>e.qty>e.sent);
  const canReturn = original&&own&&detail.kind==='SALE'&&detail.items.some(e=>e.sent>e.returned);
  const cols = [plain('商品编码','sku'),plain('商品名称','name'),count('数量','qty')];
  if (detail.kind==='RETURN') cols.push(count('可再次销售','restock'));
  if (detail.kind==='SALE') cols.push(count('已退货','returned'));
  const stockCols = [storeColumn,plain('商品','name'),column('变动',e=>signed(e.delta),true),count('变动前','before_qty'),count('变动后','after_qty')];
  const bucketCols = [...brandSpec,count('送出桶','sent'),count('回空桶','received'),count('退回满水','full_returned')];
  openModal(titles(detail.kind)+' · '+code(id),`<div class="d-detail">
    <p>${esc(detail.store)}${detail.target?' → '+esc(detail.target):''} · ${esc(clock(detail.timestamp))} · ${esc(detail.operator_name)}</p>
    ${detail.note?`<p class="notice">${esc(detail.note)}</p>`:''}
    ${detail.linked_id?`<p class="minor">关联单据 ${docLink(detail.linked_id)}</p>`:''}
    ${detail.reversal_id?`<p class="minor">已冲销 ${docLink(detail.reversal_id)}</p>`:''}
    ${detail.items.length?`<section class="card">${heading('商品明细')}${makeTable(cols,detail.items)}</section>`:''}
    ${detail.stock.length?`<section class="card">${heading('库存流水')}${makeTable(stockCols,detail.stock)}</section>`:''}
    ${detail.order_buckets.length?`<section class="card">${heading('桶账变动')}${makeTable(bucketCols,detail.order_buckets)}</section>`:''}
    <div class="d-footer">${canSend?act('补记送出','related','SEND'):''}
    ${canReturn?act('退货','related','RETURN','primary'):''}
    ${original&&own&&(isHQ()||!detail.kind.startsWith('OPEN_'))?act('冲销原单','related','REVERSE','danger'):''}</div>
    </div>`);
  modal.detail = detail;
}

function asNum(value) { return value === '' || value === undefined ? null : Number(value); }

async function submitEdit(form) {
  const method = form.dataset.save;
  const data = Object.fromEntries(new FormData(form));
  if (data.id) data.id = Number(data.id);
  if (method!=='post') {
    if (method==='users') {
      data.active = form.elements.active.checked;
      data.store_id = asNum(data.store_id);
    }
    await api('/api/'+method,data);
    modal.close();
    alertToast(method==='products/delete'?'商品已删除':'已保存');
    await boot();
    return;
  }
  const kind=data.kind;
  const body={kind,request_key:data.request_key,store_id:asNum(data.store_id),note:data.note};
  if (data.linked_id) body.linked_id=Number(data.linked_id);
  if (data.target_store) body.target_store=Number(data.target_store);
  const readLines = (from) => Array.from(form.querySelectorAll(from+' .line-grid')).map(el=>{
    const val = key=>el.querySelector('[name='+key+']')?.value;
    const item={product_id:asNum(val('product_id')),qty:asNum(val('qty'))};
    if (val('restock')!==undefined) item.restock=asNum(val('restock'));
    return item;
  });
  if (kind!=='REVERSE') body.items=readLines('#post-lines');
  if (kind==='ORDER') body.empty_returns=form.querySelector('#order-empty').hidden?[]:readLines('#empty-lines');
  const result = await api(kind==='ORDER'?'/api/orders':'/api/post',body);
  modal.close();
  alertToast((result.duplicate?'已记账 · ':'记账成功 · ')+code(result.id));
  await loadPage();
}

app.addEventListener('change',event=>{
  const item=event.target;
  if (item.closest('#filters')&&item.name==='store') {
    state.store=item.value;
    filt().page=1;
    loadPage();
  }
});
modal.addEventListener('change',event=>{
  const field=event.target;
  const form=field.form;
  if (field.name==='role') syncRole(form);
  if (field.name==='store_id'&&form.elements.target_store) {
    const target=form.elements.target_store;
    target.innerHTML=selectors(storeEntries().filter(([id])=>String(id)!==field.value),target.value,'请选择调入门店');
  }
  if (form?.elements.kind?.value==='ORDER') syncOrderReturns(form);
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if (!['login','filters','search','edit'].includes(form.id)) return;
  event.preventDefault();
  const values=Object.fromEntries(new FormData(form));
  if (form.id==='filters') {
    if (state.page==='dashboard') filt().day = values.day===state.ymd?'':values.day;
    else Object.assign(filt(),{start:values.start||'',end:values.end||state.ymd,page:1});
    await loadPage();
    return;
  }
  if (form.id==='search') {
    Object.assign(filt(),{q:values.q.trim(),type:values.type||'',brand:values.brand||'',spec:values.spec||'',page:1});
    paint();
    return;
  }
  const button=form.querySelector('[type=submit]');
  if (button.disabled) return;
  const errorBox=form.querySelector('.form-error');
  errorBox.textContent='';
  button.disabled=true;
  try {
    if (form.id==='login') {
      await api('/api/login',values);
      state.store='';
      state.page='dashboard';
      state.view={};
      state.filters={};
      await boot();
    } else await submitEdit(form);
  } catch(error) {
    if (form.id!=='login'&&error.status===401) login('登录已过期，请重新登录。');
    else errorBox.textContent=error.message;
  } finally {
    button.disabled=false;
  }
});

document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-action]');
  if (!button||button.disabled) return;
  const action=button.dataset.action;
  const value=button.dataset.value;
  const id=value?Number(value):null;
  try {
    if (action==='nav') {
      state.page=value;
      await loadPage();
    } else if (action==='tab') {
      state.view[state.page]=value;
      await loadPage();
    } else if (action==='page-step') {
      filt().page+=id;
      paint();
    } else if (action==='today') {
      Object.assign(filt(),{day:'',start:state.ymd,end:state.ymd,page:1});
      await loadPage();
    } else if (action==='reload') {
      await boot();
    } else if (action==='reset') {
      delete state.filters[state.page+'.'+view()];
      state.store=isHQ()?'':String(state.boot.user.store_id);
      await loadPage();
    } else if (action==='clear-search') {
      Object.assign(filt(),{q:'',type:'',brand:'',spec:'',page:1});
      paint();
    } else if (action==='logout') {
      await api('/api/logout',{});
      state.loaded++;
      login();
    } else if (action==='close') {
      modal.close();
    } else if (action==='product') editProduct(id);
    else if (action==='store') editStore(id);
    else if (action==='user') editUser(id);
    else if (action==='delete-product') confirmProductDelete(id);
    else if (action==='order') openPosting('ORDER',null,id);
    else if (action==='post') openPosting(value);
    else if (action==='detail') await showDetail(Number(button.dataset.id));
    else if (action==='related') openPosting(value,modal.detail);
    else if (action==='add-line'||action==='add-empty') {
      const form=button.closest('form');
      const empty=action==='add-empty';
      const lines=form.querySelector(empty?'#empty-lines':'#post-lines');
      const choices=empty?modal.emptyChoices:modal.choices;
      if (lines.childElementCount>=Math.min(100,choices.length)) throw new Error('每种商品在同一明细中限填一行');
      lines.insertAdjacentHTML('beforeend',lineHtml(empty?'RECEIVED':form.elements.kind.value,choices));
      if (form.elements.kind.value==='ORDER') syncOrderReturns(form);
      lines.lastElementChild.querySelector('select').focus();
    } else if (action==='remove-line') {
      const line=button.closest('.line-grid');
      const form=line.closest('form');
      if (line.parentElement.id==='empty-lines'||line.parentElement.childElementCount>1) line.remove();
      else throw new Error('至少保留一条明细');
      if (form.elements.kind.value==='ORDER') syncOrderReturns(form);
    }
  } catch(error) {
    if (error.status===401) login('登录已过期，请重新登录。');
    else if (modal.open&&modal.querySelector('.form-error')) modal.querySelector('.form-error').textContent=error.message;
    else alertToast(error.message);
  }
});
const businessDate = new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'});
function rollBusinessDay() {
  if (!state.boot) return;
  const day = businessDate.format(new Date());
  if (day===state.ymd) return;
  const previous = state.ymd;
  state.ymd = day;
  for (const f of Object.values(state.filters)) {
    if (f.end===previous) f.end=day;
    if (f.start===previous) f.start=day;
  }
  if (state.page==='dashboard'&&!filt().day) loadPage();
}
setInterval(rollBusinessDay,1000);
document.addEventListener('visibilitychange',()=>{if (!document.hidden) rollBusinessDay();});
boot();
