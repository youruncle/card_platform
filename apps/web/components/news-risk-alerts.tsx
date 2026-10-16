"use client";

import React, { useEffect, useState } from "react";
import type { NewsAlert } from "../lib/api";

export function NewsRiskAlerts({ alerts = [] }: { alerts?: NewsAlert[] }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const refresh = () => setNow(Date.now());
    const next = Math.min(...alerts.map(a => Date.parse(a.expires_at)).filter(t => t > Date.now()));
    // Cap the timer; recheck after sleep/backgrounding as well as expiry.
    const timer = window.setTimeout(refresh, Math.min(60000, Math.max(1, next - Date.now())));
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [alerts, now]);
  const active = alerts.filter(a => Date.parse(a.expires_at) > Math.max(now, Date.now()));
  if (!active.length) return <span className="muted-copy">—</span>;
  return <div className="news-risk-alerts">{active.map(alert => (
    <details key={alert.id} className={`news-risk-alert ${alert.direction}`}>
      <summary><strong>{alert.direction === "negative" ? "利空" : "利好"}</strong> · {alert.headline}</summary>
      <p><b>新闻事实：</b>{alert.facts}</p>
      <p><b>卡价归因（推断）：</b>{alert.attribution}</p>
      <p>判断置信度：{{ low: "低", medium: "中", high: "高" }[alert.confidence]}</p>
      {/^https?:\/\//i.test(alert.source_url) && <a href={alert.source_url} target="_blank" rel="noopener noreferrer">查看新闻 ↗</a>}
      <small>新闻发布：{new Date(alert.published_at).toLocaleString("zh-CN")}</small>
      <small>首次标注：{new Date(alert.created_at).toLocaleString("zh-CN")}</small>
      <small>到期移除：{new Date(alert.expires_at).toLocaleString("zh-CN")}</small>
    </details>
  ))}</div>;
}
