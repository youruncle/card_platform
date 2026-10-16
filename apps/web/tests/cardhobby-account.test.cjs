const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost:3000'});
global.window=dom.window;global.document=dom.window.document;global.HTMLElement=dom.window.HTMLElement;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const React=require('react');
const {render,screen,fireEvent,waitFor,cleanup}=require('@testing-library/react');
const {PurchaseCostPanel}=require('../components/purchase-cost-panel.tsx');
const {CardHobbyAccountForm}=require('../components/cardhobby-account-form.tsx');
const {searchCardHobbyOrders,connectCostSearch,CardHobbyLoginError}=require('../lib/cardhobby-cost-search.ts');
afterEach(cleanup);
const verificationDetails={captcha_url:"https://cms.cardhobby.com.cn/checkcode/index?mobile=13800000000",sms_sent:true,retry_after_seconds:0};
for(const code of ['7215','0123','123456'])test(`SMS code ${code} can submit through native form validation`,async()=>{
  let resumed=0;const calls=[];
  global.fetch=async(url,options)=>{if(String(url).endsWith("/verification"))return response(verificationDetails);calls.push(JSON.parse(options.body));return response({saved:true,state:'connected'});};
  render(React.createElement(CardHobbyAccountForm,{verification:true,onConnected(){resumed++;}}));
  await screen.findByText('卡淘已确认发送短信，请查看手机。');
  fireEvent.change(screen.getByLabelText('短信验证码'),{target:{value:code}});
  assert.equal(screen.getByLabelText('短信验证码').checkValidity(),true);
  fireEvent.click(screen.getByRole('button',{name:'验证并继续获取成本'}));
  await waitFor(()=>assert.equal(resumed,1));
  assert.deepEqual(calls,[{code}]);
});
for(const code of ['', '123', '1234567', '12a4'])test(`invalid SMS code ${JSON.stringify(code)} blocks submission`,async()=>{
  global.fetch=async(url)=>String(url).endsWith('/verification')?response(verificationDetails):assert.fail('invalid code must not submit');
  render(React.createElement(CardHobbyAccountForm,{verification:true,onConnected(){assert.fail('must not resume');}}));
  await screen.findByText('卡淘已确认发送短信，请查看手机。');
  fireEvent.change(screen.getByLabelText('短信验证码'),{target:{value:code}});
  assert.equal(screen.getByLabelText('短信验证码').checkValidity(),false);
  fireEvent.click(screen.getByRole('button',{name:'验证并继续获取成本'}));
});
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const failure=(code,message=code)=>response({detail:{code,message}},409);
const fill=()=>{
  fireEvent.change(screen.getByLabelText('卡淘用户名 / 手机号'),{target:{value:'fixture'}});
  fireEvent.change(screen.getByLabelText('卡淘密码'),{target:{value:'invented-password'}});
};
test('an unsent SMS challenge cannot submit a code or continue',async()=>{
  let requests=0;
  global.fetch=async(url)=>{assert.ok(String(url).endsWith('/verification'));requests++;return response({...verificationDetails,sms_sent:false});};
  render(React.createElement(CardHobbyAccountForm,{verification:true,onConnected(){assert.fail('must not resume');}}));
  await screen.findByText('尚未确认发送短信，请先完成卡淘滑块验证。');
  fireEvent.change(screen.getByLabelText('短信验证码'),{target:{value:'7215'}});
  const button=screen.getByRole('button',{name:'验证并继续获取成本'});
  assert.equal(button.disabled,true);fireEvent.click(button);
  assert.equal(requests,1);
});
test('server search works without Chrome extension; credentials never sent in search or status',async()=>{
  const calls=[];window.postMessage=()=>assert.fail('must not contact extension');
  global.fetch=async(url,options)=>{calls.push([url,options]);return response(String(url).endsWith('/status')?{saved:true,state:'connected'}:{orders:[],complete:true});};
  const controller=new AbortController();
  await connectCostSearch(controller.signal);
  assert.deepEqual(await searchCardHobbyOrders(['Test'],controller.signal),{orders:[],complete:true});
  assert.ok(calls.every(([,options])=>options.credentials==='include'&&options.signal===controller.signal));
  assert.deepEqual(JSON.parse(calls[1][1].body),{keywords:['Test']});
});
for(const code of ['LOGIN_REQUIRED','LOGIN_REJECTED','VERIFICATION_REQUIRED'])test(`account error ${code} opens recoverable product flow`,async()=>{
  global.fetch=async()=>failure(code,'请验证账号');
  await assert.rejects(()=>searchCardHobbyOrders(['Test']),error=>error instanceof CardHobbyLoginError&&error.code===code);
});
test('missing account opens login, successful save resumes the exact card with no empty evidence write',async()=>{
  let saved=false;const calls=[];
  global.fetch=async(url,options)=>{
    const path=String(url);calls.push([path,options]);
    if(path.endsWith('/search-plan'))return response({status:'ready',label:'Card One',keywords:['Test']});
    if(path.endsWith('/cardhobby/search'))return saved?response({orders:[],complete:true}):failure('LOGIN_REQUIRED');
    if(path.endsWith('/login')){saved=true;return response({saved:true,state:'connected'});}
    if(path.endsWith('/search-result'))return response({status:'not_found',reason:'没有购买记录',candidates:[],rejected:[]});
    assert.fail(path);
  };
  render(React.createElement(PurchaseCostPanel,{itemId:'card-one',onClose(){},onRecorded(){assert.fail('no write');}}));
  await screen.findByRole('heading',{name:'登录并绑定卡淘账号'});
  assert.equal(calls.some(([path])=>path.endsWith('/search-result')),false);
  fill();fireEvent.click(screen.getByRole('button',{name:'登录、保存并继续获取成本'}));
  await screen.findByText('没有购买记录');
  assert.equal(screen.queryByLabelText('卡淘密码'),null);
  assert.deepEqual(JSON.parse(calls.find(([path])=>path.endsWith('/login'))[1].body),{username:'fixture',password:'invented-password'});
  assert.equal(calls.filter(([path])=>path.endsWith('/cardhobby/search')).length,2);
  assert.equal(window.localStorage.length,0);
});
test('wrong password stays in form and does not continue',async()=>{
  global.fetch=async()=>failure('LOGIN_REJECTED','账号密码不正确');let resumed=0;
  render(React.createElement(CardHobbyAccountForm,{onConnected(){resumed++;}}));
  fill();fireEvent.click(screen.getByRole('button',{name:'登录、保存并继续获取成本'}));
  await screen.findByRole('alert');assert.equal(resumed,0);assert.ok(screen.getByLabelText('卡淘密码'));
});
test('SMS challenge clears password and resumes only after successful verification',async()=>{
  const calls=[];let resumed=0;
  global.fetch=async(url,options)=>{if(String(url).endsWith("/verification"))return response(verificationDetails);calls.push([String(url),options]);return String(url).endsWith('/login')?failure('VERIFICATION_REQUIRED','请输入验证码'):response({saved:true,state:'connected'});};
  render(React.createElement(CardHobbyAccountForm,{onConnected(){resumed++;}}));
  fill();fireEvent.click(screen.getByRole('button',{name:'登录、保存并继续获取成本'}));
  await screen.findByLabelText('短信验证码');assert.equal(screen.queryByLabelText('卡淘密码'),null);assert.equal(resumed,0);
  await screen.findByText('卡淘已确认发送短信，请查看手机。');
  fireEvent.change(screen.getByLabelText('短信验证码'),{target:{value:'123456'}});
  fireEvent.click(screen.getByRole('button',{name:'验证并继续获取成本'}));
  await waitFor(()=>assert.equal(resumed,1));
  assert.deepEqual(JSON.parse(calls[1][1].body),{code:'123456'});
});
test('unmount aborts login and ignores a late success',async()=>{
  let finish,signal,resumed=0;
  global.fetch=async(url,options)=>{signal=options.signal;return new Promise(resolve=>{finish=()=>resolve(response({saved:true,state:'connected'}));});};
  const view=render(React.createElement(CardHobbyAccountForm,{onConnected(){resumed++;}}));
  fill();fireEvent.click(screen.getByRole('button',{name:'登录、保存并继续获取成本'}));
  await waitFor(()=>assert.ok(signal));view.unmount();assert.ok(signal.aborted);finish();
  await new Promise(resolve=>setImmediate(resolve));assert.equal(resumed,0);
});
test('forget deletes only backend binding and resets SMS form',async()=>{
  let method;
  global.fetch=async(url,options)=>{if(String(url).endsWith('/verification'))return response(verificationDetails);assert.ok(String(url).endsWith('/account'));method=options.method;return response(null,204);};
  render(React.createElement(CardHobbyAccountForm,{verification:true,onConnected(){assert.fail('must not resume');}}));
  fireEvent.click(screen.getByRole('button',{name:'移除已保存账号 / 重新绑定'}));
  await screen.findByLabelText('卡淘密码');assert.equal(method,'DELETE');
});

