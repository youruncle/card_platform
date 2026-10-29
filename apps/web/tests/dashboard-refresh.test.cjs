// Run from apps/web: node --import tsx --test tests/dashboard-refresh.test.cjs
const { test, afterEach } = require('node:test');
const assert = require('node:assert/strict');
const deps = process.env.COST_TEST_DEPENDENCIES;
const dependency = name => require(deps ? `${deps}/${name}` : name);
const { JSDOM } = dependency('jsdom');
const dom = new JSDOM('<html><body></body></html>', { url: 'http://localhost:3000' });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, 'navigator', { value: dom.window.navigator, configurable: true });
global.HTMLElement = dom.window.HTMLElement;
global.React = require('react');
global.IS_REACT_ACT_ENVIRONMENT = true;
const { render, screen, fireEvent, cleanup, act, within } = dependency('@testing-library/react');
const Dashboard = require('../app/page.tsx').default;
require('./cost-legacy-fixture.cjs');
const intervals = new Map();
const timeouts = new Map();
let timerId = 0;
window.setInterval = (fn, ms) => { const id = ++timerId; intervals.set(id, { fn, ms }); return id; };
window.clearInterval = id => intervals.delete(id);
window.setTimeout = (fn, ms) => { const id = ++timerId; timeouts.set(id, { fn, ms }); return id; };
window.clearTimeout = id => timeouts.delete(id);
afterEach(() => { cleanup(); intervals.clear(); timeouts.clear(); });
const response = data => ({ ok: true, status: 200, json: async () => data });
const summary = { owner_id: 'fixture-owner', base_currency: 'CNY', item_count: 108,
  valued_item_count: 0, unvalued_item_count: 108, total_cost: '360', total_market_value: '599',
  unrealized_profit: '239', realized_profit: '0' };
const item = page => ({ id: `card-${page}`, player_name: `Fixture Page ${page}`, season_start: 2024,
  product_name: 'Fixture Product', purchase_price: null, recognition_confidence: null,
  recognition_quality_issues: [], valuation_status: 'queued' });
const pageData = page => ({ items: [item(page)], page, total: 108, total_pages: 4, page_size: 30 });
function fixture(initialJobs = []) {
  const state = { jobs: initialJobs, pages: [], sizes: [], queries: [], scannerCalls: 0, hold: null };
  global.fetch = async url => {
    const path = new URL(url, 'http://localhost');
    if (path.pathname === '/v1/auth/me') return response({ id: 'fixture-owner', display_name: 'Fixture' });
    if (path.pathname === '/v1/scanner/jobs') { state.scannerCalls++; return response(state.jobs); }
    if (path.pathname === '/v1/portfolio/summary') return response(summary);
    if (path.pathname === '/v1/portfolio/identity-corrections') return response({});
    if (path.pathname === '/v1/portfolio/items/page') {
      const page = Number(path.searchParams.get('page'));
      state.pages.push(page);
      state.sizes.push(Number(path.searchParams.get("page_size")));
      state.queries.push(path.searchParams.get('search'));
      if (state.pages.length > 12) throw new Error('Unbounded portfolio refresh');
      if (state.hold) return state.hold(page);
      return response(pageData(page));
    }
    throw new Error(`Unexpected endpoint: ${path.pathname}`);
  };
  return state;
}
async function mount() { await act(async () => { render(React.createElement(Dashboard)); }); }
test('manual cost entry is directly accessible and saves without contacting CardHobby',async()=>{
  const state=fixture();const originalFetch=global.fetch;const writes=[];
  global.fetch=async(url,options)=>{
    if(new URL(url,'http://localhost').pathname === '/v1/portfolio/items/card-1' && options?.method === 'PATCH'){
      writes.push(JSON.parse(options.body));
      state.hold=page=>response({...pageData(page),items:[{...item(page),purchase_price:'25.50',purchase_currency:'CNY',purchase_total_cost:'25.50'}]});
      return response({});
    }
    return originalFetch(url,options);
  };
  await mount();
  fireEvent.click(screen.getByRole('button',{name:'人工录入'}));
  const input=screen.getByRole('spinbutton',{name:'人工录入买入价'});
  fireEvent.change(input,{target:{value:'25.50'}});
  await act(async()=>{fireEvent.blur(input);});
  assert.deepEqual(writes,[{purchase_price:25.5,purchase_currency:'CNY'}]);
  assert.ok(screen.getByRole('button',{name:'修改成本'}));
});
async function tick(ms) {
  await act(async () => {
    for (const { fn, ms: delay } of [...intervals.values()]) if (delay === ms) await fn();
  });
}
async function retry() {
  await act(async () => {
    for (const [id, { fn }] of [...timeouts]) { timeouts.delete(id); await fn(); }
  });
}
const total = () => document.querySelector('.hero-metric strong').textContent;
const next = () => screen.getByRole('button', { name: '下一页' });

test('cards render without waiting for summary or correction counts', async () => {
  fixture(); const original = global.fetch;
  const held = deferred();
  global.fetch = (url, options) => {
    const path = new URL(url, 'http://localhost').pathname;
    if (['/v1/portfolio/summary', '/v1/portfolio/identity-corrections'].includes(path)) return held.promise;
    return original(url, options);
  };
  await mount();
  assert.ok(screen.getByRole('button', {name:'Fixture Page 1'}));
  assert.equal(total(),'—','pending account metrics are not shown as zero');
  assert.equal(next().disabled, false);
});

function detailFixture() {
  const state = fixture(); const original = global.fetch;
  const corrections = deferred(), review = deferred(), catalog = deferred();
  global.fetch = (url, options) => {
    const path = new URL(url,'http://localhost').pathname;
    if (path === '/v1/portfolio/items/card-1/detail') return Promise.resolve(response({item:{...item(1),strategy_tags:[],quantity_held:'1',remaining_cost:'0'},transactions:[],valuations:[]}));
    if (path.endsWith('/card-1/identity-corrections')) return corrections.promise;
    if (path.endsWith('/identity-review/items/card-1')) return review.promise;
    if (path.endsWith('/card-1/catalog-enrichment')) return catalog.promise;
    if (path.includes('purchase-cost')) return Promise.resolve(response(null));
    return original(url,options);
  };
  return Object.assign(state,{corrections,review,catalog});
}

