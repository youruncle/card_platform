'use client';
import React, { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { AuctionEditor, auctionSideName, type AuctionRequest, type AuctionPhoto } from '../../../packages/card-core/src/auction';
import { assetUrl } from '../lib/api';
import { connectLocalCamera, captureLocalCamera, type CameraConnection } from '../lib/local-camera';
const textAreaStyle: React.CSSProperties = { width: '100%', border: '1px solid var(--line)', background: 'var(--panel-2)', color: 'var(--text)', padding: '11px 12px', borderRadius: 7, font: 'inherit', fontSize: 13, lineHeight: 1.6, resize: 'vertical' };
const browserRequest: AuctionRequest = async <T,>(path: string, options: RequestInit = {}): Promise<T> => {
  const signal = options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(180000)]) : AbortSignal.timeout(180000);
  const response = await fetch(assetUrl(path)!, { ...options, signal, credentials: 'include' });
  if (response.status === 401) window.dispatchEvent(new Event('card-platform:unauthorized'));
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw Object.assign(new Error(typeof body.detail === 'string' ? body.detail : body.detail?.message || `草稿请求失败（${response.status}）`), { status: response.status }); }
  return response.json();
};
function DraftPhoto({photo}: {photo: AuctionPhoto}) {
  const [src, setSrc] = useState(''), [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let url = ''; setSrc(''); setError(false);
    void fetch(assetUrl(photo.url)!, { credentials: 'include', signal: controller.signal }).then(async response => { if (!response.ok) throw new Error('图片读取失败'); const blob = await response.blob(); if (!controller.signal.aborted) { url = URL.createObjectURL(blob); setSrc(url); } }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [photo.url]);
  return src ? <img src={src} alt={`${auctionSideName(photo.side)}草稿照片`} style={{width:100,height:130,objectFit:'contain',borderRadius:6,background:'var(--panel-2)'}}/> : <span style={{width:100,height:130,display:'grid',placeItems:'center',color:'var(--muted)'}}>{error ? '图片读取失败' : '图片加载中…'}</span>;
}
export function AuctionDraftPanel({ itemId, onClose, request = browserRequest }: { itemId: string; onClose: () => void; request?: AuctionRequest }) {
  const editor = useMemo(() => new AuctionEditor(request), [request, itemId]);
  const state = useSyncExternalStore(editor.subscribe, editor.snapshot, editor.snapshot);
  const [side, setSide] = useState<AuctionPhoto['side']>('front');
  const [connection, setConnection] = useState<CameraConnection | null>(null), [serial, setSerial] = useState('');
  const [cameraBusy, setCameraBusy] = useState(false), [cameraError, setCameraError] = useState(''), [copied, setCopied] = useState('');
  const cameraController = useRef<AbortController | null>(null), dialog = useRef<HTMLDivElement>(null);
  useEffect(() => { void editor.open(itemId); return () => { editor.dispose(); cameraController.current?.abort(); }; }, [editor, itemId]);
  useEffect(() => { const prior = document.activeElement as HTMLElement | null; dialog.current?.focus(); return () => prior?.focus(); }, []);
  const busy = state.busy || cameraBusy;
  async function camera(capture: boolean) {
    if (busy) return;
    const controller = new AbortController(); cameraController.current = controller; setCameraBusy(true); setCameraError('');
    try { if (capture && connection) { const file = await captureLocalCamera(connection, serial, controller.signal); if (!controller.signal.aborted) await editor.add(file, side, 'redmi'); } else { const next = await connectLocalCamera(controller.signal); if (!controller.signal.aborted) { setConnection(next); setSerial(next.devices[0].serial); } } }
    catch (error) { if (!controller.signal.aborted) setCameraError(error instanceof Error ? error.message : '拍照失败'); }
    finally { if (!controller.signal.aborted) setCameraBusy(false); }
  }
  async function copy(value: string, label: string) { try { await navigator.clipboard.writeText(value); setCopied(`${label}已复制`); } catch { setCopied('复制失败，请在文本框中手动选择复制'); } }
  return <div className="modal-backdrop">
    <div className="modal" ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-label="单卡拍卖草稿" style={{width:'min(840px, 100%)',maxWidth:840,maxHeight:'90vh',overflow:'auto'}} onKeyDown={event => { if(event.key==='Escape' && !busy) onClose(); if(event.key==='Tab') { const elements=Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled)')??[]);const first=elements[0],last=elements.at(-1);if(event.shiftKey && (document.activeElement===first || document.activeElement===dialog.current)){event.preventDefault();last?.focus();}else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first?.focus();} } }}>
      <div className="modal-heading" style={{alignItems:'center',gap:12}}><h2>单卡拍卖草稿</h2><button className="ghost-button" disabled={busy} onClick={onClose}>关闭</button></div>
      <p>卡淘草稿未接入：{state.draft?.external.reason || '正在核实能力'}。这里保存的是本站平台草稿。</p>
      {(state.error || cameraError) && <p role="alert" className="form-error">{state.error || cameraError}</p>}{state.notice && <p role="status" style={{padding:12,borderRadius:8,background:'var(--accent-soft)',color:'var(--accent)'}}>{state.notice}</p>}
      {state.conflict && <button className="secondary-button" disabled={busy} onClick={() => void editor.refresh()}>读取最新版本并保留本页输入</button>}
      {Object.keys(state.latestConflicts).length > 0 && <aside aria-label="其他端修改对照" style={{padding:14,border:'1px solid var(--line)',borderRadius:8,background:'var(--panel-2)'}}><strong>其他端的最新内容</strong><p>本页保留了你编辑的字段，请对照后再保存。</p>{Object.entries(state.latestConflicts).map(([key,value]) => <div key={key}><b>{({title:'标题',description:'描述',condition_notes:'品相备注',selected_photo_ids:'选中照片'} as Record<string,string>)[key] || key}</b><p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{Array.isArray(value) ? value.join('、') : value}</p></div>)}</aside>}
      {!state.draft ? <><p>{busy ? '正在恢复草稿…' : '草稿尚未载入'}</p><button className="secondary-button" disabled={busy} onClick={() => void editor.open(itemId)}>重试加载</button></> : <>
        <p>选中 {state.selected_photo_ids.length} / 12 张 · 版本 {state.draft.revision}</p>
        <div style={{display:'flex',gap:12,flexWrap:'wrap'}}>{state.draft.photos.map((photo,index) => <label key={photo.id} style={{display:'grid',gap:8,width:132,padding:10,border:state.selected_photo_ids.includes(photo.id)?'1px solid var(--accent)':'1px solid var(--line)',borderRadius:10,background:state.selected_photo_ids.includes(photo.id)?'var(--accent-soft)':'var(--panel)',cursor:'pointer'}}><DraftPhoto photo={photo}/><span style={{display:'flex',alignItems:'center',gap:6}}><input style={{width:'auto',padding:0,margin:0,flexShrink:0}} type="checkbox" aria-label={`选择${auctionSideName(photo.side)}照片 ${index+1}`} checked={state.selected_photo_ids.includes(photo.id)} disabled={busy || state.conflict} onChange={() => editor.select(photo.id)}/>{auctionSideName(photo.side)} · {photo.source === 'existing' ? '已有卡图' : '新照片'}</span></label>)}</div>
        <fieldset disabled={busy || state.conflict} style={{margin:'20px 0',padding:16,border:'1px solid var(--line)',borderRadius:10,display:'grid',gap:12}}><legend style={{padding:'0 6px',fontSize:12,color:'var(--muted)'}}>添加拍卖照片</legend><label>新照片位置 <select value={side} onChange={event=>setSide(event.target.value as AuctionPhoto['side'])}><option value="front">正面</option><option value="back">背面</option><option value="detail">细节</option></select></label> <label>上传照片 <input type="file" accept="image/*" onChange={event => { const file=event.target.files?.[0]; if(file) void editor.add(file, side, 'upload'); event.target.value=''; }}/></label>
          <div style={{display:'flex',flexWrap:'wrap',alignItems:'end',gap:8}}><button className="secondary-button" onClick={()=>void camera(false)}>连接 USB 手机</button>{connection && <><label>拍照手机 <select value={serial} onChange={event=>setSerial(event.target.value)}>{connection.devices.map(device=><option key={device.serial} value={device.serial}>{device.name} · {device.serial}</option>)}</select></label><button className="secondary-button" disabled={!serial} onClick={()=>void camera(true)}>触发 Redmi 快门</button></>}</div><p>先在电脑启动手机拍照助手，手机解锁并允许 USB 调试。新照片上传后自动生成文案。</p>
        </fieldset>
        {state.pendingFile && <p>照片「{state.pendingFile.name}」尚未上传，文件保留在本页。<button className="secondary-button" disabled={busy || state.conflict} onClick={()=>void editor.add(state.pendingFile!,state.pendingSide,state.pendingSource)}>重试上传</button></p>}
        <label style={{display:'grid',gap:6,margin:'12px 0'}}>品相备注<textarea style={textAreaStyle} aria-label="品相备注" rows={2} disabled={busy} value={state.condition_notes} onChange={event=>editor.edit({condition_notes:event.target.value})}/></label>
        <button className="secondary-button" disabled={busy || state.conflict || state.selected_photo_ids.length===0} onClick={()=>void editor.generate()}>{state.automaticGenerationPending ? '重试照片文案生成' : '生成文案'}</button><small> 重新生成会替换标题和描述，请先保存需要保留的手工文本。</small>
        <label style={{display:'grid',gap:6,margin:'12px 0'}}>标题<input aria-label="标题" disabled={busy} value={state.title} onChange={event=>editor.edit({title:event.target.value})}/></label><button className="secondary-button" onClick={()=>void copy(state.title,'标题')}>复制标题</button>
        <label style={{display:'grid',gap:6,margin:'12px 0'}}>描述<textarea style={textAreaStyle} aria-label="描述" rows={7} disabled={busy} value={state.description} onChange={event=>editor.edit({description:event.target.value})}/></label><div className="modal-actions" style={{justifyContent:'space-between',flexWrap:'wrap'}}><button className="secondary-button" onClick={()=>void copy(state.description,'描述')}>复制描述</button><button className="primary-button" disabled={busy || state.conflict} onClick={()=>void editor.save()}>保存平台草稿</button></div>
        {copied && <p role="status">{copied}</p>}{busy && <p role="status">正在处理，请稍候…</p>}
        <p>生成依据：已确认的卡片身份与人工品相备注。{state.draft.generation_basis.examples_status === 'used' ? '本人订单标题仅作为格式参考。' : '订单样例暂不可用，使用基础模板。'}</p>
      </>}
    </div>
  </div>;
}
