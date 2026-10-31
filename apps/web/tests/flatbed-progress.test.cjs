const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;global.React=require('react');
const {render,screen,fireEvent,cleanup,waitFor}=require('@testing-library/react');
const {FlatbedBatchPanel}=require('../components/flatbed-batch-panel.tsx');
afterEach(()=>{cleanup();dom.reconfigure({url:'http://localhost:3000'});});

test('restored fronts wait for a back scan and a failed automatic connection offers retry',async()=>{
 dom.reconfigure({url:'https://cardsp.cn/'});
 const response=data=>({ok:true,json:async()=>data});
 global.fetch=async url=>{
  if(String(url).endsWith(':8766/v1/device'))throw new TypeError('Failed to fetch');
  const path=new URL(url,'https://cardsp.cn').pathname;
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',regions:[{id:'front',side:'front',ordinal:0,bounds:{x:.1,y:.1,width:.4,height:.7},confidence:.9}],sheets:[],pairs:[]});
  throw Error(`Unexpected request ${url}`);
 };
 render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/已恢复.*1 张正面.*扫描背面/));
 assert.equal(screen.queryByRole('button',{name:'确认配对并录入 0 张卡片'}),null);
 assert.ok(screen.getByRole('button',{name:'重试连接扫描仪'}));
});

test('cloud Canon scans locally, retains failed upload and retries without scanning twice',async()=>{
 dom.reconfigure({url:'https://cardsp.cn/'});
 const response=data=>({ok:true,json:async()=>data});
 const metadata={actual_dpi:200,pixel_width:1700,pixel_height:2338,capture_kind:'preview',source:'image_capture_core_overview_resolution'};
 let scans=0,uploads=0,native=0;
 global.fetch=async(url,options={})=>{
  if(String(url).startsWith('http://127.0.0.1:8766')){
   assert.equal(options.credentials,'omit');
   if(String(url).endsWith('/device'))return response({name:'Canon',serial:'fixture',token:'private-local-token'});
   scans++;assert.equal(options.headers['X-Scanner-Token'],'private-local-token');
   assert.equal(JSON.parse(options.body).dpi,600);
   return new Response(new Uint8Array([137,80,78,71]),{headers:{'Content-Type':'image/png','X-Scan-Resolution':JSON.stringify(metadata)}});
  }
  const path=new URL(url,'https://cardsp.cn').pathname;
  assert.equal(options.headers?.['X-Scanner-Token'],undefined);
  if(path==='/v1/flatbed/sessions')return response([{id:'cloud-session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/cloud-session')return response({id:'cloud-session',dpi:600,regions:[],sheets:[],pairs:[]});
  if(path.includes('/capture/')||path==='/v1/flatbed/device'){native++;throw Error('cloud must not run Mac capture');}
  if(path.endsWith('/sheets/front')){
   uploads++;assert.equal(options.credentials,'include');
   assert.deepEqual(JSON.parse(options.body.get('source_resolution')),metadata);
   assert.deepEqual([...new Uint8Array(await options.body.get('image').arrayBuffer())],[137,80,78,71]);
   if(uploads===1)return {ok:false,status:502,json:async()=>({detail:'上传暂时失败'})};
   return response({id:'cloud-sheet',regions:[{id:'front',side:'front',ordinal:0,bounds:{x:.1,y:.1,width:.4,height:.7},confidence:.9}],failures:[]});
  }
  throw Error(`Unexpected request ${url}`);
 };
 render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.equal(screen.getAllByText('概览',{selector:'button'})[0].disabled,false));
 assert.equal(screen.queryByRole('button',{name:/连接.*扫描仪/}),null);
 fireEvent.click(screen.getAllByText('概览',{selector:'button'})[0]);
 const retry=await screen.findByRole('button',{name:'重试上传扫描图'});
 assert.equal(scans,1);assert.equal(uploads,1);
 assert.equal(screen.getAllByText('概览',{selector:'button'})[0].disabled,true);
 assert.equal(screen.queryByRole('button',{name:'开始下一批'}),null);
 fireEvent.click(retry);
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/检测到 1 张正面/));
 assert.equal(scans,1);assert.equal(uploads,2);assert.equal(native,0);
});

test('unavailable scanner disables native capture while whole-sheet import remains usable',async()=>{
 const response=data=>({ok:true,json:async()=>data});
 let captureRequests=0;
 global.fetch=async url=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/flatbed/device')return {ok:false,status:503,json:async()=>({detail:'请在连接 Canon 的 Mac 上扫描，或导入已有整版图片'})};
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',regions:[],sheets:[],pairs:[]});
  if(path.includes('/capture/')){captureRequests++;throw Error('native capture must stay disabled');}
  throw Error(`Unexpected request: ${path}`);
 };
 const {container}=render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.match(container.textContent,/设备不可用.*Mac/));
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/恢复/));
 for(const button of screen.getAllByRole('button',{name:/^(概览|正式扫描)$/})){
  assert.equal(button.disabled,true);
  fireEvent.click(button);
 }
 assert.equal(captureRequests,0);
 assert.equal(container.querySelector('input[type="file"]').disabled,false);
});