test('empty complete name search retries the supplied product fallback',async()=>{
  const queries=[];
  global.fetch=async(url,options)=>{
    const words=JSON.parse(options.body).keywords;queries.push(words);
    if(words[0]==='Darius Acuff Jr')return response({orders:[],complete:true});
    assert.deepEqual(words,['Topps Now']);
    return response({orders:[{external_order_id:'fixture-now',lines:[{title:'Darryn Peterson',unit_price:'19.00'}]}],complete:true});
  };
  const capture=await searchCardHobbyOrders(['Darius Acuff Jr'],undefined,['Topps Now']);
  assert.equal(capture.orders.length,1);
  assert.equal(capture.complete,true);
  assert.deepEqual(queries,[['Darius Acuff Jr'],['Topps Now']]);
});

test('incomplete name search does not turn into a complete product search',async()=>{
  let calls=0;global.fetch=async()=>{calls++;return response({orders:[],complete:false});};
  const capture=await searchCardHobbyOrders(['Darius Acuff Jr'],undefined,['Topps Now']);
  assert.equal(capture.complete,false);assert.equal(calls,1);
});

test('product fallback authentication error remains recoverable rather than no orders',async()=>{
  global.fetch=async(url,options)=>JSON.parse(options.body).keywords[0]==='Darius Acuff Jr'
    ?response({orders:[],complete:true}):failure('VERIFICATION_REQUIRED','请验证账号');
  await assert.rejects(()=>searchCardHobbyOrders(['Darius Acuff Jr'],undefined,['Topps Now']),
    error=>error instanceof CardHobbyLoginError&&error.code==='VERIFICATION_REQUIRED');
});

