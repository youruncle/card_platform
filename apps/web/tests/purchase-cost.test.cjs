const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const deps=process.env.COST_TEST_DEPENDENCIES;
const dependency=name=>require(deps ? deps+'/'+name : name);
const {JSDOM}=dependency('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost:3000'});
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;
const React=require('react');
const {render,screen,fireEvent,waitFor,cleanup}=dependency('@testing-library/react');
const {PurchaseCostPanel,PurchaseCostAudit,OrderEvidenceSnapshot,PurchaseCostEvidenceModal}=require('../components/purchase-cost-panel.tsx');
require('./cost-legacy-fixture.cjs');
afterEach(cleanup);
const candidate={unit_id:'u1',evidence_hash:'a'.repeat(64),order_id:'900001',ordered_at:'2025-09-01',seller:'Fixture Shop',status:'已完成',item_id:'900002',unit_index:0,title:'Fixture Test Card',image_urls:[],source_url:'https://cardhobby.com.cn/Order/detail/900001',card_price:'10.00',shipping:'0.04',total_cost:'10.04',currency:'CNY',confidence:'0.95',reasons:['年份一致','球员一致'],missing:[],automatic:true};
const response=data=>({ok:true,status:200,json:async()=>data});
test('inferred order snapshot exposes image title price source and uncertainty',()=>{
 const evidence={...candidate,match_method:'inferred',image_urls:['https://images.cardhobby.com/fixture.jpg'],item_url:'https://cardhobby.com.cn/market/item/900002',inference_warnings:['卖家单年与库存跨年赛季写法不同']};
 render(React.createElement(OrderEvidenceSnapshot,{candidate:evidence}));
 assert.ok(screen.getByText('Fixture Test Card'));
 assert.ok(screen.getByText(/推测匹配（请核对）/));
 assert.ok(screen.getByText(/卡价 ¥10.00.*分摊运费 ¥0.04.*总成本 ¥10.04/));
 assert.equal(screen.getByAltText('订单商品快照').getAttribute('src'),evidence.image_urls[0]);
 assert.equal(screen.getByRole('link',{name:'查看卡淘商品'}).getAttribute('href'),evidence.item_url);
 assert.ok(screen.getByRole('note').textContent.includes('跨年赛季'));
});
test('persisted inference snapshot is readable after reopening independently of transient lookup',async()=>{
 global.fetch=async()=>response({evidence:{...candidate,match_method:'inferred'},card_price:'10.00',shipping:'0.04',confirmation:'automatic'});
 render(React.createElement(PurchaseCostEvidenceModal,{itemId:'card1',onClose(){}}));
 await screen.findByText('Fixture Test Card');
 assert.ok(screen.getByRole('dialog',{name:'购入成本订单快照'}));
 assert.ok(screen.getByText(/卡淘购入成本来源 · 推测匹配/));
});
test('manual cost without an order displays an explicit empty snapshot state',async()=>{
 global.fetch=async()=>response(null);
 render(React.createElement(PurchaseCostEvidenceModal,{itemId:'card1',onClose(){}}));
 await screen.findByText(/暂无卡淘订单快照/);
});
function bridge(search = () => ({orders:[],complete:true}),ping = () => ({version:'0.3.0'})) {
 const calls=[];
 window.postMessage=(message)=>{
  calls.push(message);
  if (message.type==='CARD_PLATFORM_COST_CANCEL') return;
  Promise.resolve(message.type==='CARD_PLATFORM_COST_PING'?ping(message):Promise.resolve(search(message)).then(result=>({result}))).then(result=>{
   window.dispatchEvent(new window.MessageEvent('message',{origin:window.location.origin,source:window,data:{type:'CARD_PLATFORM_COST_RESPONSE',requestId:message.requestId,...result}}));
  });
 };
 return calls;
}
function single(result,searchPlan = () => ({item_id:'card1',label:'Test Player 2025 Hoops',keywords:['Test Player'],status:'ready'})) {
 const calls=[];
 global.fetch=async(url,options)=>{
  calls.push([url,options]);
  return response(String(url).endsWith('/search-plan')?searchPlan():String(url).endsWith('/confirm')?{status:'recorded'}:result);
 };
 return calls;
}
test('opening the cost action searches live orders and fills a unique match without upload or confirmation',async()=>{
 const searches=bridge(()=>({orders:[{fixture:'order'}],complete:true}));
 const calls=single({status:'recorded',recorded:candidate,candidates:[candidate],rejected:[],reason:''});let recorded=0;
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',onClose(){},onRecorded(){recorded++;}}));
 await screen.findByText(/已自动填入购入成本 ¥10.04/);
 assert.equal(recorded,1);assert.equal(document.querySelector('input[type=file]'),null);
 assert.equal(screen.queryByRole('textbox'),null);
 assert.deepEqual(searches.find(row=>row.type==='CARD_PLATFORM_COST_SEARCH').keywords,['Test Player']);
 assert.deepEqual(JSON.parse(calls.at(-1)[1].body),{orders:[{fixture:'order'}],complete:true,refresh:true});
 assert.equal(screen.queryByRole('button',{name:'确认所选商品并录入成本'}),null);
});
test('multiple purchases of the same card require only order selection and bind confirmation to the evidence version',async()=>{
 bridge();const calls=single({status:'needs_confirmation',candidates:[candidate,{...candidate,unit_id:'u2',order_id:'900003',seller:'Second Fixture Shop'}],rejected:[],reason:'发现相同卡片的多笔购买记录，请选择对应订单'});
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',onClose(){},onRecorded(){}}));
 await screen.findByText(/Second Fixture Shop/);
 assert.equal(screen.queryByRole('textbox'),null);
 const confirm=screen.getByRole('button',{name:'确认所选商品并录入成本'});
 assert.equal(confirm.disabled,true);assert.equal(screen.getAllByRole('radio').length,2);
 fireEvent.click(screen.getAllByRole('radio')[0]);fireEvent.click(confirm);
 await screen.findByText('购入成本已录入。');
 assert.deepEqual(JSON.parse(calls.at(-1)[1].body),{unit_id:'u1',evidence_hash:'a'.repeat(64),refresh:true});
});
const duplicateResult={status:'needs_confirmation',candidates:[candidate,{...candidate,unit_id:'u2',order_id:'900003',seller:'Second Fixture Shop'}],rejected:[],reason:'发现相同卡片的多笔购买记录，请选择对应订单'};
test('expired CardHobby login offers login, not extension installation, and retry recovers',async()=>{
 window.postMessage=message=>{
  if(message.type==='CARD_PLATFORM_COST_CANCEL')return;
  queueMicrotask(()=>window.dispatchEvent(new window.MessageEvent('message',{origin:window.location.origin,source:window,
   data:{type:'CARD_PLATFORM_COST_RESPONSE',requestId:message.requestId,...(message.type==='CARD_PLATFORM_COST_PING'?{version:'0.3.0'}:{error:'请先在此 Chrome 登录卡淘，再重试获取成本'})}})));
 };
 const calls=single({status:'not_found',candidates:[],rejected:[],reason:'搜索完成，没有订单'});
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',onClose(){},onRecorded(){assert.fail('must not record');}}));
 await screen.findByRole('heading',{name:'登录并绑定卡淘账号'});
 assert.equal(screen.queryByText('首次连接卡淘'),null);assert.equal(calls.length,1);
 bridge();
 fireEvent.change(screen.getByLabelText('卡淘用户名 / 手机号'),{target:{value:'fixture'}});
 fireEvent.change(screen.getByLabelText('卡淘密码'),{target:{value:'fixture-password'}});
 fireEvent.click(screen.getByRole('button',{name:'登录、保存并继续获取成本'}));
 await screen.findByText('搜索完成，没有订单');
 assert.equal(screen.queryByRole('link',{name:'重新登录卡淘'}),null);
});
test('a supplied duplicate result opens directly without another search and confirms once before closing',async()=>{
 const searches=bridge();const calls=single({});const callbacks=[];
 const view=render(React.createElement(PurchaseCostPanel,{itemId:'card1',initialResult:duplicateResult,initialLabel:'Background card',onClose(){callbacks.push('close');view.unmount();},onRecorded(){callbacks.push('recorded');}}));
 await screen.findByText(/Second Fixture Shop/);
 assert.ok(screen.getByText('Background card'));
 assert.equal(calls.length,0);assert.equal(searches.length,0);
 fireEvent.click(screen.getAllByRole('radio')[0]);
 const confirm=screen.getByRole('button',{name:'确认所选商品并录入成本'});
 fireEvent.click(confirm);fireEvent.click(confirm);
 await waitFor(()=>assert.deepEqual(callbacks,['recorded','close']));
 assert.equal(calls.length,1);
 assert.ok(String(calls[0][0]).endsWith('/card1/confirm'));
 assert.deepEqual(JSON.parse(calls[0][1].body),{unit_id:'u1',evidence_hash:'a'.repeat(64),refresh:true});
 assert.equal(screen.queryByRole('dialog'),null);
});
test('a supplied connection state explains setup without searching and retry delegates then closes',async()=>{
 const searches=bridge();const calls=single({});const callbacks=[];
 const view=render(React.createElement(PurchaseCostPanel,{itemId:'card1',initialNeedsConnection:true,initialLabel:'Disconnected card',onRetry(id){callbacks.push(['retry',id]);},onClose(){callbacks.push(['close']);view.unmount();},onRecorded(){}}));
 await screen.findByText('首次连接卡淘');
 assert.ok(screen.getByText('Disconnected card'));
 assert.equal(calls.length,0);assert.equal(searches.length,0);
 fireEvent.click(screen.getByRole('button',{name:'重新自动获取成本'}));
 assert.deepEqual(callbacks,[['retry','card1'],['close']]);
 assert.equal(screen.queryByRole('dialog'),null);
 assert.equal(calls.length,0);assert.equal(searches.length,0);
});
test('retry from a supplied duplicate result delegates instead of re-running the modal search',async()=>{
 const searches=bridge();const calls=single({});const callbacks=[];
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',initialResult:duplicateResult,onRetry(id){callbacks.push(id);},onClose(){callbacks.push('closed');},onRecorded(){}}));
 await screen.findByText(/Second Fixture Shop/);
 fireEvent.click(screen.getByRole('button',{name:'重新自动获取成本'}));
 assert.deepEqual(callbacks,['card1','closed']);
 assert.equal(calls.length,0);assert.equal(searches.length,0);
});
test('invalid candidates cannot turn a supplied result into a duplicate purchase choice',async()=>{
 const searches=bridge();const calls=single({});
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',initialResult:{...duplicateResult,candidates:[candidate,{...candidate,unit_id:'weak',automatic:false}]},onClose(){},onRecorded(){}}));
 await screen.findByText(duplicateResult.reason);
 assert.equal(screen.queryByRole('radio'),null);
 assert.equal(screen.queryByRole('button',{name:'确认所选商品并录入成本'}),null);
 assert.equal(calls.length,0);assert.equal(searches.length,0);
});
test('switching cards and supplied state drops the previous choices without searching',async()=>{
 const searches=bridge();const calls=single({});const props={onClose(){},onRecorded(){}};
 const view=render(React.createElement(PurchaseCostPanel,{...props,itemId:'card1',initialResult:duplicateResult,initialLabel:'First card'}));
 fireEvent.click((await screen.findAllByRole('radio'))[0]);
 view.rerender(React.createElement(PurchaseCostPanel,{...props,itemId:'card2',initialNeedsConnection:true,initialLabel:'Second card'}));
 await screen.findByText('首次连接卡淘');
 assert.ok(screen.getByText('Second card'));
 assert.equal(screen.queryByText('First card'),null);assert.equal(screen.queryByRole('radio'),null);
 const updated={...duplicateResult,reason:'新订单结果'};
 view.rerender(React.createElement(PurchaseCostPanel,{...props,itemId:'card2',initialResult:updated}));
 await screen.findByText('新订单结果');
 assert.equal(screen.queryByText('首次连接卡淘'),null);
 assert.ok(screen.getAllByRole('radio').every(input=>!input.checked));
 assert.equal(calls.length,0);assert.equal(searches.length,0);
});
test('an old search cannot overwrite a supplied result when the same dialog switches cards',async()=>{
 const searches=bridge();let finishPlan;const calls=[];
 global.fetch=async(url)=>{calls.push(url);return new Promise(resolve=>{finishPlan=()=>resolve(response({status:'ready',label:'Old card',keywords:['Old player']}));});};
 const props={onClose(){},onRecorded(){}};
 const view=render(React.createElement(PurchaseCostPanel,{...props,itemId:'card1'}));
 await waitFor(()=>assert.equal(typeof finishPlan,'function'));
 view.rerender(React.createElement(PurchaseCostPanel,{...props,itemId:'card2',initialResult:duplicateResult,initialLabel:'New card'}));
 finishPlan();
 await screen.findByText(/Second Fixture Shop/);
 assert.ok(screen.getByText('New card'));assert.equal(screen.queryByText('Old card'),null);
 assert.equal(calls.length,1);assert.equal(searches.length,0);
});
test('a late confirmation refreshes its original card without closing or changing the new dialog',async()=>{
 bridge();let finishConfirm;const callbacks=[];
 global.fetch=async()=>new Promise(resolve=>{finishConfirm=()=>resolve(response({status:'recorded'}));});
 const view=render(React.createElement(PurchaseCostPanel,{itemId:'card1',initialResult:duplicateResult,onClose(){callbacks.push('old-close');},onRecorded(){callbacks.push('old-recorded');}}));
 fireEvent.click((await screen.findAllByRole('radio'))[0]);
 fireEvent.click(screen.getByRole('button',{name:'确认所选商品并录入成本'}));
 await waitFor(()=>assert.equal(typeof finishConfirm,'function'));
 view.rerender(React.createElement(PurchaseCostPanel,{itemId:'card2',initialResult:{...duplicateResult,reason:'Second card duplicates'},onClose(){callbacks.push('new-close');},onRecorded(){callbacks.push('new-recorded');}}));
 finishConfirm();
 await waitFor(()=>assert.deepEqual(callbacks,['old-recorded']));
 assert.ok(screen.getByText('Second card duplicates'));
 assert.equal(screen.getAllByRole('radio').length,2);
 assert.equal(screen.queryByText('购入成本已录入。'),null);
});
test('retry extracts fresh keywords from the card again and searches without user input',async()=>{
 let plans=0;
 const searches=bridge();const calls=single({status:'not_found',candidates:[],rejected:[],reason:'未找到购买记录'},()=>({item_id:'card1',label:'Test Player 2025 Hoops',keywords:++plans===1?['Test Player']:['Test Player 2025','Test Player Hoops'],status:'ready'}));
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',onClose(){},onRecorded(){}}));
 await screen.findByText('未找到购买记录');
 assert.equal(screen.queryByRole('textbox'),null);
 fireEvent.click(screen.getByRole('button',{name:'重新自动获取成本'}));
 await waitFor(()=>assert.equal(searches.filter(row=>row.type==='CARD_PLATFORM_COST_SEARCH').length,2));
 assert.deepEqual(searches.filter(row=>row.type==='CARD_PLATFORM_COST_SEARCH').at(-1).keywords,['Test Player 2025','Test Player Hoops']);
 await screen.findByText('未找到购买记录');
 assert.equal(calls.filter(([url])=>String(url).endsWith('/search-plan')).length,2);
 assert.equal(screen.queryByRole('radio'),null);
});
for (const status of ['not_found','incomplete','unmatched']) test(`${status} is a system result and never asks the user to choose an unverified card`,async()=>{
 bridge();single({status,candidates:[{...candidate,automatic:false,missing:['卡号']}],rejected:[],reason:`系统处理结果：${status}`});let recorded=0;
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',onClose(){},onRecorded(){recorded++;}}));
 await screen.findByText(`系统处理结果：${status}`);
 assert.equal(screen.queryByRole('textbox'),null);
 assert.equal(screen.queryByRole('radio'),null);
 assert.equal(screen.queryByRole('button',{name:'确认所选商品并录入成本'}),null);
 assert.ok(screen.getByRole('button',{name:'重新自动获取成本'}));
 assert.equal(recorded,0);
});
test('a lone candidate cannot enable the duplicate-order confirmation flow',async()=>{
 bridge();single({status:'needs_confirmation',candidates:[candidate],rejected:[],reason:'匹配信息不足'});
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',onClose(){},onRecorded(){}}));
 await screen.findByText('匹配信息不足');
 assert.equal(screen.queryByRole('radio'),null);
 assert.equal(screen.queryByRole('button',{name:'确认所选商品并录入成本'}),null);
});
test('existing cost skips network search and retains the recorded cost',async()=>{
 const searches=bridge();global.fetch=async()=>response({status:'skipped',label:'Fixture',keywords:[],reason:'此卡已有购入成本或交易，已保留原记录'});
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',onClose(){},onRecorded(){}}));
 await screen.findByText('此卡已有购入成本或交易，已保留原记录');
 assert.equal(searches.length,0);
 assert.equal(screen.queryByRole('textbox'),null);
 assert.equal(screen.queryByRole('button',{name:'重新自动获取成本'}),null);
});
test('missing extension explains the one-time connection without a file upload flow',async()=>{
 window.postMessage=()=>{};single({});
 render(React.createElement(PurchaseCostPanel,{itemId:'card1',onClose(){},onRecorded(){}}));
 await screen.findByText('首次连接卡淘',{}, {timeout:3000});
 assert.ok(screen.getByRole('link',{name:'打开卡淘订单页'}));
 assert.equal(document.querySelector('input[type=file]'),null);
});
test('live batch searches every card automatically and only duplicate purchases offer an order choice',async()=>{
 const searches=bridge();const calls=[];const job={id:'j1',target:1,scan_limit:100,status:'queued',source_mode:'browser_search',item_ids:['card1','card2','card3'],succeeded:0,scanned:0,results:[]};
 const results=[{item_id:'card1',status:'unmatched',reason:'未能确定匹配'},{item_id:'card2',status:'needs_confirmation',reason:'发现相同卡片的两笔购买记录'},{item_id:'card3',status:'recorded',reason:'已自动录入'}];let steps=0;
 global.fetch=async(url,options)=>{
  calls.push([url,options]);
  if (String(url).endsWith('/search-plan')) return response({status:'ready',label:'Fixture',keywords:[`Test Player ${steps+1}`]});
  if (String(url).endsWith('/search-step')) {steps++;return response({...job,status:steps===3?'completed':'running',succeeded:steps===3?1:0,scanned:steps,results:results.slice(0,steps)});}
  return response(options?.method==='POST'?job:[]);
 };
 render(React.createElement(PurchaseCostPanel,{onClose(){},onRecorded(){}}));
 await waitFor(()=>assert.equal(calls.length,1));
 fireEvent.change(screen.getByLabelText('最大成功录入个数 X'),{target:{value:'1'}});
 fireEvent.click(screen.getByRole('button',{name:'开始批量获取成本'}));
 await screen.findByText('成功 1 / 1 · 已扫描 3 / 100');
 assert.equal(JSON.parse(calls.find(([url,options])=>String(url).endsWith('/jobs')&&options?.method==='POST')[1].body).source_mode,'browser_search');
 assert.deepEqual(calls.filter(([url])=>String(url).endsWith('/search-step')).map(([,options])=>JSON.parse(options.body).item_id),['card1','card2','card3']);
 assert.deepEqual(searches.filter(row=>row.type==='CARD_PLATFORM_COST_SEARCH').map(row=>row.keywords),[['Test Player 1'],['Test Player 2'],['Test Player 3']]);
 assert.equal(screen.queryByRole('textbox'),null);
 assert.equal(screen.getAllByRole('button',{name:'选择对应订单'}).length,1);
 assert.equal(screen.queryByRole('button',{name:'确认所选商品并录入成本'}),null);
});
test('cancelling a pending automatic batch does not turn cancellation into a search failure',async()=>{
 const searches=bridge(()=>new Promise(()=>{}));const calls=[];const job={id:'j1',target:1,scan_limit:100,status:'queued',source_mode:'browser_search',item_ids:['card1'],succeeded:0,scanned:0,results:[]};
 global.fetch=async(url,options)=>{
  calls.push([url,options]);
  if (String(url).endsWith('/search-plan')) return response({status:'ready',label:'Fixture',keywords:['Test Player']});
  if (String(url).endsWith('/cancel')) return response({...job,status:'cancelled'});
  return response(options?.method==='POST'?job:[]);
 };
 render(React.createElement(PurchaseCostPanel,{onClose(){},onRecorded(){}}));
 await waitFor(()=>assert.equal(calls.length,1));
 fireEvent.click(screen.getByRole('button',{name:'开始批量获取成本'}));
 await waitFor(()=>assert.ok(searches.some(row=>row.type==='CARD_PLATFORM_COST_SEARCH')));
 fireEvent.click(screen.getByRole('button',{name:'取消任务'}));
 await screen.findByText('已取消');
 assert.ok(searches.some(row=>row.type==='CARD_PLATFORM_COST_CANCEL'));
 assert.equal(calls.some(([url])=>String(url).endsWith('/search-failed')),false);
});
for (const mode of ['create','resume']) test(`closing while ${mode} waits for the extension prevents a late job request`,async()=>{
 let completePing;const searches=bridge(undefined,()=>new Promise(resolve=>{completePing=resolve;}));const calls=[];
 const job={id:'j1',target:1,scan_limit:100,status:'cancelled',source_mode:'browser_search',item_ids:['card1'],succeeded:0,scanned:0,results:[]};
 global.fetch=async(url,options)=>{calls.push([url,options]);return response(mode==='resume'?[job]:[]);};
 const view=render(React.createElement(PurchaseCostPanel,{onClose(){view.unmount();},onRecorded(){}}));
 await waitFor(()=>assert.equal(calls.length,1));
 fireEvent.click(await screen.findByRole('button',{name:mode==='resume'?'从中断处继续':'开始批量获取成本'}));
 await waitFor(()=>assert.equal(typeof completePing,'function'));
 fireEvent.click(screen.getByRole('button',{name:'关闭成本识别'}));
 completePing({version:'0.3.0'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(screen.queryByRole('dialog'),null);
 assert.ok(searches.some(row=>row.type==='CARD_PLATFORM_COST_CANCEL'));
 assert.equal(calls.some(([,options])=>options?.method==='POST'),false);
});
test('closing during an already submitted job creation cancels the returned job',async()=>{
 bridge();let completeCreate;const calls=[];
 const job={id:'j1',target:1,scan_limit:100,status:'queued',source_mode:'browser_search',item_ids:['card1'],succeeded:0,scanned:0,results:[]};
 global.fetch=async(url,options)=>{
  calls.push([url,options]);
  if (String(url).endsWith('/jobs')&&options?.method==='POST') return new Promise(resolve=>{completeCreate=()=>resolve(response(job));});
  return response(String(url).endsWith('/cancel')?{...job,status:'cancelled'}:[]);
 };
 const view=render(React.createElement(PurchaseCostPanel,{onClose(){view.unmount();},onRecorded(){}}));
 await waitFor(()=>assert.equal(calls.length,1));
 fireEvent.click(screen.getByRole('button',{name:'开始批量获取成本'}));
 await waitFor(()=>assert.equal(typeof completeCreate,'function'));
 fireEvent.click(screen.getByRole('button',{name:'关闭成本识别'}));
 completeCreate();
 await waitFor(()=>assert.ok(calls.some(([url])=>String(url).endsWith('/j1/cancel'))));
 assert.equal(calls.some(([url])=>String(url).endsWith('/search-plan')),false);
 assert.equal(screen.queryByRole('dialog'),null);
});
test('manual override retains audit evidence and highlights changed source',async()=>{
 global.fetch=async()=>response({card_price:'10.00',shipping:'0.04',confirmation:'user_confirmed',manual_override:true,source_changed:true,evidence:candidate,current_price:'12.00',current_shipping:'0.04',current_currency:'CNY'});
 render(React.createElement(PurchaseCostAudit,{itemId:'card1'}));
 await screen.findByText(/卡淘购入成本来源 · 已人工修改/);
 assert.ok(screen.getByRole('alert').textContent.includes('来源订单已变化'));
});
