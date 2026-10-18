const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;global.React=require('react');
const {render,screen,fireEvent,cleanup,waitFor}=require('@testing-library/react');
const {FlatbedBatchPanel}=require('../components/flatbed-batch-panel.tsx');
afterEach(cleanup);

test('back scan keeps reporting crop progress until pairing completes',async()=>{
 let polls=0,finishCapture;
 const region={id:'front',side:'front',ordinal:0,bounds:{x:.1,y:.1,width:.4,height:.7},confidence:.9};
 const response=data=>({ok:true,json:async()=>data});
 global.fetch=async url=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/flatbed/device')return response({name:'Canon',serial:'fixture'});
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',regions:[region],sheets:[],pairs:[]});
  if(path.includes('/capture/back'))return new Promise(resolve=>{finishCapture=()=>resolve(response({id:'sheet',regions:[{...region,id:'back',side:'back'}],failures:[]}));});
  if(path.endsWith('/status')){polls++;return response({phase:'processing',detail:`已处理 ${polls}/8 张卡片`});}
  if(path.endsWith('/pair'))return response({pairs:[{front_id:'front',back_id:'back',confidence:.95,confidence_level:'high',conflicts:[],signals:{},requires_review:false}],missing_front:[],missing_back:[],requires_review:false});
  throw Error(`Unexpected request: ${path}`);
 };
 render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.equal(screen.getAllByRole('button',{name:'正式扫描'})[1].disabled,false));
 fireEvent.click(screen.getAllByRole('button',{name:'正式扫描'})[1]);
 try{
  await waitFor(()=>assert.match(screen.getByRole('status').textContent,/已处理 2\/8/),{timeout:3500});
 }finally{finishCapture();}
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/全部为高置信度配对/));
});

test('explicit next batch creates a fresh session instead of reusing restored sheets', async()=>{
 const calls=[];
 const response=data=>({ok:true,json:async()=>data});
 global.fetch=async (url,options={})=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/flatbed/device')return response({name:'Canon',serial:'fixture'});
  if(path==='/v1/flatbed/sessions' && options.method==='POST'){
   calls.push(JSON.parse(options.body));return response({id:'fresh',dpi:300});
  }
  if(path==='/v1/flatbed/sessions')return response([{id:'old',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/old')return response({id:'old',regions:[],sheets:[],pairs:[]});
  throw Error(`Unexpected request: ${path}`);
 };
 render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/恢复/));
 fireEvent.click(screen.getByRole('button',{name:'开始下一批'}));
 await waitFor(()=>assert.equal(calls.length,1));
 assert.equal(calls[0].new_batch,true);
});

test('flatbed submit carries the token returned for the confirmed images', async()=>{
 const response=data=>({ok:true,json:async()=>data});let submitted=null,completed=null;
 const region={ordinal:0,bounds:{x:.1,y:.1,width:.4,height:.7},confidence:.9};
 global.fetch=async(url,options={})=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/flatbed/device')return response({name:'Canon',serial:'fixture'});
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',regions:[{...region,id:'front',side:'front'},{...region,id:'back',side:'back'}],sheets:[],pairs:[{front_region_id:'front',back_region_id:'back',score:.9,confidence_level:'high',identity_status:'unknown',signals:{},conflicts:[],selected:true}]});
  if(path.endsWith('/pairs'))return response({status:'paired',count:1,confirmation_token:'reviewed-current-images',skipped:{front:0,back:0}});
  if(path.endsWith('/submit')){submitted=JSON.parse(options.body);return response({status:'submitted',job_ids:['job']});}
  throw Error(`Unexpected request: ${path}`);
 };
 render(React.createElement(FlatbedBatchPanel,{onCompleted:ids=>{completed=ids;}}));
 const button=await screen.findByRole('button',{name:'确认配对并录入 1 张卡片'});
 await waitFor(()=>assert.equal(button.disabled,false));fireEvent.click(button);
 await waitFor(()=>assert.deepEqual(completed,['job']));
 assert.deepEqual(submitted,{confirmation_token:'reviewed-current-images'});
});
