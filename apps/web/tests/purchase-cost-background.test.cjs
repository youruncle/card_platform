const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const deps=process.env.COST_TEST_DEPENDENCIES;
const dependency=name=>require(deps ? deps+'/'+name : name);
const {JSDOM}=dependency('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost:3000'});
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;
const {renderHook,act,waitFor,cleanup}=dependency('@testing-library/react');
const {usePurchaseCostLookup}=require('../lib/use-purchase-cost-lookup.ts');
require('./cost-legacy-fixture.cjs');
afterEach(cleanup);

const candidate={unit_id:'u1',evidence_hash:'a'.repeat(64),order_id:'900001',ordered_at:'2025-09-01',seller:'Fixture Shop',status:'已完成',item_id:'900002',unit_index:0,title:'Fixture Test Card',image_urls:[],source_url:'https://cardhobby.com.cn/Order/detail/900001',card_price:'10.00',shipping:'0.04',total_cost:'10.04',currency:'CNY',confidence:'0.95',reasons:['年份一致','球员一致'],missing:[],automatic:true};
const outcome=(status,extra={})=>({status,candidates:[],rejected:[],reason:`结果：${status}`,...extra});
const response=data=>({ok:true,status:200,json:async()=>data});
const plan={item_id:'card1',label:'Test Player 2025 Hoops',keywords:['Test Player'],status:'ready',reason:''};
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function bridge(search=()=>({orders:[],complete:true}),ping=()=>({version:'0.3.0'})) {
  const calls=[];
  window.postMessage=message=>{
    calls.push(message);
    if(message.type==='CARD_PLATFORM_COST_CANCEL')return;
    Promise.resolve(message.type==='CARD_PLATFORM_COST_PING'?ping(message):Promise.resolve(search(message)).then(result=>({result}))).then(result=>{
      window.dispatchEvent(new window.MessageEvent('message',{origin:window.location.origin,source:window,data:{type:'CARD_PLATFORM_COST_RESPONSE',requestId:message.requestId,...result}}));
    });
  };
  return calls;
}
function api(getPlan=()=>plan,resolve=()=>outcome('recorded',{recorded:candidate})) {
  const calls=[];
  global.fetch=async(url,options)=>{
    const call={url:String(url),options};calls.push(call);
    return response(await (call.url.endsWith('/search-plan')?getPlan(call):resolve(call)));
  };
  return calls;
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('starts immediately, deduplicates the same card and preserves the result after a failed refresh',async()=>{
  const pending=deferred();const searches=bridge(()=>pending.promise);const calls=api();let recorded=0;
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>{recorded++;throw new Error('refresh unavailable');}));
  act(()=>{hook.result.current.start('card1');hook.result.current.start('card1');});
  assert.equal(hook.result.current.lookups.card1.status,'loading');
  await waitFor(()=>assert.equal(searches.filter(call=>call.type==='CARD_PLATFORM_COST_SEARCH').length,1));
  await act(async()=>{pending.resolve({orders:[{fixture:'order'}],complete:true});await settle();});
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'recorded'));
  assert.equal(recorded,1);
  assert.equal(hook.result.current.lookups.card1.result.recorded.total_cost,'10.04');
  assert.equal(calls.length,2);
  assert.deepEqual(JSON.parse(calls[1].options.body),{orders:[{fixture:'order'}],complete:true,refresh:true});
  assert.equal(calls[0].options.signal,calls[1].options.signal);
});

test('different rows immediately show loading and search sequentially through the extension',async()=>{
  const first=deferred(),second=deferred();
  const searches=bridge(message=>message.keywords[0]==='card1'?first.promise:second.promise);
  const calls=api(call=>({...plan,keywords:[call.url.includes('/card1/')?'card1':'card2']}),call=>outcome('recorded',{recorded:{...candidate,total_cost:call.url.includes('/card1/')?'11.00':'22.00'}}));
  const recorded=[];const hook=renderHook(()=>usePurchaseCostLookup('owner1',(id)=>recorded.push(id)));
  act(()=>{hook.result.current.start('card1');hook.result.current.start('card2');});
  assert.equal(hook.result.current.lookups.card1.status,'loading');
  assert.equal(hook.result.current.lookups.card2.status,'loading');
  await waitFor(()=>assert.equal(searches.filter(call=>call.type==='CARD_PLATFORM_COST_SEARCH').length,1));
  assert.equal(calls.length,1);assert.equal(calls[0].url.includes('/card1/'),true);
  await act(async()=>{first.resolve({orders:[],complete:true});await settle();});
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'recorded'));
  assert.equal(hook.result.current.lookups.card2.status,'loading');
  await waitFor(()=>assert.equal(searches.filter(call=>call.type==='CARD_PLATFORM_COST_SEARCH').length,2));
  await act(async()=>{second.resolve({orders:[],complete:true});await settle();});
  await waitFor(()=>assert.equal(hook.result.current.lookups.card2.status,'recorded'));
  assert.deepEqual(recorded,['card1','card2']);
  assert.equal(hook.result.current.lookups.card1.result.recorded.total_cost,'11.00');
  assert.equal(hook.result.current.lookups.card2.result.recorded.total_cost,'22.00');
});

