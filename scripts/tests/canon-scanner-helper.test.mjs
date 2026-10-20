import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, chmod, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import http from 'node:http';
import { createScannerHelper } from '../canon-scanner-helper.mjs';

async function fixture(t, body = '') {
  const root = await mkdtemp(join(tmpdir(), 'canon-test-'));
  const script = join(root, 'bridge');
  await writeFile(script, `#!${process.execPath}\nimport fs from 'node:fs';\nconst args=process.argv.slice(2);\nif(args[0]==='capabilities'){console.log(JSON.stringify({event:'capabilities',dpi:[300,600]}));process.exit(0);}\n${body || `const out=args[args.indexOf('--output')+1];fs.writeFileSync(out,Buffer.from('89504e470d0a1a0a','hex'));console.log(JSON.stringify({event:'scan_complete',path:out,source_resolution:{pixel_width:1700,pixel_height:2338,actual_dpi:200,capture_kind:'preview',source:'image_capture_core_overview_resolution'}}));`}`);
  await chmod(script, 0o700);
  const server = createScannerHelper({ bridgeScript: script, scanTimeoutMs: 1000 });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(root,{recursive:true,force:true}); });
  const headers = { Origin:'https://cardsp.cn','X-Scanner-Client':'cardsp-web-v1' };
  const device = () => fetch(`${url}/v1/device`, { headers });
  const scan = (token, payload={dpi:300,preview:true}) => fetch(`${url}/v1/scan`, {method:'POST',headers:{...headers,'Content-Type':'application/json','X-Scanner-Token':token},body:JSON.stringify(payload)});
  return {url, headers, device, scan};
}

test('allowed cloud origin gets native PNG and metadata; scan requires process token',async t=>{
  const f=await fixture(t);
  const device=await f.device();assert.equal(device.status,200);
  assert.equal(device.headers.get('access-control-allow-origin'),'https://cardsp.cn');
  const {token}=await device.json();assert.ok(token.length>=32);
  assert.equal((await f.scan('wrong')).status,403);
  const scan=await f.scan(token);assert.equal(scan.status,200);
  assert.equal(scan.headers.get('content-type'),'image/png');
  assert.equal(scan.headers.get('cache-control'),'no-store');
  assert.equal(JSON.parse(scan.headers.get('x-scan-resolution')).actual_dpi,200);
  assert.equal(Buffer.from(await scan.arrayBuffer()).toString('hex'),'89504e470d0a1a0a');
});

test('untrusted origin, missing client header and rebinding Host cannot reach device',async t=>{
  const f=await fixture(t);
  assert.equal((await fetch(`${f.url}/v1/device`,{headers:{...f.headers,Origin:'https://evil.example'}})).status,403);
  assert.equal((await fetch(`${f.url}/v1/device`,{headers:{Origin:'https://cardsp.cn'}})).status,403);
  assert.equal((await fetch(`${f.url}/v1/device`)).status,403);
  const status=await new Promise((resolve,reject)=>{const request=http.get(`${f.url}/v1/device`,{headers:{...f.headers,Host:'evil.example'}},response=>{response.resume();resolve(response.statusCode);});request.on('error',reject);});
  assert.equal(status,403);
});

test('private-network preflight permits only known origin, methods and headers',async t=>{
  const f=await fixture(t);
  const response=await fetch(`${f.url}/v1/scan`,{method:'OPTIONS',headers:{Origin:'https://cardsp.cn','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type,x-scanner-token,x-scanner-client','Access-Control-Request-Private-Network':'true'}});
  assert.equal(response.status,204);
  assert.equal(response.headers.get('access-control-allow-private-network'),'true');
  assert.equal((await fetch(`${f.url}/v1/scan`,{method:'OPTIONS',headers:{Origin:'https://evil.example','Access-Control-Request-Method':'POST'}})).status,403);
});

test('busy requests do not start another scan and timeout frees scanner',async t=>{
  const f=await fixture(t,"process.on('SIGINT',()=>process.exit(130));setInterval(()=>{},100);");
  const {token}=await (await f.device()).json();
  const first=f.scan(token);
  await new Promise(resolve=>setTimeout(resolve,100));
  assert.equal((await f.scan(token)).status,409);
  const timedOut=await first;assert.equal(timedOut.status,504);
  assert.equal((await f.device()).status,200);
});

test('request cannot choose output paths, arbitrary commands or unsupported DPI',async t=>{
  const f=await fixture(t);const {token}=await (await f.device()).json();
  for(const payload of [{dpi:4800,preview:false},{dpi:300,preview:'yes'},{dpi:300,preview:false,output:'/tmp/arbitrary.png'}]){
    assert.equal((await f.scan(token,payload)).status,422);
  }
});

test('disconnect interrupts an active scan and releases the device before timeout',async t=>{
  const f=await fixture(t,"process.on('SIGINT',()=>process.exit(130));setInterval(()=>{},100);");
  const {token}=await (await f.device()).json();
  const controller=new AbortController();
  const pending=fetch(`${f.url}/v1/scan`,{method:'POST',headers:{...f.headers,'Content-Type':'application/json','X-Scanner-Token':token},body:JSON.stringify({dpi:300,preview:false}),signal:controller.signal}).catch(()=>null);
  await new Promise(resolve=>setTimeout(resolve,100));controller.abort();await pending;
  let status;
  for(let attempt=0;attempt<10;attempt++){
    await new Promise(resolve=>setTimeout(resolve,40));status=(await f.device()).status;
    if(status===200)break;
  }
  assert.equal(status,200);
});
