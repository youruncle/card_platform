const {test}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require(process.env.COST_TEST_DEPENDENCIES ? process.env.COST_TEST_DEPENDENCIES+'/jsdom' : 'jsdom');
const {readFileSync}=require('node:fs');
const path=require('node:path');
const source=readFileSync(path.join(__dirname,'../cost-capture.js'),'utf8');
const fixture=`<table><tr><td>2025-09-01 订单号：900001</td><td><a href="/market/seller/9">Fixture Shop</a></td></tr>
<tr><td><a href="/market/item/900002"><img src="https://images.cardhobby.com/a.jpg?resize=100"></a><a href="/market/item/900002">2025 Topps Test Player</a><span>商品编号: 900002</span></td><td>￥10.00 x2</td><td rowspan="2">总额 ￥30.10 含运费：￥0.10</td><td rowspan="2">已完成<a href="/Order/detail/900001?nav=private">订单详情</a></td></tr>
<tr><td><a href="/market/item/900003">Test Player Second Card</a><span>商品编号: 900003</span></td><td>￥10.00 x1</td></tr></table>`;
function setup(html=fixture) {const dom=new JSDOM(html,{url:'https://cardhobby.com.cn/Order/MyOrders',runScripts:'outside-only'});dom.window.eval(source);return dom.window;}

test('real observed table grouping: order header, rowspan fee, duplicate image/text links, quantity',()=>{
 const win=setup();const orders=win.CardHobbyCostCapture.parseOrders(win.document,win.location.href,'2025-09-02T00:00:00Z');
 assert.equal(orders.length,1);const order=orders[0];assert.equal(order.lines.length,2);
 assert.equal(order.external_order_id,'900001');assert.equal(order.shipping,'0.10');assert.equal(order.lines[0].quantity,2);
 assert.equal(order.lines[0].unit_price,'10.00');assert.equal(order.lines[0].image_urls[0],'https://images.cardhobby.com/a.jpg');
 assert.equal(order.seller,'Fixture Shop');assert.equal(order.source_url,'https://cardhobby.com.cn/Order/detail/900001');assert.equal(order.complete,true);
 assert.equal(order.status,'已完成');assert.equal(order.displayed_total,'30.10');
});

test('missing shipping is never default zero; malformed row marks incomplete',()=>{
 const win=setup(fixture.replace('含运费：￥0.10','').replace('￥10.00 x1','价格未知'));
 const order=win.CardHobbyCostCapture.parseOrders(win.document,win.location.href)[0];
 assert.equal(order.shipping,null);assert.equal(order.complete,false);assert.ok(order.parser_errors.length);
});

test('detail extracts paid and adjustments without contact information',()=>{
 const win=setup('<div>收件地址：PRIVATE ADDRESS</div><p>手机：PRIVATE PHONE</p><ul><li><span>实付总额：</span><b>￥30.10</b></li><li>优惠金额：￥2.00</li></ul>');
 const result=win.CardHobbyCostCapture.detailAmounts(win.document);
 assert.equal(result.paid_total,'30.10');assert.equal(result.discount,'2.00');assert.equal(result.refund,null);
 assert.ok(!JSON.stringify(result).includes('PRIVATE'));
});

test('pagination follows observed next link, stops and reports limit',async()=>{
 const win=setup(fixture+'<a href="/Order/MyOrders?page=2">»</a>');
 const requests=[];
 win.fetch=async(url,options)=>{requests.push([url,options.credentials]);return {ok:true,text:async()=>String(url).includes('/detail/')?'<p>实付总额：￥30.10</p>':fixture.replaceAll('900001','900011')};};
 let capture=await win.CardHobbyCostCapture.exportEvidence(1);
 assert.equal(capture.pages,1);assert.equal(capture.truncated,true);assert.match(capture.next_url,/page=2/);
 capture=await win.CardHobbyCostCapture.exportEvidence(3);
 assert.equal(capture.pages,2);assert.equal(capture.orders.length,2);assert.equal(capture.truncated,false);
 assert.ok(requests.every(([,credentials])=>credentials==='same-origin'));
});

test('login expiry, cancellation and detail failure fail safely',async()=>{
 const win=setup(fixture+'<a href="/Order/MyOrders?page=2">»</a>');
 win.fetch=async()=>({ok:true,text:async()=>'<form>请登录</form>'});
 await assert.rejects(()=>win.CardHobbyCostCapture.exportEvidence(2),/未读到订单/);
 await assert.rejects(()=>win.CardHobbyCostCapture.exportEvidence(1,{aborted:true}),/取消/);
 const capture=await win.CardHobbyCostCapture.exportEvidence(1);
 assert.ok(capture.orders[0].parser_errors.includes('详情实付金额未核实'));
});

test('untrusted host cannot be fetched as a pagination link',async()=>{
 const win=setup(fixture+'<a href="https://evil.example/Order/MyOrders?page=2">»</a>');
 win.fetch=async()=>({ok:true,text:async()=>'<p>实付总额：￥30.10</p>'});
 await assert.rejects(()=>win.CardHobbyCostCapture.exportEvidence(2),/分页链接异常/);
});
