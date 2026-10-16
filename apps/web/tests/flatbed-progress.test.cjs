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