test('core detail displays while review and catalog are pending, and auxiliary errors can retry', async()=>{
  const state=detailFixture(); await mount();
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Fixture Page 1'})));
  assert.ok(screen.getByText('CARD DOSSIER / 卡片档案'));
  assert.ok(screen.getByText('正在读取纠错与身份复核…'));
  await act(async()=>state.review.resolve({ok:false,status:503,json:async()=>({detail:'review unavailable'})}));
  assert.ok(screen.getByRole('button',{name:'重试详情辅助信息'}));
  assert.ok(screen.getByText('CARD DOSSIER / 卡片档案'));
});

test('closing detail invalidates late auxiliary responses',async()=>{
  const state=detailFixture(); await mount();
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Fixture Page 1'})));
  assert.ok(screen.getByText('CARD DOSSIER / 卡片档案'));
  await act(async()=>fireEvent.click(screen.getByRole('button',{name:'关闭卡片详情'})));
  await act(async()=>state.review.resolve({ok:false,status:503,json:async()=>({detail:'late review failure'})}));
  assert.equal(screen.queryByText(/late review failure/),null);
  assert.equal(screen.queryByText('CARD DOSSIER / 卡片档案'),null);
});

test('switching cards discards the previous card catalog and auxiliary failures',async()=>{
 const state=detailFixture();state.hold=page=>response({...pageData(page),items:[item(1),item(2)]});
 const original=global.fetch;
 global.fetch=(url,options)=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path.endsWith('/card-2/detail'))return Promise.resolve(response({item:{...item(2),strategy_tags:[],quantity_held:'1',remaining_cost:'0'},transactions:[],valuations:[]}));
  if(path.endsWith('/card-2/identity-corrections'))return Promise.resolve(response([]));
  if(path.endsWith('/identity-review/items/card-2'))return Promise.resolve(response(null));
  if(path.endsWith('/card-2/catalog-enrichment'))return Promise.resolve(response({player_name:'CURRENT CATALOG'}));
  return original(url,options);
 };
 await mount();
 await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Fixture Page 1'})));
 await act(async()=>fireEvent.click(screen.getByRole('button',{name:'Fixture Page 2'})));
 await act(async()=>{state.catalog.resolve(response({player_name:'OLD CATALOG'}));state.review.resolve({ok:false,status:503,json:async()=>({detail:'OLD REVIEW FAILURE'})});});
 assert.ok(screen.getByText('CURRENT CATALOG'));
 assert.equal(screen.queryByText('OLD CATALOG'),null);
 assert.equal(screen.queryByText(/OLD REVIEW FAILURE/),null);
});

test('logout rejects a summary that completes after the session ended',async()=>{
 fixture();const original=global.fetch;const held=deferred();
 global.fetch=(url,options)=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/portfolio/summary')return held.promise;
  if(path==='/v1/auth/logout')return Promise.resolve(response({}));
  return original(url,options);
 };
 await mount();
 await act(async()=>fireEvent.click(screen.getByRole('button',{name:'退出登录'})));
 await act(async()=>held.resolve(response({...summary,total_market_value:'987654'})));
 assert.doesNotMatch(total(),/987,?654/);
});

test('session expiry clears account cards, amounts and correction state before another login',async()=>{
 fixture();await mount();
 assert.match(total(),/599/);
 await act(async()=>window.dispatchEvent(new window.Event('card-platform:unauthorized')));
 assert.doesNotMatch(total(),/599/);
 assert.equal(screen.queryByRole('button',{name:'Fixture Page 1'}),null);
});

test('auxiliary failures do not erase cards and have a retry action', async () => {
  fixture(); const original = global.fetch;
  global.fetch = (url, options) => {
    if (new URL(url,'http://localhost').pathname === '/v1/portfolio/identity-corrections') {
      return Promise.resolve({ok:false,status:503,json:async()=>({detail:'correction count unavailable'})});
    }
    return original(url, options);
  };
  await mount();
  assert.ok(screen.getByRole('button', {name:'Fixture Page 1'}));
  assert.ok(screen.getByRole('button', {name:'重试汇总与纠错统计'}));
});

test('search, sorting and page size reuse metrics while an explicit refresh reloads them', async () => {
  fixture(); const original = global.fetch; let summaries=0;
  global.fetch = (url, options) => {
    if (new URL(url,'http://localhost').pathname === '/v1/portfolio/summary') summaries++;
    return original(url, options);
  };
  await mount(); assert.equal(summaries,1);
  await act(async()=>{fireEvent.change(screen.getByLabelText('搜索全部卡片'),{target:{value:'杜兰特'}});fireEvent.click(screen.getByRole('button',{name:'搜索',exact:true}));});
  await act(async()=>fireEvent.change(screen.getByLabelText('每页显示数量'),{target:{value:'50'}}));
  await act(async()=>fireEvent.change(screen.getByLabelText('卡库排序'),{target:{value:'fair_value'}}));
  assert.equal(summaries,1);
  await tick(3000);
  assert.equal(summaries,2);
});

test('unfiltered loading projects only the page and refresh keeps metrics independent',async()=>{
 const state=fixture();const original=global.fetch;const params=[];let summaryCalls=0;
 global.fetch=async(url,options)=>{
  const path=new URL(url,'http://localhost');
  if(path.pathname==='/v1/portfolio/summary')summaryCalls++;
  if(path.pathname==='/v1/portfolio/items/page'){
   params.push(path.searchParams);
   return response({...pageData(Number(path.searchParams.get('page'))),summary,snapshot_token:'frozen'});
  }
  return original(url,options);
 };
 await mount();
 assert.equal(params[0].has('include_summary'),false);
 assert.equal(params[0].get('snapshot_mode'),'membership');
 assert.equal(summaryCalls,1,'account metrics load independently');
 await tick(3000);
 assert.equal(params.at(-1).get('snapshot_token'),null,'polling needs fresh valuations');
 assert.equal(params.at(-1).has('include_summary'),false);
 assert.equal(params.at(-1).get('snapshot_mode'),'membership');
});

