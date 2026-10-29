import React, {useEffect, useRef, useState} from 'react';
import {ApiError, type MobileApi} from './api';
import type {Card} from './types';
import {fetchPurchaseCost, type CostSearchResult} from './purchaseCostApi';

type BatchRow = {id:string; name:string; message:string; result?:CostSearchResult; failed?:boolean};
const errorText = (error:unknown) => error instanceof Error ? error.message : '操作失败，请重试';
const cardName = (card: Card) => card.player_name?.trim() || '信息待确认的卡片';
export function BulkActions({api, ownerId, items, selected, onSelectionChange, onChanged, onOpenCost, active=true}: {
  api:MobileApi; ownerId:string; items:Card[]; selected:Set<string>; active?:boolean;
  onSelectionChange:(selection:Set<string>)=>void; onChanged:()=>void;
  onOpenCost:(id:string,result?:CostSearchResult)=>void;
}) {
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[rows,setRows]=useState<BatchRow[]>([]);
  const [mode,setMode]=useState<"delete"|"cost">("cost");
  const operation = useRef<{stop:boolean}|null>(null);
  const mounted = useRef(true);
  const selectedRef=useRef(selected); selectedRef.current=selected;
  const callbacks=useRef({onChanged,onSelectionChange}); callbacks.current={onChanged,onSelectionChange};
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;if(operation.current)operation.current.stop=true;};},[]);
  useEffect(()=>{if(!active && operation.current)operation.current.stop=true;},[active]);
  function selectionIsCurrent() {
    const visible = new Set(items.map(card => card.id));
    const valid = new Set([...selected].filter(id => visible.has(id)));
    if(valid.size === selected.size) return true;
    onSelectionChange(valid);
    setMessage('选择已变化，已移除不在当前列表的卡片，请核对选择后重新操作。');
    return false;
  }
  async function remove() {
    if(operation.current || !selected.size || !selectionIsCurrent() || !window.confirm(`确定删除选中的 ${selected.size} 张卡片？它们将进入回收站，可恢复。`))return;
    const current={stop:false}; operation.current=current;setMode('delete');setBusy(true);setMessage('');setRows([]);
    const ids=[...selected], succeeded=new Set<string>(), failures:BatchRow[]=[];
    // Keep the selected scope fixed while the request is running.
    for(const id of ids) {
      if(current.stop)break;
      try {
        await api.request(`/v1/portfolio/items/${id}`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({owner_id:ownerId,reason:'手机批量删除'})});
        succeeded.add(id);
      }catch(error){
        failures.push({id,name:items.find(card=>card.id===id)?.player_name?.trim() || '信息待确认的卡片',message:errorText(error),failed:true});
        if(error instanceof ApiError && error.status===401){current.stop=true;break;}
      }
    }
    if(succeeded.size)callbacks.current.onChanged();
    if(mounted.current){
      callbacks.current.onSelectionChange(new Set([...selectedRef.current].filter(id=>!succeeded.has(id))));
      setRows(failures);
      setMessage(`已移入回收站 ${succeeded.size} 张，失败 ${failures.length} 张${current.stop?'，后续操作已暂停':''}。失败或未处理的卡片保留选择。`);
      setBusy(false);
    }
    operation.current=null;
  }
  async function costs() {
    if(operation.current || !selected.size || !selectionIsCurrent())return;
    const current={stop:false};operation.current=current;setMode('cost');setBusy(true);setMessage('正在逐张获取所选成本…');setRows([]);
    const cards=items.filter(card=>selected.has(card.id)), results:BatchRow[]=[];
    for(const card of cards){
      if(current.stop)break;
      if(card.purchase_price!=null){results.push({id:card.id,name:cardName(card),message:'保留已有成本'});}
      else {
        try {
          const result=await fetchPurchaseCost(api,card.id,undefined,{refresh:false});
          results.push({id:card.id,name:cardName(card),message:result.reason||result.status,result});
          if(result.status==='recorded')callbacks.current.onChanged();
        }catch(error){
          results.push({id:card.id,name:cardName(card),message:errorText(error),failed:true});
          // Pause at authentication challenges; binding is always a user action.
          if(error instanceof ApiError && (error.status===401 || ['LOGIN_REQUIRED','LOGIN_REJECTED','VERIFICATION_REQUIRED'].includes(error.code??'')))current.stop=true;
        }
      }
      if(mounted.current)setRows([...results]);
    }
    if(mounted.current){
      setMessage(current.stop?'已暂停后续查询，当前已发出的请求可能已保存结果；请核对后重试。':'批量处理完成。需确认或失败的卡片可逐张处理。');
      setBusy(false);
    }
    operation.current=null;
  }
  return <section className="bulk-actions" aria-label="批量管理">
    <div className="actions">
      <button disabled={busy||!items.length} onClick={()=>onSelectionChange(selected.size?new Set():new Set(items.map(card=>card.id)))}>{selected.size?'清除选择':'选择已加载卡片'}</button>
      {selected.size>0 && <><button disabled={busy} onClick={()=>void costs()}>获取选中成本 ({selected.size})</button><button className="danger" disabled={busy} onClick={()=>void remove()}>删除选中 ({selected.size})</button></>}
      {busy && <button onClick={()=>{if(operation.current)operation.current.stop=true;setMessage('将暂停后续操作，等待当前请求完成…');}}>暂停后续操作</button>}
    </div>
    {selected.size>0 && <p className="small muted">已选择 {selected.size} 张；批量获取保留已有成本。单卡可在详情重新获取。</p>}
    {message && <p role="status" className="notice">{message}</p>}
    {rows.length>0 && <div className="batch-results">{rows.map(row=><div className="evidence" key={row.id}><strong>{row.name}</strong><p>{row.message}</p>{mode==='cost'&&(row.failed||row.result?.status==='needs_confirmation') && <button disabled={busy} onClick={()=>onOpenCost(row.id,row.result)}>处理此卡成本</button>}</div>)}</div>}
  </section>;
}

