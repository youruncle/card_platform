const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const React=require('react');
const {render,cleanup,fireEvent}=require('@testing-library/react');
const {PlayerPriceBoard}=require('../components/player-price-board.tsx');
const {demoBoard,sortPlayers}=require('../lib/player-prices.ts');
afterEach(cleanup);
const empty={report_date:'2026-10-10',timezone:'Asia/Shanghai',window_days:7,min_sales:3,currency:'CNY',players:[]};

test('real empty dataset is not silently replaced with demo',()=>{
 const {container}=render(React.createElement(PlayerPriceBoard,{data:empty}));
 assert.match(container.textContent,/暂无球员统计/);
 assert.doesNotMatch(container.textContent,/Stephen Curry/);
});
test('explicit demo shows label, sort controls and representative detail',()=>{
 const {container,getByText}=render(React.createElement(PlayerPriceBoard,{data:empty}));
 fireEvent.click(getByText('查看演示数据'));
 assert.match(container.textContent,/演示数据/);
 assert.match(container.textContent,/不是真实成交统计/);
 assert.match(container.textContent,/前日7天中位价/);
 assert.match(container.textContent,/PSA 10/);
 fireEvent.click(getByText('跌幅优先'));
 const names=[...container.querySelectorAll('[data-player-name]')].map(x=>x.textContent);
 assert.equal(names[0],'Luka Dončić');
 fireEvent.change(container.querySelector('input[type="search"]'),{target:{value:'Curry'}});
 assert.equal(container.querySelectorAll('[data-player-name]').length,1);
 fireEvent.click(getByText('返回真实统计'));
 assert.match(container.textContent,/暂无球员统计/);
});
test('zero change ranks ahead of insufficient without turning null into zero',()=>{
 const players=demoBoard('2026-10-10').players;
 const sorted=sortPlayers(players,'gainers');
 assert.equal(sorted.at(-1).change_pct,null);
 assert.equal(sorted.find(p=>p.change_pct==='0.0000').player_name,'LeBron James');
});
test('source failure and no result are visible and refresh is disabled in demo',()=>{
 const data=demoBoard('2026-10-10');data.players[0].refresh.error='卡淘采集失败';
 const {container,getByText}=render(React.createElement(PlayerPriceBoard,{data,onRefresh:()=>{}}));
 assert.match(container.textContent,/卡淘采集失败/);
 fireEvent.click(getByText('查看演示数据'));
 assert.equal(container.querySelector('button[data-refresh]').disabled,true);
});
test('stale snapshots and source errors are visible before expanding a row',()=>{
 const data=demoBoard('2026-10-10');
 data.players=[{...data.players[0],status:'stale',report_date:'2026-10-09',change_pct:null,last_change_pct:'8.5000',refresh:{queued:false,error:'卡淘采集失败'}}];
 const {container}=render(React.createElement(PlayerPriceBoard,{data}));
 const summary=container.querySelector('.pp-player > summary');
 assert.match(summary.textContent,/待更新/);
 assert.match(summary.textContent,/2026-10-09/);
 assert.match(summary.textContent,/采集异常/);
});

test('single-card market survives a missing index and refresh targets just that player',()=>{
 const data=demoBoard('2026-10-10');
 const p={...data.players[0],change_pct:null,status:'insufficient',cards:[],basket_frozen:false,
   market:{report_date:'2026-10-10',window_days:30,min_sales:3,sale_count:8,card_count:1,trend_count:1,
     latest_sale_at:'2026-10-10T12:00:00+08:00',cards_truncated:false,
     cards:[{...data.players[0].cards[0],current_count:4,previous_count:4,change_pct:'20.0000',current_price:'120',previous_price:'100'}]}};
 data.players=[p];
 let refreshed;
 const {container,getByRole}=render(React.createElement(PlayerPriceBoard,{data,onRefresh:()=>{},onRefreshPlayer:id=>{refreshed=id;}}));
 assert.match(container.textContent,/有确认成交/);
 assert.match(container.textContent,/近30日中位价/);
 assert.match(container.textContent,/20.00%/);
 assert.match(container.textContent,/1 种可比卡款/);
 assert.match(container.textContent,/综合指数暂缺/);
 fireEvent.click(getByRole('button',{name:'更新 Stephen Curry 的成交'}));
 assert.equal(refreshed,p.player_id);
});

test('sampling error leaves existing market visible and explains rejected identities',()=>{
 const data=demoBoard('2026-10-10');
 data.players=[{...data.players[0],change_pct:null,status:'pending',cards:[],
   coverage:{sample_count:12,identified_count:0,rejected_count:12,rejection_reasons:{season_unclear:5,identity_unresolved:7}},
   refresh:{queued:false,error:'卡淘采集失败'}}];
 const {container}=render(React.createElement(PlayerPriceBoard,{data}));
 assert.match(container.textContent,/年份不明确.*5/);
 assert.match(container.textContent,/卡款身份未确认.*7/);
 assert.match(container.querySelector('.pp-player > summary').textContent,/采集异常/);
});