test('collection search is beside holdings and queries the whole account, preserving filters across pagination',async()=>{
 const state=fixture(); await mount();
 await act(async()=>{fireEvent.click(next());});
 const input=screen.getByRole('textbox',{name:'搜索全部卡片'});
 assert.ok(document.querySelector('#collection').contains(input));
 assert.equal(document.querySelector('.topbar input'),null);
 fireEvent.change(input,{target:{value:'杜兰特'}});
 state.hold=page=>response({...pageData(page),items:[{...item(page),player_name:'KEVIN DURANT'}]});
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'搜索'}));});
 assert.deepEqual(JSON.parse(state.queries.at(-1)),{q:'杜兰特'});
 assert.equal(state.pages.at(-1),1);
 assert.ok(screen.getByRole('button',{name:'KEVIN DURANT'}));
 await act(async()=>{fireEvent.click(next());});
 assert.deepEqual(JSON.parse(state.queries.at(-1)),{q:'杜兰特'});
 await tick(3000);
 assert.deepEqual(JSON.parse(state.queries.at(-1)),{q:'杜兰特'});
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'清空条件'}));});
 assert.equal(state.queries.at(-1),null);
 assert.equal(state.pages.at(-1),1);
});

test('new search supersedes an older page request and retains server results not matching literal Chinese text',async()=>{
 const state=fixture(); await mount(); let release;
 state.hold=()=>new Promise(resolve=>{release=resolve;});
 await tick(3000);
 const input=screen.getByRole('textbox',{name:'搜索全部卡片'});
 fireEvent.change(input,{target:{value:'凯文杜兰特'}});
 state.hold=()=>response({...pageData(1),items:[{...item(1),player_name:'KEVIN DURANT'}],total:1,total_pages:1});
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'搜索'}));});
 await act(async()=>{release(response(pageData(3)));});
 assert.ok(screen.getByRole('button',{name:'KEVIN DURANT'}));
 assert.equal(screen.queryByText('Fixture Page 3'),null);
});

test('failed search from later page remains on search page one when background work retries',async()=>{
 const state=fixture();await mount();
 await act(async()=>{fireEvent.click(next());});
 await act(async()=>{fireEvent.click(next());});
 assert.equal(state.pages.at(-1),3);
 state.hold=()=>({ok:false,status:503,json:async()=>({detail:'fixture search unavailable'})});
 fireEvent.change(screen.getByRole('textbox',{name:'搜索全部卡片'}),{target:{value:'杜兰特'}});
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'搜索'}));});
 assert.equal(state.pages.at(-1),1);
 assert.ok(screen.queryByText('找到 0 张卡片') === null, 'failed search must not claim zero matches');
 assert.ok(screen.queryByText('没有匹配的卡片') === null, 'failed search must not show a successful empty state');
 const retrySearch=screen.getByRole('button',{name:'重试搜索'});
 state.hold=page=>response({...pageData(page),items:[{...item(page),player_name:'Recovered Card'}]});
 await act(async()=>{fireEvent.click(retrySearch);});
 assert.equal(state.pages.at(-1),1);
 assert.deepEqual(JSON.parse(state.queries.at(-1)),{q:'杜兰特'});
 assert.ok(screen.getByRole('button',{name:'Recovered Card'}));
});

test('historical scanner imports on other pages refresh once without a fetch/render loop', async () => {
  const state = fixture([
    { id: 'old', status: 'succeeded', collection_item_id: 'off-page-card' },
    { id: 'pending', status: 'queued', collection_item_id: null },
  ]);
  await mount();
  assert.equal(state.pages.length, 2);
  assert.match(total(), /599/);
  assert.equal(next().disabled, false);
  for (let i = 0; i < 3; i++) await tick(2000);
  assert.equal(state.pages.length, 2, 'unchanged jobs must not trigger more portfolio requests');
  assert.equal(state.scannerCalls, 5, 'scanner effect must not restart after each item update');
  state.jobs = [...state.jobs.slice(0, 1), { id: 'pending', status: 'succeeded', collection_item_id: 'new-card' }];
  await tick(2000);
  assert.equal(state.pages.length, 3, 'the final completed import must refresh the collection');
  await retry();
  assert.equal(state.pages.length, 3);
});

test('portfolio metrics update automatically only after scanned cards are imported', async () => {
  const state = fixture([{id:'pending',status:'running',collection_item_id:null}]);
  const originalFetch = global.fetch;
  let metrics = {...summary,item_count:845,valued_item_count:505,unvalued_item_count:340,
    total_cost:'20994',total_market_value:'21724',unrealized_profit:'730'};
  global.fetch = async (url, options) => {
    const path = new URL(url,'http://localhost');
    if (path.pathname === '/v1/portfolio/summary') return response(metrics);
    if (path.pathname === '/v1/portfolio/items/page') {
      await originalFetch(url,options);
      return response({...pageData(Number(path.searchParams.get('page'))),
        total:metrics.item_count,total_pages:29,summary:metrics,snapshot_token:'fixture-snapshot'});
    }
    return originalFetch(url,options);
  };
  await mount();
  assert.ok(screen.getByText('845 张资产卡'));
  await tick(2000);
  assert.ok(screen.getByText('845 张资产卡'),'recognition drafts are not counted as holdings');
  metrics = {...metrics,item_count:846,unvalued_item_count:341,
    total_cost:'21004',total_market_value:'21734'};
  state.jobs = [{id:'pending',status:'succeeded',collection_item_id:'new-card'}];
  await tick(2000);
  assert.ok(screen.getByText('846 张资产卡'));
  assert.match(total(),/21,734/);
  assert.ok(screen.getByText('341 张按成本暂估'));
  assert.ok(screen.getByText('505 张已完成估值'));
});

test('background valuation keeps the total and pager stable; stale page response cannot undo navigation', async () => {
  const state = fixture();
  await mount();
  let release;
  state.hold = page => page === 1 ? new Promise(resolve => { release = resolve; }) : response(pageData(page));
  await tick(3000);
  assert.equal(state.pages.length, 2);
  assert.match(total(), /599/);
  assert.equal(next().disabled, false);
  await tick(3000);
  assert.equal(state.pages.length, 2, 'do not overlap background requests');
  await act(async () => { fireEvent.click(next()); });
  assert.ok(screen.getByText('Fixture Page 2'));
  assert.match(total(), /599/);
  await act(async () => { release(response(pageData(1))); });
  assert.ok(screen.getByText('Fixture Page 2'));
  assert.equal(screen.queryByText('Fixture Page 1'), null);
  state.hold = null;
  await tick(3000);
  assert.equal(state.pages.at(-1), 2, 'future background updates must stay on the selected page');
});

