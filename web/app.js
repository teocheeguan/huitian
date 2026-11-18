'use strict';

const app = document.querySelector('#app');
const modal = document.querySelector('#dialog');
const toast = document.querySelector('#toast');
const nf = new Intl.NumberFormat('zh-CN');
const pages = {
  dashboard: ['▦','运营看板'], products: ['◇','商品管理'], inventory: ['▤','库存管理'],
  buckets: ['◎','桶管理'], transfers: ['⇄','调货管理'], settings: ['⚙','基础设置'],
};
const tabs = {
  inventory: [['stock','实时库存'],['history','库存流水'],['sales','销售单据']],
  buckets: [['balances','门店桶账'],['events','桶业务单据'],['customers','客户档案']],
  dashboard: [['use','客户用水品种'],['sales','每日商品销量'],['stock','门店商品库存'],['types','按桶型统计']],
  settings: [['stores','门店'],['users','账号'],['opening','期初导入']],
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
    state.filters[key] = { q:'', type:'', brand:'', spec:'', page:1, start:shift(state.ymd,state.page==='dashboard'?-6:-13), end:state.ymd };
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
    ['type', options.types, '全部类型'],
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
const typeColumn = column('类型',e=>e.kind==='water'?'<span class="tag warm">满水</span>':'<span class="tag">空桶</span>');
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
function metric(title,value,note,icon,accent=false) {
  return `<article class="card metric ${accent?'accent':''}"><div class="metric-top">${title}<span class="metric-symbol" aria-hidden="true">${icon}</span></div>
    <strong>${num(value)}</strong><small>${note}</small></article>`;
}
function chart(daily,start,end) {
  const indexed = new Map(daily.map(e=>[e.day,e.qty]));
  const range = Math.min(31,Math.max(1,1+Math.round((new Date(end)-new Date(start))/86400000)));
  const dates = Array.from({length:range},(_,i)=>shift(end,i-range+1));
  const max = Math.max(1,...dates.map(day=>Math.abs(indexed.get(day)||0)));
  return `<div class="bar-chart" aria-label="按日净销量">${dates.map((day,i)=>{
    const qty = indexed.get(day)||0;
    const label = day.slice(5);
    return `<div class="bar-col" title="${day} · ${qty} 桶"><div class="bar-track">${qty?`<div class="bar ${qty<0?'negative':''}" style="--height:${Math.round(Math.abs(qty)/max*100)}%"></div>`:''}</div><span class="bar-label">${dates.length<12||i%5===0||i===dates.length-1?label:''}</span></div>`;
  }).join('')}</div>`;
}
function overview(data) {
  const t = data.totals;
  const storeCols = [plain('门店','name'),count('客户数','customers'),count('满水库存','water'),count('空桶库存','empty'),
    count('净销量','sold'),count('已押桶','deposits'),count('送出桶','sent'),count('回空桶','returned')];
  const dashViews = {
    use: [[storeColumn,plain('商品','name'),...brandSpec,count('客户数','customers'),count('净销量','qty')],data.use],
    sales: [[plain('日期','day'),storeColumn,plain('商品','name'),...brandSpec,count('净销量','qty')],data.per_day],
    stock: [[storeColumn,plain('编码','sku'),plain('商品','name'),...brandSpec,typeColumn,count('库存','qty')],data.stock],
    types: [[storeColumn,...brandSpec,count('已押桶','deposits'),count('送出桶','sent'),count('回空桶','returned'),count('退回满水','full_returned')],data.bucket_types],
  };
  const [cols, detail] = dashViews[view()];
  const form = act('刷新','reload','','ghost') + act('+ 销售录单','post','SALE','primary');
  return pageHead(isHQ()?'总部运营看板':'门店运营看板','查看门店库存、销量与桶账。',form) + outerFilters(true) +
    `<div class="metrics">
      ${metric('满水库存',t.water,'桶 · 截止日结存','▤',true)}
      ${metric('空桶库存',t.empty,'个 · 截止日结存','◎')}
      ${metric('净销量',t.sold,'桶 · 所选期间','↗',true)}
      ${metric('客户总数',t.customers,'户 · 截止日去重','◈')}
      </div><div class="dash-grid">
      <section class="card pad"><h2>净销量走势</h2><p>每日确认销量扣减退货及冲销，最多显示最近 31 天。</p>${chart(data.daily,data.start,data.end)}</section>
      <section class="card pad"><h2>桶账概况</h2><p>押桶为截止日余额，送出和回桶为期间变动。</p><div class="bucket-overview">
      <div><span>已押桶</span><strong>${num(t.deposits)}</strong></div><div><span>送出桶</span><strong>${num(t.sent)}</strong></div>
      <div><span>回空桶</span><strong>${num(t.returned)}</strong></div>
      ${isHQ()&&!state.store?`<div><span>客户未归还</span><strong>${num(t.held)}</strong></div>`:''}</div></section>
      </div><section class="card gap">${heading('门店经营概览')}${makeTable(storeCols,data.stores,'先在基础设置新增门店')}</section>
      <section class="card">${tabsHtml()}${searchBar()}${table(cols,detail)}</section>`;
}

const docCols = [
  column('单据号',e=>docLink(e.id)), column('业务',e=>esc(titles(e.kind))),
  storeColumn, plain('客户','customer'), column('商品明细',e=>`<span title="${esc(e.detail||'')}">${esc((e.detail||'—').slice(0,48))}${(e.detail||'').length>48?'…':''}</span>`),
  column('状态',e=>e.reversal_id?'<span class="tag gray">已冲销</span>':'<span class="tag good">已记账</span>'),
  column('时间',e=>esc(clock(e.timestamp))),
];

function filtersFor(data) {
  const distinct = key=>Array.from(new Set(data.map(e=>e[key]).filter(Boolean))).sort().map(e=>[e,e]);
  return {types:[['water','满水'],['empty','空桶']],brands:distinct('brand'),specs:distinct('spec')};
}

function inventory(data) {
  const head = pageHead('库存管理','入库、出库、盘点和销售记账。',[
    act('+ 录入销售','post','SALE','primary'),act('入库','post','IN'),act('出库','post','OUT'),act('盘点','post','COUNT'),
    isHQ()?act('期初库存','post','OPEN_STOCK'):'',
  ].join(''));
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
    plain('商品编码','sku'),plain('商品名称','name'),...brandSpec,plain('分类','category'),typeColumn,
    column('状态',e=>e.active?'<span class="tag good">启用</span>':'<span class="tag gray">停用</span>'),
    ...(isHQ()?[column('操作',e=>act('编辑','product',e.id,'ghost slim'))]:[]),
  ];
  return pageHead('商品管理','总部统一维护满水商品和对应空桶。',isHQ()?act('+ 新增商品','product','','primary'):'')+
    `<section class="card">${searchBar(filtersFor(data))}${table(cols,data,'暂无商品')}</section>`;
}
function bucketsPage(data) {
  const buttons = [act('+ 回空桶','post','BUCKET_RETURN','primary'),act('押桶','post','DEPOSIT'),act('退押','post','REFUND'),
    act('新增客户','customer'),isHQ()?act('期初桶账','post','OPEN_BUCKET'):''].join('');
  let cols = [storeColumn,column('客户',e=>`<button class="linkbtn" data-action="balance" data-value="${e.customer_id}">${esc(e.customer)}</button>`),
    ...brandSpec,count('押桶净记账','deposits'),count('送出桶','sent'),count('回空桶','returned'),count('退回满水','full_returned')];
  const which = view();
  if (which==='events') cols = docCols;
  if (which==='customers') cols = [
    plain('客户名称','name'),plain('联系电话','phone'),plain('地址','address'),
    column('操作',e=>act('查看桶账','balance',e.id,'ghost slim')+
      (isHQ()||e.creator_store===state.boot.user.store_id?act('编辑','customer',e.id,'ghost slim'):''))
  ];
  const hint = which==='balances'?heading('按门店查看桶账','门店显示各自登记的变动。点击客户名称，查看此客户全公司未归还数。'):'';
  return pageHead('桶管理','空桶按品牌和规格独立记账，支持跨店归还同款空桶。',buttons) +
    outerFilters(which!=='customers') + `<section class="card">${tabsHtml()}${hint}${searchBar({
      placeholder:which==='customers'?'搜索本门店客户或联系电话':'搜索客户、品牌或单据号'
    })}${table(cols,data)}</section>`;
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
  } else if (view()==='users') {
    contents = heading('账号列表') + table([plain('登录账号','login'),plain('显示姓名','name'),
      column('角色',e=>e.role==='hq'?'总部':'门店'),plain('门店','store'),
      column('状态',e=>e.active?'<span class="tag good">启用</span>':'<span class="tag gray">停用</span>'),
      column('操作',e=>act('编辑','user',e.id,'ghost slim'))],data);
  } else {
    contents = `<div class="pad"><h2>一次性导入期初数据</h2><p>先维护门店、商品和空桶编码。上传 .xlsx 或 UTF-8 CSV，首行标题须与模板一致。</p>
      <p class="gap">如有一行校验失败，整份表格都不会记账。每份最多 5,000 行。</p>
      <div class="inline"><a class="btn" href="./api/templates/stock">下载库存模板</a><a class="btn" href="./api/templates/bucket">下载桶账模板</a>
      ${act('导入期初表格','import','','primary')}</div></div>`;
  }
  const buttons = view()==='stores'?act('+ 新增门店','store','','primary'):
    view()==='users'?act('+ 新增账号','user','','primary'):'';
  return pageHead('基础设置','维护门店、登录账号和系统期初数据。',buttons)+`<section class="card">${tabsHtml()}${contents}</section>`;
}

