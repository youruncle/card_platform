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
const response=(data,status=200)=>({ok:status<400,status,json:async()=>data});
const failure=(code,message=code)=>response({detail:{code,message}},409);
const fill=()=>{
  fireEvent.change(screen.getByLabelText('卡淘用户名 / 手机号'),{target:{value:'fixture'}});
  fireEvent.change(screen.getByLabelText('卡淘密码'),{target:{value:'invented-password'}});
};
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
  global.fetch=async(url,options)=>{calls.push([String(url),options]);return String(url).endsWith('/login')?failure('VERIFICATION_REQUIRED','请输入验证码'):response({saved:true,state:'connected'});};
  render(React.createElement(CardHobbyAccountForm,{onConnected(){resumed++;}}));
  fill();fireEvent.click(screen.getByRole('button',{name:'登录、保存并继续获取成本'}));
  await screen.findByLabelText('短信验证码');assert.equal(screen.queryByLabelText('卡淘密码'),null);assert.equal(resumed,0);
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
  global.fetch=async(url,options)=>{assert.ok(String(url).endsWith('/account'));method=options.method;return response(null,204);};
  render(React.createElement(CardHobbyAccountForm,{verification:true,onConnected(){assert.fail('must not resume');}}));
  fireEvent.click(screen.getByRole('button',{name:'移除已保存账号 / 重新绑定'}));
  await screen.findByLabelText('卡淘密码');assert.equal(method,'DELETE');
});
