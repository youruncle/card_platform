const {test,afterEach,after}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost:3000'});
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
for(const name of ['HTMLElement','HTMLInputElement','File','FormData'])global[name]=dom.window[name];
global.FormData=class extends dom.window.FormData {constructor(form){super(form);if(form)for(const input of form.querySelectorAll('input[type=file]'))for(const file of input.files??[])this.set(input.name,file);}};
global.React=require('react');global.IS_REACT_ACT_ENVIRONMENT=true;
URL.createObjectURL=()=> 'blob:pair-preview';URL.revokeObjectURL=()=>{};
global.DataTransfer=class {constructor(){this.files=document.createElement('input').files;this.items={add:()=>{}};}};
const {render,screen,fireEvent,cleanup,waitFor,act,within}=require('@testing-library/react');
const Dashboard=require('../app/page.tsx').default;
afterEach(cleanup);
after(()=>dom.window.close());
const result=status=>({player_name:'Pair Player',brand_name:'Topps',product_name:'Chrome',season_start:2025,season_end:2026,confidence:'.9',caveats:[],search_queries:[],field_evidence:{},web_candidates:[],pair_validation:{status,reason_codes:[],evidence:['配对身份仍待核对'],front:{role:'front'},back:{role:'back'},image_content_hashes:{front:'front-hash',back:'back-hash'},version:1}});
const job=status=>({id:'pair-job',status:'succeeded',front_image_url:'/v1/capture/jobs/pair-job/images/front?revision=1',back_image_url:'/v1/capture/jobs/pair-job/images/back?revision=1',front_orientation_method:'manual',back_orientation_method:'manual',front_rotation_degrees:0,back_rotation_degrees:0,collection_item_id:null,result:result(status),attempt_count:0,created_at:'2026-10-19T00:00:00Z'});
function fixture(status='unknown'){
 const writes=[];const pair=job(status);
 global.fetch=async(url,options)=>{
  const path=new URL(url,'http://localhost').pathname;
  if(options?.method==='POST'||options?.method==='DELETE')writes.push({path,options});
  if(path==='/v1/auth/me')return response({id:'owner',display_name:'Fixture',base_currency:'CNY'});
  if(path==='/v1/portfolio/summary')return response({owner_id:'owner',base_currency:'CNY',item_count:0,valued_item_count:0,unvalued_item_count:0,total_cost:'0',total_market_value:'0',unrealized_profit:'0',realized_profit:'0'});
  if(path==='/v1/portfolio/items/page')return response({items:[],total:0,total_pages:1,page:1,page_size:30});
  if(path==='/v1/scanner/jobs')return response([pair]);
  if(path==='/v1/capture/jobs')return response(pair);
  if(path==='/v1/capture/jobs/pair-job')return response(pair);
  if(path.endsWith('/images/front')||path.endsWith('/images/back'))return {ok:true,status:200,blob:async()=>new Blob(['photo'],{type:'image/jpeg'})};
  if(path.endsWith('/confirm')||path==='/v1/portfolio/items')return response({id:'saved'});
  if(path==='/v1/flatbed/sessions')return response(options?.method==='POST'?{id:'session'}:[]);
  if(path==='/v1/flatbed/device')return response({name:'Fixture scanner'});
  return response({});
 };
 return {writes,pair};
}
const response=data=>({ok:true,status:200,json:async()=>data});
async function openRecognition(mode){
 await act(async()=>render(React.createElement(Dashboard)));
 fireEvent.click(await screen.findByRole('button',{name:'图片识别'}));
 await act(async()=>fireEvent.click(screen.getByRole('button',{name:mode==='pair'?/正反面识别/:/高拍仪扫描/})));
}
test('scanner draft is confirmed through its capture job without create-upload-delete bypass',async()=>{
 const {writes}=fixture();await openRecognition('scanner');
 fireEvent.click(screen.getByRole('button',{name:'查看旧任务'}));
 await waitFor(()=>assert.ok(screen.getByLabelText('我已查看当前正反面，确认属于同一张卡')));
 await waitFor(()=>assert.ok(screen.getByAltText('当前任务正面')));assert.ok(screen.getByAltText('当前任务背面'));
 fireEvent.click(screen.getByLabelText('我已查看当前正反面，确认属于同一张卡'));
 fireEvent.click(screen.getByRole('button',{name:'确认修改并带入录卡'}));
 fireEvent.change(screen.getByLabelText('买入价格'),{target:{value:'10'}});
 fireEvent.click(screen.getByRole('button',{name:'保存到卡库'}));
 await waitFor(()=>assert.ok(writes.some(w=>w.path==='/v1/capture/jobs/pair-job/confirm')));
 const payload=JSON.parse(writes.find(w=>w.path.endsWith('/confirm')).options.body);
 assert.deepEqual(payload.pair_review,{confirmed:true,image_content_hashes:{front:'front-hash',back:'back-hash'}});
 assert.equal(payload.card.is_rookie,null);assert.equal(payload.card.is_autograph,null);assert.equal(payload.card.is_patch,null);
 assert.equal(writes.some(w=>w.path==='/v1/portfolio/items'||w.options.method==='DELETE'),false);
});
test('raw two-photo recognition is persisted and a known conflict cannot enter the card form',async()=>{
 const {writes}=fixture('conflict');await openRecognition('pair');
 const dialog=screen.getByText('识别卡面信息').closest('.modal');
 const inputs=dialog.querySelectorAll('input[type=file]');
 fireEvent.change(inputs[0],{target:{files:[new File(['front'],'front.jpg',{type:'image/jpeg'})]}});
 fireEvent.change(inputs[1],{target:{files:[new File(['back'],'back.jpg',{type:'image/jpeg'})]}});
 fireEvent.submit(dialog.querySelector('.recognition-form'));
 await waitFor(()=>assert.ok(writes.some(w=>w.path==='/v1/capture/jobs')));
 assert.equal(writes.some(w=>w.path==='/v1/cards/recognize-pair'),false);
 await waitFor(()=>assert.ok(screen.getByText(/正反面存在明确冲突/)));
 assert.equal(screen.getByRole('button',{name:'确认修改并带入录卡'}).disabled,true);
});
function failedTask(){return {...job('unknown'),id:'canon-task',status:'failed',display_status:'failed',require_confirmation:false,available_sides:['front','back'],images:{front:{url:'/front.jpg'},back:{url:'/back.jpg'}},can_retry:true,image_content_hashes:{front:'front-hash',back:'back-hash'},attention_reasons:['识别服务超时'],history:[],replacement_job_id:null,error_message:'自动识别失败，请核对照片'};}
const taskPage=()=>({items:[failedTask()],total:1,page:1,page_size:20,total_pages:1,counts:{processing:0,needs_review:0,failed:1,imported:0,cancelled:0,total:1}});
test('library header exposes failed automatic tasks before opening any recognition mode',async()=>{
 fixture();const original=global.fetch;global.fetch=async(url,options)=>String(url).includes('/v1/capture/tasks')?response(String(url).includes('?')?taskPage():failedTask()):original(url,options);
 await act(async()=>render(React.createElement(Dashboard)));
 const entry=await screen.findByRole('button',{name:'录入任务 · 1 项待处理'});assert.equal(screen.queryByText('识别卡面信息'),null);fireEvent.click(entry);
 assert.ok(await screen.findByRole('dialog',{name:'录入任务中心'}));fireEvent.click(await screen.findByRole('button',{name:'查看任务 canon-task'}));
 assert.ok(await screen.findByAltText('任务正面'));assert.ok(screen.getByText('自动识别失败，请核对照片',{selector:'.capture-task-error'}));
});
test('Canon submission opens its new task and photos instead of leaving an empty card library',async()=>{
 fixture();const original=global.fetch;let submitted=0;
 global.fetch=async(url,options)=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/capture/tasks')return response(taskPage());
  if(path==='/v1/capture/tasks/canon-task')return response(failedTask());
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',dpi:300,sheets:[],regions:[{id:'f',side:'front',ordinal:0},{id:'b',side:'back',ordinal:0}],pairs:[{front_region_id:'f',back_region_id:'b',selected:true,score:.9,confidence_level:'high',identity_status:'consistent',signals:{},conflicts:[]}]});
  if(path.endsWith('/pairs'))return response({status:'paired',count:1,confirmation_token:'reviewed-current-images',skipped:{front:0,back:0}});
  if(path.endsWith('/submit')){submitted++;return response({status:'submitted',job_ids:['canon-task']});}
  return original(url,options);
 };
 await act(async()=>render(React.createElement(Dashboard)));
 fireEvent.click(await screen.findByRole('button',{name:'图片识别'}));fireEvent.click(screen.getByRole('button',{name:/Canon 扫描仪/}));
 const submit=await screen.findByRole('button',{name:'确认配对并录入 1 张卡片'});await waitFor(()=>assert.equal(submit.disabled,false));fireEvent.click(submit);
 assert.ok(await screen.findByRole('dialog',{name:'录入任务中心'}));assert.ok(await screen.findByAltText('任务正面'));assert.equal(submitted,1);assert.equal(screen.queryByText('识别卡面信息'),null);
});