const renderers = {dashboard:overview,products:productPage,inventory,buckets:bucketsPage,transfers:transfersPage,settings};
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
    if (page==='dashboard') value = await api('/api/dashboard?'+filter);
    if (page==='products') value = state.boot.products;
    if (page==='inventory') {
      if (current==='stock') value = await api('/api/stock?'+filter);
      if (current==='history') value = await api('/api/history?'+filter);
      if (current==='sales') value = await api('/api/documents?'+params({group:'sales'}));
    }
    if (page==='buckets') {
      if (current==='balances') value = await api('/api/buckets?'+filter);
      if (current==='events') value = await api('/api/documents?'+params({group:'bucket'}));
      if (current==='customers') value = await api('/api/customers?'+filter);
    }
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
  const kind = id ? hidden('kind',item.kind) +
    `<input value="${item.kind==='water'?'满水':'空桶'}" readonly>` : sel('kind',[['water','满水'],['empty','空桶']],'water',null);
  openModal(id?'编辑商品':'新增商品',wrapForm(`<div class="fields">
    ${id?hidden('id',id):''}${field('商品编码',fieldInput('sku',item.sku,{max:70}))}
    ${field('商品名称',fieldInput('name',item.name))}
    ${field('品牌',fieldInput('brand',item.brand,{max:60,extra:frozen}))}
    ${field('规格',fieldInput('spec',item.spec,{max:40,extra:frozen}))}
    ${field('商品分类',fieldInput('category',item.category||'桶装水',{max:60}))}${field('商品类型',kind)}</div>
    ${check('active','启用商品',item.active!==0)}
    <p class="notice">满水自动匹配同品牌、同规格的空桶。可先建立空桶编码，或由系统自动建立对应空桶。</p>`,'保存商品','products'));
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

