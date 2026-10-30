import {connect} from 'node:net';
import {randomBytes,randomUUID,createHmac,timingSafeEqual} from 'node:crypto';
import {processCameraFrame,planAutoZoom,verifyAutoFrame} from './android-camera-controls.mjs';
import {setTimeout as delay} from 'node:timers/promises';
const fail=message=>Object.assign(new Error(message),{status:409});
export function validateBridgeResponse(response,id){
 if(!response||response.id!==id)throw fail('手机相机响应标识不匹配，请重新连接。');
 if(response.ok!==true)throw fail(typeof response.error==='string'?response.error:'手机相机操作失败。');
 if(!Number.isFinite(response.zoom)||response.zoom<=0||!Number.isFinite(response.minZoom)||!Number.isFinite(response.maxZoom)||response.minZoom<1||response.maxZoom>3||response.maxZoom<response.minZoom)throw fail('手机相机倍率响应无效。');
 if(response.focusConfirmed===true){
  if(typeof response.focusTimestamp!=='string'||typeof response.previewTimestamp!=='string'||!/^\d{1,20}$/.test(response.focusTimestamp)||!/^\d{1,20}$/.test(response.previewTimestamp)||BigInt(response.focusTimestamp)<=0n||BigInt(response.previewTimestamp)<BigInt(response.focusTimestamp))throw fail('对焦已锁定，但取景尚未显示对应的新帧，请刷新取景。');
 }
 return response;
}
export function exchangeNativeCamera(port,payload,{signal,timeoutMs=25000}={}){return new Promise((resolve,reject)=>{
 signal?.throwIfAborted();const socket=connect({host:'127.0.0.1',port});let chunks=[],size=0,done=false;
 const finish=(error,value)=>{if(done)return;done=true;signal?.removeEventListener('abort',abort);socket.destroy();error?reject(error):resolve(value);};
 const body=JSON.stringify({action:payload.action,...(payload.action==='zoom'?{zoom:payload.zoom}:payload.action==='focus'?{point:payload.point}:{})});
 const sign=(domain,id,text)=>createHmac('sha256',payload.token).update(domain+'\n'+id+'\n'+text).digest('hex');
 const wire={id:payload.id,payload:body,auth:sign('request',payload.id,body)};
 const abort=()=>finish(signal.reason||fail('相机操作已取消。'));signal?.addEventListener('abort',abort,{once:true});socket.setTimeout(timeoutMs,()=>finish(fail('专用相机响应超时，请保持手机解锁并确认相机权限。')));
 socket.on('error',()=>finish(fail('无法连接专用相机，请重新连接 USB 并打开拍照组件。')));socket.on('end',()=>{if(!done)finish(fail('专用相机连接中断。'));});
 socket.on('connect',()=>socket.write(JSON.stringify(wire)+'\n'));
 socket.on('data',chunk=>{size+=chunk.length;if(size>36*1024*1024)return finish(fail('手机照片超过传输限制。'));chunks.push(chunk);if(chunk.includes(10)){try{const all=Buffer.concat(chunks);const response=JSON.parse(all.subarray(0,all.indexOf(10)).toString());if(response.id!==payload.id)throw fail('手机相机响应标识不匹配。');if(typeof response.payload!=='string'||typeof response.auth!=='string'||! /^[a-f0-9]{64}$/.test(response.auth)||!timingSafeEqual(Buffer.from(response.auth),Buffer.from(sign('response',payload.id,response.payload))))throw Object.assign(fail('手机相机服务身份认证失败，请重新启动组件后连接。'),{code:'CAMERA_AUTH_NOT_READY'});finish(null,validateBridgeResponse({...JSON.parse(response.payload),id:response.id},payload.id));}catch(error){finish(error?.status?error:fail('专用相机响应无效。'));}}});
 });}