test('closing cloud scanner aborts local capture and cannot start a late cloud upload',async()=>{
 dom.reconfigure({url:'https://cardsp.cn/'});
 const response=data=>({ok:true,json:async()=>data});
 let finishScan,scanSignal,uploads=0;
 global.fetch=async(url,options={})=>{
  if(String(url).endsWith(':8766/v1/device'))return response({name:'Canon',serial:'fixture',token:'token'});
  if(String(url).endsWith(':8766/v1/scan')){scanSignal=options.signal;return new Promise(resolve=>{finishScan=()=>resolve(new Response(new Uint8Array([137,80,78,71]),{headers:{'Content-Type':'image/png'}}));});}
  const path=new URL(url,'https://cardsp.cn').pathname;
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',regions:[],sheets:[],pairs:[]});
  if(path.includes('/sheets/')){uploads++;return response({id:'sheet',regions:[],failures:[]});}
  throw Error(`Unexpected request ${url}`);
 };
 const view=render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.equal(screen.getAllByRole('button',{name:'正式扫描'})[0].disabled,false));
 fireEvent.click(screen.getAllByRole('button',{name:'正式扫描'})[0]);
 await waitFor(()=>assert.ok(finishScan));view.unmount();
 assert.equal(scanSignal.aborted,true);finishScan();
 await new Promise(resolve=>setTimeout(resolve,20));assert.equal(uploads,0);
});

test('closing while connecting aborts the exclusive local device probe',async()=>{
 dom.reconfigure({url:'https://cardsp.cn/'});
 const response=data=>({ok:true,json:async()=>data});let probeSignal,finishProbe;
 global.fetch=async(url,options={})=>{
  if(String(url).endsWith(':8766/v1/device')){probeSignal=options.signal;return new Promise(resolve=>{finishProbe=()=>resolve(response({name:'Canon',serial:'fixture',token:'token'}));});}
  const path=new URL(url,'https://cardsp.cn').pathname;
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',regions:[],sheets:[],pairs:[]});
  throw Error(`Unexpected request ${url}`);
 };
 const view=render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.ok(finishProbe));view.unmount();
 try {assert.equal(probeSignal.aborted,true);} finally {finishProbe();}
});

test('local scan failure exposes reconnection and retry restores scanning',async()=>{
 dom.reconfigure({url:'https://cardsp.cn/'});
 const response=data=>({ok:true,json:async()=>data});let probes=0;
 global.fetch=async url=>{
  if(String(url).endsWith(':8766/v1/device')){probes++;return response({name:'Canon',serial:'fixture',token:`token-${probes}`});}
  if(String(url).endsWith(':8766/v1/scan'))return {ok:false,status:403,json:async()=>({detail:'扫描连接已失效'})};
  const path=new URL(url,'https://cardsp.cn').pathname;
  if(path==='/v1/flatbed/sessions')return response([{id:'session',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/session')return response({id:'session',regions:[],sheets:[],pairs:[]});
  throw Error(`Unexpected request ${url}`);
 };
 render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.equal(screen.getAllByRole('button',{name:'正式扫描'})[0].disabled,false));
 fireEvent.click(screen.getAllByRole('button',{name:'正式扫描'})[0]);
 await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/连接已失效/));
 fireEvent.click(screen.getByRole('button',{name:'重试连接扫描仪'}));
 await waitFor(()=>assert.equal(screen.getAllByRole('button',{name:'正式扫描'})[0].disabled,false));
 assert.equal(probes,2);
 assert.equal(screen.queryByRole('button',{name:'重试连接扫描仪'}),null);
});

