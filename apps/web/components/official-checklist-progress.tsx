"use client";

import React, { useEffect, useState } from "react";
import { getOfficialChecklistProgress, type OfficialChecklistProgress } from "@/lib/api";

export function OfficialChecklistProgressPanel() {
  const [progress, setProgress] = useState<OfficialChecklistProgress | null>(null);
  const [visible, setVisible] = useState(30);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const result = await getOfficialChecklistProgress();
        if (active) { setProgress(result); setError(""); }
      } catch { if (active) setError("进度暂时无法读取，正在重试"); }
    };
    void load();
    const timer = setInterval(() => { void load(); }, 5000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  if (!progress) return <section className="panel"><p>{error || "正在读取官方清单任务进度…"}</p></section>;
  return <section className="panel knowledge-secondary" aria-label="官方篮球清单任务进度">
    <h2>官方篮球清单自动录入</h2>
    <p>{progress.window?.start} 至 {progress.window?.end} · 每 5 秒更新</p>
    <p><strong>工程完成 {progress.engineering_percent}%</strong></p>
    <progress max={100} value={progress.engineering_percent} aria-label="工程完成百分比" />
    <p>已发现产品录入 {progress.known_products_percent}% · 已发布 {progress.summary.counts.published ?? 0} / {progress.summary.eligible_total ?? progress.summary.total}</p>
    <progress max={100} value={progress.known_products_percent} aria-label="已发现产品录入百分比" />
    <p>{progress.summary.discovery_complete ? `全量成功率 ${progress.summary.success_percent ?? 0}%` : "官方目录尚未穷尽，全量覆盖率暂不可计算"} · 发行日期待确定 {progress.summary.date_unknown}</p>
    <p>失败 {progress.summary.counts.failed ?? 0} · 待下载/处理 {(progress.summary.counts.pending ?? 0) + (progress.summary.counts.downloaded ?? 0)} · 已解析待发布 {progress.summary.counts.parsed ?? 0} · 隔离/解析异常 {progress.products.reduce((sum, product) => sum + (product.issue_count ?? 0), 0)} 行</p>
    {progress.phase_progress && <p>已知候选阶段进度：下载 {progress.phase_progress.downloaded_percent}% · 解析 {progress.phase_progress.parsed_percent}% · 发布 {progress.phase_progress.published_percent}% · 综合 {progress.phase_progress.known_pipeline_percent}%</p>}
    {progress.publishers && <table><thead><tr><th>厂商</th><th>已发现</th><th>已发布 / 候选</th><th>成功率</th><th>失败</th><th>日期未知</th></tr></thead><tbody>
      {Object.entries(progress.publishers).map(([publisher, stats]) => <tr key={publisher}><td>{publisher}</td><td>{stats.total}</td><td>{stats.counts.published ?? 0} / {stats.eligible_total}</td><td>{stats.known_success_percent}%</td><td>{stats.counts.failed ?? 0}</td><td>{stats.date_unknown}</td></tr>)}
    </tbody></table>}
    {progress.source_blockers.map((reason) => <p key={reason}>{reason}</p>)}
    {error && <p role="status">{error}</p>}
    <details><summary>查看逐产品进度和异常</summary>
      <div className="knowledge-compact-list">{progress.products.slice(0, visible).map((product) => <article key={product.key}>
        <strong>{product.title}</strong><span>{product.status} · {product.row_count ?? 0} 条卡片记录 · {product.issue_count ?? 0} 条解析异常</span>
        <small>{product.release_date || "官方发行日期待确定"}{product.knowledge_version ? ` · 知识版本 v${product.knowledge_version}` : ""}</small>
        {product.error && <small>{product.error}</small>}
        {product.source_url && <a href={product.source_url} target="_blank" rel="noreferrer">官方清单</a>}
      </article>)}</div>
      {visible < progress.products.length && <button type="button" onClick={() => setVisible(visible + 30)}>再显示 30 个产品（已显示 {visible} / {progress.products.length}）</button>}
    </details>
  </section>;
}
