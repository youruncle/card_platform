const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const dom=new JSDOM('<html><body></body></html>');
global.window=dom.window;global.document=dom.window.document;
Object.defineProperty(global,'navigator',{value:dom.window.navigator,configurable:true});
global.HTMLElement=dom.window.HTMLElement;global.CustomEvent=dom.window.CustomEvent;global.React=require('react');
const {render,screen,fireEvent,cleanup,waitFor,within}=require('@testing-library/react');
const {AuctionEditor}=require('../../../packages/card-core/src/auction.ts');
const {AuctionDraftPanel}=require('../components/auction-draft-panel.tsx');
const originalFetch=global.fetch;
afterEach(()=>{cleanup();global.fetch=originalFetch;});
function draft(overrides={}) {return {id:'draft-a',item_id:'card-a',revision:1,status:'draft',title:'原草稿',description:'原描述',condition_notes:'',selected_photo_ids:['front'],photos:[{id:'front',side:'front',source:'existing',url:'/v1/auction/drafts/draft-a/images/front',sha256:'abc'},{id:'back',side:'back',source:'existing',url:'/v1/auction/drafts/draft-a/images/back',sha256:'def'}],identity:{},generation_basis:{},external:{supported:false,status:'unavailable',reason:'未核实独立草稿接口'},updated_at:'2026-10-30',...overrides};}
test('open restores text; selected photos and condition are sent for generation and platform save',async()=>{
 let server=draft(),calls=[];
 const request=async(path,init={})=>{const body=init.body?JSON.parse(init.body):null;calls.push({path,body});if(path.endsWith('/generate'))server={...server,revision:2,title:'生成标题',description:'生成描述',selected_photo_ids:body.selected_photo_ids,condition_notes:body.condition_notes};if(init.method==='PATCH')server={...server,...body,revision:3};return server;};
 render(React.createElement(AuctionDraftPanel,{itemId:'card-a',onClose:()=>{},request}));
 await waitFor(()=>assert.equal(screen.getByLabelText('标题').value,'原草稿'));
 assert.match(screen.getByText(/卡淘草稿未接入/).textContent,/未接入/);
 assert.equal(screen.queryByRole('button',{name:/发布|上架|保存到卡淘/}),null);
 fireEvent.click(screen.getByLabelText('选择背面照片 2'));
 fireEvent.change(screen.getByLabelText('品相备注'),{target:{value:'背面边角请见图'}});
 fireEvent.click(screen.getByRole('button',{name:'生成文案'}));
 await waitFor(()=>assert.equal(screen.getByLabelText('标题').value,'生成标题'));
 assert.deepEqual(calls.find(c=>c.path.endsWith('/generate')).body,{revision:1,selected_photo_ids:['front','back'],condition_notes:'背面边角请见图',use_order_examples:true,automatic:false});
 fireEvent.change(screen.getByLabelText('标题'),{target:{value:'我编辑的标题'}});
 fireEvent.click(screen.getByRole('button',{name:'保存平台草稿'}));
 await waitFor(()=>assert.ok(screen.getByText('平台草稿已保存')));
 assert.equal(calls.at(-1).body.title,'我编辑的标题');
});
test('upload uses returned revision for automatic generation and keeps uploaded photo on generation failure',async()=>{
 let generated=false;const file=new File(['image'],'new.jpg',{type:'image/jpeg'});
 const editor=new AuctionEditor(async(path,init={})=>{if(path.endsWith('/images')) {assert.equal(init.body.get('image').name,'new.jpg');assert.equal(init.body.get('revision'),'1');return draft({revision:2,selected_photo_ids:['front','new'],photos:[...draft().photos,{id:'new',side:'detail',source:'upload',url:'/v1/new',sha256:'new'}]});}if(path.endsWith('/generate')){generated=true;assert.equal(JSON.parse(init.body).revision,2);assert.deepEqual(JSON.parse(init.body).selected_photo_ids,['front','new']);throw new Error('订单网络错误');}return draft();});
 await editor.open('card-a');editor.edit({title:'手工标题'});await editor.add(file,'detail','upload');
 assert.equal(generated,true);assert.equal(editor.state.draft.photos.length,3);assert.equal(editor.state.title,'手工标题');assert.equal(editor.state.pendingFile,null);assert.match(editor.state.error,/订单网络错误/);editor.dispose();
});
test('save conflict preserves user edits and explicit refresh keeps local text',async()=>{
 let conflict=false;
 const editor=new AuctionEditor(async(path,init={})=>{if(init.method==='PATCH'){conflict=true;throw Object.assign(new Error('旧版本'),{status:409});}return draft({revision:conflict?8:1,title:conflict?'另一端标题':'原草稿'});});
 await editor.open('card-a');editor.edit({title:'我的未保存标题',condition_notes:'我的备注'});await editor.save();
 assert.match(editor.state.error,/另一|冲突/);assert.equal(editor.state.title,'我的未保存标题');await editor.refresh();assert.equal(editor.state.title,'我的未保存标题');assert.equal(editor.state.draft.revision,8);editor.dispose();
});
test('failed upload keeps file and text; duplicate clicks cannot upload concurrently',async()=>{
 let finish,uploads=0;
 const editor=new AuctionEditor(async(path)=>{if(path.endsWith('/images')) {uploads++;await new Promise(r=>finish=r);throw new Error('断网');}return draft();});
 await editor.open('card-a');editor.edit({description:'请看实拍'});const file=new File(['image'],'a.jpg',{type:'image/jpeg'});const pending=editor.add(file,'front','camera');await editor.add(file,'front','camera');assert.equal(uploads,1);finish();await pending;assert.equal(editor.state.pendingFile,file);assert.equal(editor.state.description,'请看实拍');editor.dispose();
});
test('no selection blocks generation without a network request',async()=>{let calls=0;const editor=new AuctionEditor(async()=>{calls++;return draft();});await editor.open('card-a');editor.select('front');await editor.generate();assert.equal(calls,1);assert.match(editor.state.error,/至少/);editor.dispose();});
test('retry after automatic generation failure preserves manual text instead of replacing edits',async()=>{
 let fail=true;
 const editor=new AuctionEditor(async(path)=>{if(path.endsWith('/images'))return draft({revision:2});if(path.endsWith('/generate')) {if(fail){fail=false;throw new Error('生成失败');}return draft({revision:3,title:'我写的标题',description:'我写的描述'});}return draft();});
 await editor.open('card-a');editor.edit({title:'我写的标题',description:'我写的描述'});await editor.add(new File(['x'],'a.jpg',{type:'image/jpeg'}),'detail','upload');await editor.generate();assert.equal(editor.state.title,'我写的标题');assert.equal(editor.state.description,'我写的描述');editor.dispose();
});
test('disposing an editor prevents a delayed server response from replacing current state',async()=>{
 let finish;const editor=new AuctionEditor(()=>new Promise(resolve=>finish=resolve));const pending=editor.open('card-a');editor.dispose();finish(draft());await pending;assert.equal(editor.state.draft,null);assert.equal(editor.state.busy,false);
});
test('automatic photo generation preserves saved manual copy after reopening and persists unsaved overrides',async()=>{
 let server=draft(),generated=[];
 const request=async(path,init={})=>{
   if(init.method==='PATCH'){const body=JSON.parse(init.body);server={...server,...body,revision:server.revision+1,generation_basis:{manual_fields:['title','description']}};}
   if(path.endsWith('/images'))server={...server,revision:server.revision+1};
   if(path.endsWith('/generate')){const body=JSON.parse(init.body);generated.push(body);server={...server,revision:server.revision+1,title:body.automatic?(body.edited_title??(server.generation_basis.manual_fields?.includes('title')?server.title:'模板标题')):'模板标题',description:body.automatic?(body.edited_description??(server.generation_basis.manual_fields?.includes('description')?server.description:'模板描述')):'模板描述'};}
   return structuredClone(server);
 };
 let editor=new AuctionEditor(request);await editor.open('card-a');editor.edit({title:'已保存的手工标题',description:'已保存的手工描述'});await editor.save();editor.dispose();
 editor=new AuctionEditor(request);await editor.open('card-a');await editor.add(new File(['a'],'saved.jpg',{type:'image/jpeg'}),'front','upload');
 assert.equal(generated[0].automatic,true);assert.equal('edited_title' in generated[0],false);assert.equal(editor.state.title,'已保存的手工标题');assert.equal(editor.state.description,'已保存的手工描述');editor.dispose();
 editor=new AuctionEditor(request);await editor.open('card-a');assert.equal(editor.state.title,'已保存的手工标题');editor.edit({title:'尚未点保存的标题',description:'尚未点保存的描述'});await editor.add(new File(['b'],'unsaved.jpg',{type:'image/jpeg'}),'back','upload');
 assert.equal(generated[1].automatic,true);assert.equal(generated[1].edited_title,'尚未点保存的标题');assert.equal(generated[1].edited_description,'尚未点保存的描述');assert.equal(editor.state.draft.title,'尚未点保存的标题');assert.equal(editor.state.draft.description,'尚未点保存的描述');editor.dispose();
 editor=new AuctionEditor(request);await editor.open('card-a');assert.equal(editor.state.title,'尚未点保存的标题');assert.equal(editor.state.description,'尚未点保存的描述');await editor.generate();assert.equal(generated[2].automatic,false);assert.equal('edited_title' in generated[2],false);assert.equal(editor.state.title,'模板标题');editor.dispose();
});
test('an upload retry reuses its request UUID after a lost committed response and generates once from replay revision',async()=>{
 const keys=[],committed=new Map();let server=draft(),loseResponse=true,generations=0;
 const request=async(path,init={})=>{
   if(path.endsWith('/images')){
     const key=init.body.get('request_id');keys.push(key);
     if(!committed.has(key)){const photo={id:`new-${committed.size}`,side:init.body.get('side'),source:init.body.get('source'),url:'/v1/new',sha256:`new-${committed.size}`};server={...server,revision:server.revision+1,photos:[...server.photos,photo],selected_photo_ids:[...server.selected_photo_ids,photo.id]};committed.set(key,server);}
     if(loseResponse){loseResponse=false;throw new Error('响应中途断开');}
     return structuredClone(committed.get(key));
   }
   if(path.endsWith('/generate')){generations++;const body=JSON.parse(init.body);assert.equal(body.revision,server.revision);assert.equal(body.automatic,true);server={...server,revision:server.revision+1};}
   return structuredClone(server);
 };
 const editor=new AuctionEditor(request);await editor.open('card-a');const file=new File(['one'],'one.jpg',{type:'image/jpeg'});await editor.add(file,'detail','upload');assert.match(keys[0],/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);assert.equal(editor.state.pendingFile,file);assert.equal(generations,0);await editor.add(editor.state.pendingFile,editor.state.pendingSide,editor.state.pendingSource);
 assert.equal(keys[0],keys[1]);assert.equal(committed.size,1);assert.equal(editor.state.draft.photos.length,3);assert.equal(editor.state.pendingFile,null);assert.equal(generations,1);
 await editor.add(new File(['two'],'two.jpg',{type:'image/jpeg'}),'front','upload');assert.notEqual(keys[1],keys[2]);assert.equal(committed.size,2);editor.dispose();
});
test('conflict refresh merges untouched remote fields while exposing concurrent edits to the same field',async()=>{
 let server=draft();
 const request=async(path,init={})=>{if(init.method==='PATCH'){const body=JSON.parse(init.body);if(body.revision!==server.revision)throw Object.assign(new Error('conflict'),{status:409});server={...server,...body,revision:server.revision+1};}return structuredClone(server);};
 const editor=new AuctionEditor(request);await editor.open('card-a');editor.edit({title:'本页标题'});
 server=draft({revision:2,title:'另一端标题',description:'另一端描述',condition_notes:'另一端品相备注'});
 await editor.save();await editor.refresh();assert.equal(editor.state.title,'本页标题');assert.equal(editor.state.description,'另一端描述');assert.equal(editor.state.condition_notes,'另一端品相备注');assert.equal(editor.state.latestConflicts.title,'另一端标题');assert.equal(editor.state.latestConflicts.description,undefined);
 await editor.save();assert.equal(server.title,'本页标题');assert.equal(server.description,'另一端描述');assert.equal(server.condition_notes,'另一端品相备注');editor.dispose();
});
test('conflict refresh removes unavailable local photo ids and merges newly selected remote uploads',async()=>{
 let server=draft();const editor=new AuctionEditor(async()=>structuredClone(server));await editor.open('card-a');editor.select('front');editor.select('back');
 server=draft({revision:2,photos:[draft().photos[0],{id:'new',side:'detail',source:'upload',url:'/v1/new',sha256:'new'}],selected_photo_ids:['front','new']});
 await editor.refresh();assert.deepEqual(editor.state.selected_photo_ids,['new']);editor.dispose();
});
test('the editor visibly shows the latest conflicting server title for comparison before save',async()=>{
 let server=draft();const request=async(path,init={})=>{if(init.method==='PATCH'){server=draft({revision:2,title:'其他窗口标题',description:'其他窗口描述'});throw Object.assign(new Error('conflict'),{status:409});}return server;};
 render(React.createElement(AuctionDraftPanel,{itemId:'card-a',onClose:()=>{},request}));await waitFor(()=>assert.equal(screen.getByLabelText('标题').value,'原草稿'));fireEvent.change(screen.getByLabelText('标题'),{target:{value:'本页标题'}});fireEvent.click(screen.getByRole('button',{name:'保存平台草稿'}));await screen.findByRole('button',{name:'读取最新版本并保留本页输入'});fireEvent.click(screen.getByRole('button',{name:'读取最新版本并保留本页输入'}));await screen.findByText('其他窗口标题');assert.equal(screen.getByLabelText('标题').value,'本页标题');assert.equal(screen.getByLabelText('描述').value,'其他窗口描述');
});
test('USB connect immediately shows pending feedback and an empty-device error beside its controls',async()=>{
 let finish;
 global.fetch=async(url)=>{if(String(url)==='http://127.0.0.1:8767/v1/devices')return new Promise(resolve=>{finish=resolve;});throw new Error('fixture image unavailable');};
 render(React.createElement(AuctionDraftPanel,{itemId:'card-a',onClose:()=>{},request:async()=>draft()}));
 await screen.findByRole('button',{name:'连接 USB 手机'});
 const region=screen.getByRole('region',{name:'USB 手机拍照'}),usb=within(region);
 fireEvent.click(usb.getByRole('button',{name:'连接 USB 手机'}));
 assert.equal(usb.getByRole('button',{name:'正在查找手机…'}).disabled,true);
 assert.match(usb.getByRole('status').textContent,/正在查找手机/);
 finish(Response.json({devices:[],token:'local-token'}));
 const error=await usb.findByRole('alert');assert.match(error.textContent,/没有已授权的手机/);assert.match(error.textContent,/USB.*调试/);
 assert.equal(usb.getByRole('button',{name:'连接 USB 手机'}).disabled,false);assert.equal(usb.queryByRole('button',{name:'触发 Redmi 快门'}),null);
});
test('USB success explains the shutter step, capture reports local progress, and failed reconnect clears the old device',async()=>{
 let devices=0,finishCapture;
 global.fetch=async(url)=>{
   if(String(url)==='http://127.0.0.1:8767/v1/devices')return Response.json(++devices===1?{devices:[{serial:'redmi-a',name:'Redmi Note'}],token:'local-token'}:{devices:[],token:'local-token'});
   if(String(url)==='http://127.0.0.1:8767/v1/capture')return new Promise(resolve=>{finishCapture=resolve;});
   throw new Error('fixture image unavailable');
 };
 render(React.createElement(AuctionDraftPanel,{itemId:'card-a',onClose:()=>{},request:async()=>draft()}));
 await screen.findByRole('button',{name:'连接 USB 手机'});
 const usb=within(screen.getByRole('region',{name:'USB 手机拍照'}));
 fireEvent.click(usb.getByRole('button',{name:'连接 USB 手机'}));
 await usb.findByRole('button',{name:'触发 Redmi 快门'});
 assert.match(usb.getByRole('status').textContent,/已连接.*Redmi Note/);assert.match(usb.getByRole('status').textContent,/点击.*触发 Redmi 快门/);
 fireEvent.click(usb.getByRole('button',{name:'触发 Redmi 快门'}));
 assert.equal(usb.getByRole('button',{name:'正在拍照…'}).disabled,true);assert.match(usb.getByRole('status').textContent,/正在拍照/);
 finishCapture(Response.json({detail:'手机锁屏，请解锁后重试'},{status:409}));
 await usb.findByRole('alert');assert.match(usb.getByRole('alert').textContent,/手机锁屏/);
 assert.equal(usb.getByRole('button',{name:'触发 Redmi 快门'}).disabled,false);
 fireEvent.click(usb.getByRole('button',{name:'连接 USB 手机'}));
 await waitFor(()=>assert.match(usb.getByRole('alert').textContent,/没有已授权的手机/));
 assert.equal(usb.queryByRole('button',{name:'触发 Redmi 快门'}),null);assert.equal(usb.queryByLabelText('拍照手机'),null);assert.equal(usb.queryByText(/已连接.*Redmi Note/),null);
});
test('camera preview and manual/automatic zoom read actual values without uploading a photo',async()=>{
 const ops=[],cloud=[];let zoom=1;
 global.fetch=async(url,init={})=>{
  if(String(url).endsWith('/v1/devices'))return Response.json({devices:[{serial:'redmi-a',name:'Redmi'}],token:'t'});
  if(String(url).endsWith('/v1/camera')){const body=JSON.parse(init.body);ops.push(body);if(body.action==='zoom')zoom=body.zoom;if(body.action==='auto')zoom=1.5;return Response.json({zoom,presets:[.6,1,3],width:1080,height:1440,preview:'data:image/jpeg;base64,AA==',rectangles:[],message:body.action==='auto'?'自动调整完成':'取景已刷新'});}
  throw Error('image unavailable');
 };
 render(React.createElement(AuctionDraftPanel,{itemId:'card-a',onClose:()=>{},request:async(path)=>{cloud.push(path);return draft();}}));await screen.findByRole('button',{name:'连接 USB 手机'});fireEvent.click(screen.getByRole('button',{name:'连接 USB 手机'}));await screen.findByRole('button',{name:'刷新取景'});
 fireEvent.click(screen.getByRole('button',{name:'刷新取景'}));await screen.findByAltText('Redmi 当前取景');assert.match(screen.getByLabelText('手机实际倍率').textContent,/1.0/);
 fireEvent.change(screen.getByLabelText('目标缩放倍率'),{target:{value:'1.6'}});fireEvent.click(screen.getByRole('button',{name:'应用缩放'}));await waitFor(()=>assert.match(screen.getByLabelText('手机实际倍率').textContent,/1.6/));
 fireEvent.click(screen.getByRole('button',{name:'自动调整'}));await waitFor(()=>assert.match(screen.getByLabelText('手机实际倍率').textContent,/1.5/));assert.deepEqual(ops.map(o=>o.action),['preview','zoom','auto']);assert.equal(cloud.some(p=>p.endsWith('/images')),false);
 fireEvent.click(screen.getByRole('button',{name:'触发 Redmi 快门'}));await screen.findByRole('alert');await waitFor(()=>assert.equal(screen.queryAllByAltText('Redmi 当前取景').length,0));
 fireEvent.click(screen.getByRole('button',{name:'连接 USB 手机'}));await waitFor(()=>assert.equal(screen.queryAllByAltText('Redmi 当前取景').length,0));
});
test('pending zoom disables shutter, and unmount aborts the local control request',async()=>{
 let operationSignal,finish;
 global.fetch=async(url,init={})=>{
  if(String(url).endsWith('/v1/devices'))return Response.json({devices:[{serial:'redmi-a',name:'Redmi'}],token:'t'});
  if(String(url).endsWith('/v1/camera')){operationSignal=init.signal;return new Promise(resolve=>finish=resolve);}
  throw Error('image unavailable');
 };
 const view=render(React.createElement(AuctionDraftPanel,{itemId:'card-a',onClose:()=>{},request:async()=>draft()}));await screen.findByRole('button',{name:'连接 USB 手机'});fireEvent.click(screen.getByRole('button',{name:'连接 USB 手机'}));await screen.findByRole('button',{name:'自动调整'});fireEvent.click(screen.getByRole('button',{name:'自动调整'}));
 assert.equal(screen.getByRole('button',{name:'触发 Redmi 快门'}).disabled,true);assert.equal(screen.getByRole('button',{name:'正在自动调整…'}).disabled,true);
 view.unmount();assert.equal(operationSignal.aborted,true);finish(Response.json({zoom:1,presets:[1],width:1080,height:1440,preview:'data:image/jpeg;base64,AA==',rectangles:[],message:'ok'}));
});

