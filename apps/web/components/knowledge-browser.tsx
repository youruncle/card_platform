"use client";

import React, { useEffect, useState } from "react";
import {
  browserPath, fetchKnowledge, knowledgeEntityLabels, mediaCategory, mediaKind, mediaTitle, returnQuery, seasonLabel,
  sideLabel, sourceLabel, type KnowledgeBrowserSummary, type KnowledgeCard, type KnowledgeCardDetail,
  type KnowledgeGlossaryEntry, type KnowledgeMedia, type KnowledgePage, type KnowledgeProduct,
  type KnowledgeProductDetail, type KnowledgeSource,
} from "../lib/knowledge-browser";
import { KnowledgeImage, KnowledgeImageViewer, type KnowledgeViewerState } from "./knowledge-image-viewer";

type Phase = "loading" | "ready" | "error" | "preparing";
type Resource<T> = { data: T | null; phase: Phase; error: string; autoRetry?: boolean; retry: () => void };
type Navigate = (changes: Record<string, string | null>, replace?: boolean) => void;
const views = { overview: "总览", products: "产品目录", cards: "卡片检索", gallery: "图片库", glossary: "知识词典" };
const productTabs = { overview: "产品概览", cards: "卡片清单", knowledge: "卡组与平行", images: "产品图库", sources: "来源" };

function useKnowledge<T>(path: string | null): Resource<T> {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string | null; data: T | null; phase: Phase; error: string; autoRetry?: boolean }>({ key: null, data: null, phase: "loading", error: "" });
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (path) {
      setState({ key: path, data: null, phase: "loading", error: "" });
      async function load(retries = 0) {
        try {
          const data = await fetchKnowledge<T>(path!, controller.signal);
          if (!controller.signal.aborted) setState({ key: path, data, phase: "ready", error: "" });
        } catch (error) {
          if (controller.signal.aborted) return;
          const message = error instanceof Error ? error.message : "知识内容加载失败";
          const preparing = message.includes("知识目录索引正在准备");
          setState({ key: path, data: null, phase: preparing ? "preparing" : "error", error: message, autoRetry: preparing && retries < 10 });
          if (preparing && retries < 10) timer = setTimeout(() => void load(retries + 1), 3000);
        }
      }
      void load();
    }
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [path, attempt]);
  return { ...(state.key === path ? state : { data: null, phase: "loading" as const, error: "" }), retry: () => setAttempt(value => value + 1) };
}

function ResourceStatus<T>({ resource }: { resource: Resource<T> }) {
  if (resource.phase === "ready") return null;
  return <div className={`kb-status kb-status-${resource.phase}`} role={resource.phase === "error" ? "alert" : "status"}>
    <strong>{resource.phase === "loading" ? "正在加载已发布知识…" : resource.error}</strong>
    {resource.phase === "preparing" && <p>{resource.autoRetry ? "正在建立当前版本的浏览目录，会自动重试。" : "自动重试已结束，可稍后点击立即重试。"}准备期间不代表知识内容为空。</p>}
    {resource.phase !== "loading" && <button onClick={resource.retry}>立即重试</button>}
  </div>;
}

