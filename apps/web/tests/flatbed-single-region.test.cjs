const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost:3000'});
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;global.React=require('react');
const {render,screen,within,fireEvent,cleanup,waitFor}=require('@testing-library/react');
const {FlatbedBatchPanel}=require('../components/flatbed-batch-panel.tsx');
afterEach(cleanup);

for(const [failFirst,failPair] of [[false,false],[true,false],[false,true]])test(`single-card correction visibly saves, retries errors and preserves other pair choices (${failFirst},${failPair})`,async()=>{
 const response=data=>({ok:true,json:async()=>data});
 const region=(id,side,ordinal)=>({id,side,ordinal,bounds:{x:.1+ordinal*.45,y:.1,width:.35,height:.7},confidence:.8});
 const fronts=[region('f0','front',0),region('f1','front',1)];
 const backs=[region('b0','back',0),region('b1','back',1)];
 const fresh=[region('new-b0','back',0),region('new-b1','back',1)];
 let layouts=0,pairings=0,releaseSave,submitted;
 global.fetch=async(url,options={})=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/flatbed/device')return response({name:'Canon',serial:'fixture'});
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',dpi:300,regions:[...fronts,...backs],sheets:[{id:'front-sheet',side:'front',revision:1},{id:'back-sheet',side:'back',revision:1}],pairs:fronts.map((front,i)=>({front_region_id:front.id,back_region_id:backs[1-i].id,score:.8,confidence_level:'medium',identity_status:'unknown',conflicts:[],signals:{},selected:true}))});
  if(path==='/v1/flatbed/sheets/back-sheet/layout'){
   layouts++;const body=JSON.parse(options.body);
   assert.equal(body.region_id,'b1');assert.equal(body.regions.length,1);
   if(failFirst&&layouts===1)return {ok:false,status:422,json:async()=>({detail:'保存未完成，请重试'})};
   return new Promise(resolve=>{releaseSave=()=>resolve(response({id:'new-back-sheet',revision:2,regions:fresh,failures:[]}));});
  }
  if(path.endsWith('/pair')){
   pairings++;
   if(failPair&&pairings===1)return {ok:false,status:503,json:async()=>({detail:'配对暂时不可用'})};
   return response({pairs:fronts.map((front,i)=>({front_id:front.id,back_id:fresh[i].id,confidence:.9,confidence_level:'high',identity_status:'consistent',conflicts:[],signals:{}})),missing_front:[],missing_back:[]});
  }
  if(path.endsWith('/pairs')){submitted=JSON.parse(options.body).pairs;return response({confirmation_token:'fresh'});}
  if(path.endsWith('/submit'))return response({job_ids:['j0','j1']});
  throw Error(`unexpected ${path}`);
 };
 render(React.createElement(FlatbedBatchPanel));
 fireEvent.click(await screen.findByRole('button',{name:'校正背面 2'}));
 const dialog=screen.getByRole('dialog');
 assert.match(dialog.textContent,/仅调整.*背面 2/);
 assert.equal(dialog.querySelectorAll('polygon').length,1);
 fireEvent.click(within(dialog).getByRole('button',{name:'确认并重建裁切'}));
 if(failFirst){
  await waitFor(()=>assert.match(within(dialog).getByRole('alert').textContent,/保存未完成/));
  fireEvent.click(within(dialog).getByRole('button',{name:'确认并重建裁切'}));
 }
 await waitFor(()=>assert.ok(releaseSave));
 assert.equal(within(dialog).getByRole('button',{name:'正在保存…'}).disabled,true);
 assert.match(within(dialog).getByRole('status').textContent,/保存/);
 releaseSave();
 await waitFor(()=>assert.equal(screen.queryByRole('dialog')===null,true));
 if(failPair){
  const retry=await screen.findByRole('button',{name:'重新生成配对并继续录入'});
  assert.equal(screen.getByRole('button',{name:'确认配对并录入 2 张卡片'}).disabled,true);
  assert.equal(screen.getByRole('button',{name:'校正背面 2'}).disabled,false);
  fireEvent.click(retry);
 }
 await waitFor(()=>assert.equal(screen.getByRole('button',{name:'确认配对并录入 2 张卡片'}).disabled,false));
 fireEvent.click(screen.getByRole('button',{name:'确认配对并录入 2 张卡片'}));
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/已提交/));
 assert.deepEqual(submitted,[{front_region_id:'f0',back_region_id:'new-b1'},{front_region_id:'f1',back_region_id:'new-b0'}]);
 assert.equal(layouts,failFirst?2:1);
});
