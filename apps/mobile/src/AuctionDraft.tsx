import React, { useEffect, useMemo, useState, useSyncExternalStore } from 'react';
import { AuctionEditor, auctionSideName, type AuctionPhoto } from '../../../packages/card-core/src/auction';
import type { MobileApi } from './api';
import { PrivateImage } from './PrivateImage';
export function AuctionDraft({api, itemId, onClose}: {api: MobileApi; itemId: string; onClose: () => void}) {
  const editor = useMemo(() => new AuctionEditor((path, options) => api.request(path, options, false, options?.body instanceof FormData ? 180000 : 90000)), [api, itemId]);
  const state = useSyncExternalStore(editor.subscribe, editor.snapshot, editor.snapshot);
  const [side, setSide] = useState<AuctionPhoto['side']>('front'), [copied, setCopied] = useState('');
  useEffect(() => { void editor.open(itemId); return () => editor.dispose(); }, [editor, itemId]);
  async function copy(value: string, label: string) { try { await navigator.clipboard.writeText(value); setCopied(`${label}已复制`); } catch { setCopied('复制失败，请在文本框中手动选择复制'); } }
  return <section className="panel" aria-label="单卡拍卖草稿">
    <button disabled={state.busy} onClick={onClose}>‹ 返回卡库</button><h1>单卡拍卖草稿</h1>
    <p>卡淘草稿未接入：{state.draft?.external.reason || '正在核实能力'}。这里保存的是本站平台草稿。</p>
    {state.error && <p role="alert" className="error">{state.error}</p>}{state.notice && <p role="status">{state.notice}</p>}
    {state.conflict && <button disabled={state.busy} onClick={()=>void editor.refresh()}>读取最新版本并保留本页输入</button>}
      {Object.keys(state.latestConflicts).length > 0 && <aside aria-label="其他端修改对照"><strong>其他端的最新内容</strong><p>本页保留了你编辑的字段，请对照后再保存。</p>{Object.entries(state.latestConflicts).map(([key,value]) => <div key={key}><b>{({title:'标题',description:'描述',condition_notes:'品相备注',selected_photo_ids:'选中照片'} as Record<string,string>)[key] || key}</b><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{Array.isArray(value) ? value.join('、') : value}</p></div>)}</aside>}
    {!state.draft ? <><p>{state.busy ? '正在恢复草稿…' : '草稿尚未载入'}</p><button disabled={state.busy} onClick={()=>void editor.open(itemId)}>重试加载</button></> : <>
      <p>选中 {state.selected_photo_ids.length} / 12 张 · 版本 {state.draft.revision}</p>
      <div style={{display:'flex',gap:12,flexWrap:'wrap'}}>{state.draft.photos.map((photo,index)=><div key={photo.id} style={{width:120}}><PrivateImage api={api} path={photo.url} alt={`${auctionSideName(photo.side)}草稿照片`}/><label><input type="checkbox" aria-label={`选择${auctionSideName(photo.side)}照片 ${index+1}`} checked={state.selected_photo_ids.includes(photo.id)} disabled={state.busy || state.conflict} onChange={()=>editor.select(photo.id)}/>{auctionSideName(photo.side)} · {photo.source==='existing' ? '已有卡图' : '新照片'}</label></div>)}</div>
      <fieldset disabled={state.busy || state.conflict} style={{margin:'16px 0'}}><legend>添加拍卖照片</legend><label>新照片位置<select value={side} onChange={event=>setSide(event.target.value as AuctionPhoto['side'])}><option value="front">正面</option><option value="back">背面</option><option value="detail">细节</option></select></label>
        <label>手机拍照<input aria-label="手机拍照" type="file" accept="image/*" capture="environment" onChange={event=>{const file=event.target.files?.[0];if(file)void editor.add(file,side,'camera');event.target.value='';}}/></label>
        <label>上传照片<input aria-label="上传照片" type="file" accept="image/*" onChange={event=>{const file=event.target.files?.[0];if(file)void editor.add(file,side,'upload');event.target.value='';}}/></label><p>新照片上传后自动生成文案。</p>
      </fieldset>
      {state.pendingFile && <p>照片「{state.pendingFile.name}」尚未上传，文件保留在本页。<button disabled={state.busy || state.conflict} onClick={()=>void editor.add(state.pendingFile!,state.pendingSide,state.pendingSource)}>重试上传</button></p>}
      <label style={{display:'grid',gap:6,margin:'12px 0'}}>品相备注<textarea aria-label="品相备注" rows={2} disabled={state.busy} value={state.condition_notes} onChange={event=>editor.edit({condition_notes:event.target.value})}/></label>
      <button disabled={state.busy || state.conflict || state.selected_photo_ids.length===0} onClick={()=>void editor.generate()}>{state.automaticGenerationPending ? '重试照片文案生成' : '生成文案'}</button><p className="muted">重新生成会替换标题和描述，请先保存需要保留的手工文本。</p>
      <label style={{display:'grid',gap:6,margin:'12px 0'}}>标题<input aria-label="标题" disabled={state.busy} value={state.title} onChange={event=>editor.edit({title:event.target.value})}/></label><button onClick={()=>void copy(state.title,'标题')}>复制标题</button>
      <label style={{display:'grid',gap:6,margin:'12px 0'}}>描述<textarea aria-label="描述" rows={7} disabled={state.busy} value={state.description} onChange={event=>editor.edit({description:event.target.value})}/></label><div className="actions"><button onClick={()=>void copy(state.description,'描述')}>复制描述</button><button disabled={state.busy || state.conflict} onClick={()=>void editor.save()}>保存平台草稿</button></div>
      {copied && <p role="status">{copied}</p>}{state.busy && <p role="status">正在处理，请稍候…</p>}
      <p className="muted">生成依据：已确认的卡片身份与人工品相备注。{state.draft.generation_basis.examples_status === 'used' ? '本人订单标题仅作为格式参考。' : '订单样例暂不可用，使用基础模板。'}</p>
    </>}
  </section>;
}
