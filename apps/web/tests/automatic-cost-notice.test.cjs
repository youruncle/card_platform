const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost:3000'});
global.window=dom.window;global.document=dom.window.document;global.HTMLElement=dom.window.HTMLElement;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const React=require('react');
const {render,screen,fireEvent,waitFor,cleanup,act}=require('@testing-library/react');
const {AutomaticCostNotice}=require('../components/automatic-cost-notice.tsx');
afterEach(cleanup);
const response=data=>({ok:true,status:200,json:async()=>data});
const blocked={blocked_count:25,notice_key:'batch-one',latest_blocked_at:'2026-10-28T00:00:20Z',code:'LOGIN_REQUIRED',worker_enabled:true};

test('background account blocks open login without manually fetching a card; closing keeps a reminder',async()=>{
 const calls=[];global.fetch=async(url,options)=>{calls.push([String(url),options]);return response(blocked);};
 render(React.createElement(AutomaticCostNotice,{ownerId:'owner-one'}));
 await screen.findByRole('dialog');
 assert.ok(screen.getByLabelText('卡淘用户名 / 手机号'));
 assert.match(screen.getByRole('dialog').textContent,/25/);
 fireEvent.click(screen.getByRole('button',{name:'稍后处理'}));
 assert.equal(screen.queryByRole('dialog')===null,true);
 fireEvent(window,new window.Event('focus'));
 await waitFor(()=>assert.ok(calls.length>=2));
 assert.equal(screen.queryByRole('dialog')===null,true);
 fireEvent.click(screen.getByRole('button',{name:'登录或验证卡淘'}));
 assert.ok(screen.getByRole('dialog'));
 assert.ok(calls.every(([url,options])=>url.endsWith('/automatic/attention')&&(!options.method||options.method==='GET')));
});

test('late background failure and a new batch prompt; verification never sends SMS automatically',async()=>{
 let state={...blocked,blocked_count:0,notice_key:null,code:null};const calls=[];
 global.fetch=async(url,options)=>{calls.push(String(url));return response(String(url).endsWith('/verification')?{sms_sent:false,retry_after_seconds:0,captcha_url:'https://cms.cardhobby.com.cn/checkcode/index?mobile=13800000000'}:state);};
 render(React.createElement(AutomaticCostNotice,{ownerId:'owner-one'}));
 await waitFor(()=>assert.equal(calls.length,1));assert.equal(screen.queryByRole('dialog')===null,true);
 state={...blocked,code:'VERIFICATION_REQUIRED'};fireEvent(window,new window.Event('focus'));
 await screen.findByRole('heading',{name:'卡淘短信验证'});
 await screen.findByText('尚未确认发送短信，请先完成卡淘滑块验证。');
 assert.equal(calls.some(url=>url.endsWith('/send-sms')||url.endsWith('/login')),false);
 fireEvent.click(screen.getByRole('button',{name:'稍后处理'}));
 state={...state,notice_key:'batch-two',blocked_count:26,latest_blocked_at:'2026-10-28T00:00:21Z'};fireEvent(window,new window.Event('focus'));
 await screen.findByRole('dialog');
});

test('login success clears the block and never starts a second per-card lookup',async()=>{
 let connected=false;const calls=[];
 global.fetch=async(url,options)=>{const path=String(url);calls.push(path);if(path.endsWith('/login')){connected=true;return response({saved:true,state:'connected'});}assert.ok(path.endsWith('/automatic/attention'));return response(connected?{...blocked,blocked_count:0,notice_key:null,code:null}:blocked);};
 render(React.createElement(AutomaticCostNotice,{ownerId:'owner-one'}));
 await screen.findByRole('dialog');
 fireEvent.change(screen.getByLabelText('卡淘用户名 / 手机号'),{target:{value:'fixture'}});
 fireEvent.change(screen.getByLabelText('卡淘密码'),{target:{value:'fixture-password'}});
 fireEvent.click(screen.getByRole('button',{name:'登录、保存并继续获取成本'}));
 await waitFor(()=>assert.equal(screen.queryByRole('dialog')===null,true));
 await waitFor(()=>assert.equal(screen.queryByRole('button',{name:'登录或验证卡淘'})===null,true));
 assert.equal(calls.filter(p=>p.endsWith('/login')).length,1);
});

test('a late response from the previous owner cannot display its block after an account switch',async()=>{
 let finish,signal;
 global.fetch=async(url,options)=>{if(!finish){signal=options.signal;return new Promise(resolve=>{finish=()=>resolve(response(blocked));});}return response({...blocked,blocked_count:0,notice_key:null,code:null});};
 const view=render(React.createElement(AutomaticCostNotice,{ownerId:'first'}));
 await waitFor(()=>assert.ok(finish));
 view.rerender(React.createElement(AutomaticCostNotice,{ownerId:'second'}));
 assert.equal(signal.aborted,true);
 await act(async()=>finish());
 assert.equal(screen.queryByRole('dialog')===null,true);
});

test('removing the latest blocked card updates the reminder without reopening the dismissed batch',async()=>{
 let state=blocked;
 global.fetch=async()=>response(state);
 render(React.createElement(AutomaticCostNotice,{ownerId:'owner-one'}));
 await screen.findByRole('dialog');fireEvent.click(screen.getByRole('button',{name:'稍后处理'}));
 state={...blocked,blocked_count:24,notice_key:'older-remaining-block',latest_blocked_at:'2026-10-28T00:00:19Z'};
 await act(async()=>{fireEvent(window,new window.Event('focus'));});
 assert.equal(screen.queryByRole('dialog')===null,true);
 assert.match(screen.getByRole('status').textContent,/24/);
});