export function KnowledgeBrowser() {
  const [query, setQuery] = useState<string | null>(null);
  const summary = useKnowledge<KnowledgeBrowserSummary>("/summary");
  const [viewer, setViewer] = useState<KnowledgeViewerState | null>(null);
  useEffect(() => {
    const read = () => setQuery(window.location.search.slice(1));
    read(); window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  const params = new URLSearchParams(query ?? "");
  const currentView = params.get("view") ?? "overview";
  const view = currentView in views ? currentView as keyof typeof views : "overview";
  const navigate: Navigate = (changes, replace = false) => {
    const next = new URLSearchParams(query ?? "");
    for (const [key, value] of Object.entries(changes)) value ? next.set(key, value) : next.delete(key);
    go(next, replace);
  };
  function go(next: URLSearchParams, replace = false) {
    const search = next.toString();
    window.history[replace ? "replaceState" : "pushState"]({}, "", `/knowledge${search ? `?${search}` : ""}`);
    setQuery(search);
  }
  function openProduct(id: string) {
    navigate({ view: "products", product_id: id, product_tab: "overview", product_return: `?${params}`, card_id: null, card_return: null, card_offset: null, media_offset: null });
  }
  function openCard(id: string) {
    navigate({ card_id: id, card_return: params.has("card_id") ? params.get("card_return") : `?${params}` });
  }
  function openImages(images: KnowledgeMedia[], index: number, compare: boolean, opener: HTMLElement) {
    opener.focus(); setViewer({ images, index, compare, opener });
  }
  const versionId = summary.data?.version_id ?? undefined;
  const productId = params.get("product_id");
  const cardId = params.get("card_id");
  return <main className="kb-shell">
    <header className="kb-header">
      <div><a className="kb-back" href="/">← 返回资产台</a><p className="kb-eyebrow">PUBLISHED KNOWLEDGE</p><h1>卡牌知识库</h1><p>从制造商和赛季探索产品、卡片、图片与已审核知识。</p></div>
      <div className="kb-header-meta"><span className="kb-version">已发布 {summary.data?.version == null ? "版本未知" : `v${summary.data.version}`}</span><a href="/knowledge/manage">知识管理 ↗</a></div>
    </header>
    <nav className="kb-nav" aria-label="知识库导航">{Object.entries(views).map(([key, label]) => <button key={key} aria-current={view === key ? "page" : undefined} onClick={() => navigate({ view: key, product_id: null, product_return: null, product_tab: null, card_id: null, card_return: null, offset: null })}>{label}</button>)}</nav>
    <ResourceStatus resource={summary} />
    {summary.data && query !== null && <>
      <div className="kb-version-note">当前浏览固定于 v{summary.data.version ?? "未知"} · 已知发布内容，<strong>全量覆盖率未知</strong>。设计样张不计作具体卡片正反面。</div>
      {view !== "overview" && summary.data.issues.length > 0 && <div className="kb-source-notice">当前版本有 {summary.data.issues.length} 项来源问题。<button onClick={() => navigate({ view: "overview", product_id: null, card_id: null })}>查看来源缺口</button></div>}
      {cardId ? <CardDetail id={cardId} versionId={versionId} openCard={openCard} openProduct={openProduct} onImages={openImages} onBack={() => go(returnQuery(params.get("card_return"), params, ["card_id", "card_return"]))} />
        : productId && view === "products" ? <ProductDetail key={productId} id={productId} params={params} summary={summary.data} versionId={versionId} navigate={navigate} openCard={openCard} openProduct={openProduct} onImages={openImages} onBack={() => go(returnQuery(params.get("product_return"), params, ["product_id", "product_tab", "product_return", "card_q", "section", "parallel", "card_offset"]))} />
          : view === "overview" ? <Overview summary={summary.data} navigate={navigate} />
            : view === "products" ? <ProductList params={params} summary={summary.data} versionId={versionId} navigate={navigate} openProduct={openProduct} />
              : view === "cards" ? <CardList params={params} summary={summary.data} versionId={versionId} navigate={navigate} openCard={openCard} />
                : view === "gallery" ? <GalleryList params={params} versionId={versionId} navigate={navigate} openProduct={openProduct} onImages={openImages} />
                  : <Glossary params={params} summary={summary.data} versionId={versionId} navigate={navigate} />}
    </>}
    {viewer && <KnowledgeImageViewer viewer={viewer} onClose={() => setViewer(null)} />}
  </main>;
}

function Overview({ summary, navigate }: { summary: KnowledgeBrowserSummary; navigate: Navigate }) {
  const metrics = [["制造商", summary.summary.manufacturers], ["已知产品", summary.summary.products], ["卡片版本事实", summary.summary.card_facts], ["参考图片", summary.summary.images], ["逐卡正反面配对", summary.summary.paired_cards]] as const;
  return <div className="kb-stack">
    <section className="kb-metrics" aria-label="已发布内容统计">{metrics.map(([label, count]) => <div key={label}><small>{label}</small><strong>{count.toLocaleString()}</strong></div>)}</section>
    <section className="kb-panel"><div className="kb-section-heading"><div><h2>按制造商探索</h2><p>保留已有知识但尚无产品的制造商；未知字段不推断。</p></div></div>
      <div className="kb-manufacturers">{summary.manufacturers.map(item => <button key={item.name} onClick={() => navigate({ view: "products", manufacturer: item.name, offset: null, q: null, product_id: null })}><strong>{item.name}</strong><span>{item.product_count} 产品 · {item.card_count.toLocaleString()} 卡片事实</span><small>{item.image_count} 张图片{item.product_count === 0 && " · 暂无已发布产品"}</small><b aria-hidden="true">↗</b></button>)}</div>
      {!summary.manufacturers.length && <Empty>暂无已发布制造商</Empty>}
    </section>
    <section className="kb-panel"><h2>按赛季探索</h2><div className="kb-chips">{summary.seasons.map(season => <button key={season} onClick={() => navigate({ view: "products", season: String(season), manufacturer: null, q: null, offset: null })}>{season} 起始年</button>)}</div>{!summary.seasons.length && <p className="kb-muted">暂无可核实的赛季信息。</p>}</section>
    <section className="kb-panel"><h2>知识词典</h2><div className="kb-chips">{summary.entity_types.map(item => <button key={item.type} onClick={() => navigate({ view: "glossary", entity_type: item.type, q: null, offset: null })}>{knowledgeEntityLabels[item.type] ?? item.type}<span>{item.count.toLocaleString()}</span></button>)}</div></section>
    <section className="kb-panel"><h2>来源与内容缺口</h2><p className="kb-muted">统计反映当前发布快照；来源失败或缺图会保留，不代表其他产品完整。</p>{summary.issues.length ? <ul className="kb-issues">{summary.issues.map((issue, i) => <li key={`${issue.source_key}-${i}`}><strong>{issue.source_key}</strong><span>{issue.message}</span></li>)}</ul> : <p className="kb-muted">当前快照未报告来源错误。全量资料完整性仍未知。</p>}</section>
  </div>;
}

function Empty({ children }: { children: React.ReactNode }) { return <div className="kb-empty"><strong>{children}</strong><p>可清除筛选或切换其他目录。未采集内容不会自动补为已知事实。</p></div>; }
function Pagination({ total, offset, limit, onPage }: { total: number; offset: number; limit: number; onPage: (offset: number) => void }) {
  return <div className="kb-pagination"><span>共 {total.toLocaleString()} 条{total > 0 && ` · ${offset + 1}–${Math.min(offset + limit, total)}`}</span><div><button disabled={offset === 0} onClick={() => onPage(Math.max(0, offset - limit))}>上一页</button><button disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}>下一页</button></div></div>;
}
function readOffset(params: URLSearchParams, key = "offset") { const value = Number(params.get(key)); return Number.isSafeInteger(value) && value > 0 ? value : 0; }
function filterValues(form: HTMLFormElement) {
  const values: Record<string, string | null> = {};
  new FormData(form).forEach((value, key) => { values[key] = String(value).trim() || null; });
  return values;
}
function ScopeFields({ params, summary }: { params: URLSearchParams; summary: KnowledgeBrowserSummary }) {
  return <><label>制造商<select name="manufacturer" defaultValue={params.get("manufacturer") ?? ""}><option value="">全部制造商</option>{summary.manufacturers.map(item => <option key={item.name}>{item.name}</option>)}</select></label><label>年份 / 赛季起始年<select name="season" defaultValue={params.get("season") ?? ""}><option value="">全部赛季</option>{summary.seasons.map(item => <option key={item} value={item}>{item} 起始年</option>)}</select></label></>;
}
function SourceLink({ url, authority, locator }: { url: string | null; authority?: string | null; locator?: string | null }) {
  return <div className="kb-source">{url ? <a href={url} target="_blank" rel="noreferrer">查看来源 ↗</a> : <span>来源链接缺失</span>}{authority !== undefined && <span>{sourceLabel(authority)}</span>}{locator && <span>{locator}</span>}</div>;
}
function CardSources({ sources }: { sources: KnowledgeSource[] }) { return <div className="kb-sources">{sources.length ? sources.map((item, i) => <SourceLink key={`${item.url}-${i}`} url={item.url} authority={item.authority} locator={item.locator} />) : <p className="kb-muted">暂无来源记录</p>}</div>; }

function ProductList({ params, summary, versionId, navigate, openProduct }: { params: URLSearchParams; summary: KnowledgeBrowserSummary; versionId?: string; navigate: Navigate; openProduct: (id: string) => void }) {
  const resource = useKnowledge<KnowledgePage<KnowledgeProduct>>(browserPath("/products", { manufacturer: params.get("manufacturer"), season: params.get("season"), q: params.get("q"), has_images: params.get("has_images"), offset: readOffset(params), limit: 24, version_id: versionId }));
  return <section className="kb-stack"><div className="kb-section-heading"><div><h2>产品目录</h2><p>按已发布产品与审核别名检索。</p></div></div>
    <form key={params.toString()} role="search" className="kb-filters" onSubmit={event => { event.preventDefault(); navigate({ ...filterValues(event.currentTarget), offset: null }); }}>
      <label className="kb-search">搜索产品<input type="search" name="q" defaultValue={params.get("q") ?? ""} placeholder="产品名称、厂牌或已知别名" /></label><ScopeFields params={params} summary={summary} />
      <label>参考图片<select name="has_images" defaultValue={params.get("has_images") ?? ""}><option value="">全部</option><option value="true">有图片</option><option value="false">暂无图片</option></select></label><button type="submit">搜索</button><button type="button" onClick={() => navigate({ q: null, manufacturer: null, season: null, has_images: null, offset: null })}>清除筛选</button>
    </form><ResourceStatus resource={resource} />{resource.data && <><div className="kb-product-grid">{resource.data.items.map(product => <article className="kb-product" key={product.id}>
      <button className="kb-product-open" aria-label={`打开产品 ${product.name}`} onClick={() => openProduct(product.id)}><div className="kb-product-cover">{product.cover_image_url ? <KnowledgeImage src={product.cover_image_url} alt={`${product.name} 产品参考图`} /> : <div className="kb-no-image">暂无参考图<span>保留文字知识</span></div>}</div><div><small>{product.manufacturer ?? "制造商未知"} · {seasonLabel(product.season_start, product.season_end)}</small><h3>{product.name}</h3><p>{product.card_count.toLocaleString()} 卡片事实 · {product.image_count} 图片</p><span className="kb-badge">{textStatus(product.text_status)}</span></div></button>
      {product.aliases.length > 0 && <p className="kb-aliases">别名：{product.aliases.join("、")}</p>}
    </article>)}</div>{!resource.data.items.length && <Empty>暂无已发布产品符合当前筛选</Empty>}<Pagination {...resource.data} onPage={offset => navigate({ offset: String(offset) })} /></>}
  </section>;
}
function textStatus(value: KnowledgeProduct["text_status"]) { return value === "available" ? "文字已收录" : value === "partial" ? "部分文字" : "文字待补充"; }

function CardList({ params, summary, versionId, navigate, openCard, product }: { params: URLSearchParams; summary: KnowledgeBrowserSummary; versionId?: string; navigate: Navigate; openCard: (id: string) => void; product?: KnowledgeProductDetail }) {
  const productId = product?.product.id;
  const queryKey = productId ? "card_q" : "q";
  const offsetKey = productId ? "card_offset" : "offset";
  const path = browserPath("/cards", { product_id: productId ?? params.get("product_id"), manufacturer: productId ? null : params.get("manufacturer"), season: productId ? null : params.get("season"), q: params.get(queryKey), section: params.get("section"), parallel: params.get("parallel"), image_state: params.get("image_state") ?? "any", offset: readOffset(params, offsetKey), limit: 40, version_id: versionId });
  const resource = useKnowledge<KnowledgePage<KnowledgeCard>>(path);
  return <section className="kb-stack"><div className="kb-section-heading"><div><h2>卡片检索</h2><p>搜索球员、卡号、卡组、产品与已知别名；设计样张不计入卡图筛选。</p></div></div>
    <form key={params.toString()} role="search" className="kb-filters" onSubmit={event => { event.preventDefault(); navigate({ ...filterValues(event.currentTarget), [offsetKey]: null }); }}>
      <label className="kb-search">搜索卡片<input name={queryKey} type="search" defaultValue={params.get(queryKey) ?? ""} placeholder="球员、卡号或名称" /></label>{!productId && <ScopeFields params={params} summary={summary} />}
      <label>卡组 / Insert{product ? <select name="section" defaultValue={params.get("section") ?? ""}><option value="">全部卡组</option>{product.sections.map(item => <option key={item.name} value={item.name}>{item.name} ({item.card_count})</option>)}</select> : <input name="section" defaultValue={params.get("section") ?? ""} placeholder="卡组名称" />}</label>
      <label>平行版本{product ? <select name="parallel" defaultValue={params.get("parallel") ?? ""}><option value="">全部版本</option>{product.parallels.map(item => <option key={item.name} value={item.name}>{item.name} ({item.card_count})</option>)}</select> : <input name="parallel" defaultValue={params.get("parallel") ?? ""} placeholder="平行名称" />}</label>
      <label>图片状态<select name="image_state" defaultValue={params.get("image_state") ?? "any"}><option value="any">全部卡片</option><option value="has_image">有具体卡图</option><option value="missing_front">缺少正面图</option><option value="missing_back">缺少背面图</option></select></label>
      <button type="submit">搜索</button><button type="button" onClick={() => navigate({ [queryKey]: null, section: null, parallel: null, image_state: null, [offsetKey]: null, ...(!productId ? { manufacturer: null, season: null } : {}) })}>清除筛选</button>
    </form><ResourceStatus resource={resource} />{resource.data && <><CardRows cards={resource.data.items} openCard={openCard} />{!resource.data.items.length && <Empty>暂无符合条件的已发布卡片</Empty>}<Pagination {...resource.data} onPage={offset => navigate({ [offsetKey]: String(offset) })} /></>}
  </section>;
}
function CardRows({ cards, openCard }: { cards: KnowledgeCard[]; openCard: (id: string) => void }) {
  return <div className="kb-card-list">{cards.map(card => <article className="kb-card-row" key={card.id}>
    <button className="kb-card-open" aria-label={`打开卡片 ${card.name}`} onClick={() => openCard(card.id)}>
      <div className="kb-card-thumbnail">{card.front_image_url || card.back_image_url ? <KnowledgeImage src={(card.front_image_url || card.back_image_url)!} alt={`${card.name} ${card.front_image_url ? "正面" : "背面"}`} /> : <span>暂无<br />具体卡图</span>}</div>
      <div className="kb-card-copy"><small>{seasonLabel(card.season_start, card.season_end)} · {card.product_name ?? "产品未知"}</small><h3>{card.name}</h3><p>{card.player ?? "球员未知"} · #{card.card_number ?? "卡号未知"}</p><div className="kb-chips"><span>{card.section ?? "卡组未知"}</span><span>{card.parallel ?? "平行未知"}</span>{card.serial_total != null && <span>限量 /{card.serial_total}</span>}</div></div>
      <div className="kb-card-images"><span>{card.front_image_url ? "有正面图" : "缺少正面图"}</span><span>{card.back_image_url ? "有背面图" : "缺少背面图"}</span><b aria-hidden="true">→</b></div>
    </button>
  </article>)}</div>;
}

function ProductDetail({ id, params, summary, versionId, navigate, openCard, openProduct, onBack, onImages }: { id: string; params: URLSearchParams; summary: KnowledgeBrowserSummary; versionId?: string; navigate: Navigate; openCard: (id: string) => void; openProduct: (id: string) => void; onBack: () => void; onImages: OpenImages }) {
  const resource = useKnowledge<KnowledgeProductDetail>(browserPath(`/products/${encodeURIComponent(id)}`, { version_id: versionId }));
  const tab = params.get("product_tab") ?? "overview";
  const product = resource.data?.product;
  return <div className="kb-stack"><button className="kb-back kb-back-button" onClick={onBack}>← 返回产品列表</button><ResourceStatus resource={resource} />{product && <>
    <section className="kb-panel kb-product-heading"><div><p className="kb-eyebrow">{product.manufacturer ?? "制造商未知"} · {seasonLabel(product.season_start, product.season_end)}</p><h2>{product.name}</h2><p>{product.brand ?? "厂牌未知"} · {product.sport ?? "运动未知"} · {product.league ?? "联盟未知"}</p><div className="kb-chips"><span>{textStatus(product.text_status)}</span><span>{product.card_count.toLocaleString()} 卡片事实</span><span>{product.image_count} 张参考图</span><span>{product.paired_card_count} 逐卡配对</span></div></div></section>
    <nav className="kb-subnav" aria-label="产品详情导航">{Object.entries(productTabs).map(([key, label]) => <button key={key} aria-current={tab === key ? "page" : undefined} onClick={() => navigate({ product_tab: key })}>{label}</button>)}</nav>
    {tab === "cards" ? <CardList params={params} summary={summary} versionId={versionId} navigate={navigate} openCard={openCard} product={resource.data!} />
      : tab === "images" ? <GalleryList params={params} productId={id} versionId={versionId} navigate={navigate} openProduct={openProduct} onImages={onImages} />
        : tab === "knowledge" ? <ProductFacets detail={resource.data!} onFilter={(key, value) => navigate({ product_tab: "cards", [key]: value, card_offset: null })} />
          : tab === "sources" ? <section className="kb-panel"><h3>产品来源</h3><SourceLink url={product.source_url} authority={product.source_authority} /><p className="kb-muted">逐卡来源与原文位置可在卡片详情查看。来源缺失不补成官方证据。</p></section>
            : <><section className="kb-panel"><h3>产品说明</h3><p className="kb-description">{product.description ?? "暂未收录产品说明。"}</p><h3>已知别名</h3><p>{product.aliases.length ? product.aliases.join("、") : "暂无已审核别名"}</p><SourceLink url={product.source_url} authority={product.source_authority} /><p className="kb-muted">{product.image_count ? "参考图包含产品设计样张，具体卡图与侧别以每张图片标记为准。" : "当前产品暂无参考图；文字事实仍可浏览。"}</p></section><ProductFacets detail={resource.data!} onFilter={(key, value) => navigate({ product_tab: "cards", [key]: value, card_offset: null })} /></>}
  </>}</div>;
}
function ProductFacets({ detail, onFilter }: { detail: KnowledgeProductDetail; onFilter: (key: string, value: string) => void }) {
  return <div className="kb-facet-grid">{([["卡组 / Insert", "section", detail.sections], ["平行版本", "parallel", detail.parallels]] as const).map(([label, key, items]) => <section className="kb-panel" key={key}><h3>{label}</h3><p className="kb-muted">来自实际已发布卡片事实，不推导版本组合。</p><div className="kb-chips">{items.map(item => <button key={item.name} onClick={() => onFilter(key, item.name)}>{item.name}<span>{item.card_count.toLocaleString()}</span></button>)}</div>{!items.length && <p>暂无已核实条目</p>}</section>)}</div>;
}

function CardDetail({ id, versionId, openCard, openProduct, onBack, onImages }: { id: string; versionId?: string; openCard: (id: string) => void; openProduct: (id: string) => void; onBack: () => void; onImages: OpenImages }) {
  const resource = useKnowledge<KnowledgeCardDetail>(browserPath(`/cards/${encodeURIComponent(id)}`, { version_id: versionId }));
  const card = resource.data?.card;
  return <div className="kb-stack"><button className="kb-back kb-back-button" onClick={onBack}>← 返回卡片列表</button><ResourceStatus resource={resource} />{card && <>
    <section className="kb-panel"><p className="kb-eyebrow">{card.manufacturer ?? "制造商未知"} · {seasonLabel(card.season_start, card.season_end)}</p><h2>{card.name}</h2>{card.product_id ? <button className="kb-text-button" onClick={() => openProduct(card.product_id!)}>{card.product_name ?? "查看产品"} ↗</button> : <p>产品未知</p>}
      <dl className="kb-facts">{[["球员", card.player], ["卡号", card.card_number], ["卡组 / Insert", card.section], ["平行版本", card.parallel], ["限量", card.serial_total], ["新秀", booleanLabel(card.is_rookie)], ["签字", booleanLabel(card.is_autograph)], ["实物", booleanLabel(card.is_patch)]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value ?? "未知"}</dd></div>)}</dl>
    </section>
    <section className="kb-panel"><h3>具体卡图</h3><div className="kb-chips"><span>{card.front_image_url ? "有正面图" : "缺少正面图"}</span><span>{card.back_image_url ? "有背面图" : "缺少背面图"}</span></div>{resource.data!.images.length ? <MediaGallery images={resource.data!.images} onImages={onImages} openProduct={openProduct} /> : <div className="kb-empty"><strong>暂无与本卡明确绑定的图片</strong><p>产品设计样张不会补作这张卡的照片，也不会据此推断配对。</p></div>}</section>
    <section className="kb-panel"><h3>证据来源</h3><CardSources sources={card.sources} /></section>
    <section className="kb-panel"><h3>相关版本</h3><p className="kb-muted">同产品、赛季、球员、卡号与卡组的已知版本，最多显示 30 条。</p><CardRows cards={resource.data!.related_versions} openCard={openCard} />{!resource.data!.related_versions.length && <p>暂无其他已核实版本</p>}</section>
  </>}</div>;
}
function booleanLabel(value: boolean | null) { return value === null ? "未知" : value ? "是" : "否"; }

