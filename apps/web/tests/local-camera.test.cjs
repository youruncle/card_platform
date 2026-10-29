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
