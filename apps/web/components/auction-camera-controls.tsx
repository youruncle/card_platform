'use client';
import React, {useEffect,useState} from 'react';
import type {CameraFrame,CameraControlAction} from '../lib/local-camera';
export function AuctionCameraControls({frame,busy,action,onControl}:{frame:CameraFrame|null;busy:boolean;action:CameraControlAction|'connect'|'capture'|null;onControl:(action:CameraControlAction,zoom?:number)=>void}) {
 const [target,setTarget]=useState(1),[rotation,setRotation]=useState(0);
 useEffect(()=>{if(frame)setTarget(frame.zoom);},[frame]);
 const minZoom=frame?.min_zoom??.6,maxZoom=frame?.max_zoom??3;
 const card=frame?.card, aspect=frame?frame.width/frame.height:.75;
 return <div style={{marginTop:14,padding:12,border:'1px solid var(--line)',borderRadius:8}}>
  <div style={{display:'flex',flexWrap:'wrap',gap:8,alignItems:'center'}}>
   <button className="secondary-button" disabled={busy} onClick={()=>onControl('preview')}>{action==='preview'?'正在读取取景…':'刷新取景'}</button>
   <button className="secondary-button" disabled={busy} onClick={()=>onControl('auto')}>{action==='auto'?'正在自动调整…':'自动调整'}</button>
   <span aria-label="手机实际倍率">手机实际倍率：{frame?`${frame.zoom.toFixed(1)}×`:'待读取'}</span>
  </div>
  {frame && <>
   <div style={{margin:'12px auto',maxWidth:480}}>
    <div style={{position:'relative',aspectRatio:rotation%180===0?String(aspect):String(1/aspect),overflow:'hidden',background:'#111',borderRadius:7}}>
     <div style={{position:'absolute',width:rotation%180===0?'100%':`${aspect*100}%`,height:rotation%180===0?'100%':`${100/aspect}%`,top:'50%',left:'50%',transform:`translate(-50%,-50%) rotate(${rotation}deg)`}}>
      <img src={frame.preview} alt="Redmi 当前取景" style={{width:'100%',height:'100%',objectFit:'contain',opacity:busy?.6:1}}/>
      {card && <div aria-label="检测到的卡片边框" style={{position:'absolute',left:`${card.x*100}%`,top:`${card.y*100}%`,width:`${card.width*100}%`,height:`${card.height*100}%`,border:'2px solid #63ec9c',boxSizing:'border-box',pointerEvents:'none'}}/>}
     </div>
    </div>
    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:8,marginTop:6}}><small>最近一次取景 · 点击刷新更新</small><button className="ghost-button" disabled={busy} onClick={()=>setRotation(value=>(value+90)%360)}>旋转预览</button></div>
   </div>
   <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
    <button className="secondary-button" aria-label="缩小 0.1 倍" disabled={busy||frame.zoom<=minZoom} onClick={()=>onControl('zoom',Math.max(minZoom,Math.round((frame.zoom-.1)*10)/10))}>−0.1×</button>
    {frame.presets.map(value=><button key={value} className="secondary-button" disabled={busy} onClick={()=>onControl('zoom',value)}>{value.toFixed(1)}×</button>)}
    <button className="secondary-button" aria-label="放大 0.1 倍" disabled={busy||frame.zoom>=maxZoom} onClick={()=>onControl('zoom',Math.min(maxZoom,Math.round((frame.zoom+.1)*10)/10))}>+0.1×</button>
   </div>
  </>}
  <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap',marginTop:12}}>
   <label style={{display:'flex',gap:10,alignItems:'center',flex:'1 1 240px'}}>目标倍率 {target.toFixed(1)}×<input aria-label="目标缩放倍率" type="range" min={minZoom} max={maxZoom} step="0.1" value={target} disabled={busy||!frame} style={{minWidth:130,flex:1}} onChange={event=>setTarget(Number(event.target.value))}/></label>
   <button className="secondary-button" disabled={busy||!frame} onClick={()=>onControl('zoom',target)}>{action==='zoom'?'正在调整倍率…':'应用缩放'}</button>
  </div>
  {!frame && <small>先刷新取景，读取手机镜头的实际倍率范围。</small>}
  <p style={{fontSize:12,lineHeight:1.7,color:'var(--muted)',marginBottom:0}}>自动调整会在本机检测单张完整卡片，选择留有边距的倍率，不会自动拍照。卡片偏离中心时请移动手机；预览旋转只改变此处显示。调好后再点击快门。</p>
 </div>;
}
