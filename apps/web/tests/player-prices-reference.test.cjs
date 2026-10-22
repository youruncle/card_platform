const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const React=require('react');
const {render,cleanup,fireEvent}=require('@testing-library/react');
const {PlayerPriceBoard}=require('../components/player-price-board.tsx');
const {sortPlayers}=require('../../../packages/card-core/src/player-prices.ts');
afterEach(cleanup);
function player(name,index,reference){
 const card={card_key:name,identity:{season_start:2024,product_name:'Prizm',card_number:'134',parallel_name:'Base'},selection_count:7,status:'ready',current_count:3,previous_count:4,current_price:'120',previous_price:'100',change_pct:reference,latest_sale_at:'2026-10-20T12:00:00+08:00',sources:[]};
 return {player_id:name,player_name:name,status:'insufficient',change_pct:index,cards:[],coverage:{},refresh:{queued:false,error:null},market:{report_date:'2026-10-21',window_days:30,min_sales:3,sale_count:7,card_count:1,trend_count:reference===null?0:1,latest_sale_at:card.latest_sale_at,cards:[card],cards_truncated:false,reference_card:reference===null?null:card,current_window:{start:'2026-09-22T00:00:00+08:00',end:'2026-10-22T00:00:00+08:00'},previous_window:{start:'2026-08-23T00:00:00+08:00',end:'2026-09-22T00:00:00+08:00'}}};
}
const board=players=>({report_date:'2026-10-21',timezone:'Asia/Shanghai',window_days:7,min_sales:3,currency:'CNY',players});
test('shared sorting ranks the disclosed monthly card and leaves a missing reference last',()=>{
 const players=[player('up','-99','20'),player('down','99','-10'),player('flat','8','0'),player('missing','999',null)];
 assert.deepEqual(sortPlayers(players,'gainers','2026-10-21').map(p=>p.player_id),['up','flat','down','missing']);
 assert.deepEqual(sortPlayers(players,'losers','2026-10-21').map(p=>p.player_id),['down','flat','up','missing']);
 players[0].market.report_date='2026-10-20';
 assert.equal(sortPlayers(players,'gainers','2026-10-21').at(-1).player_id,'up');
});
test('collapsed row exposes monthly signal, exact identity, medians and sample counts without an index',()=>{
 const {container}=render(React.createElement(PlayerPriceBoard,{data:board([player('Curry',null,'20')])}));
 const summary=container.querySelector('.pp-player > summary');
 for(const expected of [/\+20.00%/,/#134/,/100%/,/120.00/,/100.00/,/近30日.*3 笔/,/此前30日.*4 笔/])assert.match(summary.textContent,expected);
 assert.doesNotMatch(summary.textContent,/综合指数暂缺/);
});
test('index-only data cannot appear as a monthly change or a false zero',()=>{
 const {container}=render(React.createElement(PlayerPriceBoard,{data:board([player('Only index','99',null)])}));
 const summary=container.querySelector('.pp-player > summary');
 assert.doesNotMatch(summary.textContent,/99.00%|0.00%/);
 assert.match(summary.textContent,/可比样本不足/);
});
test('monthly sorting works through user controls and reference outside detail limit remains visible',()=>{
 const p=player('Reference',null,'20');
 p.market.cards=[{...p.market.cards[0],card_key:'other',identity:{product_name:'Other'},change_pct:null,status:'insufficient'}];
 p.market.cards_truncated=true;
 const {container,getByText}=render(React.createElement(PlayerPriceBoard,{data:board([p,player('Down','99','-10')])}));
 fireEvent.click(getByText('跌幅优先'));
 assert.equal(container.querySelector('[data-player-name]').textContent,'Down');
 const row=[...container.querySelectorAll('.pp-player')].find(r=>r.textContent.includes('Reference'));
 assert.match(row.querySelector('.pp-card').textContent,/#134/);
});
test('both declared comparison windows are displayed in Shanghai calendar dates',()=>{
 const {marketWindowDescription}=require('../../../packages/card-core/src/player-prices.ts');
 assert.equal(typeof marketWindowDescription,'function');
 const p=player('Window',null,'20');
 assert.equal(marketWindowDescription(p),'近30日 2026/09/22—2026/10/21 · 此前30日 2026/08/23—2026/09/21');
 p.market.current_window={start:'2026-09-21T16:00:00Z',end:'2026-10-21T16:00:00Z'};
 assert.equal(marketWindowDescription(p),'近30日 2026/09/22—2026/10/21 · 此前30日 2026/08/23—2026/09/21');
});
test('default activity ranking excludes an older market and shows its actual report date',()=>{
 const fresh=player('Fresh',null,null);fresh.market.sale_count=0;
 const stale=player('Stale','99','20');stale.market.report_date='2026-10-20';stale.market.sale_count=999;
 const legacy={...player('Legacy','999',null),market:undefined,cards:[{current_count:9999}]};
 assert.deepEqual(sortPlayers([stale,legacy,fresh],'activity','2026-10-21').map(p=>p.player_name),['Fresh','Legacy','Stale']);
 const {container}=render(React.createElement(PlayerPriceBoard,{data:board([stale,fresh])}));
 assert.equal(container.querySelector('[data-player-name]').textContent,'Fresh');
 const row=[...container.querySelectorAll('.pp-player > summary')].find(r=>r.textContent.includes('Stale'));
 assert.match(row.textContent,/旧行情 2026-10-20/);
 assert.doesNotMatch(row.textContent,/20.00%|99.00%/);
});
test('reference identity preserves a printed two-year season and its manufacturer',()=>{
 const {priceIdentity}=require('../../../packages/card-core/src/player-prices.ts');
 assert.match(priceIdentity({season_start:2023,season_end:2024,brand_name:'Panini',product_name:'Prizm',card_number:'136',parallel_name:'Base'}),/2023-24 · Panini · Prizm · #136/);
 assert.match(priceIdentity({season_start:2025,season_end:2025,brand_name:'Topps',product_name:'Chrome'}),/^2025 · Topps · Chrome/);
});