test('final scanner import retries after a failed refresh even after active polling stops', async () => {
  const state = fixture([{ id: 'pending', status: 'queued', collection_item_id: null }]);
  await mount();
  state.hold = async () => { throw new Error('Temporary network failure'); };
  state.jobs = [{ id: 'pending', status: 'succeeded', collection_item_id: 'new-card' }];
  await tick(2000);
  assert.equal(state.pages.length, 2);
  assert.match(total(), /599/);
  assert.equal(next().disabled, false);
  assert.equal([...intervals.values()].some(timer => timer.ms === 2000), false);
  state.hold = null;
  await retry();
  assert.equal(state.pages.length, 3);
  await retry();
  assert.equal(state.pages.length, 3);
});

test('scanner completion waits for navigation, then refreshes the selected page without hiding the total', async () => {
  const state = fixture([{ id: 'pending', status: 'queued', collection_item_id: null }]);
  await mount();
  let release;
  state.hold = page => new Promise(resolve => { release = () => resolve(response(pageData(page))); });
  await act(async () => { fireEvent.click(next()); });
  assert.equal(next().disabled, true, 'only foreground navigation disables the pager');
  assert.match(total(), /599/, 'navigation must keep the loaded summary visible');
  state.jobs = [{ id: 'pending', status: 'succeeded', collection_item_id: 'new-card' }];
  await tick(2000);
  assert.equal(state.pages.length, 2, 'scanner refresh waits for the pending navigation');
  await act(async () => { release(); });
  state.hold = null;
  await retry();
  assert.deepEqual(state.pages, [1, 2, 2]);
  assert.ok(screen.getByText('Fixture Page 2'));
  assert.equal(next().disabled, false);
});

const costCandidate = {
  unit_id: 'fixture-unit-1', evidence_hash: 'a'.repeat(64), order_id: '900001',
  ordered_at: '2025-09-01', seller: 'Fixture Shop', status: '已完成', item_id: '900002',
  unit_index: 0, title: 'Fixture Test Card', image_urls: [],
  source_url: 'https://cardhobby.com.cn/Order/detail/900001', card_price: '10.00',
  shipping: '0.04', total_cost: '10.04', currency: 'CNY', confidence: '0.95',
  reasons: ['年份一致', '球员一致'], missing: [], automatic: true,
};
const recordedCost = candidate => ({ status: 'recorded', recorded: candidate, candidates: [], rejected: [], reason: '' });
const emptyCost = (status = 'not_found', reason = '未找到购买记录') => ({ status, candidates: [], rejected: [], reason });
function deferred() {
  let resolve;
  const promise = new Promise(accept => { resolve = accept; });
  return { promise, resolve };
}
function costFixture(itemsByPage = {}) {
  const state = fixture();
  const portfolioFetch = global.fetch;
  Object.assign(state, {
    costCalls: [], bridgeCalls: [], costs: new Map(), result: recordedCost(costCandidate),
    search: () => ({ orders: [{ fixture: 'order' }], complete: true }),
    ping: () => ({ version: '0.3.0' }),
  });
  state.withCosts = page => ({ ...pageData(page), items: (itemsByPage[page] ?? pageData(page).items).map(card => {
    const saved = state.costs.get(card.id);
    return saved ? { ...card, purchase_price: saved.card_price, purchase_shipping: saved.shipping,
      purchase_total_cost: saved.total_cost, purchase_currency: saved.currency } : card;
  }) });
  global.fetch = async (url, options) => {
    const path = new URL(url, 'http://localhost').pathname;
    const costPath = path.match(/^\/v1\/purchase-cost\/items\/([^/]+)\/(search-plan|search-result|confirm)$/);
    if (costPath) {
      state.costCalls.push({ itemId: costPath[1], action: costPath[2], options });
      if (costPath[2] === 'search-plan') return response({ item_id: costPath[1], label: 'Fixture Card', keywords: ['Fixture Player'], status: 'ready', reason: '' });
      if (costPath[2] === 'confirm') {
        const selected = JSON.parse(options.body).unit_id;
        const candidate = state.result.candidates.find(candidate => candidate.unit_id === selected);
        assert.ok(candidate, 'confirmation must refer to an actual duplicate purchase');
        state.costs.set(costPath[1], candidate);
        return response({ status: 'recorded' });
      }
      const result = typeof state.result === 'function' ? await state.result(costPath[1]) : state.result;
      if (result.recorded) state.costs.set(costPath[1], result.recorded);
      return response(result);
    }
    const data = await portfolioFetch(url, options);
    if (path === '/v1/portfolio/items/page') {
      const page = await data.json();
      const query=JSON.parse(new URL(url,'http://localhost').searchParams.get('search')??'{}');
      if(query.q){
        const matches=Object.values(itemsByPage).flat().filter(card=>card.player_name.includes(query.q));
        return response({...page,items:matches,total:matches.length,total_pages:1});
      }
      return response({ ...page, items: (itemsByPage[page.page] ?? page.items).map(card => {
        const saved = state.costs.get(card.id);
        return saved ? { ...card, purchase_price: saved.card_price, purchase_shipping: saved.shipping,
          purchase_total_cost: saved.total_cost, purchase_currency: saved.currency } : card;
      }) });
    }
    return data;
  };
  window.postMessage = message => {
    state.bridgeCalls.push(message);
    if (message.type === 'CARD_PLATFORM_COST_CANCEL') return;
    const reply = message.type === 'CARD_PLATFORM_COST_PING' ? state.ping(message) : Promise.resolve(state.search(message)).then(result => ({ result }));
    Promise.resolve(reply).then(result => {
      if (result === undefined) return;
      window.dispatchEvent(new window.MessageEvent('message', { origin: window.location.origin, source: window,
        data: { type: 'CARD_PLATFORM_COST_RESPONSE', requestId: message.requestId, ...result } }));
    });
  };
  return state;
}
const cardRow = (page = 1) => screen.getByText(`Fixture Page ${page}`).closest('tr');
const costCell = (page = 1) => cardRow(page).querySelector('.purchase-price-cell');
const searches = state => state.bridgeCalls.filter(message => message.type === 'CARD_PLATFORM_COST_SEARCH');
async function startCost(page = 1) {
  const row = cardRow(page);
  const menu = row.querySelector('.row-more-menu');
  assert.equal(within(menu).queryByRole('button', { name: '获取成本' }), null);
  await act(async () => { fireEvent.click(within(costCell(page)).getByRole('button', { name: '获取成本' })); });
  return menu;
}
async function flushTimeout(ms) {
  await act(async () => {
    for (const [id, timer] of [...timeouts]) if (timer.ms === ms) {
      timeouts.delete(id);
      await timer.fn();
    }
  });
}

