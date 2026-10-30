import test from 'node:test';
import assert from 'node:assert/strict';
import{spawnSync}from'node:child_process';
import{mkdtempSync,writeFileSync,rmSync}from'node:fs';
import{tmpdir}from'node:os';
import{join}from'node:path';
import{createHmac}from'node:crypto';
test('native zoom refuses invalid limits and can only confirm a matching finite camera result',t=>{
 const root=mkdtempSync(join(tmpdir(),'cardsp-zoom-'));t.after(()=>rmSync(root,{recursive:true,force:true}));
 writeFileSync(join(root,'Check.java'),`import cn.cardsp.camera.ZoomPolicy; import cn.cardsp.camera.BridgeAuth; public class Check {public static void main(String[] a)throws Exception{String authPayload=new String(java.util.Base64.getDecoder().decode("eyJhY3Rpb24iOiJwcmV2aWV3In0="),java.nio.charset.StandardCharsets.UTF_8);if(!BridgeAuth.sign("test-usb-secret","request","0123456789abcdef",authPayload).equals("b5ffac013a116e54e6dd4624045c1c85c89761673fef7e050634e83daf9100e9"))throw new Error();if(ZoomPolicy.cameraScore(false,10,8,6)>=0||ZoomPolicy.cameraScore(true,0,10,10)>=ZoomPolicy.cameraScore(true,10,4,3)||ZoomPolicy.cameraScore(true,10,4,3)>=ZoomPolicy.cameraScore(true,10,8,6))throw new Error();float[] focus=ZoomPolicy.meteringPoint(.2,.3,90);if(Math.abs(focus[0]-.3)>.001||Math.abs(focus[1]-.8)>.001)throw new Error();focus=ZoomPolicy.meteringPoint(.2,.3,180);if(Math.abs(focus[0]-.8)>.001||Math.abs(focus[1]-.7)>.001)throw new Error();if(ZoomPolicy.validate(1.6,1,3)!=1.6f)throw new Error();for(double n:new double[]{0.6,4,Double.NaN,Double.POSITIVE_INFINITY}){try{ZoomPolicy.validate(n,1,3);throw new Error();}catch(IllegalArgumentException good){}}if(ZoomPolicy.previewReady(100,99)||!ZoomPolicy.previewReady(100,101)||ZoomPolicy.previewReady(-1,101))throw new Error();if(ZoomPolicy.same(1.4,1.6)||ZoomPolicy.same(Double.NaN,1.6)||!ZoomPolicy.same(1.60001,1.6))throw new Error();try{ZoomPolicy.requestId("bad");throw new Error();}catch(IllegalArgumentException good){}System.out.println("ok");}}`);
 const compile=spawnSync('/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/javac',['-d',root,'tools/android-camera/src/cn/cardsp/camera/ZoomPolicy.java','tools/android-camera/src/cn/cardsp/camera/BridgeAuth.java',join(root,'Check.java')],{encoding:'utf8'});assert.equal(compile.status,0,compile.stderr);
 const result=spawnSync('/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java',['-cp',root,'Check'],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);assert.equal(result.stdout.trim(),'ok');
});
test('camera bridge only accepts matching nonce and returns actual native zoom plus private preview',async()=>{
 const {validateBridgeResponse}=await import('../android-camera-native.mjs');
 const id='0123456789abcdef';assert.throws(()=>validateBridgeResponse({id:'wrong',zoom:1.6},id),/标识|响应/);assert.throws(()=>validateBridgeResponse({id,ok:false,error:'camera denied'},id),/camera denied/);
 assert.equal(validateBridgeResponse({id,ok:true,zoom:1.6,minZoom:1,maxZoom:3},id).zoom,1.6);
});
test('USB channel bounds replies, validates nonce and aborts an outstanding command',async t=>{
 const {createServer}=await import('node:net');const {exchangeNativeCamera}=await import('../android-camera-native.mjs');
 const sockets=new Set();let mode='ok';const server=createServer(socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));socket.on('data',data=>{const request=JSON.parse(String(data));if(mode==='hang')return;const id=mode==='wrong'?'different':request.id,body=JSON.stringify({ok:true,zoom:1.6,minZoom:1,maxZoom:3});assert.equal('token' in request,false);assert.equal(request.auth,createHmac('sha256','private-usb-token').update('request\n'+request.id+'\n'+request.payload).digest('hex'));socket.end(JSON.stringify({id,payload:body,auth:createHmac('sha256','private-usb-token').update('response\n'+id+'\n'+body).digest('hex')})+'\n');});});await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(async()=>{for(const s of sockets)s.destroy();await new Promise(r=>server.close(r));});
 const payload={id:'0123456789abcdef',token:'private-usb-token',action:'state'};assert.equal((await exchangeNativeCamera(server.address().port,payload)).zoom,1.6);mode='wrong';await assert.rejects(exchangeNativeCamera(server.address().port,payload),/标识/);mode='hang';const controller=new AbortController(),pending=exchangeNativeCamera(server.address().port,payload,{signal:controller.signal});controller.abort();await assert.rejects(pending);
});
test('cancellation immediately after ADB forward creation still removes the allocated port',async()=>{
 const {withNativeCamera}=await import('../android-camera-native.mjs');const controller=new AbortController();let present=false,removed=0;
 const onDevice=async args=>{if(args.includes('pm'))return 'package:cn.cardsp.camera\n';if(args.includes('am'))return 'Status: ok';if(args[0]==='forward'){present=true;controller.abort();throw new Error('cancelled after forward allocated');}throw Error(JSON.stringify(args));};
 const cleanup=async args=>{if(args.includes('--list'))return present?'redmi tcp:12345 localabstract:cardsp-camera-v1\n':'';if(args.includes('--remove')){present=false;removed++;return '';}if(args[0]==='forward'){present=true;controller.abort();return '12345';}throw Error(JSON.stringify(args));};
 await assert.rejects(withNativeCamera(onDevice,async()=>{}, {signal:controller.signal,cleanupDevice:cleanup,serial:'redmi'}));assert.equal(present,false);assert.equal(removed,1);
});
test('an impersonating service never receives the USB key and cannot forge camera output',async t=>{
 const {createServer}=await import('node:net');const {exchangeNativeCamera}=await import('../android-camera-native.mjs');let seen;
 const server=createServer(socket=>socket.on('data',data=>{seen=JSON.parse(String(data));socket.end(JSON.stringify({id:seen.id,ok:true,zoom:1.6,minZoom:1,maxZoom:3})+'\n');}));await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 await assert.rejects(exchangeNativeCamera(server.address().port,{id:'0123456789abcdef',token:'secret-key-never-on-socket',action:'preview'}),/身份|认证|响应/);assert.equal('token' in seen,false);assert.equal(JSON.stringify(seen).includes('secret-key-never-on-socket'),false);
});
test('unknown forward creation outcome recovers only newly added ports on the same device and endpoint',async()=>{
 const {withNativeCamera}=await import('../android-camera-native.mjs');let created=false;const removed=[];
 const onDevice=async args=>args.includes('pm')?'package:cn.cardsp.camera\n':'Status: ok';
 const cleanup=async args=>{if(args.includes('--list'))return 'redmi tcp:11111 localabstract:cardsp-camera-v1\nother tcp:9999 localabstract:cardsp-camera-v1\n'+(created?'redmi tcp:12345 localabstract:cardsp-camera-v1\nredmi tcp:54321 localabstract:other-service\n':'');if(args.includes('--remove')){removed.push(args.at(-1));return '';}created=true;throw new Error('adb timeout after allocation');};
 await assert.rejects(withNativeCamera(onDevice,async()=>{}, {cleanupDevice:cleanup,serial:'redmi'}));assert.deepEqual(removed,['tcp:12345']);
});
test('component launch authenticates readiness before any zoom or shutter and retries only the readiness check',async t=>{
 const {createServer}=await import('node:net');const {withNativeCamera}=await import('../android-camera-native.mjs');let token,requests=0,operations=0;const actions=[];
 const server=createServer(socket=>socket.on('data',data=>{const request=JSON.parse(String(data)),body=JSON.parse(request.payload);actions.push(body.action);const payload=JSON.stringify({ok:true,zoom:1.6,minZoom:1,maxZoom:3}),auth=++requests<3?'0'.repeat(64):createHmac('sha256',token).update('response\n'+request.id+'\n'+payload).digest('hex');socket.end(JSON.stringify({id:request.id,payload,auth})+'\n');}));await new Promise(r=>server.listen(0,'127.0.0.1',r));t.after(()=>new Promise(r=>server.close(r)));
 const device=async args=>{if(args.includes('pm'))return 'package:cn.cardsp.camera\n';token=args.at(-1);return 'Status: ok';};const cleanup=async args=>args.includes('--list')?'':args.includes('--remove')?'':String(server.address().port);
 const result=await withNativeCamera(device,async rpc=>{operations++;return rpc('zoom',1.6);},{serial:'redmi',cleanupDevice:cleanup});assert.equal(result.zoom,1.6);assert.equal(operations,1);assert.deepEqual(actions,['state','state','state','zoom']);
});
test('focus confirmation is accepted only with a preview from the locked-focus frame or later',async()=>{
 const {validateBridgeResponse}=await import('../android-camera-native.mjs'),id='0123456789abcdef';const base={id,ok:true,zoom:1.4,minZoom:1,maxZoom:3,focusConfirmed:true,focusTimestamp:'1000'};
 assert.throws(()=>validateBridgeResponse({...base,previewTimestamp:'999'},id),/取景|对焦|帧/);
 assert.throws(()=>validateBridgeResponse(base,id),/取景|对焦|帧/);
 assert.equal(validateBridgeResponse({...base,previewTimestamp:'1001'},id).zoom,1.4);
});
test('each requested photo focuses in the same authenticated session before exactly one shutter',async()=>{
 const {prepareNativeShot}=await import('../android-camera-native.mjs');const calls=[],image=Buffer.from('ffd8ffe000104a46494600010100000100010000ffd9','hex').toString('base64');
 const rpc=async(action,value)=>{calls.push({action,value});if(action==='preview')return {image,width:640,height:480,zoom:1.4};if(action==='focus')return {focusConfirmed:true};return {image,photoTimestamp:'1234'};};
 const result=await prepareNativeShot(rpc,{frameProcessor:async()=>({rectangles:[{x:.2,y:.2,width:.4,height:.5,confidence:1,document:1}]})});assert.equal(result.photoTimestamp,'1234');assert.deepEqual(calls.map(v=>v.action),['preview','focus','capture']);assert.deepEqual(calls[1].value,{x:.4,y:.45});
 calls.length=0;await assert.rejects(prepareNativeShot(async action=>{calls.push({action});return action==='preview'?{image,width:640,height:480,zoom:1.4}:{focusConfirmed:false};},{frameProcessor:async()=>({rectangles:[]})}),/对焦/);assert.equal(calls.some(v=>v.action==='capture'),false);
});
