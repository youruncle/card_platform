import React, { useEffect, useState } from "react";
import { priceIdentity, sortPlayers, marketReference, marketCards, marketWindowDescription, priceSummary, marketDescription, rejectionDescription, type PriceBoard, type PricePlayer, type PriceCard } from "../../../packages/card-core/src/player-prices";
import type { MobileApi } from "./api";

const money = (value: string | null) => value === null ? "—" : `¥${Number(value).toFixed(2)}`;
const change = (value: string | null) => value === null ? "数据不足" : `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(2)}%`;
function sourceLink(value: string) {
  try { const url = new URL(value); return url.protocol === "https:" && ["cardhobby.com.cn", "www.cardhobby.com.cn"].includes(url.hostname) && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}
function PlayerRow({player,onRefresh,disabled,reportDate}: {player: PricePlayer;onRefresh:()=>void;disabled:boolean;reportDate:string}) {
  const reference=marketReference(player,reportDate);
  const cards=marketCards(player,reportDate);
  const monthly=Boolean(player.market);
  const renderCards=(items:PriceCard[],monthly:boolean)=>(items.map(card => <section className="price-card" key={card.card_key}>
      <h2>{priceIdentity(card.identity)}</h2>
      <p className="small muted">{monthly&&card.card_key===reference?.card_key?"参考卡 · 权重100% · ":""}近90天 {card.selection_count} 笔 · {change(card.change_pct)}</p>
      <div className="price-windows"><div><small>{monthly?"此前30日中位价":"前日7天中位价"}</small><strong>{money(card.previous_price)}</strong><small>{card.previous_count} 笔</small></div>
        <div><small>{monthly?"近30日中位价":"当前7天中位价"}</small><strong>{money(card.current_price)}</strong><small>{card.current_count} 笔</small></div></div>
      {card.latest_sale_at && <p className="small muted">最近成交 {new Date(card.latest_sale_at).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai"})}</p>}
      {card.sources.length > 0 && <details><summary>查看成交来源</summary>{card.sources.map(source => <p className="small" key={source.source_id}>
        {sourceLink(source.source_url) ? <a href={sourceLink(source.source_url)} target="_blank" rel="noopener noreferrer">{source.raw_title} · {money(source.sale_price)}</a> : <span>{source.raw_title} · {money(source.sale_price)}</span>}
      </p>)}</details>}
    </section>));
  return <section className="panel price-player" role="group" aria-label={player.player_name}><details>
    <summary><span>{player.player_name}</span><strong className={Number(reference?.change_pct) > 0 ? "price-up" : Number(reference?.change_pct) < 0 ? "price-down" : ""}>
      {reference?change(reference.change_pct):(player.market?player.market.report_date!==reportDate:player.status === "stale") ? "待更新" : "可比样本不足"}
    </strong></summary>
    <p className="small muted">最近尝试：{player.refresh.last_attempt_at?new Date(player.refresh.last_attempt_at).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"}):"尚未采集"} · 最近成功统计：{player.refresh.last_success_at?new Date(player.refresh.last_success_at).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"}):"尚未完成"}</p>
    {monthly&&<p className="small muted">行情截至 {player.market!.report_date} · 近30日与此前30日样本比较</p>}
    {renderCards(cards,monthly)}
    {player.market?.cards_truncated&&<p className="small muted">展示参考卡及最多20种卡款，概览覆盖全部确认成交。</p>}
    {monthly&&<p className="small muted">三卡指数：{player.change_pct===null?"综合指数暂缺":change(player.change_pct)} · {player.cards.filter(c=>c.status==="ready").length}/3 种代表卡满足7日窗口条件</p>}
    {monthly&&player.cards.length>0&&<details><summary>查看三卡指数依据 · 7日窗口</summary>{renderCards(player.cards,false)}</details>}
    {rejectionDescription(player)&&<p className="small muted">未纳入原因：{rejectionDescription(player)}</p>}
    {player.coverage.sample_count !== undefined && <p className="small muted">候选 {player.coverage.sample_count} · 可确认 {player.coverage.identified_count ?? 0} · 未纳入 {player.coverage.rejected_count ?? 0}</p>}
    <p className="small muted">{player.basket_frozen ? `代表卡固定于 ${player.basket_selected_on}` : "代表卡待达标，三种均满足7日窗口条件后按月固定。"}</p>
  </details>
    {reference&&<p className="small price-reference" data-price-reference>参考卡（100%）：{priceIdentity(reference.identity)}<br/>近30日 {money(reference.current_price)} · {reference.current_count} 笔<br/>此前30日 {money(reference.previous_price)} · {reference.previous_count} 笔<br/><span className="muted">{marketWindowDescription(player)}</span></p>}
    <p className="small muted">{marketDescription(player,reportDate)}</p>
    <button aria-label={`更新 ${player.player_name} 的成交`} onClick={onRefresh} disabled={disabled||player.refresh.queued}>更新该球员</button>
    {player.status === "stale" && <p className="small muted">三卡7日指数待更新 · 上次统计 {player.report_date}</p>}
    {player.refresh.error && <p className="error">{player.refresh.error}</p>}
    {player.refresh.queued && <p className="small muted">公开成交更新已排队</p>}
    {(player.coverage.budget_exhausted || player.coverage.local_limit_reached) && <p className="small muted">采样已达上限，统计仅覆盖已取得的成交样本。</p>}
  </section>;
}

export function PlayerPricesPage({api, onBack}: {api: MobileApi; onBack: () => void}) {
  const [data, setData] = useState<PriceBoard | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState<"activity" | "gainers" | "losers">("activity");
  const mounted = React.useRef(true);
  useEffect(() => {
    let active = true;
    setError("");
    api.get<PriceBoard>("/v1/player-prices").then(value => { if (active) setData(value); })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : "统计读取失败"); });
    return () => { active = false; };
  }, [api, retry]);
  useEffect(() => {
    if (!data?.players.some(player => player.refresh.queued)) return;
    const timer = setInterval(() => setRetry(n => n + 1), 30000);
    return () => clearInterval(timer);
  }, [data]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  async function refresh(playerId?:string) {
    if(busy) return;
    setBusy(true);setError("");setNotice("");
    try {
      const result=await api.post<{message:string}>(`/v1/player-prices/refresh${playerId?`?player_id=${encodeURIComponent(playerId)}`:""}`);
      if(mounted.current){setNotice(result.message);setRetry(n=>n+1);}
    } catch(e){if(mounted.current)setError(e instanceof Error?e.message:"更新请求失败");}
    finally{if(mounted.current)setBusy(false);}
  }
  const summary=data?priceSummary(data):null;
  return <div className="player-prices-page">
    <div className="price-page-heading"><button onClick={onBack}>‹ 我的</button><h1>球员行情</h1>
      <button aria-label="更新公开成交" disabled={busy || !data || data.worker_enabled === false} onClick={() => void refresh()}>{busy ? "请求中…" : "更新"}</button>
    </div>
    {data && <p className="small muted">报告日 {data.report_date} · 北京时间 · 当前持仓篮球球员</p>}
    <details className="panel small"><summary>同款30日变化 · 每窗至少3笔</summary><p>首屏以样本最活跃的一款同款卡作参考，权重100%，比较近30日与此前30日中位价，每窗至少3笔；卡款与样本量随行披露。三卡7日指数单列在明细。成交价不含运费。</p></details>
    {data?.worker_enabled === false && <p className="small muted">服务器未开启自动采集，展示已保存的公开样本。</p>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert" className="error">{error} <button onClick={() => setRetry(n => n + 1)}>重试</button></p>}
    {!data && !error && <p>正在读取统计…</p>}
    {data && <>
      {summary&&<section className="panel price-overview" aria-label="行情覆盖"><p>跟踪球员 {summary.tracked} · 有确认成交 {summary.with_sales}</p><p>有卡款趋势 {summary.with_trends} · 有综合指数 {summary.with_index}</p>{(summary.queued>0||summary.source_errors>0)&&<p className="small muted">{summary.queued} 位待更新 · {summary.source_errors} 位采集异常</p>}</section>}
      <div className="price-sort"><button aria-pressed={direction === "activity"} onClick={() => setDirection("activity")}>成交优先</button><button aria-pressed={direction === "gainers"} onClick={() => setDirection("gainers")}>涨幅优先</button><button aria-pressed={direction === "losers"} onClick={() => setDirection("losers")}>跌幅优先</button></div>
      <label>搜索球员<input aria-label="搜索球员" value={query} onChange={e => setQuery(e.target.value)} placeholder="球员名称" /></label>
      {data.players.length === 0 && <p className="empty">当前持仓还没有可统计的篮球球员。</p>}
      {sortPlayers(data.players, direction, data.report_date).filter(player => player.player_name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).map(player => <PlayerRow key={player.player_id} player={player} reportDate={data.report_date} onRefresh={()=>void refresh(player.player_id)} disabled={busy||data.worker_enabled===false} />)}
      <p className="small muted">已采集样本中的活跃卡型，并非全市场成交排行。窗口滚动也会带来变化；指标反映卡价变化。</p>
    </>}
  </div>;
}