type OpenImages = (images: KnowledgeMedia[], index: number, compare: boolean, opener: HTMLElement) => void;
function GalleryList({ params, productId, versionId, navigate, openProduct, onImages }: { params: URLSearchParams; productId?: string; versionId?: string; navigate: Navigate; openProduct: (id: string) => void; onImages: OpenImages }) {
  const offsetKey = productId ? "media_offset" : "offset";
  const resource = useKnowledge<KnowledgePage<KnowledgeMedia>>(browserPath("/media", { product_id: productId ?? params.get("product_id"), kind: params.get("kind") ?? "all", offset: readOffset(params, offsetKey), limit: 24, version_id: versionId }));
  return <section className="kb-stack"><div className="kb-section-heading"><div><h2>图片库</h2><p>仅展示已审核且实际捕获的参考图片，保留未知侧别。</p></div></div>
    <form key={params.toString()} role="search" className="kb-filters" onSubmit={event => { event.preventDefault(); navigate({ ...filterValues(event.currentTarget), [offsetKey]: null }); }}><label>图片类型<select name="kind" defaultValue={params.get("kind") ?? "all"}><option value="all">全部参考图</option><option value="design">设计样张</option><option value="specimen">实卡参考图</option></select></label><button type="submit">筛选</button></form>
    <ResourceStatus resource={resource} />{resource.data && <><MediaGallery images={resource.data.items} onImages={onImages} openProduct={openProduct} />{!resource.data.items.length && <Empty>当前筛选下暂无已捕获图片</Empty>}<Pagination {...resource.data} onPage={offset => navigate({ [offsetKey]: String(offset) })} /></>}
  </section>;
}
function MediaGallery({ images, onImages, openProduct }: { images: KnowledgeMedia[]; onImages: OpenImages; openProduct: (id: string) => void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const key = images.map(item => item.id).join("|");
  useEffect(() => { setSelected([]); }, [key]);
  return <div className="kb-stack">
    <div className="kb-gallery-toolbar"><span className="kb-muted">选择两张实际图片进行对照；对照不代表正反面配对。</span><button disabled={selected.length !== 2} onClick={event => onImages(images.filter(item => selected.includes(item.id)), 0, true, event.currentTarget)}>对照所选图片</button></div>
    <div className="kb-media-grid">{images.map((image, index) => {
      const caption = mediaTitle(image, index);
      const paired = mediaCategory(image) === "specimen" && image.card_id && image.pair_id && image.side !== "unknown" && images.some(other => other.id !== image.id && other.card_id === image.card_id && other.pair_id === image.pair_id && other.side !== "unknown" && other.side !== image.side);
      return <article className="kb-media" key={image.id}><button className="kb-media-open" aria-label={`查看图片 ${caption}`} onClick={event => onImages(images, index, false, event.currentTarget)}><KnowledgeImage src={image.thumbnail_url} alt={caption} /></button><div className="kb-media-copy"><div className="kb-chips"><span>{mediaKind(image)}</span><span>{sideLabel(image.side)}</span>{!image.card_id && mediaCategory(image) !== "design" && <span>未绑定具体卡牌</span>}{paired && <span>已核实配对</span>}</div><h3>{caption}</h3><div className="kb-media-product"><strong>{image.product_name ?? "系列名称待补充"}</strong><span>{image.manufacturer ?? "制造商未知"} · {seasonLabel(image.season_start ?? null, image.season_end)}</span>{image.product_id && <button className="kb-text-button" onClick={() => openProduct(image.product_id!)}>查看所属系列 →</button>}</div>{image.player && <p>{image.player} · #{image.card_number ?? "未知"}{image.parallel && ` · ${image.parallel}`}</p>}<SourceLink url={image.source_url} authority={image.source_authority} /><label className="kb-compare-check"><input type="checkbox" aria-label={`对照 ${caption}`} checked={selected.includes(image.id)} disabled={selected.length >= 2 && !selected.includes(image.id)} onChange={event => setSelected(value => event.target.checked ? [...value, image.id] : value.filter(id => id !== image.id))} />选择对照</label></div></article>;
    })}</div>
  </div>;
}

function Glossary({ params, summary, versionId, navigate }: { params: URLSearchParams; summary: KnowledgeBrowserSummary; versionId?: string; navigate: Navigate }) {
  const resource = useKnowledge<KnowledgePage<KnowledgeGlossaryEntry>>(browserPath("/entities", { entity_type: params.get("entity_type"), q: params.get("q"), offset: readOffset(params), limit: 40, version_id: versionId }));
  return <section className="kb-stack"><div className="kb-section-heading"><div><h2>知识词典</h2><p>已发布实体、已审核别名与说明，保留原始来源。</p></div></div><form key={params.toString()} role="search" className="kb-filters" onSubmit={event => { event.preventDefault(); navigate({ ...filterValues(event.currentTarget), offset: null }); }}><label className="kb-search">搜索知识<input name="q" type="search" placeholder="名称或已知别名" defaultValue={params.get("q") ?? ""} /></label><label>知识类型<select name="entity_type" defaultValue={params.get("entity_type") ?? ""}><option value="">全部类型</option>{summary.entity_types.map(item => <option key={item.type} value={item.type}>{knowledgeEntityLabels[item.type] ?? item.type} ({item.count.toLocaleString()})</option>)}</select></label><button type="submit">搜索</button><button type="button" onClick={() => navigate({ q: null, entity_type: null, offset: null })}>清除筛选</button></form><ResourceStatus resource={resource} />{resource.data && <><div className="kb-glossary">{resource.data.items.map(item => <article className="kb-panel" key={item.id}><span className="kb-badge">{knowledgeEntityLabels[item.entity_type] ?? item.entity_type}</span><h3>{item.canonical_name}</h3><div className="kb-chips">{item.aliases.map(alias => <span key={alias}>{alias}</span>)}</div><p className="kb-description">{item.description ?? "暂无已发布说明"}</p><SourceLink url={item.source_url} /></article>)}</div>{!resource.data.items.length && <Empty>暂无符合条件的已发布知识</Empty>}<Pagination {...resource.data} onPage={offset => navigate({ offset: String(offset) })} /></>}</section>;
}