function editCustomer(id) {
  const item = (Array.isArray(state.data)?state.data:[]).find(e=>e.id===id) || {};
  openModal(id?'编辑客户':'新增客户',wrapForm(`<div class="fields">
    ${id?hidden('id',id):''}${customerFields(item)}</div>`,'保存客户','customers'));
}

function customerFields(item={}) {
  return field('客户名称',fieldInput('name',item.name,{max:100}))+
    field('联系电话',fieldInput('phone',item.phone,{type:'tel',max:40}))+
    field('地址（选填）',fieldInput('address',item.address,{required:false,max:220}),true);
}

function searchCustomerFields() {
  return `<div>
    <div class="inline"><label class="label">客户联系电话<input name="customer_phone" type="tel" required maxlength="40" placeholder="完整手机号或联系电话"></label>
    <div class="actions">${act('查找客户','lookup')}</div></div>
    <input type="hidden" name="customer_id"><p class="minor" id="match-customer" aria-live="polite"></p>
    <div class="fields" id="create-customer" hidden>${field('新客户名称',fieldInput('new_customer_name','',{required:false,max:100}))}
    ${field('新客户地址（选填）',fieldInput('new_customer_address','',{required:false,max:220}))}</div>
    </div>`;
}

function openImport() {
  openModal('导入期初表格',wrapForm(`
    ${hidden('request_key',uuid())}
    ${field('导入类型',sel('kind',[['stock','期初库存'],['bucket','期初桶账']],'stock',null))}
    ${field('Excel 或 CSV 文件',fieldInput('file','',{type:'file',extra:'accept=".xlsx,.csv"'}))}
    <p class="notice">使用「基础设置 → 期初导入」的模板填写编码和数量。请将 Excel 中的联系电话列设为文本。</p>
    `,'确认导入','import'));
}