for(const source of ['local','import'])test(`${source} front uses a new batch after submit and never uploads to the submitted batch`,async()=>{
 if(source==='local')dom.reconfigure({url:'https://cardsp.cn/'});
 const response=data=>({ok:true,json:async()=>data});
 const region={ordinal:0,bounds:{x:.1,y:.1,width:.4,height:.7},confidence:.9};
 let creates=0,scans=0,uploads=0,failedCreate=source==='import';
 global.fetch=async(url,options={})=>{
  if(String(url).endsWith(':8766/v1/device'))return response({name:'Canon',serial:'fixture',token:'token'});
  if(String(url).endsWith(':8766/v1/scan')){scans++;return new Response(new Uint8Array([137,80,78,71]),{headers:{'Content-Type':'image/png'}});}
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/flatbed/device')return response({name:'Canon',serial:'fixture'});
  if(path==='/v1/flatbed/sessions'&&options.method==='POST'){
   creates++;assert.equal(JSON.parse(options.body).new_batch,true);
   if(failedCreate){failedCreate=false;return {ok:false,status:503,json:async()=>({detail:'创建暂时失败'})};}
   return response({id:'fresh',dpi:300,submitted_at:null});
  }
  if(path==='/v1/flatbed/sessions')return response([{id:'old',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/old')return response({id:'old',dpi:300,regions:[{...region,id:'front',side:'front'},{...region,id:'back',side:'back'}],sheets:[],pairs:[{front_region_id:'front',back_region_id:'back',score:.9,confidence_level:'high',identity_status:'consistent',signals:{},conflicts:[],selected:true}]});
  if(path.endsWith('/pairs'))return response({confirmation_token:'confirmed'});
  if(path.endsWith('/submit'))return response({job_ids:['job']});
  if(path.includes('/sheets/front')){
   assert.equal(path,'/v1/flatbed/sessions/fresh/sheets/front');uploads++;
   if(source==='local'&&uploads===1)return {ok:false,status:502,json:async()=>({detail:'上传暂时失败'})};
   return response({id:'sheet',regions:[{...region,id:'next-front',side:'front'}],failures:[]});
  }
  throw Error(`Unexpected request ${url}`);
 };
 const {container}=render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.equal(screen.getByRole('button',{name:'确认配对并录入 1 张卡片'}).disabled,false));
 fireEvent.click(screen.getByRole('button',{name:'确认配对并录入 1 张卡片'}));
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/已提交/));
 if(source==='local'){
  fireEvent.click(screen.getAllByRole('button',{name:'正式扫描'})[0]);
  fireEvent.click(await screen.findByRole('button',{name:'重试上传扫描图'}));
  assert.equal(scans,1);
 }else{
  const input=container.querySelector('input[type="file"]');
  const file=new File([new Uint8Array([137,80,78,71])],'sheet.png',{type:'image/png'});
  fireEvent.change(input,{target:{files:[file]}});
  await waitFor(()=>assert.match(screen.getByRole('alert').textContent,/创建暂时失败/));
  assert.equal(uploads,0);
  fireEvent.change(input,{target:{files:[file]}});
 }
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/检测到 1 张正面/));
 assert.equal(creates,source==='import'?2:1);
 assert.equal(uploads,source==='local'?2:1);
 assert.equal(screen.getAllByRole('button',{name:'正式扫描'})[1].disabled,false);
});

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

test('front rescans stay in the current batch and scanning after submit creates a fresh batch', async()=>{
 const calls=[],captures=[];
 const response=data=>({ok:true,json:async()=>data});
 const region={ordinal:0,bounds:{x:.1,y:.1,width:.4,height:.7},confidence:.9};
 global.fetch=async (url,options={})=>{
  const path=new URL(url,'http://localhost').pathname;
  if(path==='/v1/flatbed/device')return response({name:'Canon',serial:'fixture'});
  if(path==='/v1/flatbed/sessions' && options.method==='POST'){
   calls.push(JSON.parse(options.body));return response({id:'fresh',dpi:300});
  }
  if(path==='/v1/flatbed/sessions')return response([{id:'old',submitted_at:null}]);
  if(path==='/v1/flatbed/sessions/old')return response({id:'old',dpi:300,regions:[{...region,id:'front',side:'front'},{...region,id:'back',side:'back'}],sheets:[],pairs:[{front_region_id:'front',back_region_id:'back',score:.9,confidence_level:'high',identity_status:'consistent',signals:{},conflicts:[],selected:true}]});
  if(path.includes('/capture/front')){captures.push(path);return response({id:'sheet',regions:[{...region,id:'front',side:'front'}],failures:[]});}
  if(path.endsWith('/pair'))return response({pairs:[{front_id:'front',back_id:'back',confidence:.9,confidence_level:'high',requires_review:false,conflicts:[],signals:{}}],missing_front:[],missing_back:[],requires_review:false});
  if(path.endsWith('/pairs'))return response({confirmation_token:'confirmed'});
  if(path.endsWith('/submit'))return response({job_ids:['job']});
  throw Error(`Unexpected request: ${path}`);
 };
 render(React.createElement(FlatbedBatchPanel));
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/恢复/));
 assert.equal(screen.queryByRole('button',{name:'开始下一批'}),null);
 fireEvent.click(screen.getAllByRole('button',{name:'正式扫描'})[0]);
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/正面已更新/),{timeout:2000});
 assert.deepEqual(captures,['/v1/flatbed/sessions/old/capture/front']);
 assert.equal(calls.length,0);
 fireEvent.click(screen.getByRole('button',{name:'确认配对并录入 1 张卡片'}));
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/已提交/));
 assert.equal(screen.queryByRole('button',{name:'确认配对并录入 1 张卡片'}),null);
 assert.equal(screen.getAllByRole('button',{name:'正式扫描'})[1].disabled,true);
 fireEvent.click(screen.getAllByRole('button',{name:'正式扫描'})[0]);
 await waitFor(()=>assert.equal(calls.length,1));
 assert.equal(calls[0].new_batch,true);
 await waitFor(()=>assert.match(screen.getByRole('status').textContent,/检测到 1 张正面/),{timeout:2000});
 assert.deepEqual(captures,['/v1/flatbed/sessions/old/capture/front','/v1/flatbed/sessions/fresh/capture/front']);
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
