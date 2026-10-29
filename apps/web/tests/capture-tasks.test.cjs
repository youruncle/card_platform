const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>',{url:'http://localhost:3000'});
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;global.React=require('react');
const {render,screen,fireEvent,cleanup,waitFor,act}=require('@testing-library/react');
const {CaptureTasksCenter}=require('../components/capture-tasks-panel.tsx');
afterEach(cleanup);
const response=data=>({ok:true,status:200,json:async()=>data});
function task(overrides={}){return {id:'failed-task',status:'failed',display_status:'failed',require_confirmation:false,available_sides:['front','back'],attempt_count:4,error_message:'正反面需要人工核对',created_at:'2026-10-30T01:00:00Z',images:{front:{url:'/front.jpg'},back:{url:'/back.jpg'}},image_content_hashes:{front:'front-hash',back:'back-hash'},history:[{id:'event-1',occurred_at:'2026-10-30T01:00:00Z',attempt_count:4,display_status:'failed',message:'第四次识别超时',source:'worker'}],attention_reasons:['识别服务超时'],can_retry:true,result:null,...overrides};}
function page(items=[task()],overrides={}){return {items,total:items.length,page:1,page_size:20,total_pages:1,counts:{processing:0,needs_review:0,failed:items.length,imported:0,cancelled:0,total:items.length},...overrides};}
function mount(props={}){return render(React.createElement(CaptureTasksCenter,{ownerId:'owner',open:true,onOpen:()=>{},onClose:()=>{},onSaved:()=>{},...props}));}
test('persistent task entry reports failed automatic captures without opening scanner or modal',async()=>{
 let opened=0;global.fetch=async()=>response(page());mount({open:false,onOpen:()=>opened++});
 const entry=await screen.findByRole('button',{name:'录入任务 · 1 项待处理'});assert.equal(screen.queryByRole('dialog'),null);fireEvent.click(entry);assert.equal(opened,1);
});
test('task request errors stay visible and are never rendered as an empty task list',async()=>{
 global.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'任务服务暂时不可用'})});mount();
 await waitFor(()=>assert.match(screen.getByRole('dialog').textContent,/任务服务暂时不可用/));assert.equal(screen.queryByText('当前筛选没有任务'),null);
});
test('task pages retain filters and reach historical records after the first 100',async()=>{
 const queries=[];global.fetch=async url=>{const parsed=new URL(url,'http://localhost');queries.push(parsed.search);return response(page([task({id:`task-${parsed.searchParams.get('page')}`})],{page:Number(parsed.searchParams.get('page')),total:125,total_pages:7}));};mount();
 await screen.findByText('第 1 / 7 页 · 共 125 项');fireEvent.change(screen.getByLabelText('任务状态'),{target:{value:'all'}});
 for(let n=2;n<=7;n++){await waitFor(()=>assert.equal(screen.getByRole('button',{name:'任务下一页'}).disabled,false));fireEvent.click(screen.getByRole('button',{name:'任务下一页'}));await screen.findByText(`第 ${n} / 7 页 · 共 125 项`);}
 assert.ok(queries.some(q=>q.includes('state=all')&&q.includes('page=7')));assert.equal(screen.getByLabelText('任务状态').value,'all');
});
test('failed task opens both images, attempt history, manual fields and bound-image confirmation',async()=>{
 let submitted,saved=0;global.fetch=async(url,init)=>{if(url.endsWith('/confirm')){submitted=JSON.parse(init.body);return response({id:'new-card'});}return response(url.includes('?')?page():task());};mount({onSaved:()=>saved++});
 fireEvent.click(await screen.findByRole('button',{name:'查看任务 failed-task'}));
 assert.ok(await screen.findByAltText('任务正面'));assert.ok(screen.getByAltText('任务背面'));assert.ok(await screen.findByText('第四次识别超时'));
 const confirm=screen.getByRole('button',{name:'确认入库'});assert.equal(confirm.disabled,true);
 for(const [label,value] of [['球员 / 主体','Player'],['品牌','Topps'],['产品','Chrome'],['发行年','2025']])fireEvent.change(screen.getByLabelText(label),{target:{value}});
 fireEvent.click(screen.getByLabelText('我已查看当前正反面，确认属于同一张卡'));fireEvent.click(confirm);
 await waitFor(()=>assert.equal(saved,1));assert.deepEqual(submitted.pair_review,{confirmed:true,image_content_hashes:{front:'front-hash',back:'back-hash'}});assert.equal(submitted.card.season_start,2025);assert.equal(screen.getByLabelText('任务状态').value,'attention');
});
test('true face conflict cannot be manually confirmed and replacement preserves one idempotency key after failure',async()=>{
 const conflicted=task({result:{confidence:0,pair_validation:{status:'conflict',image_content_hashes:{front:'front-hash',back:'back-hash'}}}});const writes=[];
 global.fetch=async(url,init)=>{if(url.endsWith('/images')){writes.push(init);throw Error('上传网络中断');}return response(url.includes('?')?page([conflicted]):conflicted);};mount();fireEvent.click(await screen.findByRole('button',{name:'查看任务 failed-task'}));
 await screen.findByText(/正反面存在明确冲突/);assert.equal(screen.queryByLabelText('我已查看当前正反面，确认属于同一张卡'),null);assert.equal(screen.getByRole('button',{name:'确认入库'}).disabled,true);
 fireEvent.change(screen.getByLabelText('替换正面照片'),{target:{files:[new Blob(['front'],{type:'image/jpeg'})]}});fireEvent.change(screen.getByLabelText('替换背面照片'),{target:{files:[new Blob(['back'],{type:'image/jpeg'})]}});
 fireEvent.click(screen.getByRole('button',{name:'替换照片并重新识别'}));await screen.findByText('上传网络中断');fireEvent.click(screen.getByRole('button',{name:'替换照片并重新识别'}));await waitFor(()=>assert.equal(writes.length,2));
 assert.equal(writes[0].headers['Idempotency-Key'],writes[1].headers['Idempotency-Key']);assert.ok(writes[0].body.has('front'));assert.ok(writes[0].body.has('back'));
});
test('manual retry refreshes detail without removing earlier failure history or switching filters',async()=>{
 let retries=0;global.fetch=async(url,init)=>{if(url.endsWith('/retry')){retries++;return response(task({display_status:'processing',status:'queued',attempt_count:4,can_retry:false}));}return response(url.includes('?')?page():task());};mount();fireEvent.click(await screen.findByRole('button',{name:'查看任务 failed-task'}));
 fireEvent.click(await screen.findByRole('button',{name:'重新尝试识别'}));await waitFor(()=>assert.equal(retries,1));assert.ok(screen.getByText('第四次识别超时'));assert.equal(screen.getByLabelText('任务状态').value,'attention');
});
test('Canon submitted ids are opened directly in task center even when task is imported',async()=>{
 let opened;const imported=task({id:'canon-job',status:'succeeded',display_status:'imported',collection_item_id:'card-7',can_retry:false});
 global.fetch=async url=>response(url.includes('?')?page([]):imported);mount({initialTaskIds:['canon-job'],onOpenItem:id=>opened=id});
 fireEvent.click(await screen.findByRole('button',{name:'查看已入库卡片'}));assert.equal(opened,'card-7');assert.equal(screen.queryByRole('button',{name:'确认入库'}),null);
});
test('unknown flags stay unknown when untouched and explicit tri-state corrections are included',async()=>{
 let submitted;const draft=task({display_status:'needs_review',result:{confidence:'.3',player_name:'Player',brand_name:'Topps',product_name:'Chrome',season_start:2025,is_rookie:null,is_autograph:null,is_patch:null,is_memorabilia:null}});
 global.fetch=async(url,init)=>{if(url.endsWith('/confirm')){submitted=JSON.parse(init.body);return response({id:'card'});}return response(url.includes('?')?page([draft]):draft);};mount();fireEvent.click(await screen.findByRole('button',{name:'查看任务 failed-task'}));
 const autograph=await screen.findByLabelText('签名');assert.equal(autograph.value,'unknown');fireEvent.change(screen.getByLabelText('新秀'),{target:{value:'false'}});
 fireEvent.click(screen.getByLabelText('我已查看当前正反面，确认属于同一张卡'));fireEvent.click(screen.getByRole('button',{name:'确认入库'}));
 await waitFor(()=>assert.ok(submitted));assert.equal(submitted.card.is_autograph,null);assert.equal(submitted.card.is_rookie,false);assert.equal(submitted.card.is_patch,null);
});
test('finishing an older retry cannot replace the task currently being reviewed',async()=>{
 let finish;const other=task({id:'other-task'});
 global.fetch=async(url,init)=>{if(url.endsWith('/retry'))return new Promise(resolve=>{finish=()=>resolve(response(task({display_status:'processing',can_retry:false})));});return response(url.includes('?')?page([task(),other]):url.endsWith('other-task')?other:task());};mount();
 fireEvent.click(await screen.findByRole('button',{name:'查看任务 failed-task'}));fireEvent.click(await screen.findByRole('button',{name:'重新尝试识别'}));
 fireEvent.click(screen.getByRole('button',{name:'查看任务 other-task'}));await waitFor(()=>assert.match(screen.getByLabelText('任务详情').textContent,/任务 other-task/));
 await act(async()=>finish());assert.match(screen.getByLabelText('任务详情').textContent,/任务 other-task/);
});