const customerPosting = ['SALE','BUCKET_RETURN','DEPOSIT','REFUND','OPEN_BUCKET'];
const bucketPosting = ['BUCKET_RETURN','DEPOSIT','REFUND','OPEN_BUCKET'];
const postHint = {
  SALE:'确认后立即扣减满水库存并计入销量。稍后实际交付，可从原销售单补记送出。',
  TRANSFER:'请在实物交接时确认。系统会在同一笔单据中扣减调出店并增加调入店库存。',
  COUNT:'填写实盘数量。系统根据当前库存自动记录盘盈、盘亏或无差异。',
  OPEN_STOCK:'只用于商品在该门店首次开账。已经发生出入库，请用盘点。',
  OPEN_BUCKET:'填写开账时已押桶数和实际未归还数，两列分别计算。',
  BUCKET_RETURN:'仅记录收到的空桶。数量将增加到收桶门店，并扣减对应品牌和规格的未归还数。',
  REFUND:'由原押桶门店登记已办理的退押数量。',
  RETURN:'填写退回满水总量和其中可再次销售的数量。按实退数冲减销量。',
  SEND:'按本次实际交付的数量补记送出桶数。库存和销量已在销售确认时记账。',
  REVERSE:'原始单据保留，生成一笔关联的反向流水。若已发生真实退货，请使用满水退货。',
};

function postOptions(kind, detail) {
  if (kind==='RETURN'||kind==='SEND') {
    return detail.items.map(p=>{
      const available = kind==='RETURN'?p.sent-p.returned:p.qty-p.sent;
      return { id:p.product_id, label: p.sku+' · '+p.name+'（最多 '+available+'）', available };
    }).filter(p=>p.available>0);
  }
  return state.boot.products.filter(p=>
    (p.active||['OUT','COUNT','REFUND','BUCKET_RETURN'].includes(kind))&&
    (!bucketPosting.includes(kind)||p.kind==='empty')&& (kind!=='SALE'||p.kind==='water'))
    .map(p=>({id:p.id,label:p.sku+' · '+p.name+(p.kind==='empty'?'（空桶）':'')+(p.active?'':'（已停用）')}));
}

function lineHtml(kind, options) {
  const special = kind==='RETURN'||kind==='OPEN_BUCKET';
  const quantity = kind==='COUNT'?'实盘数':kind==='OPEN_BUCKET'?'已押桶数':'数量';
  const canZero = ['COUNT','OPEN_BUCKET','OPEN_STOCK'].includes(kind);
  const qtyOptions = {type:'number',extra:`min="${canZero?0:1}" max="1000000" step="1"${canZero?'':' inputmode="numeric"'}`};
  const item = sel('product_id',options.map(p=>[p.id,p.label]));
  return `<div class="line-grid ${special?'four':''}">
    ${field('商品',item)}${field(quantity,fieldInput('qty','',qtyOptions))}
    ${special?field(kind==='RETURN'?'可再售':'未归还',fieldInput(kind==='RETURN'?'restock':'held','0',
      {type:'number',extra:'min="0" max="1000000" step="1"'})):''}
    <div> <span class="minor" aria-hidden="true">&nbsp;</span><br>${act('移除','remove-line','','ghost slim')}</div>
    </div>`;
}