test('getting cost directly from the price cell stays in the list and updates the price while refreshing quietly', async () => {
  const state = costFixture();
  const capture = deferred();
  state.search = () => capture.promise;
  await mount();
  const menu = await startCost();
  assert.equal(menu.open, false);
  assert.equal(screen.queryByRole('dialog'), null);
  assert.match(costCell().textContent, /成本获取中/);
  assert.equal(within(costCell()).getByRole('button', { name: '获取成本' }).disabled, true);
  assert.equal(next().disabled, false);
  assert.match(total(), /599/);
  assert.deepEqual(searches(state).map(message => message.keywords), [['Fixture Player']]);
  await startCost();
  assert.equal(state.costCalls.filter(call => call.action === 'search-plan').length, 1, 'repeated clicks must reuse the pending lookup');
  assert.equal(searches(state).length, 1);

  const refresh = deferred();
  state.hold = () => refresh.promise;
  await act(async () => { capture.resolve({ orders: [{ fixture: 'order' }], complete: true }); });
  assert.match(costCell().textContent, /¥10\.00/);
  assert.match(costCell().textContent, /购入总成本 ¥10\.04/);
  assert.doesNotMatch(costCell().textContent, /成本获取中/);
  assert.equal(next().disabled, false, 'saving a cost must not turn a background refresh into navigation loading');
  assert.match(total(), /599/);
  assert.equal(screen.queryByRole('dialog'), null);
  assert.equal(state.pages.length, 2);
  await act(async () => { refresh.resolve(response(state.withCosts(1))); });
  assert.match(costCell().textContent, /¥10\.00/);
  assert.equal(state.costCalls.filter(call => call.action === 'search-result').length, 1);
});

for (const oldPrice of ['0.00', '25.00']) test(`explicit cost lookup refreshes an existing ${oldPrice} cost and immediately displays new price`, async () => {
  const state = costFixture({1:[{...item(1),purchase_price:oldPrice,purchase_total_cost:oldPrice}]});
  await mount();
  const capture = deferred(); state.search = () => capture.promise;
  await startCost();
  assert.match(costCell().textContent, /成本获取中/);
  assert.equal(searches(state).length,1);
  const refresh = deferred(); state.hold = () => refresh.promise;
  await act(async()=>{capture.resolve({orders:[],complete:true});});
  assert.match(costCell().textContent,/购入总成本 ¥10\.04/);
  assert.match(costCell().textContent,/¥10\.00/);
  assert.doesNotMatch(costCell().textContent,/已跳过/);
  await act(async()=>{refresh.resolve(response(state.withCosts(1)));});
});

test('pending cost follows its card across pages and completion refreshes the currently selected page', async () => {
  const state = costFixture();
  const capture = deferred();
  state.search = () => capture.promise;
  await mount();
  await startCost();
  await act(async () => { fireEvent.click(next()); });
  assert.doesNotMatch(costCell(2).textContent, /成本获取中/);
  assert.match(total(), /599/);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '上一页' })); });
  assert.match(costCell().textContent, /成本获取中/);
  assert.equal(searches(state).length, 1, 'returning to a page must not restart the search');
  await act(async () => { fireEvent.click(next()); });
  await act(async () => { capture.resolve({ orders: [], complete: true }); });
  assert.ok(screen.getByText('Fixture Page 2'));
  assert.equal(state.pages.at(-1), 2, 'the completed card must not reset the visible page');
  assert.doesNotMatch(costCell(2).textContent, /¥10\.00|成本获取中/);
  assert.equal(next().disabled, false);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '上一页' })); });
  assert.match(costCell().textContent, /购入总成本 ¥10\.04/);
  assert.doesNotMatch(costCell().textContent, /成本获取中/);
});

test('different cards queue their searches while each row keeps its own pending state and result', async () => {
  const state = costFixture();
  const first = deferred();
  const second = deferred();
  state.search = () => searches(state).length === 1 ? first.promise : second.promise;
  state.result = id => recordedCost(id === 'card-1' ? costCandidate : { ...costCandidate, card_price: '25.00', shipping: '2.00', total_cost: '27.00' });
  await mount();
  await startCost();
  await act(async () => { fireEvent.click(next()); });
  await startCost(2);
  assert.match(costCell(2).textContent, /成本获取中/);
  assert.equal(searches(state).length, 1, 'the signed-in browser can process only one search at a time');
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '上一页' })); });
  assert.match(costCell().textContent, /成本获取中/);
  await act(async () => { fireEvent.click(next()); });
  await act(async () => { first.resolve({ orders: [], complete: true }); });
  assert.equal(searches(state).length, 2, 'the queued card must start once the first search finishes');
  assert.match(costCell(2).textContent, /成本获取中/);
  await act(async () => { second.resolve({ orders: [], complete: true }); });
  assert.match(costCell(2).textContent, /购入总成本 ¥27\.00/);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '上一页' })); });
  assert.match(costCell().textContent, /购入总成本 ¥10\.04/);
  assert.equal(searches(state).length, 2);
});

test('no matching order ends the row loading state and a later click starts a fresh automatic lookup', async () => {
  const state = costFixture();
  state.result = emptyCost();
  await mount();
  await startCost();
  assert.doesNotMatch(costCell().textContent, /成本获取中/);
  assert.match(costCell().textContent, /未找到/);
  assert.equal(screen.queryByRole('dialog'), null);
  state.result = recordedCost(costCandidate);
  await startCost();
  assert.equal(searches(state).length, 2);
  assert.equal(state.costCalls.filter(call => call.action === 'search-plan').length, 2);
  assert.match(costCell().textContent, /购入总成本 ¥10\.04/);
  assert.equal(screen.queryByRole('dialog'), null);
});

