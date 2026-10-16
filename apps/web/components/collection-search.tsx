"use client";

import {type ReactNode,FormEvent,useEffect,useRef,useState} from 'react';
import {Search,SlidersHorizontal,X} from 'lucide-react';
import {SearchFilters,boolFilters,textFilters,numberFilters,valuationChoices,filterLabels,searchDraft,parseSearchDraft} from '../lib/portfolio-search';

export function CollectionSearch({filters,total,busy,onApply,currency='CNY',requestError='',onRetry,viewControls}:{filters:SearchFilters;total:number;busy:boolean;onApply:(filters:SearchFilters)=>void;currency?:string;requestError?:string;onRetry?:()=>void;viewControls?:ReactNode}){
  const [draft,setDraft]=useState(()=>searchDraft(filters));
  const [expanded,setExpanded]=useState(false);
  const [error,setError]=useState('');
  const composing=useRef(false);
  const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{setDraft(searchDraft(filters));setError('');},[filters]);
  useEffect(()=>{
    const focus=(event:KeyboardEvent)=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();input.current?.focus();}};
    window.addEventListener('keydown',focus);return()=>window.removeEventListener('keydown',focus);
  },[]);
  const update=(key:string,value:string)=>setDraft(current=>({...current,[key]:value}));
  function submit(event:FormEvent){
    event.preventDefault();if(composing.current)return;
    try{const next=parseSearchDraft(draft);setError('');onApply(next);}catch(e){setError(e instanceof Error?e.message:'搜索条件无效');}
  }
  function remove(key:string){const next={...filters};delete next[key as keyof SearchFilters];onApply(next);}
  const entries=Object.entries(filters);
  return <div className="collection-search">
    <form onSubmit={submit} noValidate aria-label="全库搜索与筛选">
      <div className="collection-search-row">
        <div className="collection-search-input" title="支持中英文别名、部分关键词及轻微英文拼写错误；优先显示最相关结果，纯数字按卡片ID查找"><Search size={19}/><input ref={input} aria-label="搜索全部卡片" placeholder="输入球员、系列、特征… 找最相关的卡片" value={draft.q??''} onChange={e=>update('q',e.target.value)} onCompositionStart={()=>{composing.current=true;}} onCompositionEnd={()=>{composing.current=false;}}/><kbd>⌘ K</kbd></div>
        <button type="submit" className="primary-button">搜索</button>
        <select aria-label="卡片状态" value={draft.holding_status??''} onChange={e=>update('holding_status',e.target.value)}>
          <option value="">全部状态</option><option value="held">持有</option><option value="sold">已卖出</option>
        </select>
        <button type="button" className="secondary-button" aria-expanded={expanded} aria-controls="collection-advanced-search" onClick={()=>setExpanded(v=>!v)}><SlidersHorizontal size={16}/>高级筛选</button>
      </div>
      {expanded&&<div id="collection-advanced-search" className="collection-advanced">
        <p className="collection-search-hint">不同属性同时满足；同一属性多个值用逗号分隔，满足任一即可。金额按账户本位币 {currency}，估值仅含正式估值。</p>
        <div className="collection-filter-grid">
          {textFilters.map(([key,label,placeholder])=><label key={key}>{label}<input aria-label={label} value={draft[key]??''} placeholder={placeholder} onChange={e=>update(key,e.target.value)}/></label>)}
          {boolFilters.map(([key,label])=><label key={key}>{label}<select aria-label={label} value={draft[key]??''} onChange={e=>update(key,e.target.value)}><option value="">全部</option><option value="true">是</option><option value="false">否</option></select></label>)}
          {numberFilters.map(([key,label,min,max,step])=><label key={key}>{label}<input type="number" aria-label={label} min={min} max={max} step={step} value={draft[key]??''} placeholder="不限" onChange={e=>update(key,e.target.value)}/></label>)}
        </div>
        <fieldset className="collection-statuses"><legend>估值状态（可多选）</legend>{valuationChoices.map(([value,label])=><label key={value}><input type="checkbox" checked={(draft.valuation_status??'').split(',').includes(value)} onChange={e=>{const current=(draft.valuation_status??'').split(',').filter(Boolean);update('valuation_status',(e.target.checked?[...current,value]:current.filter(v=>v!==value)).join(','));}}/>{label}</label>)}</fieldset>
      </div>}
      {error&&<p role="alert" className="collection-search-error">{error}</p>}
    </form>
    <div className="collection-search-footer">
    <div className="collection-search-meta" aria-live="polite">
      <span role="status">{busy?'正在搜索全部卡片…':requestError?'搜索失败，请重试':`找到 ${total} 张卡片`}</span>
      {!busy&&requestError&&<button type="button" className="text-button" onClick={onRetry}>重试搜索</button>}
      {entries.length>0&&<button type="button" className="text-button" onClick={()=>onApply({})}>清空条件</button>}
    </div>
    {viewControls}
    </div>
    {entries.length>0&&<div className="collection-search-tags">{entries.map(([key,value])=><button type="button" key={key} aria-label={`移除 ${filterLabels[key]}`} onClick={()=>remove(key)}>
      {filterLabels[key]}：{key==='holding_status'?(value==='sold'?'已卖出':'持有'):key==='valuation_status'?(value as string[]).map(v=>valuationChoices.find(([s])=>s===v)?.[1]??v).join(' / '):Array.isArray(value)?value.join(' / '):typeof value==='boolean'?(value?'是':'否'):value}<X size={13}/>
    </button>)}</div>}
  </div>;
}