export function TrashPage({api,ownerId,onBack,onChanged}: {api:MobileApi;ownerId:string;onBack:()=>void;onChanged:()=>void}){
  const [cards,setCards]=useState<Card[]|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[revision,setRevision]=useState(0);
  const lock=useRef(false),mounted=useRef(true);
  const changed=useRef(onChanged);changed.current=onChanged;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    let alive=true;setError('');
    api.get<Card[]>(`/v1/portfolio/trash?${new URLSearchParams({owner_id:ownerId})}`).then(value=>{if(alive)setCards(value);}).catch(e=>{if(alive)setError(errorText(e));});
    return()=>{alive=false;};
  },[api,ownerId,revision]);
  async function act(card:Card,permanent=false){
    if(lock.current)return;
    if(permanent && (!window.confirm(`永久删除 ${cardName(card)}？相关交易、估值和图片将清除。`) || !window.confirm('此操作不可恢复。请再次确认永久删除。')))return;
    lock.current=true;setBusy(true);setError('');
    try{
      if(permanent)await api.request(`/v1/portfolio/items/${card.id}/permanent`,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({owner_id:ownerId})});
      else await api.post(`/v1/portfolio/items/${card.id}/restore`,{owner_id:ownerId});
      changed.current();
      if(mounted.current)setCards(previous=>previous?.filter(value=>value.id!==card.id)??null);
    }catch(e){if(mounted.current)setError(errorText(e));}
    finally{lock.current=false;if(mounted.current)setBusy(false);}
  }
  return <>
    <button onClick={onBack}>‹ 返回卡库</button><h1>回收站</h1><p className="muted">移入回收站的卡片可以恢复。永久删除不可撤销。</p>
    <button disabled={busy} onClick={()=>setRevision(v=>v+1)}>刷新回收站</button>
    {error && <p className="error" role="alert">{error} <button disabled={busy} onClick={()=>setRevision(v=>v+1)}>重新加载回收站</button></p>}
    {!cards ? <p>正在读取回收站…</p> : cards.length===0 ? <p className="empty">回收站为空</p> : cards.map(card=><section className="panel" key={card.id}><h2>{cardName(card)}</h2><p>{card.season_start ?? '—'} · {card.product_name ?? '—'}</p><div className="actions"><button disabled={busy} onClick={()=>void act(card)}>恢复卡片</button><button disabled={busy} className="danger" onClick={()=>void act(card,true)}>永久删除</button></div></section>)}
  </>;
}
