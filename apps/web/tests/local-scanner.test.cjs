const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {getLocalScannerDevice,scanLocalScanner}=require('../lib/local-scanner.ts');
afterEach(()=>{delete global.fetch;});

test('local requests omit cloud credentials and capture returns exact image plus native metadata',async()=>{
 const calls=[];const metadata={actual_dpi:200,pixel_width:1700,pixel_height:2338,capture_kind:'preview',source:'image_capture_core_overview_resolution'};
 global.fetch=async(url,init)=>{calls.push({url,init});return calls.length===1?new Response(JSON.stringify({name:'Canon',serial:'device',token:'process-token'})):new Response(new Uint8Array([137,80,78,71]),{headers:{'Content-Type':'image/png','X-Scan-Resolution':JSON.stringify(metadata)}});};
 const device=await getLocalScannerDevice();const scan=await scanLocalScanner(300,true,device.token);
 assert.deepEqual(calls.map(c=>c.url),['http://127.0.0.1:8766/v1/device','http://127.0.0.1:8766/v1/scan']);
 assert.ok(calls.every(c=>c.init.credentials==='omit'));
 assert.equal(calls[1].init.headers['X-Scanner-Token'],'process-token');
 assert.deepEqual(JSON.parse(calls[1].init.body),{dpi:300,preview:true});
 assert.deepEqual([...new Uint8Array(await scan.file.arrayBuffer())],[137,80,78,71]);
 assert.deepEqual(scan.sourceResolution,metadata);
});

test('helper/network errors remain recoverable and never redirect to cloud native capture',async()=>{
 global.fetch=async()=>{throw new TypeError('Failed to fetch');};
 await assert.rejects(getLocalScannerDevice(),/本机.*助手|本机网络/);
 global.fetch=async()=>new Response(JSON.stringify({detail:'扫描仪正忙'}),{status:409});
 await assert.rejects(scanLocalScanner(600,false,'token'),/正忙/);
});