function openPosting(kind, detail=null) {
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
    alertToast(detail?'当前没有符合条件的待处理商品。':'没有可选商品，请先维护商品资料。');
    return;
  }
  const store = detail?detail.store_id:state.store;
  const fixedStore = detail||!isHQ();
  const stores = storeEntries();
  const selectStore = fixedStore?hidden('store_id',store)+`<input value="${esc(detail?.store||state.boot.user.store)}" readonly>`:
    sel('store_id',stores,store|| (stores.length===1?stores[0][0]:''),'请选择门店');
  const sale = kind==='SALE' ? `<div class="fields">
    ${field('订单来源',sel('source',['小程序','微信','电话','到店','其他'].map(x=>[x,x]),'微信',null))}
    ${field('原订单号（小程序必填）',fieldInput('external_number','',{required:false,max:90}))}
    </div>${check('dispatch_now','满水已实际交付，同时登记送出桶数')}`:'';
  openModal(detail?titles(kind)+' · '+code(detail.id):titles(kind),wrapForm(`
    ${hidden('kind',kind)}${hidden('request_key',uuid())}${detail?hidden('linked_id',detail.id):''}
    <div class="fields">${field(kind==='TRANSFER'?'调出门店':'所属门店',selectStore)}
    ${kind==='TRANSFER'?field('调入门店',sel('target_store',stores.filter(([id])=>String(id)!==String(store)),'','请选择调入门店')):''}</div>
    ${customerPosting.includes(kind)?searchCustomerFields():''}${sale}
    ${kind==='REVERSE'?'':`<div class="lines" id="post-lines">${lineHtml(kind,choices)}</div>
    <div>${act('+ 添加商品','add-line','','slim')}</div>`}
    ${postHint[kind]?`<p class="notice">${postHint[kind]}</p>`:''}
    ${field(kind==='REVERSE'?'冲销原因':'备注（选填）',area('note',kind==='REVERSE'))}
    `,kind==='REVERSE'?'确认冲销':'确认记账','post'));
  modal.choices = choices;
  modal.detail = detail;
  if (detail && choices.length===1) modal.querySelector('[name=product_id]').value = String(choices[0].id);
}

function getCustomerForm(form) {
  return form.querySelector('#match-customer');
}
async function lookupCustomer(form) {
  const phone = form.elements.customer_phone.value.trim();
  const message = getCustomerForm(form);
  if (!phone) {
    message.textContent = '请输入完整联系电话';
    return;
  }
  const phoneAtStart = phone;
  const found = await api('/api/customers?'+new URLSearchParams({phone}));
  if (form.elements.customer_phone.value.trim()!==phoneAtStart) return;
  const item = found[0];
  const newFields = form.querySelector('#create-customer');
  form.elements.customer_id.value = item?.id || '';
  newFields.hidden = Boolean(item);
  if (!item) {
    message.textContent = '未找到客户，可填写名称后建立档案。';
    form.elements.new_customer_name.focus();
  } else {
    message.textContent = '已选客户 · ' + item.name;
    if (bucketPosting.includes(form.elements.kind.value)) {
      const info = await api('/api/customers/'+item.id+'/balances?'+new URLSearchParams({phone}));
      if (form.elements.customer_id.value!==String(item.id)) return;
      if (info.length) {
        message.textContent += ' ｜ ' + info.map(e=>e.brand+' '+e.spec+' 未还 '+e.held+' / 已押 '+e.deposits).join('，');
      }
    }
  }
}

