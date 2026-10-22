"use client";
import React, {useState} from "react";
import {ArrowDownRight,ArrowUpRight,ArrowLeft,RefreshCw,TrendingUp,FlaskConical,Minus,ExternalLink} from "lucide-react";
import {demoBoard,sortPlayers,marketReference,marketCards,marketWindowDescription,priceIdentity,priceSummary,marketDescription,rejectionDescription,type PriceBoard,type PricePlayer,type PriceCard} from "../lib/player-prices";

function money(value:string|null) {
  return value===null?"—":new Intl.NumberFormat("zh-CN",{style:"currency",currency:"CNY",maximumFractionDigits:2}).format(Number(value));
}
function Change({value}:{value:string|null}) {
  if(value===null) return <span className="pp-change pp-muted">数据不足</span>;
  const n=Number(value),Icon=n>0?ArrowUpRight:n<0?ArrowDownRight:Minus;
  return <span className={`pp-change ${n>0?"pp-up":n<0?"pp-down":"pp-flat"}`}><Icon size={19}/>{n>0?"+":""}{n.toFixed(2)}%</span>;
}
function stamp(value?:string|null) {
  return value?new Date(value).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false}):"尚未更新";
}
function safeSource(url:string) {
  try {const u=new URL(url);return u.protocol==="https:"&&["cardhobby.com.cn","www.cardhobby.com.cn"].includes(u.hostname)?url:null;}catch{return null;}
}
function PlayerRow({player,rank,demo,onRefresh,refreshDisabled,reportDate}:{player:PricePlayer;rank:number;demo:boolean;onRefresh?:()=>void;refreshDisabled:boolean;reportDate:string}) {
  const reference=marketReference(player,reportDate);
  const cards=marketCards(player,reportDate);
  const monthly=Boolean(player.market);
  const renderCards=(items:PriceCard[],monthly:boolean)=>(<div className="pp-cards">{items.map(card=><article className="pp-card" key={card.card_key}>
        <p className="pp-card-label">{monthly?(card.card_key===reference?.card_key?"参考卡 · 权重100%":"确认卡款"):"代表卡"} · 近90天 {card.selection_count} 笔</p>
        <h3>{priceIdentity(card.identity)}</h3><Change value={card.change_pct}/>
        <div className="pp-prices"><div><small>{monthly?"此前30日中位价":"前日7天中位价"}</small><strong>{money(card.previous_price)}</strong><span>{card.previous_count} 笔</span></div>
        <div><small>{monthly?"近30日中位价":"当前7天中位价"}</small><strong>{money(card.current_price)}</strong><span>{card.current_count} 笔</span></div></div>
        <p className="pp-detail-note">最近成交：{stamp(card.latest_sale_at)}</p>
        {!!card.sources.length&&<details className="pp-evidence"><summary>查看成交证据（最多30笔）</summary><ul>{card.sources.map(source=>{
          const href=safeSource(source.source_url);
          return <li key={source.source_id}><div>{href?<a href={href} target="_blank" rel="noreferrer">{source.raw_title}<ExternalLink size={12}/></a>:source.raw_title}</div>
            <small>{stamp(source.occurred_at)} · {money(source.sale_price)}</small></li>;
        })}</ul></details>}
      </article>)}</div>);
  return <details className="pp-player">
    <summary>
      <span className="pp-rank">{reference===null?"—":String(rank).padStart(2,"0")}</span>
      <div className="pp-player-name"><strong data-player-name>{player.player_name}</strong><small>
        {marketDescription(player,reportDate)}
        {player.status==="stale"?` · 旧快照 ${player.report_date}`:""}
        {player.refresh.error?" · 采集异常":""}
        {player.refresh.queued?" · 更新已排队":""}
      </small>
      {reference&&<><small className="pp-reference">参考卡（100%）：{priceIdentity(reference.identity)}</small><small className="pp-reference">近30日 {money(reference.current_price)} · {reference.current_count} 笔 ｜ 此前30日 {money(reference.previous_price)} · {reference.previous_count} 笔</small><small>{marketWindowDescription(player)}</small></>}
      </div>
      {reference?<Change value={reference.change_pct}/>:(player.market?player.market.report_date!==reportDate:player.status==="stale")?<span className="pp-change pp-muted">待更新</span>:<span className="pp-change pp-muted">可比样本不足</span>}<span className="pp-expand">明细 <span>⌄</span></span>
    </summary>
    <div className="pp-detail">
      {player.refresh.error&&<p className="pp-error" role="status">{player.refresh.error}；当前展示已保存结果。</p>}
      {player.status==="stale"&&<p className="pp-error">综合指数尚未更新；上次指数快照为 {player.report_date}，上次球员涨跌 {player.last_change_pct===null?"数据不足":`${Number(player.last_change_pct).toFixed(2)}%`}，不参与本日排名。</p>}
      <p className="pp-detail-note">{demo?"演示样本，不提供真实成交链接。":`最近尝试：${stamp(player.refresh.last_attempt_at)} · 指数生成：${stamp(player.computed_at)} · 最近成功统计：${stamp(player.refresh.last_success_at)}`}</p>
      {player.current_window&&<p className="pp-detail-note">三卡指数窗口 {player.current_window.start.slice(0,10)} — {new Date(new Date(player.current_window.end).getTime()-86400000).toLocaleDateString("zh-CN",{timeZone:"Asia/Shanghai"})} · 前日窗口向前移动一天</p>}
      <div className="pp-row-tools"><strong>{monthly?"卡款行情 · 30日样本变化":"三卡指数 · 7日中位价较昨日变化"}</strong><button className="pp-secondary" aria-label={`更新 ${player.player_name} 的成交`} onClick={onRefresh} disabled={refreshDisabled||!onRefresh||player.refresh.queued}>更新该球员</button></div>
      {monthly&&<p className="pp-detail-note">行情截至 {player.market!.report_date} · 近30日与此前30日比较，每窗至少3笔才显示变化；少量成交只展示价格。综合指数仍需三种卡全部达标。</p>}
      {renderCards(cards,monthly)}
      {!cards.length&&<p className="pp-detail-note">{marketDescription(player,reportDate)}。缺少身份或成交样本时，不生成涨跌。</p>}
      {player.market?.cards_truncated&&<p className="pp-detail-note">展示参考卡及最多20种卡款，概览覆盖全部确认成交。</p>}
      {monthly&&<p className="pp-detail-note">三卡指数：{player.change_pct===null?"综合指数暂缺":`${Number(player.change_pct).toFixed(2)}%`} · {player.cards.filter(c=>c.status==="ready").length}/3 种代表卡满足7日窗口条件{!player.basket_frozen?"；达标后再固定篮子":""}</p>}
      {monthly&&player.cards.length>0&&<details className="pp-index-evidence"><summary>查看三卡指数依据 · 7日窗口</summary>{renderCards(player.cards,false)}</details>}
      {rejectionDescription(player)&&<p className="pp-detail-note">未纳入原因：{rejectionDescription(player)}</p>}
      <div className="pp-coverage"><span>采样范围：{demo?"演示":player.coverage.scope==="local_sample"?"已有公开样本":"卡淘有预算检索"}</span>
        <span>候选 {player.coverage.sample_count??0} · 可确认 {player.coverage.identified_count??0} · 未纳入 {player.coverage.rejected_count??0}</span>
        <span>{player.coverage.pages??0} 页{player.coverage.budget_exhausted||player.coverage.local_limit_reached?" · 已达采样上限":""}</span>
        <span>{player.basket_frozen?`篮子固定于 ${player.basket_selected_on}`:"篮子待补齐"}</span>
      </div>
    </div>
  </details>;
}
export function PlayerPriceBoard({data,error="",loading=false,refreshing=false,onRefresh,onRefreshPlayer,onReload,message=""}:{
  data:PriceBoard;error?:string;loading?:boolean;refreshing?:boolean;onRefresh?:()=>void;onRefreshPlayer?:(playerId:string)=>void;onReload?:()=>void;message?:string;
}) {
  const [demo,setDemo]=useState(false),[direction,setDirection]=useState<"activity"|"gainers"|"losers">("activity"),[search,setSearch]=useState("");
  const shown=demo?demoBoard(data.report_date):data;
  const players=sortPlayers(shown.players,direction,shown.report_date).filter(p=>p.player_name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const summary=priceSummary(shown);
  const ready=shown.players.map(p=>marketReference(p,shown.report_date)).filter((c):c is PriceCard=>c!==null),up=ready.filter(c=>Number(c.change_pct)>0).length,down=ready.filter(c=>Number(c.change_pct)<0).length;
  return <main className="pp-shell">
    <header className="pp-header"><div><a className="pp-back" href="/"><ArrowLeft size={15}/> 返回资产台</a><p className="pp-eyebrow">PLAYER MARKET PULSE</p>
      <h1>球员行情<span>成交与趋势</span></h1><p className="pp-intro">查看已确认成交、同款价格变化与三卡综合指数。</p></div>
      <div className="pp-header-actions"><button onClick={()=>{setDemo(!demo);setSearch("");}} className="pp-secondary"><FlaskConical size={16}/>{demo?"返回真实统计":"查看演示数据"}</button>
        <button data-refresh className="pp-primary" onClick={onRefresh} disabled={demo||refreshing||!onRefresh||shown.worker_enabled===false}><RefreshCw size={16}/>{refreshing?"正在排队…":"更新公开成交"}</button></div></header>
    {demo&&<div className="pp-demo" role="status"><FlaskConical size={18}/><div><strong>演示数据 · 不是真实成交统计</strong><span>用于预览排序和卡片明细，不会写入你的卡库或市场价格。</span></div></div>}
    {!demo&&error&&<div className="pp-error" role="alert">{error} <a href="/">前往资产台</a>{onReload&&<button onClick={onReload}>重试</button>}</div>}
    {!demo&&data.worker_enabled===false&&<div className="pp-message">当前环境未开启自动采集，展示已保存的公开样本。</div>}
    {!demo&&message&&<div className="pp-message" role="status">{message}</div>}
    <p className="pp-detail-note">报告日 {shown.report_date} · 北京时间 · {summary.queued} 位待更新 · {summary.source_errors} 位采集异常</p>
    <section className="pp-stats" aria-label="行情覆盖"><div><small>跟踪球员</small><strong>{summary.tracked}</strong><span>当前持仓篮球球员</span></div>
      <div><small>有确认成交</small><strong>{summary.with_sales}</strong><span>近90日已确认同款的独立成交</span></div>
      <div><small>有卡款趋势</small><strong>{summary.with_trends}</strong><span>30日参考卡 · 上涨 {up} / 下跌 {down}</span></div>
      <div><small>有综合指数</small><strong>{summary.with_index}</strong><span>三卡7日窗口全部达标</span></div></section>
    <section className="pp-method"><TrendingUp size={21}/><details><summary>同款30日变化 · 每窗至少3笔</summary><p>首屏以样本最活跃的一款同款卡作参考，权重100%，比较近30日与此前30日中位价，每窗至少3笔；卡款、价格和样本量随行披露。三卡7日指数单列在明细。所有价格不含运费。</p></details><span>卡淘 · 人民币</span></section>
    <section className="pp-board"><div className="pp-toolbar"><div className="pp-tabs"><button className={direction==="activity"?"active":""} onClick={()=>setDirection("activity")}>成交优先</button><button className={direction==="gainers"?"active":""} onClick={()=>setDirection("gainers")}>涨幅优先</button><button className={direction==="losers"?"active":""} onClick={()=>setDirection("losers")}>跌幅优先</button></div>
      <input type="search" aria-label="搜索球员" placeholder="搜索球员…" value={search} onChange={e=>setSearch(e.target.value)}/></div>
      <div className="pp-table-head"><span>球员 / 确认成交</span><span>同款30日变化</span></div>
      {!demo&&loading?<div className="pp-empty">正在加载每日统计…</div>:players.length?players.map((p,i)=><PlayerRow key={p.player_id} player={p} rank={i+1} demo={demo} reportDate={shown.report_date} onRefresh={onRefreshPlayer?()=>onRefreshPlayer(p.player_id):undefined} refreshDisabled={demo||refreshing||shown.worker_enabled===false}/>):<div className="pp-empty"><TrendingUp size={32}/><h2>{search?"没有匹配的球员":"暂无球员统计"}</h2><p>默认跟踪你活动篮球卡库里的球员。登录后请求更新，或先查看演示数据。</p></div>}
    </section>
    <footer className="pp-footer"><span>已采集样本中的活跃卡型，并非全市场成交排行。</span><span>窗口滚动也会带来变化；数据不足不记作0%。指标反映卡价变化。</span></footer>
  </main>;
}
