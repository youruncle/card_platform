const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost:3000'});
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
const React=require('react');
const {render,screen,fireEvent,waitFor,cleanup}=require('@testing-library/react');
const {CardHobbySmsVerification}=require('../components/cardhobby-sms-verification.tsx');
afterEach(cleanup);
test('expired challenge offers restart and recovers the slider without asking for credentials',async()=>{
 let restarted=false;const paths=[];
 global.fetch=async(url)=>{
  paths.push(String(url));
  if(String(url).endsWith('/restart-verification')){restarted=true;return {ok:false,status:409,json:async()=>({detail:{code:'VERIFICATION_REQUIRED',message:'需要验证'}})};}
  if(!restarted)return {ok:false,status:409,json:async()=>({detail:{code:'VERIFICATION_EXPIRED',message:'验证码会话已过期'}})};
  return response(details);
 };
 render(React.createElement(CardHobbySmsVerification,{onReady(){}}));
 fireEvent.click(await screen.findByRole('button',{name:'重新发起验证'}));
 await screen.findByRole('button',{name:'获取短信验证码'});
 assert.equal(paths.filter(path=>path.endsWith('/restart-verification')).length,1);
 assert.equal(screen.queryByText('验证码会话已过期'),null);
});
const details={captcha_url:'https://cms.cardhobby.com.cn/checkcode/index?mobile=13800000000',sms_sent:false,retry_after_seconds:0};
const response=data=>({ok:true,json:async()=>data});
const message=(source,origin='https://cms.cardhobby.com.cn')=>fireEvent(window,new window.MessageEvent('message',{origin,source,data:'checkcode'}));

test('only the current official captcha frame can trigger one SMS request, and only success marks sent',async()=>{
 const calls=[];let ready=false;
 global.fetch=async(url)=>{calls.push(String(url));return response(String(url).endsWith('/send-sms')?{...details,sms_sent:true,retry_after_seconds:60}:details);};
 render(React.createElement(CardHobbySmsVerification,{onReady:value=>{ready=value;}}));
 await screen.findByText('尚未确认发送短信，请先完成卡淘滑块验证。');
 assert.equal(ready,false);assert.equal(calls.length,1);
 fireEvent.click(screen.getByRole('button',{name:'获取短信验证码'}));
 const frame=screen.getByTitle('卡淘官方滑块验证');
 assert.equal(frame.src,details.captcha_url);
 message(frame.contentWindow,'https://attacker.example');message(window);
 assert.equal(calls.length,1);
 message(frame.contentWindow);message(frame.contentWindow);
 await screen.findByText('卡淘已确认发送短信，请查看手机。');
 assert.equal(ready,true);assert.equal(calls.filter(url=>url.endsWith('/send-sms')).length,1);
 assert.ok(screen.getByRole('button',{name:/60秒后可重新获取/}).disabled);
 assert.equal(screen.queryByTitle('卡淘官方滑块验证'),null);
});

test('send failure never shows success and reloads server cooldown',async()=>{
 let sent=false;let ready=false;
 global.fetch=async(url)=>{
  if(String(url).endsWith('/send-sms')){sent=true;return {ok:false,status:409,json:async()=>({detail:{code:'SMS_SEND_FAILED',message:'短信发送失败'}})};}
  return response({...details,retry_after_seconds:sent?60:0});
 };
 render(React.createElement(CardHobbySmsVerification,{onReady:value=>{ready=value;}}));
 fireEvent.click(await screen.findByRole('button',{name:'获取短信验证码'}));
 message(screen.getByTitle('卡淘官方滑块验证').contentWindow);
 await screen.findByText('短信发送失败');
 await waitFor(()=>assert.ok(screen.getByRole('button',{name:/60秒后可重新获取/}).disabled));
 assert.equal(ready,false);assert.equal(screen.queryByText('卡淘已确认发送短信，请查看手机。'),null);
});

test('refresh restores confirmed send without resending',async()=>{
 let calls=0,ready=false;
 global.fetch=async()=>{calls++;return response({...details,sms_sent:true,retry_after_seconds:35});};
 render(React.createElement(CardHobbySmsVerification,{onReady:value=>{ready=value;}}));
 await screen.findByText('卡淘已确认发送短信，请查看手机。');
 assert.equal(ready,true);assert.equal(calls,1);
});