export async function withNativeCamera(onDevice,operation,{signal,cleanupDevice=onDevice,serial}={}){
 const installed=String(await onDevice(['shell','pm','list','packages','cn.cardsp.camera']));if(!/^package:cn\.cardsp\.camera\s*$/m.test(installed))throw fail('缩放功能需要安装 CardSP USB 相机组件。安装后重新连接手机；原有系统快门仍可使用。');
 const token=randomBytes(32).toString('hex');const launched=String(await onDevice(['shell','am','start','-W','-n','cn.cardsp.camera/.MainActivity','--es','cardsp_token',token]));if(!/Status:\s*ok/.test(launched))throw fail('专用相机无法启动，请保持手机解锁。');
 const forwardedPorts=output=>new Set(String(output).split(/\r?\n/).flatMap(line=>{const parts=line.trim().split(/\s+/);const match=parts[1]?.match(/^tcp:(\d+)$/);return parts[0]===serial&&parts[2]==='localabstract:cardsp-camera-v1'&&match?[Number(match[1])]:[];}));
 const before=serial?forwardedPorts(await cleanupDevice(['forward','--list'])):new Set();
 let port,creationStarted=false;
 try{
  signal?.throwIfAborted();creationStarted=true;
  // Obtain the allocated port through the bounded independent channel, so a
  // cancellation after adb creates it cannot hide its number from finally.
  port=Number(String(await cleanupDevice(['forward','tcp:0','localabstract:cardsp-camera-v1'])).trim());if(!Number.isInteger(port)||port<1024||port>65535){port=undefined;throw fail('USB 相机端口创建失败。');}
  signal?.throwIfAborted();
  const rpc=async(action,zoom)=>exchangeNativeCamera(port,{id:randomUUID().replaceAll('-',''),token,action,...(action==='zoom'?{zoom}:action==='focus'?{point:zoom}:{})},{signal});
  // am start can return before onNewIntent applies the new key. Authenticate a
  // read-only readiness check first; never retry a shutter or zoom command.
  for(let attempt=0;attempt<5;attempt++){
   try {await rpc('state');break;}
   catch(error){if(error?.code!=='CAMERA_AUTH_NOT_READY'||attempt===4)throw error;await delay(150,undefined,{signal});}
  }
  return await operation(rpc);
 }finally{
  if(port)await cleanupDevice(['forward','--remove',`tcp:${port}`]).catch(()=>{});
  else if(creationStarted&&serial){
   // If adb timed out after allocating but before returning stdout, only
   // recover newly added forwards for this serial and this camera endpoint.
   const after=await cleanupDevice(['forward','--list']).then(forwardedPorts).catch(()=>new Set());
   for(const added of after)if(!before.has(added))await cleanupDevice(['forward','--remove',`tcp:${added}`]).catch(()=>{});
  }
 }
}
function jpegBytes(value){if(typeof value!=='string'||value.length>35*1024*1024||!/^[A-Za-z0-9+/]+=*$/.test(value))throw fail('手机未返回有效照片。');const image=Buffer.from(value,'base64');if(image.length<4||image.length>25*1024*1024||image[0]!==255||image[1]!==216||image.at(-2)!==255||image.at(-1)!==217)throw fail('手机未返回有效 JPEG 照片。');return image;}
export function createNativeCameraControls(onDevice,options={}){
 const decode=async response=>{const image=jpegBytes(response.image),frame=await processCameraFrame(image,{x:0,y:0,width:response.width,height:response.height},options.signal);return {...frame,focus_confirmed:response.focusConfirmed,focus_timestamp:response.focusTimestamp,preview_timestamp:response.previewTimestamp,zoom:response.zoom,min_zoom:response.minZoom,max_zoom:response.maxZoom,presets:[1,1.5,2,3].filter(n=>n>=response.minZoom&&n<=response.maxZoom),message:'取景已刷新；此预览仅在本机显示，尚未拍照。'};};
 return async(action,zoom)=>withNativeCamera(onDevice,async rpc=>{
  if(action==='zoom'){
   let frame=await decode(await rpc('zoom',zoom));const target=planAutoZoom(frame.rectangles,frame.zoom).card;
   if(target)frame=await decode(await rpc('focus',{x:target.x+target.width/2,y:target.y+target.height/2}));
   return {...frame,message:frame.focus_confirmed===false?'倍率已读回，但对焦未确认；请检查取景清晰度并调整手机位置。':'镜头倍率已调整并由相机实际回读，请确认取景后拍照。'};
  }
  const frame=await decode(await rpc('preview'));if(action==='preview')return frame;
  const plan=planAutoZoom(frame.rectangles,frame.zoom);if(plan.zoom===null)return {...frame,card:plan.card,message:plan.message,verified:false,adjusted:false};
  const target=Math.max(frame.min_zoom,Math.min(frame.max_zoom,plan.zoom)),after=await decode(await rpc('zoom',target));
  const initial=verifyAutoFrame(plan.card,frame.zoom,after.rectangles,after.zoom);
  const focused=initial.verified?await decode(await rpc('focus',{x:initial.card.x+initial.card.width/2,y:initial.card.y+initial.card.height/2})):after;
  const final=verifyAutoFrame(plan.card,frame.zoom,focused.rectangles,focused.zoom);
  const message=!final.verified?'已读回实际倍率，但仍需检查完整卡片与边距；请移动手机或拉远后重试。':focused.focus_confirmed===false?'取景已调整，但对焦未确认；请检查清晰度后拍照。':plan.message;
  return {...focused,...final,adjusted:Math.abs(focused.zoom-frame.zoom)>.01,message};
 },options);
}
export async function prepareNativeShot(rpc,{signal,frameProcessor=processCameraFrame}={}){
 const preview=await rpc('preview');
 const frame=await frameProcessor(jpegBytes(preview.image),{x:0,y:0,width:preview.width,height:preview.height},signal);
 const card=planAutoZoom(frame.rectangles,preview.zoom).card;
 const focused=await rpc('focus',card?{x:card.x+card.width/2,y:card.y+card.height/2}:{x:.5,y:.5});
 if(focused.focusConfirmed!==true)throw fail('拍照前未能确认对焦，请检查卡片位置和距离后重试。');
 return rpc('capture'); // Exactly one shutter, after focus, in this same session.
}
export async function captureNativeCamera(onDevice,options){return withNativeCamera(onDevice,async rpc=>jpegBytes((await prepareNativeShot(rpc,options)).image),options);}
