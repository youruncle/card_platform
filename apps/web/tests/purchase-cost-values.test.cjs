const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const React=require('react');
const {render,screen,cleanup}=require('@testing-library/react');
const {PurchaseCostValues}=require('../components/purchase-cost-values.tsx');
afterEach(cleanup);
test('unknown purchase cost renders no cost amount in list or detail',()=>{
 for(const detail of [false,true]) {
  const view=render(React.createElement(PurchaseCostValues,{price:null,total:null,detail}));
  assert.equal(view.container.textContent,'');view.unmount();
 }
});
test('real order amounts appear and genuine zero remains visible',()=>{
 const view=render(React.createElement(PurchaseCostValues,{price:'6.00',total:'6.69'}));
 assert.ok(screen.getByText('¥6.00'));assert.ok(screen.getByText('购入总成本 ¥6.69'));
 view.rerender(React.createElement(PurchaseCostValues,{price:'0',total:'0.00'}));
 assert.ok(screen.getByText('¥0.00'));assert.ok(screen.getByText('购入总成本 ¥0.00'));
});
test('missing total is hidden without replacing it with card price or zero',()=>{
 const view=render(React.createElement(PurchaseCostValues,{price:'6.00',total:null}));
 assert.equal(view.container.textContent,'¥6.00');
 view.rerender(React.createElement(PurchaseCostValues,{price:'6.00',total:null,detail:true}));
 assert.equal(view.container.textContent,'');
});
test('detail renders known cost in the original currency',()=>{
 render(React.createElement(PurchaseCostValues,{total:'1234.50',currency:'USD',detail:true}));
 assert.ok(screen.getByText('购入总成本：USD 1,234.50'));
});