test('auction photos open a full-size gallery without changing selection; Escape returns to the draft',async()=>{
 const {GlobalImageViewer}=require('../components/image-viewer.tsx');
 let closes=0;
 global.fetch=async()=>new Response(new Blob(['fixture'],{type:'image/jpeg'}));
 render(React.createElement(React.Fragment,null,
  React.createElement(AuctionDraftPanel,{itemId:'card-a',onClose:()=>{closes++;},request:async()=>draft()}),
  React.createElement(GlobalImageViewer)));
 const thumbnail=await screen.findByRole('button',{name:'放大查看正面照片 1'});
 thumbnail.focus();fireEvent.click(thumbnail);
 const viewer=await screen.findByRole('dialog',{name:'正面草稿照片 1'});
 assert.equal(document.activeElement,viewer);
 fireEvent.keyDown(viewer,{key:'Tab',shiftKey:true});
 assert.equal(document.activeElement,within(viewer).getByRole('button',{name:'查看下一张图片'}));
 fireEvent.keyDown(document.activeElement,{key:'Tab'});
 assert.equal(document.activeElement,within(viewer).getByRole('button',{name:'缩小'}));
 assert.equal(within(viewer).getByRole('img').getAttribute('src'),'/v1/auction/drafts/draft-a/images/front');
 assert.equal(screen.getByLabelText('选择正面照片 1').checked,true);
 fireEvent.click(within(viewer).getByRole('button',{name:'放大'}));
 assert.equal(within(viewer).getByText('125%').textContent,'125%');
 fireEvent.click(within(viewer).getByRole('button',{name:'查看下一张图片'}));
 const back=screen.getByRole('dialog',{name:'背面草稿照片 2'});
 assert.equal(within(back).getByRole('img').getAttribute('src'),'/v1/auction/drafts/draft-a/images/back');
 assert.equal(screen.getByLabelText('选择背面照片 2').checked,false);
 fireEvent.keyDown(document.activeElement,{key:'Escape'});
 await waitFor(()=>assert.equal(screen.queryByRole('dialog',{name:'背面草稿照片 2'}),null));
 assert.equal(closes,0);assert.equal(document.activeElement,thumbnail);
 fireEvent.click(screen.getByLabelText('选择背面照片 2'));
 assert.equal(screen.getByLabelText('选择背面照片 2').checked,true);
 assert.equal(screen.getAllByRole('dialog').length,1);
 fireEvent.click(thumbnail);
 await screen.findByRole('dialog',{name:'正面草稿照片 1'});
 fireEvent.click(screen.getByRole('button',{name:'关闭图片查看器'}));
 assert.equal(closes,0);assert.ok(screen.getByRole('dialog',{name:'单卡拍卖草稿'}));
});