test('a search failure ends the row loading state and does not prevent retry', async () => {
  const state = costFixture();
  const failed = deferred();
  state.search = () => failed.promise;
  await mount();
  await startCost();
  await act(async () => {
    const request = searches(state)[0];
    window.dispatchEvent(new window.MessageEvent('message', { origin: window.location.origin, source: window,
      data: { type: 'CARD_PLATFORM_COST_RESPONSE', requestId: request.requestId, error: 'Fixture search unavailable' } }));
  });
  assert.doesNotMatch(costCell().textContent, /成本获取中/);
  assert.equal(screen.queryByRole('dialog'), null);
  assert.equal(state.costCalls.filter(call => call.action === 'search-result').length, 0);
  state.search = () => ({ orders: [], complete: true });
  await startCost();
  assert.equal(searches(state).length, 2);
  assert.match(costCell().textContent, /购入总成本 ¥10\.04/);
});

test('duplicate purchases wait in the row and opening their choice uses the already fetched evidence', async () => {
  const state = costFixture();
  state.result = { status: 'needs_confirmation', candidates: [costCandidate,
    { ...costCandidate, unit_id: 'fixture-unit-2', order_id: '900003', seller: 'Second Fixture Shop', card_price: '25.00', shipping: '2.00', total_cost: '27.00' }],
    rejected: [], reason: '发现相同卡片的多笔购买记录，请选择对应订单' };
  await mount();
  await startCost();
  assert.equal(screen.queryByRole('dialog'), null, 'finding a duplicate must not interrupt browsing');
  assert.doesNotMatch(costCell().textContent, /成本获取中/);
  const choice = within(cardRow()).getByRole('button', { name: '选择对应订单' });
  await act(async () => { fireEvent.click(choice); });
  const dialog = await screen.findByRole('dialog');
  assert.equal(within(dialog).getAllByRole('radio').length, 2);
  assert.equal(searches(state).length, 1);
  assert.equal(state.costCalls.filter(call => call.action === 'search-plan').length, 1);
  assert.equal(state.costCalls.filter(call => call.action === 'search-result').length, 1);
  assert.equal(state.costCalls.filter(call => call.action === 'confirm').length, 0);
  await act(async () => {
    fireEvent.click(within(dialog).getAllByRole('radio')[1]);
  });
  await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: '确认所选商品并录入成本' })); });
  const confirmation = state.costCalls.find(call => call.action === 'confirm');
  assert.deepEqual(JSON.parse(confirmation.options.body), { unit_id: 'fixture-unit-2', evidence_hash: 'a'.repeat(64), refresh:true });
  assert.match(costCell().textContent, /购入总成本 ¥27\.00/);
  assert.equal(searches(state).length, 1);
});

test('a missing connection stops row loading and opens connection instructions only when requested', async () => {
  const state = costFixture();
  state.ping = () => undefined;
  await mount();
  await startCost();
  assert.match(costCell().textContent, /成本获取中/);
  await flushTimeout(1500);
  assert.doesNotMatch(costCell().textContent, /成本获取中/);
  assert.equal(screen.queryByRole('dialog'), null);
  const connect = within(cardRow()).getByRole('button', { name: '连接卡淘' });
  await act(async () => { fireEvent.click(connect); });
  const dialog = await screen.findByRole('dialog');
  assert.ok(within(dialog).getByRole('link', { name: '打开卡淘订单页' }));
  assert.equal(searches(state).length, 0);
  assert.equal(state.costCalls.filter(call => call.action === 'search-plan').length, 1, 'opening instructions must not repeat the failed lookup');
});

const selectedCostButton = () => screen.getByRole('button', { name: /批量录入卡淘成本价/ });
test('expired login is visible in the row with a login link and no cost write', async () => {
  const state = costFixture();
  window.postMessage = message => {
    if (message.type === 'CARD_PLATFORM_COST_CANCEL') return;
    queueMicrotask(() => window.dispatchEvent(new window.MessageEvent('message', {
      origin: window.location.origin, source: window,
      data: {type:'CARD_PLATFORM_COST_RESPONSE',requestId:message.requestId,
        ...(message.type === 'CARD_PLATFORM_COST_PING' ? {version:'0.3.0'} : {error:'请先在此 Chrome 登录卡淘，再重试获取成本'})},
    })));
  };
  await mount();await startCost();
  assert.match(costCell().textContent, /卡淘登录已过期/);
  assert.match(costCell().textContent, /本次未修改已有成本/);
  assert.ok(within(costCell()).getByRole('button', {name:'登录卡淘'}));
  assert.ok(screen.getByRole('heading', {name:'登录并绑定卡淘账号'}));
  assert.equal(screen.queryByRole('button', {name:'连接卡淘'}), null);
  assert.equal(state.costCalls.filter(call => call.action === 'search-result').length, 0);
  assert.equal(within(costCell()).getByRole('button', {name:'获取成本'}).disabled, false);
});
const selectCard = page => fireEvent.click(within(cardRow(page)).getByRole('checkbox'));

