"use client";

import { ArrowLeft, CheckCircle2, ExternalLink, ShieldAlert, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  getMarketIdentityConflicts,
  resolveMarketIdentityConflict,
  externalSourceUrl,
  type MarketIdentityConflict,
} from "@/lib/api";

function identityText(identity: Record<string, unknown>) {
  return [
    identity.player_name,
    identity.season_start && identity.season_end
      ? `${identity.season_start}-${String(identity.season_end).slice(-2)}`
      : identity.season_start,
    identity.product_name,
    identity.insert_name,
    identity.parallel_name,
    identity.card_number ? `#${identity.card_number}` : null,
    identity.print_run ? `/${identity.print_run}` : null,
  ].filter(Boolean).join(" · ");
}

function money(value: string, shipping: string, currency: string) {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value) + Number(shipping));
}

export default function MarketDataConflictsPage() {
  const [conflicts, setConflicts] = useState<MarketIdentityConflict[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setConflicts(await getMarketIdentityConflicts());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "成交冲突加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function resolve(
    conflict: MarketIdentityConflict,
    keepMarketCompId?: string,
  ) {
    const action = keepMarketCompId
      ? "保留所选身份并拒绝其他绑定"
      : "拒绝该成交的全部身份绑定";
    if (!window.confirm(`${action}？原始证据会保留，但被拒绝的记录不再参与估价。`)) {
      return;
    }
    const key = `${conflict.source_code}:${conflict.external_id}`;
    setBusyKey(key);
    setMessage("");
    try {
      await resolveMarketIdentityConflict(
        conflict.source_code,
        conflict.external_id,
        keepMarketCompId
          ? { keep_market_comp_id: keepMarketCompId }
          : { reject_all: true },
      );
      await load();
      setMessage(`已处理 ${conflict.external_id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "冲突处理失败");
    } finally {
      setBusyKey("");
    }
  }

  return (
    <main className="market-review-shell">
      <header className="market-review-header">
        <div>
          <a href="/" className="knowledge-back"><ArrowLeft size={16} /> 返回资产台</a>
          <p className="eyebrow">MARKET EVIDENCE REVIEW</p>
          <h1>成交身份冲突复核</h1>
          <p>
            同一底层成交只能属于一个准确卡片身份。未处理记录始终隔离，不参与估价或回测。
          </p>
        </div>
        <div className="market-review-count">
          <ShieldAlert size={22} />
          <div><small>待复核</small><strong>{conflicts.length}</strong></div>
        </div>
      </header>

      {message && <div className="knowledge-message">{message}</div>}

      {loading ? (
        <section className="panel benchmark-empty">正在加载冲突成交…</section>
      ) : conflicts.length === 0 ? (
        <section className="panel market-review-empty">
          <CheckCircle2 size={28} />
          <h2>没有未解决冲突</h2>
          <p>所有底层成交目前只绑定到一个卡片身份。</p>
        </section>
      ) : (
        <section className="market-review-list">
          {conflicts.map((conflict) => {
            const key = `${conflict.source_code}:${conflict.external_id}`;
            const busy = busyKey === key;
            return (
              <article className="panel market-review-card" key={key}>
                <div className="market-review-source">
                  <div>
                    <small>{conflict.source_code} · {conflict.external_id}</small>
                    <h2>{conflict.listing_title || "无成交标题"}</h2>
                    <span>
                      {money(conflict.sale_price, conflict.shipping_price, conflict.currency)}
                      {" · "}{new Date(conflict.occurred_at).toLocaleDateString("zh-CN")}
                    </span>
                  </div>
                  {conflict.source_url && (
                    <a href={externalSourceUrl(conflict.source_url)} target="_blank" rel="noreferrer">
                      查看原始成交 <ExternalLink size={14} />
                    </a>
                  )}
                </div>
                <div className="market-binding-list">
                  {conflict.bindings.map((binding) => (
                    <div className="market-binding" key={binding.market_comp_id}>
                      <div>
                        <strong>{identityText(binding.identity)}</strong>
                        <small>
                          {binding.comparable_tier}
                          {" · "}匹配度 {binding.match_confidence
                            ? `${Math.round(Number(binding.match_confidence) * 100)}%`
                            : "未知"}
                        </small>
                      </div>
                      <button
                        disabled={busy}
                        onClick={() => void resolve(conflict, binding.market_comp_id)}
                      >
                        <CheckCircle2 size={15} /> 保留此身份
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  className="market-reject-all"
                  disabled={busy}
                  onClick={() => void resolve(conflict)}
                >
                  <XCircle size={15} /> 所有身份都不可信
                </button>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}
