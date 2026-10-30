import {spawn} from 'node:child_process';
import {readFile, mkdir, access, rename, rm} from 'node:fs/promises';
import {createHash, randomBytes} from 'node:crypto';
import {homedir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
const fail = message => Object.assign(new Error(message), {status:409});
const xmlPath='/sdcard/cardsp-camera-controls.xml';
function attributes(tag) {return Object.fromEntries([...tag.matchAll(/([\w:-]+)="([^"]*)"/g)].map(m=>[m[1],m[2].replaceAll('&quot;','"').replaceAll('&amp;','&')]));}
function bounds(value) {const m=value?.match(/^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$/);if(!m)return null;const [x,y,r,b]=m.slice(1).map(Number);return x>=0&&y>=0&&r>x&&b>y&&r<=10000&&b<=10000?{x,y,width:r-x,height:b-y}:null;}
function ratio(value) {const n=Number(value?.match(/(\d+(?:\.\d+)?)\s*(?:倍变焦|[x×])/i)?.[1]);return n>=0.1&&n<=100?n:null;}
export function parseCameraUI(xml) {
 const nodes=[...String(xml).matchAll(/<node\b[^>]*>/g)].map(m=>attributes(m[0]));
 const find=id=>nodes.find(n=>n['resource-id']===`com.android.camera:id/${id}`);
 const toggle=find('zoom_ratio_toggle_button'), frame=bounds(find('v6_frame_layout')?.bounds), mode=bounds(find('mode_select_view')?.bounds),zoom=ratio(toggle?.['content-desc']);
 if(!toggle||!zoom)throw fail('当前相机不支持电脑缩放，请使用 Redmi 系统相机的拍照模式。');
 if(!frame||!mode||mode.y<frame.y+frame.height)throw fail('无法确认相机取景区域，请退出相机菜单后刷新取景。');
 const buttons=nodes.filter(n=>n.clickable==='true'&&ratio(n['content-desc'])&&bounds(n.bounds)).map(n=>({zoom:ratio(n['content-desc']),bounds:bounds(n.bounds)}));
 if(buttons.length!==3||!buttons.some(b=>b.zoom===0.6)||!buttons.some(b=>b.zoom===3))throw fail('此版本相机的缩放控件尚不支持，请继续使用手机调节倍率。');
 return {zoom,frame,mode,buttons,presets:buttons.map(b=>b.zoom),current:buttons.find(b=>b.zoom===zoom)?.bounds||bounds(toggle.bounds)};
}
function validRect(r){return r&&['x','y','width','height','confidence'].every(k=>Number.isFinite(r[k]))&&r.x>=0&&r.y>=0&&r.width>.05&&r.height>.05&&r.x+r.width<=1.001&&r.y+r.height<=1.001&&r.confidence>=.75;}
export function planAutoZoom(rectangles,current) {
 const rectanglesValid=rectangles.filter(validRect).sort((a,b)=>Number(b.document===1)-Number(a.document===1)||b.width*b.height-a.width*a.height);
 if(!rectanglesValid.length)return {zoom:null,card:null,message:'未找到完整卡片边框，请让整张卡片进入画面后重试，或手动缩放。'};
 const card=rectanglesValid[0];
 const separate=rectanglesValid.slice(1).some(r=>{const ix=Math.max(0,Math.min(card.x+card.width,r.x+r.width)-Math.max(card.x,r.x)),iy=Math.max(0,Math.min(card.y+card.height,r.y+r.height)-Math.max(card.y,r.y));return ix*iy/Math.min(r.width*r.height,card.width*card.height)<.8&&r.width*r.height>Math.max(.04,card.width*card.height*.35);});
 if(separate)return {zoom:null,card:null,message:'检测到多个矩形目标，请只放置一张卡片后重试，或使用手动缩放。'};
 const clipped=card.x<.015||card.y<.015||card.x+card.width>.985||card.y+card.height>.985;
 if(clipped&&current<=.6)return {zoom:null,card,message:'已到最小倍率，目标仍贴边；请移动手机或拉远距离，让整张卡片进入画面。'};
 const extent=Math.max(Math.abs(card.x-.5),Math.abs(card.x+card.width-.5),Math.abs(card.y-.5),Math.abs(card.y+card.height-.5))*2;
 const proposed=Math.max(.6,Math.min(1.8,current*.9/extent,clipped?current*.85:3));
 const zoom=Math.floor((proposed+1e-6)*10)/10;
 const offCenter=Math.abs(card.x+card.width/2-.5)>.1||Math.abs(card.y+card.height/2-.5)>.1;
 return {zoom,card,message:offCenter?'卡片偏离画面中心，已优先保留整张卡片；请移动手机使卡片居中，再点自动调整。':'已按完整卡片和边距调整倍率，请确认取景后点击快门。'};
}
export function verifyAutoFrame(origin,oldZoom,rectangles,currentZoom){
 const check=planAutoZoom(rectangles,currentZoom),card=check.card,scale=currentZoom/oldZoom;
 const expected={x:.5+(origin.x-.5)*scale,y:.5+(origin.y-.5)*scale,width:origin.width*scale,height:origin.height*scale};
 const intersection=card?Math.max(0,Math.min(card.x+card.width,expected.x+expected.width)-Math.max(card.x,expected.x))*Math.max(0,Math.min(card.y+card.height,expected.y+expected.height)-Math.max(card.y,expected.y)):0;
 const overlap=card?intersection/(card.width*card.height+expected.width*expected.height-intersection):0;
 return {card,verified:!!card&&check.zoom!==null&&card.x>=.015&&card.y>=.015&&card.x+card.width<=.985&&card.y+card.height<=.985&&overlap>=.45};
}
function runProcess(executable,args,input,signal,timeoutMs=45000){return new Promise((resolve,reject)=>{
 signal?.throwIfAborted();const child=spawn(executable,args,{stdio:['pipe','pipe','pipe'],shell:false});let output=[],size=0,error;
 const stop=reason=>{error??=reason;child.kill('SIGKILL');};const abort=()=>stop(signal.reason||fail('相机操作已取消。'));
 const timer=setTimeout(()=>stop(fail('本地取景处理超时，请重试。')),timeoutMs);timer.unref();signal?.addEventListener('abort',abort,{once:true});
 const clean=()=>{clearTimeout(timer);signal?.removeEventListener('abort',abort);};
 child.stdout.on('data',chunk=>{size+=chunk.length;if(size>3*1024*1024)stop(fail('相机预览过大。'));else output.push(chunk);});child.stderr.resume();child.stdin.on('error',()=>{});
 child.on('error',()=>{clean();reject(fail('本机取景处理不可用，请安装 Xcode 命令行工具后重试。'));});child.on('close',code=>{clean();if(error)reject(error);else if(code!==0)reject(fail('本地取景处理失败，请确认 Mac 的 Xcode 命令行工具可用。'));else resolve(Buffer.concat(output));});child.stdin.end(input);
});}
let compiler;
async function frameBinary(signal){
 if(process.platform!=='darwin')throw fail('自动取景目前需要在 Mac 上运行手机助手，手动快门仍可使用。');
 const source=fileURLToPath(new URL('./android-camera-frame.swift',import.meta.url)),bytes=await readFile(source);
 const cache=join(homedir(),'Library','Caches','CardPlatform','camera');await mkdir(cache,{recursive:true,mode:0o700});
 const binary=join(cache,'frame-'+createHash('sha256').update(bytes).digest('hex').slice(0,24));
 try {await access(binary);return binary;} catch {}
 if(!compiler){compiler=(async()=>{const temp=binary+'-'+randomBytes(6).toString('hex');try{await runProcess('/usr/bin/xcrun',['swiftc',source,'-O','-o',temp],undefined,signal,60000);await rename(temp,binary);}finally{await rm(temp,{force:true});}})();compiler.catch(()=>{compiler=undefined;});}
 await compiler;return binary;
}
export async function processCameraFrame(image,frame,signal){const binary=await frameBinary(signal);return JSON.parse(String(await runProcess(binary,[frame.x,frame.y,frame.width,frame.height].map(String),image,signal,20000)));}
export function createCameraControls(onDevice,{frameProcessor=processCameraFrame,settleMs=150,signal,releasePointer=onDevice}={}){
 const readUI=async(recoveries=2)=>{
  const dump=String(await onDevice(['shell','uiautomator','dump','--compressed',xmlPath]));if(!/dumped to:/i.test(dump))throw fail('相机界面忙，无法读取实际倍率，请关闭手机上的弹窗后重试。');
  const xml=String(await onDevice(['exec-out','cat',xmlPath]));
  const nodes=[...xml.matchAll(/<node\b[^>]*>/g)].map(m=>attributes(m[0]));
  const cover=nodes.find(n=>n.package==='com.android.camera'&&n['resource-id']==='com.android.camera:id/hibernation_cover');
  const expanded=!nodes.some(n=>n['resource-id']==='com.android.camera:id/zoom_ratio_toggle_button')&&nodes.some(n=>ratio(n['content-desc'])===30)&&nodes.some(n=>ratio(n['content-desc'])===.6);
  const b=bounds((cover||expanded&&nodes.find(n=>n['resource-id']==='com.android.camera:id/v6_frame_layout'))?.bounds);
  if(recoveries>0&&b){await onDevice(['shell','input','tap',String(Math.round(b.x+b.width/2)),String(Math.round(b.y+b.height/2))]);await delay(settleMs,undefined,{signal});return readUI(recoveries-1);}
  return parseCameraUI(xml);
 };
 const settle=()=>delay(settleMs,undefined,{signal});
 const tap=async b=>{await onDevice(['shell','input','tap',String(Math.round(b.x+b.width/2)),String(Math.round(b.y+b.height/2))]);await settle();};
 async function setZoom(ui,target){
  if(typeof target!=='number'||!Number.isFinite(target)||target<.6||target>3)throw fail('缩放倍率应在 0.6–3× 之间。');
  target=Math.round(target*10)/10;
  if(Math.abs(ui.zoom-target)<.05)return ui;
  // MIUI's middle button resets to 1× even when its label shows a custom ratio.
  const preset=target===1?ui.buttons[1]:ui.buttons.find(b=>b.zoom===target);
  if(preset){await tap(preset.bounds);ui=await readUI();if(Math.abs(ui.zoom-target)<.05)return ui;}
  let fineGain=140;
  for(let attempt=0;attempt<4;attempt++){
   const before=ui.zoom,b=ui.current,x=Math.round(b.x+b.width/2),y=Math.round(b.y+b.height/2);
   await onDevice(['shell','input','swipe',String(x),String(y),String(x),String(y),'700']);
   // The observed MIUI zoom ruler replaces this mode selector. Derive its coordinates from the current UI.
   const mid=Math.round(ui.mode.x+ui.mode.width/2),sy=Math.round(ui.mode.y+ui.mode.height*.6),difference=target-ui.zoom;
   const fine=Math.abs(difference)<=.25;
   let fineDelta=0;
   if(fine){
    // MIUI ignores short swipes. Cross touch slop, return to the precise final
    // position, then release without velocity. Cleanup bypasses cancellation.
    fineDelta=Math.round(Math.max(-40,Math.min(40,difference*fineGain)));
    const end=mid+fineDelta,overshoot=mid+Math.sign(difference)*60;
    try {
     await onDevice(['shell','input','motionevent','DOWN',String(mid),String(sy)]);
     for(const position of [overshoot,end,end])await onDevice(['shell','input','motionevent','MOVE',String(position),String(sy)]);
    } finally { await releasePointer(['shell','input','motionevent','UP',String(end),String(sy)]); }
   } else {
    const delta=Math.round(Math.max(-120,Math.min(120,difference*140)));
    await onDevice(['shell','input','swipe',String(mid),String(sy),String(mid+delta),String(sy),'400']);
   }
   await settle();ui=await readUI();
   if(Math.abs(ui.zoom-target)<.051)return ui;
   if(fine){fineGain=Math.abs(before-ui.zoom)<.01?Math.min(350,fineGain*1.5):Math.abs(fineDelta/(ui.zoom-before));}
   else if(Math.abs(before-ui.zoom)<.01)break;
  }
  throw fail(`手机未能调到目标倍率，实际为 ${ui.zoom.toFixed(1)}×。请刷新取景或在手机上调节。`);
 }
 const preview=async ui=>{const image=await onDevice(['exec-out','screencap','-p'],25*1024*1024);const frame=await frameProcessor(image,ui.frame,signal);return {...frame,zoom:ui.zoom,presets:[.6,1,3],message:'取景已刷新；此预览仅在本机显示，尚未拍照。'};};
 return async(action,target)=>{
  if(!['preview','zoom','auto'].includes(action))throw fail('未知的相机控制操作。');
  if(action==='zoom'&&(typeof target!=='number'||!Number.isFinite(target)||target<.6||target>3))throw fail('缩放倍率应在 0.6–3× 之间。');
  let ui=await readUI();
  if(action==='zoom'){ui=await setZoom(ui,target);return {...await preview(ui),message:`已读取手机实际倍率 ${ui.zoom.toFixed(1)}×，请确认取景后拍照。`};}
  const frame=await preview(ui);if(action==='preview')return frame;
  const plan=planAutoZoom(frame.rectangles,ui.zoom);if(plan.zoom===null)return {...frame,...plan,zoom:ui.zoom,adjusted:false,verified:false};
  const oldZoom=ui.zoom;ui=await setZoom(ui,plan.zoom);const after=await preview(ui);const check=planAutoZoom(after.rectangles,ui.zoom);
  const card=check.card,scale=ui.zoom/oldZoom;
  const expected={x:.5+(plan.card.x-.5)*scale,y:.5+(plan.card.y-.5)*scale,width:plan.card.width*scale,height:plan.card.height*scale};
  const intersection=card?Math.max(0,Math.min(card.x+card.width,expected.x+expected.width)-Math.max(card.x,expected.x))*Math.max(0,Math.min(card.y+card.height,expected.y+expected.height)-Math.max(card.y,expected.y)):0;
  const overlap=card?intersection/(card.width*card.height+expected.width*expected.height-intersection):0;
  const verified=!!card&&check.zoom!==null&&card.x>=.015&&card.y>=.015&&card.x+card.width<=.985&&card.y+card.height<=.985&&overlap>=.45;
  return {...after,card,message:verified?plan.message:'倍率已调整，但新取景的边距或目标一致性尚未确认；请检查完整卡片，必要时移动手机后重试。',adjusted:Math.abs(ui.zoom-oldZoom)>.01,verified};
 };
}
