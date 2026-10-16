"use client";

import { useEffect, useRef, useState } from "react";
import { captureForm, type CaptureDraft, type ClientCapabilities, type Quad, type Side } from "../../../packages/card-core/src/capture";
import { confirmationPayload } from "../../../packages/card-core/src/confirmation";
import type { RecognitionInput } from "../../../packages/card-core/src/recognition";

import {identitySuggestions,acceptIdentitySuggestions,supplementIdentityLabels,type Identity} from "../../../packages/card-core/src/supplement";

import {captureCapabilities} from "../lib/capture-capabilities";

const base = process.env.NEXT_PUBLIC_API_URL ?? "";
const endpoint = (path:string) => `${base}${path}`;
type Job = import("../../../packages/card-core/src/contracts").CaptureJob;
type Proposal = {proposal_id:string;source_hash:string;source_url:string;preview_url:string;quad:Quad|null;confidence:number;width:number;height:number};
const fields = {player_name:"球员",brand_name:"品牌",product_name:"产品",season_start:"赛季起始年",season_end:"赛季结束年",card_number:"卡号",parallel_name:"平行",insert_name:"特卡",serial_number:"编号",serial_total:"限编",grade_company:"评级公司",grade_value:"评级分数"};
const statusLabels:Record<string,string> = {queued:"排队中",running:"识别中",succeeded:"待确认",failed:"失败"};
const retainedLabels:Record<string,string>={player_birth_date:"球员生日",manufacturer:"制造商",brand_tier:"品牌等级",is_rookie:"新秀",is_autograph:"签名",autograph_type:"签名类型",has_inscription:"题字",inscription_text:"题字内容",is_memorabilia:"实物材质",memorabilia_types:"材质类型",memorabilia_usage:"材质来源",memorabilia_player:"材质球员",is_patch:"Patch",is_case_hit:"箱货",print_run:"限编总量"};
function displayIdentity(value:unknown){if(value===null||value===undefined||value==='')return '未知';if(typeof value==='boolean')return value?'是':'否';return Array.isArray(value)?value.join('、')||'无':String(value);}
async function request<T>(path:string,init?:RequestInit):Promise<T>{
 const response=await fetch(endpoint(path),{...init,credentials:"include"});
 if(response.status===401) window.dispatchEvent(new Event("card-platform:unauthorized"));
 if(!response.ok){const body=await response.json().catch(()=>({}));throw Error(typeof body.detail==='string'?body.detail:`请求失败 (${response.status})`);}
 return response.json();
}

function LocalCaptureImage({file}:{file:Blob}){
 const [url,setUrl]=useState<string|null>(null);
 useEffect(()=>{if(typeof URL.createObjectURL!=="function")return;const next=URL.createObjectURL(file);setUrl(next);return()=>URL.revokeObjectURL(next);},[file]);
 return url?<img src={url} alt="待提交照片预览" style={{display:"block",maxWidth:220,maxHeight:220,objectFit:"contain"}}/>:null;
}