async function showDetail(id) {
  const detail = await api('/api/documents/'+id);
  const original = !detail.reversal_id && detail.kind!=='REVERSE';
  const own = isHQ() || detail.store_id===state.boot.user.store_id;
  const canSend = original&&own&&detail.kind==='SALE'&&detail.items.some(e=>e.qty>e.sent);
  const canReturn = original&&own&&detail.kind==='SALE'&&detail.items.some(e=>e.sent>e.returned);
  const cols = [plain('商品编码','sku'),plain('商品名称','name'),count('数量','qty')];
  if (detail.kind==='RETURN') cols.push(count('可再次销售','restock'));
  if (detail.kind==='SALE') cols.push(count('已送出','sent'),count('已退满水','returned'));
  const stockCols = [storeColumn,plain('商品','name'),column('变动',e=>signed(e.delta),true),count('变动前','before_qty'),count('变动后','after_qty')];
  const bucketCols = [plain('空桶','name'),column('押桶变动',e=>signed(e.deposit_delta),true),
    column('未归还变动',e=>signed(e.held_delta),true),count('送出','sent'),count('回空桶','returned'),count('退回满水','full_returned')];
  openModal(titles(detail.kind)+' · '+code(id),`<div class="d-detail">
    <p>${esc(detail.store)}${detail.target?' → '+esc(detail.target):''} · ${esc(clock(detail.timestamp))} · ${esc(detail.operator_name)}</p>
    ${detail.customer?`<p class="minor">客户 ${esc(detail.customer)}</p>`:''}
    ${detail.source?`<p class="minor">来源 ${esc(detail.source)} ${esc(detail.external_number||'')}</p>`:''}
    ${detail.note?`<p class="notice">${esc(detail.note)}</p>`:''}
    ${detail.linked_id?`<p class="minor">关联单据 ${docLink(detail.linked_id)}</p>`:''}
    ${detail.reversal_id?`<p class="minor">已冲销 ${docLink(detail.reversal_id)}</p>`:''}
    ${detail.items.length?`<section class="card">${heading('商品明细')}${makeTable(cols,detail.items)}</section>`:''}
    ${detail.stock.length?`<section class="card">${heading('库存流水')}${makeTable(stockCols,detail.stock)}</section>`:''}
    ${detail.buckets.length?`<section class="card">${heading('桶账变动')}${makeTable(bucketCols,detail.buckets)}</section>`:''}
    <div class="d-footer">${canSend?act('补记送出','related','SEND'):''}
    ${canReturn?act('满水退货','related','RETURN','primary'):''}
    ${original&&own&&(isHQ()||!detail.kind.startsWith('OPEN_'))?act('冲销原单','related','REVERSE','danger'):''}</div>
    </div>`);
  modal.detail = detail;
}

async function showBalances(id) {
  const result = await api('/api/customers/'+id+'/balances');
  openModal('客户桶账 · 全公司余额',`<div class="d-detail"><section class="card">${makeTable([
    plain('空桶编码','sku'),...brandSpec,count('已押桶','deposits'),count('未归还','held'),
  ],result,'该客户当前没有已押或未归还空桶')}</section><div class="d-footer">${act('关闭','close')}</div></div>`);
}

function fileContent(file) {
  return new Promise((resolve,reject)=>{
    const reader = new FileReader();
    reader.onload = ()=>resolve(String(reader.result).split(',')[1]);
    reader.onerror = ()=>reject(new Error('文件读取失败'));
    reader.readAsDataURL(file);
  });
}
function asNum(value) { return value === '' || value === undefined ? null : Number(value); }