test('selected cost recording skips unselected cards and existing zero costs, then updates queued rows in order', async () => {
  const state = costFixture({ 1: [item(1), item(2), item(3),
    { ...item(4), purchase_price: '0.00' }, { ...item(5), purchase_total_cost: '0.00' }] });
  const first = deferred();
  const second = deferred();
  state.search = () => searches(state).length === 1 ? first.promise : second.promise;
  state.result = id => recordedCost(id === 'card-1' ? costCandidate
    : { ...costCandidate, card_price: '25.00', shipping: '2.00', total_cost: '27.00' });
  await mount();
  assert.equal(screen.queryByRole('button', { name: /批量录入卡淘成本价/ }), null);
  await act(async () => { [1, 2, 4, 5].forEach(selectCard); });
  assert.match(selectedCostButton().textContent, /\(4\)/);
  await act(async () => { fireEvent.click(selectedCostButton()); });
  assert.equal(screen.queryByRole('dialog'), null);
  assert.match(costCell(1).textContent, /成本获取中/);
  assert.match(costCell(2).textContent, /成本获取中/);
  for (const page of [3, 4, 5]) assert.doesNotMatch(costCell(page).textContent, /成本获取中/);
  for (const page of [1, 2, 4, 5]) assert.equal(within(cardRow(page)).getByRole('checkbox').checked, true);
  assert.equal(selectedCostButton().disabled, true, 'already queued and manually priced selections cannot start another batch');
  await act(async () => { fireEvent.click(selectedCostButton()); });
  assert.deepEqual(state.costCalls.filter(call => call.action === 'search-plan').map(call => call.itemId), ['card-1']);

  await act(async () => { first.resolve({ orders: [], complete: true }); });
  assert.match(costCell(1).textContent, /购入总成本 ¥10\.04/);
  assert.match(costCell(2).textContent, /成本获取中/);
  assert.equal(searches(state).length, 2);
  await act(async () => { second.resolve({ orders: [], complete: true }); });
  assert.match(costCell(2).textContent, /购入总成本 ¥27\.00/);
  assert.doesNotMatch(costCell(1).textContent + costCell(2).textContent, /成本获取中/);
  assert.deepEqual(state.costCalls.filter(call => call.action === 'search-result').map(call => call.itemId), ['card-1', 'card-2']);
  assert.equal(state.costs.has('card-3') || state.costs.has('card-4') || state.costs.has('card-5'), false);
  assert.equal(selectedCostButton().disabled, true);
});

test('a batch joins an existing lookup once and keeps its submitted cards after deselection and navigation', async () => {
  const state = costFixture({ 1: [item(1), item(3), item(4)] });
  const active = deferred();
  const queued = deferred();
  state.search = () => searches(state).length === 1 ? active.promise : queued.promise;
  await mount();
  await startCost(1);
  await act(async () => { [1, 3].forEach(selectCard); });
  await act(async () => { fireEvent.click(selectedCostButton()); });
  assert.match(costCell(3).textContent, /成本获取中/);
  assert.doesNotMatch(costCell(4).textContent, /成本获取中/);
  assert.equal(searches(state).length, 1, 'an already pending selection keeps its original request');
  await act(async () => { selectCard(3); });
  await act(async () => { fireEvent.change(screen.getByRole('textbox', { name: '搜索全部卡片' }), { target: { value: 'Fixture Page 4' } }); fireEvent.click(screen.getByRole('button',{name:'搜索'})); });
  assert.equal(screen.queryByText('Fixture Page 3'), null);
  await act(async () => { fireEvent.click(screen.getByRole('button',{name:'清空条件'})); });
  await act(async () => { fireEvent.click(next()); });
  assert.ok(screen.getByText('Fixture Page 2'));
  await act(async () => { active.resolve({ orders: [], complete: true }); });
  assert.deepEqual(state.costCalls.filter(call => call.action === 'search-plan').map(call => call.itemId), ['card-1', 'card-3']);
  assert.equal(searches(state).length, 2, 'the submitted card still runs after it is deselected and leaves the page');
  await act(async () => { queued.resolve({ orders: [], complete: true }); });
  assert.ok(screen.getByText('Fixture Page 2'));
  assert.doesNotMatch(costCell(2).textContent, /成本获取中|购入总成本 ¥10\.04/);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '上一页' })); });
  assert.match(costCell(1).textContent, /购入总成本 ¥10\.04/);
  assert.match(costCell(3).textContent, /购入总成本 ¥10\.04/);
  assert.equal(state.costs.has('card-4'), false);
  assert.equal(screen.queryByRole('dialog'), null);
});

test('selected retry preserves duplicate choices and retries only the unmatched card', async () => {
  const state = costFixture({ 1: [item(1), item(2)] });
  state.result = id => id === 'card-1'
    ? { status: 'needs_confirmation', candidates: [costCandidate,
      { ...costCandidate, unit_id: 'fixture-unit-2', order_id: '900003', seller: 'Second Fixture Shop' }],
      rejected: [], reason: '发现相同卡片的多笔购买记录，请选择对应订单' }
    : emptyCost('unmatched', '未识别到同款');
  await mount();
  await startCost(1);
  await startCost(2);
  assert.ok(within(cardRow(1)).getByRole('button', { name: '选择对应订单' }));
  assert.match(costCell(2).textContent, /未识别到同款/);
  await act(async () => { [1, 2].forEach(selectCard); });
  state.result = recordedCost(costCandidate);
  await act(async () => { fireEvent.click(selectedCostButton()); });
  assert.deepEqual(state.costCalls.filter(call => call.action === 'search-plan').map(call => call.itemId), ['card-1', 'card-2', 'card-2']);
  assert.ok(within(cardRow(1)).getByRole('button', { name: '选择对应订单' }));
  assert.match(costCell(2).textContent, /购入总成本 ¥10\.04/);
  assert.equal(selectedCostButton().disabled, true);
  assert.equal(screen.queryByRole('dialog'), null);
  await act(async () => { fireEvent.click(within(cardRow(1)).getByRole('button', { name: '选择对应订单' })); });
  assert.equal(within(screen.getByRole('dialog')).getAllByRole('radio').length, 2, 'batching must preserve the original duplicate evidence');
  assert.equal(searches(state).length, 3);
});


test('page size defaults to 30 and persists through filtered navigation and polling', async () => {
  const state = fixture(); await mount();
  assert.equal(state.sizes.at(-1), 30);
  const size = screen.getByRole('combobox', { name: '每页显示数量' });
  assert.equal(size.value, '30');
  fireEvent.change(screen.getByRole('textbox', { name: '搜索全部卡片' }), { target: { value: '杜兰特' } });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '搜索' })); });
  await act(async () => { fireEvent.click(next()); });
  await act(async () => { fireEvent.change(size, { target: { value: '50' } }); });
  assert.equal(state.pages.at(-1), 1);
  assert.equal(state.sizes.at(-1), 50);
  assert.deepEqual(JSON.parse(state.queries.at(-1)), { q: '杜兰特' });
  await act(async () => { fireEvent.click(next()); });
  await tick(3000);
  assert.equal(state.sizes.at(-1), 50);
  assert.equal(state.pages.at(-1), 2);
  assert.deepEqual(JSON.parse(state.queries.at(-1)), { q: '杜兰特' });
});

