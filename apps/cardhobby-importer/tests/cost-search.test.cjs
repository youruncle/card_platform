const {test} = require('node:test');
const assert = require('node:assert/strict');
const {JSDOM} = require(process.env.COST_TEST_DEPENDENCIES ? process.env.COST_TEST_DEPENDENCIES+'/jsdom' : 'jsdom');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const form = '<input placeholder="商品编号/商品名称">';
const order = id => `<table><tr><td>2025-09-01 订单号：${id}</td><td><a href="/market/seller/9">Fixture Shop</a></td></tr><tr><td><a href="/market/item/900002">2025 Topps Test Player</a>商品编号: 900002</td><td>￥10.00 x2</td><td>总额 ￥21.00 含运费：￥1.00</td><td>已完成<a href="/Order/detail/${id}">订单详情</a></td></tr></table>`;
function setup() {
 const dom = new JSDOM(form,{url:'https://cardhobby.com.cn/Order/MyOrders',runScripts:'outside-only'});
 for (const name of ['cost-capture.js','cost-search.js']) dom.window.eval(readFileSync(path.join(__dirname,'../'+name),'utf8'));
 return dom.window;
}
const response = (html,url) => ({ok:true,status:200,url,text:async()=>html});
test('uses observed searchTxt GET interface, full history and signed-in same-origin credentials', async()=>{
 const win=setup(), calls=[];
 win.fetch=async(url,options)=>{calls.push([url,options.credentials]);return response(url.includes('/detail/')?'<p>实付总额：￥21.00</p>':form+order('900001'),url);};
 const result=await win.CardHobbyCostSearch.search(['Test Player']);
 assert.equal(result.complete,true);assert.equal(result.orders.length,1);
 const url=new URL(calls[0][0]);assert.equal(url.pathname,'/Order/MyOrders');assert.equal(url.searchParams.get('searchTxt'),'Test Player');
 assert.equal(url.searchParams.get('beginDate'),'2000-01-01 00:00:00');assert.equal(url.searchParams.get('status'),'0');
 assert.equal(result.orders[0].paid_total,'21.00');assert.equal(result.orders[0].lines[0].quantity,2);
 assert.ok(calls.every(([,credentials])=>credentials==='same-origin'));
});
test('follows keyword-preserving next pages and deduplicates orders across aliases',async()=>{
 const win=setup();let details=0;
 win.fetch=async url=>{
  if(url.includes('/detail/')) {details++;return response('<p>实付总额：￥21.00</p>',url);}
  const parsed=new URL(url);const page=parsed.searchParams.get('page');
  parsed.searchParams.set('page','2');
  return response(form+order(page?'900011':'900001')+(page?'':`<a href="${parsed.href.replaceAll('&','&amp;')}">»</a>`),url);
 };
 const result=await win.CardHobbyCostSearch.search(['Test Player','测试球员']);
 assert.equal(result.orders.length,2);assert.equal(result.pages,4);assert.equal(details,2);assert.equal(result.complete,true);
});
test('recognized empty search is successful; login or malformed result must not become no-match',async()=>{
 const win=setup();win.fetch=async url=>response(form+'<p>暂无订单</p>',url);
 assert.equal((await win.CardHobbyCostSearch.search(['Nobody'])).orders.length,0);
 win.fetch=async()=>response('<input type="password">','https://cardhobby.com.cn/login');
 await assert.rejects(()=>win.CardHobbyCostSearch.search(['Nobody']),/登录/);
 win.fetch=async url=>response(form+order('900001').replace('￥10.00 x2','未加载'),url);
 await assert.rejects(()=>win.CardHobbyCostSearch.search(['Nobody']),/未完整读取/);
});
test('truncation disables automatic resolution and foreign/keyword-losing pagination is rejected',async()=>{
 const win=setup();
 win.fetch=async url=>response(url.includes('/detail/')?'<p>实付总额：￥21.00</p>':form+order('900001')+'<a href="/Order/MyOrders?searchTxt=Test&page=2">»</a>',url);
 assert.equal((await win.CardHobbyCostSearch.search(['Test'],{maxPages:1})).complete,false);
 win.fetch=async url=>response(form+'<a href="https://evil.example/Order/MyOrders?searchTxt=Test">»</a>',url);
 await assert.rejects(()=>win.CardHobbyCostSearch.search(['Test']),/异常订单链接/);
 win.fetch=async url=>response(form+'<a href="/Order/MyOrders?page=2">»</a>',url);
 await assert.rejects(()=>win.CardHobbyCostSearch.search(['Test']),/丢失关键字/);
});
test('search rejects cancellation and unavailable detail amounts remain explicitly unusable',async()=>{
 const win=setup();let count=0;
 win.fetch=async url=>{count++;return response(url.includes('/detail/')?'<p>等待付款</p>':form+order('900001'),url);};
 const controller=new win.AbortController();controller.abort();
 await assert.rejects(()=>win.CardHobbyCostSearch.search(['Test'],{signal:controller.signal}));assert.equal(count,0);
 const result=await win.CardHobbyCostSearch.search(['Test']);
 assert.ok(result.orders[0].parser_errors.includes('详情实付金额未核实'));
});