async function submitEdit(form) {
  const method = form.dataset.save;
  const data = Object.fromEntries(new FormData(form));
  if (data.id) data.id = Number(data.id);
  if (method==='import') {
    const file=form.elements.file.files[0];
    if (!file||file.size>2600000) throw new Error('请选择小于 2.6 MB 的文件');
    const result = await api('/api/import',{kind:data.kind,request_key:data.request_key,filename:file.name,content:await fileContent(file)});
    modal.close();
    alertToast('已导入 '+num(result.count)+' 行');
    await boot();
    return;
  }
  if (method!=='post') {
    if (method==='products'||method==='users') data.active = form.elements.active.checked;
    if (method==='users') data.store_id = asNum(data.store_id);
    const result = await api('/api/'+method,data);
    modal.close();
    alertToast('已保存');
    if (method==='customers' && state.page!=='buckets') {
      state.view.buckets='customers';
      state.page='buckets';
    }
    if (result.id) await boot();
    return;
  }
  const kind=data.kind;
  const body={kind,request_key:data.request_key,store_id:asNum(data.store_id),note:data.note};
  if (data.linked_id) body.linked_id=Number(data.linked_id);
  if (data.target_store) body.target_store=Number(data.target_store);
  if (kind!=='REVERSE') {
    body.items = Array.from(form.querySelectorAll('.line-grid')).map(el=>{
      const val = key=>el.querySelector('[name='+key+']')?.value;
      const item={product_id:asNum(val('product_id')),qty:asNum(val('qty'))};
      for (const field of ['held','restock']) if (val(field)!==undefined) item[field]=asNum(val(field));
      return item;
    });
  }
  if (customerPosting.includes(kind)) {
    if (!data.customer_id) {
      const found = await api('/api/customers?'+new URLSearchParams({phone:data.customer_phone}));
      if (found.length) data.customer_id=found[0].id;
      else if (data.new_customer_name && !form.querySelector('#create-customer').hidden) {
        const created=await api('/api/customers',{name:data.new_customer_name,phone:data.customer_phone,address:data.new_customer_address});
        data.customer_id=created.id;
      } else {
        await lookupCustomer(form);
        throw new Error('请确认客户，或补填新客户名称后再提交');
      }
      form.elements.customer_id.value=String(data.customer_id);
    }
    body.customer_id=Number(data.customer_id);
    body.customer_phone=data.customer_phone;
  }
  if (kind==='SALE') {
    body.source=data.source;
    body.external_number=data.external_number;
    body.dispatch_now=form.elements.dispatch_now.checked;
  }
  const result = await api('/api/post',body);
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
modal.addEventListener('input',event=>{
  if (event.target.name==='customer_phone') {
    const form=event.target.form;
    form.elements.customer_id.value='';
    form.querySelector('#create-customer').hidden=true;
    form.elements.new_customer_name.value='';
    form.elements.new_customer_address.value='';
    getCustomerForm(form).textContent='';
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
  if (field.name==='source'&&form.elements.external_number) form.elements.external_number.required=field.value==='小程序';
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if (!['login','filters','search','edit'].includes(form.id)) return;
  event.preventDefault();
  const values=Object.fromEntries(new FormData(form));
  if (form.id==='filters') {
    Object.assign(filt(),{start:values.start||'',end:values.end||state.ymd,page:1});
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
      Object.assign(filt(),{start:state.ymd,end:state.ymd,page:1});
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
    else if (action==='customer') editCustomer(id);
    else if (action==='import') openImport();
    else if (action==='post') openPosting(value);
    else if (action==='detail') await showDetail(Number(button.dataset.id));
    else if (action==='balance') await showBalances(id);
    else if (action==='related') openPosting(value,modal.detail);
    else if (action==='lookup') await lookupCustomer(button.closest('form'));
    else if (action==='add-line') {
      const form=button.closest('form');
      const lines=form.querySelector('#post-lines');
      if (lines.childElementCount>=Math.min(100,modal.choices.length)) throw new Error('每个商品在一张单据中只能添加一次');
      lines.insertAdjacentHTML('beforeend',lineHtml(form.elements.kind.value,modal.choices));
      lines.lastElementChild.querySelector('select').focus();
    } else if (action==='remove-line') {
      const line=button.closest('.line-grid');
      if (line.parentElement.childElementCount>1) line.remove();
      else throw new Error('至少保留一条明细');
    }
  } catch(error) {
    if (error.status===401) login('登录已过期，请重新登录。');
    else if (modal.open&&modal.querySelector('.form-error')) modal.querySelector('.form-error').textContent=error.message;
    else alertToast(error.message);
  }
});
boot();
