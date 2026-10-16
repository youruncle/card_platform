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
