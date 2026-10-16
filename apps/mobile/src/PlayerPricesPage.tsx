import React, { useEffect, useState } from "react";
import { priceIdentity, sortPlayers, type PriceBoard, type PricePlayer } from "../../../packages/card-core/src/player-prices";
import type { MobileApi } from "./api";

const money = (value: string | null) => value === null ? "—" : `¥${Number(value).toFixed(2)}`;
const change = (value: string | null) => value === null ? "数据不足" : `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(2)}%`;
function sourceLink(value: string) {
  try { const url = new URL(value); return url.protocol === "https:" && ["cardhobby.com.cn", "www.cardhobby.com.cn"].includes(url.hostname) && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}
function PlayerRow({player}: {player: PricePlayer}) {
  return <section className="panel price-player" role="group" aria-label={player.player_name}><details>
    <summary><span>{player.player_name}</span><strong className={Number(player.change_pct) > 0 ? "price-up" : Number(player.change_pct) < 0 ? "price-down" : ""}>
      {player.status === "stale" ? "待更新" : player.status === "pending" ? "等待统计" : change(player.change_pct)}
    </strong></summary>
    <p className="small muted">{player.cards.length}/3 种代表卡 · 当前窗口 {player.cards.reduce((n, c) => n + c.current_count, 0)} 笔成交</p>
    {player.cards.map(card => <section className="price-card" key={card.card_key}>
      <h2>{priceIdentity(card.identity)}</h2>
      <p className="small muted">近90天 {card.selection_count} 笔 · {change(card.change_pct)}</p>
      <div className="price-windows"><div><small>前日7天中位价</small><strong>{money(card.previous_price)}</strong><small>{card.previous_count} 笔</small></div>
        <div><small>当前7天中位价</small><strong>{money(card.current_price)}</strong><small>{card.current_count} 笔</small></div></div>
      {card.latest_sale_at && <p className="small muted">最近成交 {new Date(card.latest_sale_at).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai"})}</p>}
      {card.sources.length > 0 && <details><summary>查看成交来源</summary>{card.sources.map(source => <p className="small" key={source.source_id}>
        {sourceLink(source.source_url) ? <a href={sourceLink(source.source_url)} target="_blank" rel="noopener noreferrer">{source.raw_title} · {money(source.sale_price)}</a> : <span>{source.raw_title} · {money(source.sale_price)}</span>}
      </p>)}</details>}
    </section>)}
    {player.coverage.sample_count !== undefined && <p className="small muted">候选 {player.coverage.sample_count} · 可确认 {player.coverage.identified_count ?? 0} · 未纳入 {player.coverage.rejected_count ?? 0}</p>}
    <p className="small muted">{player.basket_frozen ? `代表卡固定于 ${player.basket_selected_on}` : "代表卡待补齐，凑齐三种后按月固定。"}</p>
  </details>
    {player.status === "stale" && <p className="small muted">待更新 · 上次统计 {player.report_date}</p>}
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
  const [direction, setDirection] = useState<"gainers" | "losers">("gainers");
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
  return <>
    <button onClick={onBack}>‹ 返回我的</button>
    <h1>球员卡价涨跌</h1>
    <p className="muted">用三种常见卡的成交变化，观察收藏市场热度。</p>
    {data && <p className="small muted">报告日 {data.report_date} · 北京时间 · 当前持仓篮球球员</p>}
    <p className="panel small">近90天有效成交选出三种代表卡，按月固定；比较两个7日窗口成交中位价，三卡等权。每卡每个窗口至少3笔，成交价不含运费。样本不足不记为0%。</p>
    {data?.worker_enabled === false && <p className="small muted">服务器未开启自动采集，展示已保存的公开样本。</p>}
    <button disabled={busy || !data || data.worker_enabled === false} className="wide" onClick={async () => {
      if (busy) return;
      setBusy(true); setError(""); setNotice("");
      try {
        const result = await api.post<{message: string}>("/v1/player-prices/refresh");
        if (mounted.current) { setNotice(result.message); setRetry(n => n + 1); }
      } catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : "更新请求失败"); }
      finally { if (mounted.current) setBusy(false); }
    }}>{busy ? "请求中…" : "更新公开成交"}</button>
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert" className="error">{error} <button onClick={() => setRetry(n => n + 1)}>重试</button></p>}
    {!data && !error && <p>正在读取统计…</p>}
    {data && <>
      <div className="price-sort"><button aria-pressed={direction === "gainers"} onClick={() => setDirection("gainers")}>涨幅优先</button><button aria-pressed={direction === "losers"} onClick={() => setDirection("losers")}>跌幅优先</button></div>
      <label>搜索球员<input aria-label="搜索球员" value={query} onChange={e => setQuery(e.target.value)} placeholder="球员名称" /></label>
      {data.players.length === 0 && <p className="empty">当前持仓还没有可统计的篮球球员。</p>}
      {sortPlayers(data.players, direction).filter(player => player.player_name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).map(player => <PlayerRow key={player.player_id} player={player} />)}
      <p className="small muted">已采集样本中的活跃卡型，并非全市场成交排行。窗口滚动也会带来变化；指标反映卡价变化。</p>
    </>}
  </>;
}