test('a rejected name candidate also gets one bounded product retry with both order scopes',async()=>{
  const {resolveCostSearchWithFallback}=require('../lib/cardhobby-cost-search.ts');
  let resolutions=0;
  global.fetch=async(url,options)=>{
    if(String(url).endsWith('/cardhobby/search'))return response({orders:[{external_order_id:'second'}],complete:true});
    assert.ok(String(url).endsWith('/search-result'));
    const body=JSON.parse(options.body);resolutions++;
    if(resolutions===1)return response({status:'unmatched',candidates:[],rejected:[],reason:'姓名候选不同款'});
    assert.deepEqual(body.orders.map(order=>order.external_order_id),['first','second']);
    return response({status:'recorded',candidates:[],rejected:[],reason:'已匹配'});
  };
  const result=await resolveCostSearchWithFallback('fixture-item',{orders:[{external_order_id:'first'}],complete:true},
    {fallback_keywords:['Topps Now']});
  assert.equal(result.status,'recorded');assert.equal(resolutions,2);
});

test('image title correction is disclosed in the rendered order evidence',()=>{
  const {OrderEvidenceSnapshot}=require('../components/purchase-cost-panel.tsx');
  render(React.createElement(OrderEvidenceSnapshot,{candidate:{match_method:'image_title_correction',confidence:'.95',
    title:'Seller typo',order_id:'fixture',ordered_at:'2026-10-01',seller:'Fixture',status:'已完成',
    card_price:'19.00',shipping:'2.50',total_cost:'21.50',currency:'CNY',reasons:[],image_urls:[],
    source_url:'https://cardhobby.com.cn/order/detail/1',inference_warnings:['卖家标题写错球员']}}));
  assert.ok(screen.getByText(/图片核验纠正卖家标题/));
});

test('closing the modal after final matching starts still reports a confirmed write',async()=>{
  let release,started=false,recorded=0;
  global.fetch=async(url,options)=>{
    if(String(url).endsWith('/search-plan'))return response({status:'ready',label:'Fixture',keywords:['Name']});
    if(String(url).endsWith('/cardhobby/search'))return response({orders:[],complete:true});
    if(String(url).endsWith('/search-result')){
      started=true;
      return new Promise((resolve,reject)=>{
        release=()=>resolve(response({status:'recorded',reason:'已录入',candidates:[],rejected:[]}));
        options.signal?.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError')),{once:true});
      });
    }
    assert.fail(String(url));
  };
  const view=render(React.createElement(PurchaseCostPanel,{itemId:'fixture',onClose(){},onRecorded(){recorded++;}}));
  await waitFor(()=>assert.equal(started,true));view.unmount();release();
  await waitFor(()=>assert.equal(recorded,1));
});

test('cancelling as product fallback returns cannot start another write',async()=>{
  const {resolveCostSearchWithFallback}=require('../lib/cardhobby-cost-search.ts');
  const controller=new AbortController();let writes=0;
  global.fetch=async(url)=>{
    if(String(url).endsWith('/search-result')){writes++;return response({status:'unmatched',candidates:[],rejected:[],reason:'未匹配'});}
    assert.ok(String(url).endsWith('/cardhobby/search'));controller.abort();
    return response({orders:[{external_order_id:'late'}],complete:true});
  };
  await resolveCostSearchWithFallback('fixture',{orders:[],complete:true},{fallback_keywords:['Topps Now']},controller.signal);
  assert.equal(writes,1);
});
