import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCameraUI, planAutoZoom, createCameraControls } from '../android-camera-controls.mjs';
const xml = (zoom=1,slider=false) => `<hierarchy rotation="0"><node package="com.android.camera" resource-id="com.android.camera:id/v6_frame_layout" bounds="[0,240][1080,1680]"/><node resource-id="com.android.camera:id/mode_select_view" bounds="[0,1691][1080,1824]"/><node resource-id="com.android.camera:id/zoom_ratio_toggle_button" content-desc="${zoom}倍变焦" bounds="[312,1510][768,1680]"><node content-desc="0.6倍变焦" clickable="true" bounds="[369,1510][483,1680]"/><node content-desc="${zoom===0.6||zoom===3?1:zoom}倍变焦" clickable="true" bounds="[483,1510][597,1680]"/><node content-desc="3.0倍变焦" clickable="true" bounds="[597,1510][711,1680]"/></node>${slider?'<node resource-id="com.android.camera:id/zoom_view" bounds="[50,1691][1030,1824]"/>':''}</hierarchy>`;
test('reads actual ratio and safe MIUI controls, rejecting missing camera and malformed bounds',()=>{
 const ui=parseCameraUI(xml(1.6));assert.equal(ui.zoom,1.6);assert.deepEqual(ui.frame,{x:0,y:240,width:1080,height:1440});assert.deepEqual(ui.presets,[0.6,1.6,3]);
 assert.throws(()=>parseCameraUI(xml().replaceAll('com.android.camera','evil.app')),/相机/);
 assert.throws(()=>parseCameraUI(xml().replace('[0,240][1080,1680]','[-1,0][99999,99999]')),/取景/);
});
test('auto zoom fits the whole off-center card, caps magnification and refuses blank or multiple cards',()=>{
 const centered={x:.35,y:.3,width:.3,height:.4,confidence:.99};
 assert.equal(planAutoZoom([centered],1).zoom,1.8);
 const offset={x:.6,y:.3,width:.35,height:.4,confidence:.99};
 const plan=planAutoZoom([offset],1.6);assert.equal(plan.zoom,1.6);assert.match(plan.message,/中心/);
 assert.equal(planAutoZoom([],1).zoom,null);
 assert.equal(planAutoZoom([centered,{...centered,x:.05}],1).zoom,null);
 const nested={x:.36,y:.31,width:.28,height:.38,confidence:.99};assert.equal(planAutoZoom([centered,nested],1).zoom,1.8);
 assert.equal(planAutoZoom([{...centered,x:-.1}],1).zoom,null);
});
test('manual zoom is read back from device; failed movement cannot report requested value as success',async()=>{
 const calls=[];let zoom=1,slider=false;
 const device=async args=>{calls.push(args);if(args.includes('uiautomator'))return Buffer.from('UI hierchary dumped to: /sdcard/cardsp-camera-controls.xml');if(args[0]==='exec-out'&&args[1]==='cat')return Buffer.from(xml(zoom,slider));if(args[0]==='exec-out'&&args[1]==='screencap')return Buffer.from('png');if(args.includes('swipe')){if(args[3]===args[5])slider=true;else zoom=Math.round((zoom+(Number(args[5])-Number(args[3]))/140)*10)/10;return Buffer.from('');}if(args.includes('tap')){zoom=Number(args[3])>600?3:1;return Buffer.from('');}throw Error(JSON.stringify(args));};
 const controls=createCameraControls(device,{frameProcessor:async()=>({preview:'data:image/jpeg;base64,AA==',rectangles:[]}),settleMs:0});
 const result=await controls('zoom',1.6);assert.equal(result.zoom,1.6);assert.equal(calls.some(c=>c.includes('keyevent')),false);
 const stuck=createCameraControls(async args=>args[0]==='exec-out'?Buffer.from(xml(1)):Buffer.from('UI hierchary dumped to: /sdcard/cardsp-camera-controls.xml'),{settleMs:0});
 await assert.rejects(stuck('zoom',1.6),/缩放|倍率/);
 for(const invalid of [0,4,NaN,'1.6'])await assert.rejects(controls('zoom',invalid),/倍率/);
});
test('MIUI camera hibernation wakes only its known camera cover before reading controls',async()=>{
 let asleep=true,taps=0;
 const device=async args=>{
  if(args.includes('uiautomator'))return Buffer.from('UI hierchary dumped to: /sdcard/cardsp-camera-controls.xml');
  if(args[0]==='exec-out'&&args[1]==='cat')return Buffer.from(asleep?'<node package="com.android.camera" resource-id="com.android.camera:id/hibernation_cover" bounds="[0,0][1080,2270]"/>':xml());
  if(args.includes('tap')){asleep=false;taps++;return Buffer.from('');}
  if(args.includes('screencap'))return Buffer.from('image');throw Error(JSON.stringify(args));
 };
 const c=createCameraControls(device,{frameProcessor:async()=>({preview:'data:image/jpeg;base64,AA==',rectangles:[]}),settleMs:0});
 assert.equal((await c('preview')).zoom,1);assert.equal(taps,1);
});
test('expanded MIUI ruler is dismissed before real ratio readback',async()=>{
 let expanded=true;
 const device=async args=>{
  if(args.includes('uiautomator'))return Buffer.from('UI hierchary dumped to: x');
  if(args[0]==='exec-out'&&args[1]==='cat')return Buffer.from(expanded?xml().replace(/<node resource-id="com.android.camera:id\/zoom_ratio_toggle_button"[^>]*>/,'').replace('content-desc="3.0倍变焦"','content-desc="30倍变焦"'):xml(1.6));
  if(args.includes('tap')){expanded=false;return Buffer.from('');}if(args.includes('screencap'))return Buffer.from('image');throw Error(JSON.stringify(args));
 };
 const c=createCameraControls(device,{frameProcessor:async()=>({preview:'data:image/jpeg;base64,AA==',rectangles:[]}),settleMs:0});assert.equal((await c('preview')).zoom,1.6);
});
test('auto cannot claim fit at minimum zoom or when post-adjustment detection changes targets',async()=>{
 const edge={x:0,y:.2,width:.7,height:.6,confidence:.99};assert.equal(planAutoZoom([edge],.6).zoom,null);assert.match(planAutoZoom([edge],.6).message,/拉远|移动/);
 let current=1,images=0;
 const device=async args=>{if(args.includes('uiautomator'))return Buffer.from('UI hierchary dumped to: x');if(args[0]==='exec-out'&&args[1]==='cat')return Buffer.from(xml(current));if(args.includes('swipe')){if(args[3]!==args[5])current=Math.round((current+(Number(args[5])-Number(args[3]))/140)*10)/10;return Buffer.from('');}if(args.includes('screencap'))return Buffer.from('image');if(args.includes('tap'))return Buffer.from('');throw Error(JSON.stringify(args));};
 const controls=createCameraControls(device,{settleMs:0,frameProcessor:async()=>({preview:'data:image/jpeg;base64,AA==',rectangles:[++images===1?{x:.35,y:.3,width:.3,height:.4,confidence:.99}:{x:.05,y:.05,width:.2,height:.3,confidence:.99}]})});
 const result=await controls('auto');assert.equal(result.verified,false);assert.match(result.message,/确认|变化|检查/);
});
test('small background rectangles do not make one dominant card ambiguous',()=>{
 const card={x:.3,y:.2,width:.5,height:.6,confidence:.95};const background={x:.02,y:.78,width:.22,height:.2,confidence:1};
 const plan=planAutoZoom([card,background],1);assert.equal(plan.zoom,1.5);assert.deepEqual(plan.card,card);
});
test('0.1 zoom steps overcome MIUI touch slop and always release the injected pointer',async()=>{
 let zoom=1.4,start=0,releases=0,presses=0;
 const device=async args=>{
  if(args.includes('uiautomator'))return Buffer.from('UI hierchary dumped to: x');if(args[0]==='exec-out'&&args[1]==='cat')return Buffer.from(xml(zoom));if(args.includes('screencap'))return Buffer.from('image');
  if(args.includes('motionevent')){if(args[3]==='DOWN'){start=Number(args[4]);presses++;}if(args[3]==='UP'){zoom=Math.round((zoom+(Number(args[4])-start)/70)*10)/10;releases++;}return Buffer.from('');}
  if(args.includes('swipe'))return Buffer.from('');if(args.includes('tap'))return Buffer.from('');throw Error(JSON.stringify(args));
 };
 const c=createCameraControls(device,{frameProcessor:async()=>({preview:'data:image/jpeg;base64,AA==',rectangles:[]}),settleMs:0,releasePointer:device});assert.equal((await c('zoom',1.5)).zoom,1.5);assert.equal(releases,presses);assert.ok(releases>0);assert.equal((await c('zoom',1.4)).zoom,1.4);assert.equal(releases,presses);
});
test('an interrupted fine zoom releases pointer through an independent cleanup channel',async()=>{
 let released=0;
 const device=async args=>{if(args.includes('uiautomator'))return Buffer.from('UI hierchary dumped to: x');if(args[0]==='exec-out')return Buffer.from(xml(1.4));if(args.includes('motionevent'))throw Error('request cancelled after DOWN');return Buffer.from('');};
 const c=createCameraControls(device,{settleMs:0,releasePointer:async args=>{assert.equal(args[3],'UP');released++;}});
 await assert.rejects(c('zoom',1.5),/cancelled/);assert.equal(released,1);
});
test('fine zoom calibrates from actual readback rather than oscillating across the target',async()=>{
 let zoom=1.4,start=0;
 const device=async args=>{
  if(args.includes('uiautomator'))return Buffer.from('UI hierchary dumped to: x');if(args[0]==='exec-out'&&args[1]==='cat')return Buffer.from(xml(zoom));if(args.includes('screencap'))return Buffer.from('image');
  if(args.includes('motionevent')){if(args[3]==='DOWN')start=Number(args[4]);if(args[3]==='UP'){const delta=Number(args[4])-start;if(Math.abs(delta)>8)zoom=Math.round((zoom+delta/70)*10)/10;}return Buffer.from('');}return Buffer.from('');
 };
 const c=createCameraControls(device,{frameProcessor:async()=>({preview:'data:image/jpeg;base64,AA==',rectangles:[]}),settleMs:0,releasePointer:device});assert.equal((await c('zoom',1.5)).zoom,1.5);
});
test('final auto frame validates the focused preview rather than trusting the pre-focus frame',async()=>{
 const {verifyAutoFrame}=await import('../android-camera-controls.mjs');const before={x:.2,y:.2,width:.4,height:.5,confidence:.99};
 assert.equal(verifyAutoFrame(before,1,[before],1).verified,true);
 assert.equal(verifyAutoFrame(before,1,[{...before,x:0}],1).verified,false);
 assert.equal(verifyAutoFrame(before,1,[{...before,x:.7,width:.2,height:.2}],1).verified,false);
});
test('auto prefers the document card over a surrounding stand rectangle',()=>{
 const card={x:.03,y:.146,width:.551,height:.465,confidence:.978,document:1};const stand={x:.014,y:.018,width:.545,height:.604,confidence:1,document:0};
 const plan=planAutoZoom([stand,card],1.4);assert.deepEqual(plan.card,card);assert.ok(plan.zoom!==null);
});