test('a skipped search plan never contacts the extension or submits orders',async()=>{
  const searches=bridge();const calls=api(()=>({...plan,status:'skipped',reason:'已有购入成本',keywords:[]}));
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>assert.fail('must not record')));
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'skipped'));
  assert.equal(hook.result.current.lookups.card1.message,'已有购入成本');
  assert.equal(searches.length,0);assert.equal(calls.length,1);
});

test('only multiple verified same-card purchases produce a confirmation state',async()=>{
  bridge();let attempt=0;
  api(undefined,()=>outcome('needs_confirmation',{candidates:++attempt===1?[candidate,{...candidate,unit_id:'weak',automatic:false}]:[candidate,{...candidate,unit_id:'u2'},{...candidate,unit_id:'weak',automatic:false}]}));
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>assert.fail('must not record')));
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'unmatched'));
  assert.equal(hook.result.current.lookups.card1.result.status,'unmatched');
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'needs_confirmation'));
  assert.equal(hook.result.current.lookups.card1.result.candidates.length,2);
});

for(const status of ['not_found','incomplete','unmatched'])test(`${status} remains a completed row result without a confirmation callback`,async()=>{
  bridge();api(undefined,()=>outcome(status,{candidates:[{...candidate,automatic:false}]}));
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>assert.fail('must not record')));
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,status));
  assert.equal(hook.result.current.lookups.card1.message,`结果：${status}`);
});

test('a missing extension has a distinct connection-required result',async()=>{
  window.postMessage=()=>{};const calls=api();
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>assert.fail('must not record')));
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'connection_required'),{timeout:3000});
  assert.match(hook.result.current.lookups.card1.message,/首次使用请连接卡淘/);
  assert.equal(calls.length,1);
});

test('a search failure settles the row and permits retry',async()=>{
  let attempt=0;bridge();
  api(()=>{if(++attempt===1)throw new Error('服务暂不可用');return plan;});
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>{}));
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'error'));
  assert.equal(hook.result.current.lookups.card1.message,'服务暂不可用');
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'recorded'));
});

for (const failure of [
  {error:'请先在此 Chrome 登录卡淘，再重试获取成本'},
  {error:'卡淘登录已过期，请在卡淘订单页重新登录后重试'},
  {error:'Authentication expired',code:'AUTH_REQUIRED'},
]) test(`expired login is recoverable without submitting empty orders: ${failure.error}`,async()=>{
  window.postMessage=message=>{
    if(message.type==='CARD_PLATFORM_COST_CANCEL')return;
    queueMicrotask(()=>window.dispatchEvent(new window.MessageEvent('message',{
      origin:window.location.origin,source:window,data:{type:'CARD_PLATFORM_COST_RESPONSE',requestId:message.requestId,
        ...(message.type==='CARD_PLATFORM_COST_PING'?{version:'0.3.0'}:failure)},
    })));
  };
  const calls=api();let recorded=0;
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>recorded++));
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'login_required'));
  assert.match(hook.result.current.lookups.card1.message,/重新登录卡淘/);
  assert.equal(calls.length,1);assert.equal(recorded,0);
  bridge();
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'recorded'));
  assert.equal(recorded,1);
});

test('a failed first row releases the queue so the next card still completes',async()=>{
  const first=deferred();const calls=api(undefined,call=>call.url.includes('/card1/')?first.promise:outcome('recorded',{recorded:candidate}));
  bridge();const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>{}));
  act(()=>{hook.result.current.start('card1');hook.result.current.start('card2');});
  await waitFor(()=>assert.equal(calls.length,2));
  await act(async()=>{first.resolve(outcome('incomplete'));await settle();});
  await waitFor(()=>assert.equal(hook.result.current.lookups.card2.status,'recorded'));
  assert.equal(hook.result.current.lookups.card1.status,'incomplete');
  assert.equal(calls.length,4);
});

test('an exception in the first lookup does not leave later cards stuck in the queue',async()=>{
  bridge();api(call=>{if(call.url.includes('/card1/'))throw new Error('search plan failed');return plan;});
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>{}));
  act(()=>{hook.result.current.start('card1');hook.result.current.start('card2');});
  await waitFor(()=>assert.equal(hook.result.current.lookups.card2.status,'recorded'));
  assert.equal(hook.result.current.lookups.card1.status,'error');
});

