const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {connectLocalCamera,captureLocalCamera}=require('../lib/local-camera.ts');
const originalFetch=global.fetch;afterEach(()=>global.fetch=originalFetch);
test('camera connects and captures only enumerated device without forwarding cloud credentials',async()=>{
 const calls=[];global.fetch=async(url,options)=>{calls.push({url,options});return url.endsWith('/devices')?Response.json({devices:[{serial:'redmi-a',name:'Redmi'}],token:'local-token'}):new Response(new Uint8Array([1,2]),{headers:{'Content-Type':'image/jpeg'}});};
 const connection=await connectLocalCamera();const file=await captureLocalCamera(connection,'redmi-a');
 assert.equal(file.type,'image/jpeg');assert.equal(file.size,2);
 assert.equal(calls[0].url,'http://127.0.0.1:8767/v1/devices');assert.equal(calls[1].options.credentials,'omit');assert.equal(calls[1].options.headers['X-Camera-Client'],'cardsp-web-v1');assert.equal(calls[1].options.headers['X-Camera-Token'],'local-token');assert.equal(calls[1].options.headers.Authorization,undefined);assert.deepEqual(JSON.parse(calls[1].options.body),{serial:'redmi-a'});
 await assert.rejects(captureLocalCamera(connection,'unknown'),/请选择已连接/);assert.equal(calls.length,2);
});
test('missing devices, unavailable helper, and non-image capture remain failures',async()=>{
 global.fetch=async()=>Response.json({devices:[],token:'token'});await assert.rejects(connectLocalCamera(),/没有已授权/);
 global.fetch=async()=>{throw new Error('offline');};await assert.rejects(connectLocalCamera(),/无法连接/);
 global.fetch=async()=>new Response('no new photo',{headers:{'Content-Type':'text/plain'}});await assert.rejects(captureLocalCamera({devices:[{serial:'a',name:'a'}],token:'t'},'a'),/未返回有效照片/);
});
test('camera control checks actual response and confines commands to an enumerated phone',async()=>{
 const {controlLocalCamera}=require('../lib/local-camera.ts');const connection={devices:[{serial:'r',name:'Redmi'}],token:'t'};const calls=[];
 global.fetch=async(url,options)=>{calls.push({url,options});return Response.json({zoom:1.6,presets:[.6,1,3],width:1080,height:1440,preview:'data:image/jpeg;base64,AA==',rectangles:[],message:'倍率已回读'});};
 const frame=await controlLocalCamera(connection,'r','zoom',1.6);assert.equal(frame.zoom,1.6);assert.deepEqual(JSON.parse(calls[0].options.body),{serial:'r',action:'zoom',zoom:1.6});assert.equal(calls[0].options.credentials,'omit');assert.equal(calls[0].options.headers.Authorization,undefined);
 await assert.rejects(controlLocalCamera(connection,'other','auto'),/请选择/);assert.equal(calls.length,1);
 global.fetch=async()=>Response.json({zoom:1.6,presets:[1],preview:'https://evil.example/image',rectangles:[],message:'ok'});await assert.rejects(controlLocalCamera(connection,'r','preview'),/预览|无效/);
 global.fetch=async()=>new Response('',{status:404});await assert.rejects(controlLocalCamera(connection,'r','preview'),/更新.*助手/);
});