test('size change supersedes older requests and failed size changes retry at page one', async () => {
  const state = fixture(); await mount();
  await act(async () => { fireEvent.click(next()); });
  let release;
  state.hold = () => new Promise(resolve => { release = resolve; });
  await tick(3000);
  state.hold = () => ({ ok: false, status: 503, json: async () => ({ detail: 'Temporary failure' }) });
  await act(async () => { fireEvent.change(screen.getByRole('combobox', { name: '每页显示数量' }), { target: { value: '100' } }); });
  await act(async () => { release(response(pageData(2))); });
  assert.equal(screen.queryByText('Fixture Page 2'), null);
  state.hold = null;
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重试搜索' })); });
  assert.equal(state.pages.at(-1), 1);
  assert.equal(state.sizes.at(-1), 100);
  assert.ok(screen.getByText('Fixture Page 1'));
});

test('marking sold and restoring refresh both card state and portfolio metrics',async()=>{
 const state=fixture(); const baseFetch=global.fetch; let status='held';const writes=[];
 global.fetch=async(url,options)=>{
   const path=new URL(url,'http://localhost').pathname;
   if(path==='/v1/portfolio/items/card-1'&&options?.method==='PATCH'){
     const payload=JSON.parse(options.body);writes.push(payload);status=payload.holding_status;
     return response({});
   }
   if(path==='/v1/portfolio/summary')return response({...summary,total_market_value:status==='sold'?'0':'599',item_count:status==='sold'?0:1});
   return baseFetch(url,options);
 };
 state.hold=page=>response({...pageData(page),items:[{...item(page),holding_status:status}]});
 await mount();
 const menu=document.querySelector('.row-more-menu');menu.open=true;
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'标记为已卖出'}));});
 assert.deepEqual(writes,[{holding_status:'sold'}]);
 assert.match(total(),/0/);
 assert.ok(screen.getByRole('button',{name:'恢复持有'}));
 await act(async()=>{fireEvent.click(screen.getByRole('button',{name:'恢复持有'}));});
 assert.deepEqual(writes,[{holding_status:'sold'},{holding_status:'held'}]);
 assert.match(total(),/599/);
});


test('first page is requested without competing full-account metrics',async()=>{
 const state=fixture();const original=global.fetch;const held=deferred();let metrics=0;
 global.fetch=(url,options)=>{
  const path=new URL(url,'http://localhost');
  if(path.pathname==='/v1/portfolio/summary'||path.pathname==='/v1/portfolio/identity-corrections')metrics++;
  if(path.pathname==='/v1/portfolio/items/page')return held.promise;
  return original(url,options);
 };
 await mount();assert.equal(metrics,0,'heavy metrics start after first-page response');
 await act(async()=>held.resolve(response(pageData(1))));
 assert.ok(screen.getByRole('button',{name:'Fixture Page 1'}));
 assert.equal(metrics,2);
});

test('next page rebuilds an expired snapshot without returning to page one', async () => {
  fixture();
  const original = global.fetch;
  const queries = [];
  global.fetch = async (url, options) => {
    const path = new URL(url, 'http://localhost');
    if (path.pathname !== '/v1/portfolio/items/page') return original(url, options);
    queries.push(path.searchParams);
    if (queries.length === 2) return {ok:false,status:409,json:async()=>({detail:{code:'snapshot_expired',message:'expired'}})};
    return response({...pageData(Number(path.searchParams.get('page'))), snapshot_token: queries.length === 1 ? 'old-token' : 'fresh-token'});
  };
  await mount();
  await act(async () => fireEvent.click(next()));
  assert.ok(screen.getByRole('button', {name:'Fixture Page 2'}));
  assert.ok(screen.getByText('第 2 / 4 页'));
  assert.equal(queries[1].get('snapshot_token'), 'old-token');
  assert.equal(queries[2].get('snapshot'), 'true');
  assert.equal(queries[2].get('snapshot_token'), null);
  await act(async () => fireEvent.click(next()));
  assert.ok(screen.getByRole('button', {name:'Fixture Page 3'}));
  assert.equal(queries[3].get('snapshot_token'), 'fresh-token');
});

test('search superseding the first load still starts initial account metrics once',async()=>{
 fixture();const original=global.fetch;const initial=deferred();let pageCalls=0,metrics=0;
 global.fetch=(url,options)=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/portfolio/items/page'&&++pageCalls===1)return initial.promise;
  if(path==='/v1/portfolio/summary')metrics++;
  return original(url,options);
 };
 await mount();assert.equal(metrics,0);
 await act(async()=>{
  fireEvent.change(screen.getByLabelText('搜索全部卡片'),{target:{value:'Fixture'}});
  fireEvent.click(screen.getByRole('button',{name:'搜索',exact:true}));
 });
 assert.ok(screen.getByRole('button',{name:'Fixture Page 1'}));
 assert.equal(metrics,1,'the latest page must initialize metrics even if a filtered refresh');
 await act(async()=>initial.resolve(response(pageData(1))));
 assert.equal(metrics,1,'superseded first response cannot initialize duplicate metrics');
});

test('incomplete imported card stays visible with pending marker and blank-safe library and detail identity',async()=>{
 const pending={...item(1),player_name:null,brand_name:null,product_name:null,season_start:null,season_end:null,identity_review_required:true,identity_missing_fields:['player_name','brand_name','product_name','season_start']};
 const state=detailFixture();state.hold=()=>response({...pageData(1),items:[pending]});const original=global.fetch;
 global.fetch=(url,options)=>new URL(url,'http://localhost').pathname==='/v1/portfolio/items/card-1/detail'?Promise.resolve(response({item:{...pending,strategy_tags:[],quantity_held:'1',remaining_cost:'0'},transactions:[],valuations:[]})):original(url,options);
 await mount();assert.ok(screen.getByText('信息待确认'));const row=screen.getByRole('button',{name:'未确认卡片'}).closest('tr');assert.doesNotMatch(row.textContent,/null|undefined/);
 await act(async()=>fireEvent.click(screen.getByRole('button',{name:'未确认卡片'})));
 const detail=screen.getByText('CARD DOSSIER / 卡片档案').closest('.modal');assert.ok(within(detail).getByText('信息待确认'));assert.ok(within(detail).getByRole('heading',{name:'未确认卡片'}));assert.doesNotMatch(detail.querySelector('.detail-subtitle').textContent,/null|undefined/);
});
