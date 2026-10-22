import React, { useEffect, useState, useRef, useLayoutEffect } from "react";
import { ApiError, type MobileApi } from "./api";
import type { Card, Evidence, Page } from "./types";
import { parseSearchDraft } from "../../../packages/card-core/src/search";
import {
  imageVariantPath,
  LEGACY_CAPABILITIES,
  type ClientCapabilities,
  type Side,
} from "../../../packages/card-core/src/capture";
import {
  DEFAULT_PORTFOLIO_SORT,
  PORTFOLIO_SORT_OPTIONS,
  portfolioSortQuery,
  type PortfolioSort,
} from "../../../packages/card-core/src/sorting";
import { PrivateImage } from "./PrivateImage";
import { BulkActions, TrashPage } from "./LibraryActions";
import { PurchaseCostPanel } from "./PurchaseCost";
import type { CostSearchResult } from "./purchaseCostApi";
import { CardManagement } from "./CardManagement";
export const statusName = (status?: string | null) =>
  ({
    queued: "排队中",
    running: "处理中",
    identifying: "核验身份中",
    searching: "搜索证据中",
    calculating: "计算估值中",
    succeeded: "已完成",
    failed: "处理失败",
    cancelled: "已取消",
    insufficient_evidence: "证据不足",
    completed: "已完成",
  })[status ?? ""] ??
  status ??
  "尚未估值";