for(const stage of ['plan','search','resolve'])test(`owner changes abort a pending ${stage} and ignore its late completion`,async()=>{
  const pending=deferred();
  const searches=bridge(()=>stage==='search'?pending.promise:{orders:[],complete:true});
  const calls=api(()=>stage==='plan'?pending.promise:plan,()=>stage==='resolve'?pending.promise:outcome('recorded',{recorded:candidate}));
  let recorded=0;const hook=renderHook(({owner})=>usePurchaseCostLookup(owner,()=>{recorded++;}),{initialProps:{owner:'owner1'}});
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.ok(stage==='search'?searches.some(call=>call.type==='CARD_PLATFORM_COST_SEARCH'):calls.length===(stage==='resolve'?2:1)));
  const signal=calls.at(-1).options.signal;
  hook.rerender({owner:'owner2'});
  assert.equal(signal.aborted,true);assert.deepEqual(hook.result.current.lookups,{});
  await act(async()=>{pending.resolve(stage==='plan'?plan:stage==='search'?{orders:[],complete:true}:outcome('recorded',{recorded:candidate}));await settle();});
  assert.deepEqual(hook.result.current.lookups,{});assert.equal(recorded,0);
  assert.equal(calls.length,stage==='resolve'?2:1);
  if(stage==='search')assert.ok(searches.some(call=>call.type==='CARD_PLATFORM_COST_CANCEL'));
});

test('unmount cancels the active search and discards queued rows without late writes',async()=>{
  const pending=deferred();const searches=bridge(()=>pending.promise);const calls=api();let recorded=0;
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>{recorded++;}));
  act(()=>{hook.result.current.start('card1');hook.result.current.start('card2');});
  await waitFor(()=>assert.equal(searches.filter(call=>call.type==='CARD_PLATFORM_COST_SEARCH').length,1));
  hook.unmount();
  assert.equal(calls.every(call=>call.options.signal.aborted),true);
  assert.equal(searches.filter(call=>call.type==='CARD_PLATFORM_COST_CANCEL').length,1);
  await act(async()=>{pending.resolve({orders:[],complete:true});await settle();});
  assert.equal(calls.length,1);assert.equal(recorded,0);
});

test('clearing a queued row skips its plan and does not interrupt the active card',async()=>{
  const first=deferred();const calls=api();const searches=bridge(()=>first.promise);
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>{}));
  act(()=>{hook.result.current.start('card1');hook.result.current.start('card2');});
  await waitFor(()=>assert.equal(searches.filter(call=>call.type==='CARD_PLATFORM_COST_SEARCH').length,1));
  act(()=>hook.result.current.clear('card2'));
  assert.equal(hook.result.current.lookups.card2,undefined);
  assert.equal(calls[0].options.signal.aborted,false);
  await act(async()=>{first.resolve({orders:[],complete:true});await settle();});
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'recorded'));
  assert.equal(calls.length,2);
  assert.equal(searches.filter(call=>call.type==='CARD_PLATFORM_COST_SEARCH').length,1);
});

test('clear cancels an old lookup and a late response cannot replace its retry',async()=>{
  const pending=deferred();let attempt=0;
  const searches=bridge(()=>++attempt===1?pending.promise:{orders:[],complete:true});const calls=api();
  const hook=renderHook(()=>usePurchaseCostLookup('owner1',()=>{}));
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(attempt,1));
  act(()=>hook.result.current.clear('card1'));
  assert.equal(hook.result.current.lookups.card1,undefined);
  assert.equal(calls[0].options.signal.aborted,true);
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'recorded'));
  await act(async()=>{pending.resolve({orders:[{fixture:'stale'}],complete:true});await settle();});
  assert.equal(hook.result.current.lookups.card1.status,'recorded');
  assert.equal(calls.filter(call=>call.url.endsWith('/search-result')).length,1);
  assert.ok(searches.some(call=>call.type==='CARD_PLATFORM_COST_CANCEL'));
});

test('uses the latest refresh callback without restarting pending work',async()=>{
  const pending=deferred();bridge(()=>pending.promise);const calls=api();let oldCalls=0,newCalls=0;
  const hook=renderHook(({onRecorded})=>usePurchaseCostLookup('owner1',onRecorded),{initialProps:{onRecorded:()=>{oldCalls++;}}});
  act(()=>hook.result.current.start('card1'));
  hook.rerender({onRecorded:()=>{newCalls++;}});
  await act(async()=>{pending.resolve({orders:[],complete:true});await settle();});
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'recorded'));
  assert.equal(oldCalls,0);assert.equal(newCalls,1);assert.equal(calls.length,2);
});

test('logged-out sessions cannot start a lookup and logout clears completed results',async()=>{
  bridge();const calls=api();
  const hook=renderHook(({owner})=>usePurchaseCostLookup(owner,()=>{}),{initialProps:{owner:null}});
  act(()=>hook.result.current.start('card1'));
  assert.equal(calls.length,0);
  hook.rerender({owner:'owner1'});
  act(()=>hook.result.current.start('card1'));
  await waitFor(()=>assert.equal(hook.result.current.lookups.card1.status,'recorded'));
  hook.rerender({owner:null});
  assert.deepEqual(hook.result.current.lookups,{});
  act(()=>hook.result.current.start('card1'));
  assert.equal(calls.length,2);
});
