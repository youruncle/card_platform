const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;global.React=require('react');
const {render,screen,fireEvent,cleanup,waitFor}=require('@testing-library/react');
const {CapturePanel}=require('../components/capture-panel.tsx');
afterEach(cleanup);
test('only-back capture sends a real back file and keeps the same request id after network failure',async()=>{
 const requests=[];
 global.fetch=async(url,init)=>{
  if(url==='/v1/client-capabilities')return {ok:true,json:async()=>({capture_jobs:true,single_side:true,crop_proposals:true})};
  if(init?.method==='POST'){requests.push(init);throw Error('network unavailable');}
  return {ok:true,json:async()=>[]};
 };
 render(React.createElement(CapturePanel,{ownerId:'owner',onClose:()=>{},onSaved:()=>{}}));
 await waitFor(()=>assert.ok(screen.getByText('只有背面')));
 fireEvent.change(screen.getByLabelText('照片模式'),{target:{value:'back'}});
 fireEvent.change(screen.getByLabelText('背面照片'),{target:{files:[new Blob(['test'],{type:'image/png'})]}});
 fireEvent.click(screen.getByRole('button',{name:'保留背面原图'}));
 fireEvent.click(screen.getByRole('button',{name:'提交识别'}));
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/network unavailable/));
 fireEvent.click(screen.getByRole('button',{name:'提交识别'}));
 await waitFor(()=>assert.equal(requests.length,2));
 assert.ok(requests[0].body.has('back'));assert.equal(requests[0].body.has('front'),false);
 assert.equal(requests[0].headers['Idempotency-Key'],requests[1].headers['Idempotency-Key']);
});
test('supplement confirms images only and retains human identity',async()=>{
 let submitted;
 global.fetch=async(url,init)=>{
  if(url==='/v1/client-capabilities')return {ok:true,json:async()=>({capture_jobs:true,single_side:true})};
  if(url.endsWith('/confirm')){submitted=JSON.parse(init.body);return {ok:true,json:async()=>({id:'item'})};}
  return {ok:true,json:async()=>[{id:'job',status:'succeeded',available_sides:['back'],target_item_id:'item',result:{confidence:'.8',player_name:'Suggested'},images:{}}]};
 };
 render(React.createElement(CapturePanel,{ownerId:'owner',onClose:()=>{},onSaved:()=>{}}));
 await waitFor(()=>assert.ok(screen.getByRole('button',{name:'查看任务'})));
 fireEvent.click(screen.getByRole('button',{name:'查看任务'}));
 fireEvent.click(screen.getByRole('button',{name:'确认补拍（保留身份）'}));
 await waitFor(()=>assert.deepEqual(submitted,{}));
});
test('supplement requires explicit field checkboxes and merges complete current identity',async()=>{
 const current={player_name:'Confirmed',brand_name:'Test',product_name:'Test',season_start:2026,season_end:null,card_number:'1',is_autograph:true,autograph_type:'on_card',inscription_text:'MVP',manufacturer:'Preserved'};
 let submitted;
 global.fetch=async(url,init)=>{
  if(url==='/v1/client-capabilities')return {ok:true,json:async()=>({capture_jobs:true,single_side:true})};
  if(url.endsWith('/items/item'))return {ok:true,json:async()=>({identity:current})};
  if(url.endsWith('/confirm')){submitted=JSON.parse(init.body);return {ok:true,json:async()=>({id:'item'})};}
  return {ok:true,json:async()=>[{id:'job',status:'succeeded',available_sides:['back'],target_item_id:'item',result:{confidence:'.8',player_name:'Suggested',card_number:'2',brand_name:null},images:{}}]};
 };
 render(React.createElement(CapturePanel,{ownerId:'owner',onClose:()=>{},onSaved:()=>{}}));
 await waitFor(()=>assert.ok(screen.getByRole('button',{name:'查看任务'})));fireEvent.click(screen.getByRole('button',{name:'查看任务'}));
 await waitFor(()=>assert.ok(screen.getByLabelText('接受卡号建议')));
 assert.equal(screen.getByLabelText('接受球员建议').checked,false);assert.equal(screen.getByLabelText('接受卡号建议').checked,false);
 const button=screen.getByRole('button',{name:'接受所选字段并补拍'});assert.equal(button.disabled,true);
 fireEvent.click(screen.getByLabelText('接受卡号建议'));fireEvent.click(button);
 await waitFor(()=>assert.deepEqual(submitted,{accepted_identity:{...current,card_number:'2'}}));
});
test('formally bound identity explains read-only suggestions and still permits pictures only',async()=>{
 const current={player_name:'Confirmed',brand_name:'Test',product_name:'Test',season_start:2026};let submitted;
 global.fetch=async(url,init)=>{
  if(url.endsWith('/items/item'))return {ok:true,json:async()=>({identity:current,can_accept_identity:false,identity_acceptance_reason:'已有审核身份，请到身份审核修改'})};
  if(url.endsWith('/confirm')){submitted=JSON.parse(init.body);return {ok:true,json:async()=>({id:'item'})};}
  return {ok:true,json:async()=>[{id:'job',status:'succeeded',available_sides:['back'],target_item_id:'item',result:{confidence:'.8',player_name:'Suggested'},images:{}}]};
 };
 render(React.createElement(CapturePanel,{ownerId:'owner',onClose:()=>{},onSaved:()=>{}}));
 await waitFor(()=>assert.ok(screen.getByRole('button',{name:'查看任务'})));fireEvent.click(screen.getByRole('button',{name:'查看任务'}));
 await waitFor(()=>assert.ok(screen.getByText('已有审核身份，请到身份审核修改')));
 assert.equal(screen.getByLabelText('接受球员建议').disabled,true);
 assert.equal(screen.getByRole('button',{name:'接受所选字段并补拍'}).disabled,true);
 fireEvent.click(screen.getByRole('button',{name:'确认补拍（保留身份）'}));await waitFor(()=>assert.deepEqual(submitted,{}));
});
