const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const React=require('react');
const {render,cleanup,act}=require('@testing-library/react');
const {OfficialChecklistProgressPanel}=require('../components/official-checklist-progress.tsx');
afterEach(cleanup);
test('shows engineering progress without inventing full directory coverage',async()=>{
 global.fetch=async()=>({ok:true,status:200,json:async()=>({engineering_percent:75,known_products_percent:77.5,
 summary:{total:40,counts:{published:31,blocked:8,excluded:1},discovery_complete:false,success_percent:null,date_unknown:32},
 publishers:{'Upper Deck':{total:8,eligible_total:7,counts:{published:4,failed:3},known_success_percent:57.1,date_unknown:5}},source_blockers:['Panini 403'],products:[{key:'a',title:'Official Product',status:'published',row_count:100,issue_count:2}]})});
 let view;await act(async()=>{view=render(React.createElement(OfficialChecklistProgressPanel));});
 assert.match(view.container.textContent,/工程完成 75%/);
 assert.match(view.container.textContent,/全量覆盖率暂不可计算/);
 assert.match(view.container.textContent,/Panini 403/);
 assert.match(view.container.textContent,/Upper Deck/);
 assert.match(view.container.textContent,/57.1%/);
 assert.match(view.container.textContent,/2 条解析异常/);
 assert.equal(view.container.querySelector('progress').value,75);
});