export function LibraryPage({
  api,
  onOpen,
  capabilities = LEGACY_CAPABILITIES,
  active = true,
  ownerId,
}: {
  active?: boolean;
  ownerId?: string;
  capabilities?: ClientCapabilities;
  api: MobileApi;
  onOpen: (id: string) => void;
}) {
  const [query, setQuery] = useState(""),
    [search, setSearch] = useState(""),
    [holding, setHolding] = useState(""),
    [page, setPage] = useState(1),
    [data, setData] = useState<Page | null>(null),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [trash, setTrash] = useState(false);
  const [costItem, setCostItem] = useState<{id:string; result?:CostSearchResult}|null>(null);
  const [sort, setSort] = useState<PortfolioSort>(DEFAULT_PORTFOLIO_SORT);
  const snapshot = useRef<string | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const busy = useRef(false);
  const sentinel = useRef<HTMLDivElement>(null);
  const scroll = useRef(0);
  const updated = useRef(0);
  const dirty = useRef(false);
  const loadedPages = useRef(1);
  const refreshPages = useRef(1);
  useLayoutEffect(() => {
    if (!active) return;
    window.scrollTo(0, scroll.current);
    const save = () => { scroll.current = window.scrollY; };
    window.addEventListener("scroll", save, { passive: true });
    return () => window.removeEventListener("scroll", save);
  }, [active]);
  useEffect(() => api.subscribeLibrary?.(() => {
    dirty.current = true;
    refresh();
  }), [api, active]);
  useEffect(() => {
    const revalidate = () => {
      if (active && !document.hidden && (dirty.current || (updated.current && Date.now() - updated.current >= 60000))) refresh();
    };
    revalidate();
    document.addEventListener("visibilitychange", revalidate);
    return () => document.removeEventListener("visibilitychange", revalidate);
  }, [active]);
  function refresh() {
    refreshPages.current = loadedPages.current;
    snapshot.current = undefined;
    setPage(1);
    setRevision(v => v + 1);
  }
  function resetPage() {
    setSelected(new Set());
    setCostItem(null);
    loadedPages.current = 1;
    refreshPages.current = 1;
    setData(null);
    setRevision(v => v + 1);
    updated.current = 0;
    scroll.current = 0;
    snapshot.current = undefined;
    setPage(1);
  }
  useEffect(() => {
    let active = true;
    busy.current = true;
    setLoading(true);
    setError("");
    const targetPage = page === 1 ? refreshPages.current : page;
    const request = (number: number, token?: string) => {
      const params = new URLSearchParams({
        page: String(number), page_size: "30",
        search: JSON.stringify(parseSearchDraft({ q: search, holding_status: holding })),
      });
      const sorting = capabilities.portfolio_sort
        ? portfolioSortQuery(sort, token) : "";
      return api.get<Page>(`/v1/portfolio/items/page?${params}${sorting}`);
    };
    const load = async () => {
      let value = await request(page, snapshot.current);
      // Revalidate the already-loaded window atomically, keeping old cards visible.
      if (page === 1) {
        const items = [...value.items];
        for (let next = 2; active && next <= Math.min(targetPage, value.total_pages); next++) {
          value = await request(next, value.snapshot_token ?? undefined);
          items.push(...value.items);
        }
        value = { ...value, items: [...new Map(items.map(item => [item.id, item])).values()] };
      }
      return value;
    };
    load()
      .then((value) => {
        if (active) {
          snapshot.current = value.snapshot_token ?? undefined;
          setData(previous => page === 1 || !previous ? value : {
            ...value,
            items: [...new Map([...previous.items, ...value.items].map(item => [item.id, item])).values()],
          });
          loadedPages.current = value.page;
          refreshPages.current = 1;
          updated.current = Date.now();
          dirty.current = false;
        }
      })
      .catch((e) => {
        if (active) {
          if (e instanceof ApiError && e.status === 409 && snapshot.current) {
            refresh();
          } else setError(e.message);
        }
      }).finally(() => {
        if (active) { busy.current = false; setLoading(false); }
      });
    return () => {
      active = false;
    };
  }, [api, search, holding, page, revision, sort, capabilities.portfolio_sort]);
  function more() {
    if (!busy.current && !error && data && data.page < data.total_pages) {
      busy.current = true;
      setPage(data.page + 1);
    }
  }
  useEffect(() => {
    if (!active || trash || costItem || loading || error || !sentinel.current || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) more();
    }, { rootMargin: "500px" });
    observer.observe(sentinel.current);
    return () => observer.disconnect();
  }, [active, trash, costItem, loading, error, data]);
  return (
    <>
      {trash && ownerId && <TrashPage api={api} ownerId={ownerId} onBack={() => setTrash(false)} onChanged={refresh} />}
      {costItem && <section className="cost-focus" aria-label="所选卡片成本"><button onClick={() => setCostItem(null)}>‹ 返回卡库</button><h1>购入成本</h1><PurchaseCostPanel key={costItem.id} api={api} itemId={costItem.id} initialResult={costItem.result} onChanged={refresh} /></section>}
      <div hidden={trash || !!costItem}>
      <div className="heading">
        <div>
          <p className="eyebrow">MY COLLECTION</p>
          <h1>我的卡库</h1>
        </div>
        <button
          onClick={() => {
            refresh();
          }}
        >
          刷新
        </button>
      </div>
      {ownerId && <div className="actions"><button onClick={() => { setCostItem(null); setTrash(true); }}>回收站</button></div>}
      <form
        className="search"
        onSubmit={(e) => {
          e.preventDefault();
          resetPage();
          setSearch(query.trim());
        }}
      >
        <input
          aria-label="搜索卡片"
          placeholder="球员、品牌、卡号…"
          value={query}
          maxLength={160}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button>搜索</button>
      </form>
      <label>
        持仓状态
        <select
          aria-label="持仓状态"
          value={holding}
          onChange={(e) => {
            resetPage();
            setHolding(e.target.value);
          }}
        >
          <option value="">全部卡片</option>
          <option value="held">持有中</option>
          <option value="sold">已出售</option>
        </select>
      </label>
      {capabilities.portfolio_sort && (
        <div className="actions">
          <label>
            全库排序
            <select
              aria-label="全库排序"
              value={sort.sort_by}
              onChange={(e) => {
                resetPage();
                setSort({
                  ...sort,
                  sort_by: e.target.value as PortfolioSort["sort_by"],
                });
              }}
            >
              {PORTFOLIO_SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            顺序
            <select
              aria-label="排序方向"
              value={sort.order}
              onChange={(e) => {
                resetPage();
                setSort({
                  ...sort,
                  order: e.target.value as PortfolioSort["order"],
                });
              }}
            >
              <option value="desc">降序</option>
              <option value="asc">升序</option>
            </select>
          </label>
        </div>
      )}
      {error && <p role="alert" className="error">{error} <button disabled={loading} onClick={() => setRevision(v => v + 1)}>重试</button></p>}
      {!data ? (
        <p className="muted">正在加载卡库…</p>
      ) : (
        <>
          <p className="muted">共 {data.total} 张卡片</p>
          {ownerId && <BulkActions api={api} ownerId={ownerId} items={data.items} selected={selected} onSelectionChange={setSelected} onChanged={refresh} active={active && !trash && !costItem} onOpenCost={(id,result) => setCostItem({id,result})} />}
          {data.snapshot_at && (
            <p className="small muted">
              列表更新于{" "}
              {new Date(data.snapshot_at).toLocaleTimeString("zh-CN")} ·
              刷新获取最新结果
            </p>
          )}
          {data.items.length === 0 ? (
            <section className="empty">
              <span>◇</span>
              <h2>{search ? "没有找到匹配卡片" : "你的收藏，从第一张开始"}</h2>
              <p>
                {search
                  ? "试试其他球员、品牌或卡号。"
                  : "前往「录卡」拍摄正反面，识别后确认入库。"}
              </p>
            </section>
          ) : (
            <div className="collection-grid">
              {data.items.map((card) => (
                <article className="panel card-tile" key={card.id}>
                  {ownerId && <label className="check card-select"><input type="checkbox" aria-label={`选择 ${card.player_name}`} checked={selected.has(card.id)} onChange={event => {const checked=event.target.checked;setSelected(current => {const next=new Set(current);if(checked)next.add(card.id);else next.delete(card.id);return next;});}} />选择</label>}
                  <PrivateImage
                    api={api}
                    path={
                      capabilities.image_variants
                        ? imageVariantPath(
                            card.id,
                            card.image_front_url ? "front" : "back",
                            card.image_front_url ?? card.image_back_url,
                            "thumb",
                          )
                        : (card.image_front_url ?? card.image_back_url)
                    }
                    originalPath={card.image_front_url ?? card.image_back_url}
                    alt={`${card.player_name}正面`}
                  />
                  <button
                    className="card-title"
                    onClick={() => onOpen(card.id)}
                  >
                    <strong>{card.player_name}</strong>
                    <span>
                      {card.season_start} · {card.product_name}
                    </span>
                    <span className="card-prices">
                      <span className="card-price-row">
                        <span>成本价</span>
                        <span className="card-price-amount">
                          {card.purchase_price != null
                            ? `${card.purchase_currency ?? ""} ${card.purchase_price}`.trim()
                            : "未录入"}
                        </span>
                      </span>
                      <span className="card-price-row">
                        <span>估价</span>
                        <span className={card.fair_value != null ? "card-price-amount value" : "card-price-empty"}>
                          {card.fair_value != null
                            ? `${card.valuation_currency ?? ""} ${card.fair_value}`.trim()
                            : statusName(card.valuation_status)}
                        </span>
                      </span>
                    </span>
                  </button>
                  {ownerId && <div className="actions tile-actions"><button onClick={() => setCostItem({id:card.id})}>获取成本</button><button onClick={() => onOpen(card.id)}>估值与管理</button></div>}
                </article>
              ))}
            </div>
          )}
          <div className="pagination" ref={sentinel}>
            <span>{data.page} / {data.total_pages}</span>
            {data.page < data.total_pages && <button disabled={loading} onClick={more}>加载更多</button>}
            {loading && <span role="status">正在加载…</span>}
          </div>
        </>
      )}
      </div>
    </>
  );
}
function safeLink(url?: string | null) {
  try {
    const parsed = new URL(url ?? "");
    return ["https:", "http:"].includes(parsed.protocol)
      ? parsed.href
      : undefined;
  } catch {
    return undefined;
  }
}
export function DetailPage({
  api,
  id,
  onBack,
  capabilities = LEGACY_CAPABILITIES,
  onSupplement,
  ownerId,
  baseCurrency = "CNY",
}: {
  api: MobileApi;
  id: string;
  capabilities?: ClientCapabilities;
  onSupplement?: (side: Side) => Promise<void>;
  ownerId?: string;
  baseCurrency?: string;
  onBack: () => void;
}) {
  const [revision, setRevision] = useState(0);
  const [supplementing, setSupplementing] = useState(false);
  const [card, setCard] = useState<Card | null>(null),
    [evidence, setEvidence] = useState<Evidence | null>(null),
    [error, setError] = useState(""),
    [evidenceError, setEvidenceError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .get<{ item: Card }>(`/v1/portfolio/items/${id}/detail`)
      .then((v) => {
        if (active) setCard(v.item);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    api
      .get<Evidence>(`/v1/portfolio/items/${id}/automated-valuation`)
      .then((v) => {
        if (active) setEvidence(v);
      })
      .catch((e) => {
        if (active) setEvidenceError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api, id, revision]);
  return (
    <>
      <button onClick={onBack}>‹ 返回卡库</button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {card ? (
        <>
          <p className="eyebrow">CARD DETAILS</p>
          <h1>{card.player_name}</h1>
          <p className="muted">
            {card.season_start} · {card.brand_name} · {card.product_name}
          </p>
          <div className="photo-grid">
            {(["front", "back"] as const).map((side) => (
              <PrivateImage
                key={side}
                api={api}
                path={
                  capabilities.image_variants
                    ? imageVariantPath(
                        card.id,
                        side,
                        card[`image_${side}_url`],
                        "preview",
                      )
                    : card[`image_${side}_url`]
                }
                originalPath={card[`image_${side}_url`]}
                alt={side === "front" ? "卡片正面" : "卡片背面"}
              />
            ))}
          </div>
          {capabilities.supplement &&
            onSupplement &&
            (["front", "back"] as const)
              .filter((side) => !card[`image_${side}_url`])
              .map((side) => (
                <button
                  key={side}
                  disabled={supplementing}
                  onClick={async () => {
                    setSupplementing(true);
                    try {
                      await onSupplement(side);
                    } catch (e) {
                      setError(
                        e instanceof Error ? e.message : "无法创建补拍草稿",
                      );
                      setSupplementing(false);
                    }
                  }}
                >
                  补拍{side === "front" ? "正面" : "背面"}
                </button>
              ))}
          <section className="panel">
            <dl>
              <dt>卡号 / 版本</dt>
              <dd>
                {card.card_number ?? "未知"} / {card.parallel_name ?? "未标注"}
              </dd>
              <dt>评级</dt>
              <dd>
                {card.grade_company ?? "未评级"} {card.grade_value ?? ""}
              </dd>
              <dt>成本</dt>
              <dd>
                {card.purchase_currency ?? ""} {card.remaining_cost}
              </dd>
              <dt>市场估值</dt>
              <dd>
                {card.fair_value
                  ? `${card.valuation_currency ?? ""} ${card.fair_value}`
                  : "尚无可靠估值"}
                {card.fair_value && card.valuation_status !== "succeeded" && (
                  <small> 历史参考；本轮{statusName(card.valuation_status)}，未验证此价格</small>
                )}
              </dd>
            </dl>
          </section>
          {ownerId && <>
            <PurchaseCostPanel api={api} itemId={id} onChanged={() => setRevision(v => v + 1)} />
            <CardManagement api={api} id={id} ownerId={ownerId} baseCurrency={baseCurrency} onChanged={() => setRevision(v => v + 1)} onDeleted={onBack} />
          </>}
          <section className="panel">
            <details>
              <summary>估值与成交证据</summary>
              <p>{statusName(evidence?.status ?? card.valuation_status)}</p>
              {(evidence?.error_message ||
                card.valuation_error ||
                evidenceError) && (
                <p className="notice">
                  {evidence?.error_message ||
                    card.valuation_error ||
                    evidenceError}
                </p>
              )}
              {evidence?.comparables.length ? (
                evidence.comparables.map((comp) => (
                  <div className="evidence" key={comp.id}>
                    <strong>
                      {comp.currency} {comp.sale_price}
                    </strong>
                    <p>{comp.listing_title ?? "成交参考"}</p>
                    {safeLink(comp.source_url) && (
                      <a
                        href={safeLink(comp.source_url)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        查看来源 ↗
                      </a>
                    )}
                  </div>
                ))
              ) : (
                <p className="muted">
                  暂未取得可靠成交证据，不以采购成本代替市场价格。
                </p>
              )}
            </details>
          </section>
        </>
      ) : (
        !error && <p>加载详情…</p>
      )}
    </>
  );
}