export function CapturePanel({ownerId,target,onClose,onSaved}:{ownerId:string;target?:{id:string;player_name?:string}|null;onClose:()=>void;onSaved:()=>void|Promise<void>}){
 const [capabilities,setCapabilities]=useState<ClientCapabilities|null>(null);
 const [framing,setFraming]=useState<'card'|'slab'>('card');
 const [mode,setMode]=useState<'front'|'back'|'pair'>('front');
 const [draft,setDraft]=useState<CaptureDraft>(()=>({requestId:crypto.randomUUID(),singleSide:true}));
 const [jobs,setJobs]=useState<Job[]>([]),[selected,setSelected]=useState<Job|null>(null);
 const [currentIdentity,setCurrentIdentity]=useState<Identity|null>(null);
 const [acceptanceAllowed,setAcceptanceAllowed]=useState(true),[acceptanceReason,setAcceptanceReason]=useState<string|null>(null);
 const [acceptedKeys,setAcceptedKeys]=useState<string[]>([]);
 const identityRequest=useRef(0);
 const suggestions=currentIdentity&&selected?identitySuggestions(currentIdentity,selected.result??{}):[];
 const [values,setValues]=useState<Record<string,string>>({});
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const [crop,setCrop]=useState<{side:Side;proposal:Proposal;quad:Quad}|null>(null);
 const active=useRef(true),drag=useRef<number|null>(null);
 useEffect(()=>{
  active.current=true;let enabled=false;const abort=new AbortController();
  async function refresh(){try{const list=await request<Job[]>('/v1/capture/jobs',{signal:abort.signal});if(active.current)setJobs(list);}catch(e){if(active.current)setError((e as Error).message);}}
  void captureCapabilities().then(async value=>{if(!active.current)return;setCapabilities(value);enabled=value.capture_jobs;if(enabled)await refresh();}).catch(()=>{if(active.current)setError('服务器能力读取失败，请关闭后重试；原有图片识别入口仍可使用');});
  const timer=setInterval(()=>{if(active.current&&enabled)void refresh();},5000);
  return()=>{active.current=false;abort.abort();clearInterval(timer);};
 },[ownerId]);
 useEffect(()=>{
  if(!target)return;const abort=new AbortController();
  void request<{revision:string;available_sides:Side[]}>(`/v1/capture/items/${target.id}`,{signal:abort.signal}).then(info=>{
   if(!active.current)return;const side:Side=info.available_sides.includes('front')?'back':'front';setMode(side);setDraft({requestId:crypto.randomUUID(),singleSide:true,targetItemId:target.id,expectedRevision:info.revision,missingSide:side});
  }).catch(e=>{if(active.current)setError(e.message);});return()=>abort.abort();
 },[target]);
 async function run(action:()=>Promise<void>){setBusy(true);setError('');try{await action();}catch(e){if(active.current)setError((e as Error).message);}finally{if(active.current)setBusy(false);}}
 function choose(side:Side,file:Blob|undefined){setDraft(old=>({...old,requestId:crypto.randomUUID(),[side]:file,[side==='front'?'frontSource':'backSource']:file,recipes:{...old.recipes,[side]:undefined}}));}
 async function propose(side:Side){const source=draft[side==='front'?'frontSource':'backSource'];if(!source)return;await run(async()=>{const form=new FormData();form.append('image',source,`${side}.jpg`);form.append('target',framing);const p=await request<Proposal>('/v1/capture/crop-proposals',{method:'POST',body:form});if(active.current)setCrop({side,proposal:p,quad:p.quad??[[0,0],[1,0],[1,1],[0,1]]});});}
 async function apply(quad:Quad|null){if(!crop)return;await run(async()=>{
  const result=await request<Proposal>(`/v1/capture/crop-proposals/${crop.proposal.proposal_id}/apply`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({quad})});
  const response=await fetch(endpoint(result.preview_url),{credentials:'include'});if(!response.ok)throw Error('裁切预览下载失败');const blob=await response.blob();if(!active.current)return;
  setDraft(old=>({...old,requestId:crypto.randomUUID(),[crop.side]:blob,recipes:{...old.recipes,[crop.side]:{quad,source_hash:result.source_hash,confirmed:true}}}));setCrop(null);setNotice(quad?'已确认裁切':'已保留原图');
 });}
 function openJob(job:Job){
  const sequence=++identityRequest.current;setSelected(job);setCurrentIdentity(null);setAcceptedKeys([]);setAcceptanceAllowed(true);setAcceptanceReason(null);
  setValues(Object.fromEntries(Object.keys(fields).map(k=>[k,String(job.result?.[k as keyof RecognitionInput]??'')])));
  if(job.target_item_id)void request<{identity:Identity;can_accept_identity?:boolean;identity_acceptance_reason?:string|null}>(`/v1/capture/items/${job.target_item_id}`).then(info=>{if(active.current&&sequence===identityRequest.current){setCurrentIdentity(info.identity??null);setAcceptanceAllowed(info.can_accept_identity!==false);setAcceptanceReason(info.identity_acceptance_reason??null);}}).catch(e=>{if(active.current&&sequence===identityRequest.current)setError(e.message);});
 }
 async function upload(){
  const pending=(['front','back'] as const).find(side=>draft[side]&&!draft.recipes?.[side]?.confirmed);
  if(pending){if(capabilities?.crop_proposals)await propose(pending);else setError('请确认保留原图后再提交');return;}
  await run(async()=>{
  if(target&&!draft.expectedRevision)throw Error('正在读取目标卡片，请稍后重试');
  if(mode==='pair'&&(!draft.front||!draft.back))throw Error('请选择正面和背面照片，或切换单面模式');
  const job=await request<Job>('/v1/capture/jobs',{method:'POST',headers:{'Idempotency-Key':draft.requestId},body:captureForm(draft)});
  if(!active.current)return;setJobs(old=>[job,...old.filter(j=>j.id!==job.id)]);setDraft({requestId:crypto.randomUUID(),singleSide:true});setSelected(null);setNotice('任务已保存；识别完成后请在下方确认');
 });}
 async function confirm(acceptFields=false){if(!selected)return;await run(async()=>{
  if(acceptFields&&(!acceptanceAllowed||!currentIdentity||!acceptedKeys.length))throw Error('请先选择要接受的身份字段');
  const body=selected.target_item_id?(acceptFields?{accepted_identity:acceptIdentitySuggestions(currentIdentity!,suggestions,acceptedKeys)}:{}):confirmationPayload(ownerId,selected.result??{confidence:0},values);
  await request(`/v1/capture/jobs/${selected.id}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!active.current)return;setJobs(old=>old.filter(j=>j.id!==selected.id));setSelected(null);setNotice('已保存到卡库');await onSaved();if(active.current)onClose();
 });}
 return <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="通用照片录卡" style={{maxWidth:860,maxHeight:'90vh',overflow:'auto',padding:24}}>
  <header style={{display:'flex',justifyContent:'space-between'}}><h2>{target?`补拍：${target.player_name??'卡片'}`:'照片录卡与任务'}</h2><button onClick={onClose}>关闭</button></header>
  <p>支持单独正面、背面或双面。识别结果待人工确认；未知字段请补充。</p>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {capabilities&&!capabilities.capture_jobs&&<p>服务器尚不支持通用录卡，请使用原有图片识别入口。</p>}{capabilities?.capture_jobs&&<>
   <label>保留范围 <select aria-label="保留范围" value={framing} disabled={busy||!!crop} onChange={e=>setFraming(e.target.value as typeof framing)}><option value="card">卡面</option><option value="slab">评级盒与标签</option></select></label><label>照片模式 <select aria-label="照片模式" value={mode} disabled={!!target||busy||!!crop} onChange={e=>{const value=e.target.value as typeof mode;setMode(value);setDraft({requestId:crypto.randomUUID(),singleSide:value!=='pair'});setCrop(null);}}><option value="front">只有正面</option><option value="back">只有背面</option><option value="pair">正反两面</option></select></label>
   {(mode==='pair'?['front','back'] as const:[mode] as Side[]).map(side=><fieldset key={side}><legend>{side==='front'?'正面':'背面'}</legend><input type="file" aria-label={side==='front'?'正面照片':'背面照片'} accept="image/jpeg,image/png,image/webp" disabled={busy||!!crop} onChange={e=>choose(side,e.target.files?.[0])}/>{draft[side]&&<LocalCaptureImage file={draft[side]!}/>} {draft[side]&&<span>已选择照片 · {Math.round(draft[side]!.size/1024)} KB </span>}{draft[side]&&<button disabled={busy||!!crop} onClick={()=>setDraft(old=>({...old,requestId:crypto.randomUUID(),[side]:old[side==='front'?'frontSource':'backSource'],recipes:{...old.recipes,[side]:{confirmed:true,quad:null}}}))}>保留{side==='front'?'正面':'背面'}原图</button>}{draft.recipes?.[side]?.confirmed&&<small> · 已确认范围 </small>}{draft[side]&&capabilities.crop_proposals&&<button disabled={busy||!!crop} onClick={()=>void propose(side)}>检查{side==='front'?'正面':'背面'}裁切</button>}</fieldset>)}
   <button className="primary-button" disabled={busy||!!crop||(!draft.front&&!draft.back)} onClick={()=>void upload()}>{busy?'处理中…':'提交识别'}</button>
   {crop&&<section><h3>确认裁切范围</h3><img src={endpoint(crop.proposal.preview_url)} alt="服务器初始裁切建议" style={{maxWidth:220,maxHeight:220,objectFit:"contain"}}/><p>拖动四角或填写坐标（0–1）；保留卡面、评级盒与标签需要的全部边缘。建议置信度 {Math.round(crop.proposal.confidence*100)}%。</p><svg role="img" aria-label="裁切四角预览" viewBox={`0 0 ${crop.proposal.width} ${crop.proposal.height}`} style={{width:"100%",maxWidth:Math.min(420,420*crop.proposal.width/crop.proposal.height),aspectRatio:`${crop.proposal.width}/${crop.proposal.height}`,touchAction:"none"}} onPointerMove={e=>{if(drag.current===null)return;const box=e.currentTarget.getBoundingClientRect();const point:[number,number]=[Math.max(0,Math.min(1,(e.clientX-box.left)/box.width)),Math.max(0,Math.min(1,(e.clientY-box.top)/box.height))];setCrop(old=>{if(!old)return old;const quad=old.quad.map(p=>[...p]) as Quad;quad[drag.current!]=point;return {...old,quad};});}} onPointerUp={()=>drag.current=null} onPointerCancel={()=>drag.current=null}>
    <image href={endpoint(crop.proposal.source_url)} width={crop.proposal.width} height={crop.proposal.height}/><polygon points={crop.quad.map(p=>`${p[0]*crop.proposal.width},${p[1]*crop.proposal.height}`).join(' ')} fill="transparent" stroke="#58f5bd" strokeWidth="5"/>{crop.quad.map((p,i)=><circle key={i} cx={p[0]*crop.proposal.width} cy={p[1]*crop.proposal.height} r={crop.proposal.width*.025} fill="#58f5bd" onPointerDown={e=>{drag.current=i;e.currentTarget.setPointerCapture(e.pointerId);}}/>)}</svg>
    <div style={{display:'flex',flexWrap:'wrap',gap:8}}>{crop.quad.map((p,i)=><label key={i}>{['左上','右上','右下','左下'][i]}{p.map((value,axis)=><input key={axis} aria-label={`${['左上','右上','右下','左下'][i]}${axis?'Y':'X'}`} type="number" min="0" max="1" step="0.01" value={value} style={{width:80}} onChange={e=>setCrop(old=>{if(!old)return old;const quad=old.quad.map(q=>[...q]) as Quad;quad[i][axis]=Number(e.target.value);return {...old,quad};})}/>)}</label>)}</div>
    <button disabled={busy} onClick={()=>void apply(crop.quad)}>接受这次裁切</button><button disabled={busy} onClick={()=>void apply(null)}>保留原图</button><button disabled={busy} onClick={()=>setCrop(null)}>取消</button></section>}
   <h3>我的录卡任务</h3>{jobs.length===0&&<p>暂无任务</p>}{jobs.filter(j=>!j.collection_item_id).map(job=><article key={job.id} style={{borderBottom:'1px solid #ddd',padding:12}}><span>{job.available_sides.map(s=>s==='front'?'正面':'背面').join(' + ')} · {statusLabels[job.status]??job.status}{job.target_item_id?' · 补拍':''}</span>{job.error_message&&<p>{job.error_message}</p>}<button disabled={job.status!=='succeeded'} onClick={()=>openJob(job)}>查看任务</button>{job.status==='failed'&&<button disabled={busy} onClick={()=>void run(async()=>{const retry=await request<Job>(`/v1/capture/jobs/${job.id}/retry`,{method:'POST'});if(active.current)setJobs(old=>old.map(j=>j.id===retry.id?retry:j));})}>重试识别</button>}</article>)}
   {selected&&<section><h3>识别结果确认</h3>{selected.available_sides.length===1&&<p>仅有{selected.available_sides[0]==='front'?'正面':'背面'}，缺失信息保持未知。</p>}<div style={{display:'flex',gap:12}}>{selected.available_sides.map(side=>selected.images[side]&&<img key={side} src={endpoint(selected.images[side]!.preview_url??selected.images[side]!.url)} alt={side==='front'?'任务正面':'任务背面'} style={{maxWidth:'45%',maxHeight:240,objectFit:'contain'}}/>)}</div>
    {selected.target_item_id?<section><p>默认只补拍图片。可勾选球员、品牌、赛季、产品、系列、卡号、特卡和平行建议；未知值不作为清空建议。签名、材质、限编及其他原字段保持不变。</p>{!currentIdentity&&<p>正在读取原身份；仍可只确认图片。</p>}{acceptanceReason&&<p role="status">{acceptanceReason}</p>}{currentIdentity&&suggestions.length===0&&<p>没有可接受的主身份差异。</p>}{suggestions.length>0&&<table><thead><tr><th>接受</th><th>字段</th><th>原人工确认值</th><th>新识别建议</th></tr></thead><tbody>{suggestions.map(suggestion=><tr key={suggestion.key}><td><input type="checkbox" aria-label={`接受${suggestion.label}建议`} disabled={!acceptanceAllowed||busy} checked={acceptedKeys.includes(suggestion.key)} onChange={e=>setAcceptedKeys(old=>e.target.checked?[...old,suggestion.key]:old.filter(key=>key!==suggestion.key))}/></td><td>{suggestion.label}</td><td>{displayIdentity(suggestion.current)}</td><td>{displayIdentity(suggestion.suggested)}</td></tr>)}</tbody></table>}{currentIdentity&&<details><summary>其他已确认身份（保持不变）</summary><dl>{Object.entries(currentIdentity).filter(([key])=>!(key in supplementIdentityLabels)).map(([key,value])=><div key={key}><dt>{retainedLabels[key]??key}</dt><dd>{displayIdentity(value)}</dd></div>)}</dl></details>}<button disabled={busy||!acceptanceAllowed||!currentIdentity||acceptedKeys.length===0||selected.status!=='succeeded'} onClick={()=>void confirm(true)}>接受所选字段并补拍</button></section>:<div style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:10}}>{Object.entries(fields).map(([key,label])=><label key={key}>{label}<input aria-label={label} value={values[key]??''} onChange={e=>setValues(old=>({...old,[key]:e.target.value}))}/></label>)}</div>}
    <button disabled={busy||selected.status!=='succeeded'||!!selected.collection_item_id} onClick={()=>void confirm()}>{selected.target_item_id?'确认补拍（保留身份）':'确认入库'}</button><button onClick={()=>setSelected(null)}>收起结果</button></section>}
  </>}
 </section></div>;
}
