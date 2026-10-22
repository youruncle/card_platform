import React, { useEffect, useRef, useState } from "react";
import type {
  KnowledgeBrowserSummary,
  KnowledgeCard,
  KnowledgeCardDetail,
  KnowledgeGlossaryEntry,
  KnowledgeMedia,
  KnowledgePage as Page,
  KnowledgeProduct,
  KnowledgeProductDetail,
} from "../../../packages/card-core/src/knowledge-browser";
import type { MobileApi } from "./api";
import {
  browsePath,
  entityTypeLabel,
  flagLabel,
  KnowledgeCardRows,
  KnowledgePager,
  KnowledgeSources,
  KnowledgeStatus,
  seasonLabel,
  useKnowledge,
} from "./knowledge-browser";
import {
  isVerifiedMedia,
  KnowledgeGallery,
  KnowledgeImage,
} from "./knowledge-media";
import "./knowledge-browser.css";

type BrowseTab = "products" | "cards" | "media" | "entities";
const tabs: [BrowseTab, string][] = [
  ["products", "系列目录"],
  ["cards", "卡牌查询"],
  ["media", "图库"],
  ["entities", "知识词条"],
];

export function KnowledgePage({
  api,
  onBack,
}: {
  api: MobileApi;
  onBack: () => void;
}) {
  const summary = useKnowledge<KnowledgeBrowserSummary>(
    api,
    browsePath("summary", null),
  );
  const version = summary.data?.version_id ?? null;
  const [tab, setTab] = useState<BrowseTab>("products");
  const [productId, setProductId] = useState<string | null>(null);
  const [cardId, setCardId] = useState<string | null>(null);
  const [manufacturer, setManufacturer] = useState("");
  const [season, setSeason] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [imageState, setImageState] = useState("any");
  const [hasImages, setHasImages] = useState(false);
  const [kind, setKind] = useState("all");
  const [entityType, setEntityType] = useState("");
  const params = {
    offset,
    limit: tab === "cards" || tab === "entities" ? 40 : 24,
    ...(tab === "products" || tab === "cards"
      ? { manufacturer, season, q: search }
      : {}),
    ...(tab === "products" && hasImages ? { has_images: true } : {}),
    ...(tab === "cards" ? { image_state: imageState } : {}),
    ...(tab === "media" ? { kind } : {}),
    ...(tab === "entities" ? { entity_type: entityType, q: search } : {}),
  };
  const list = useKnowledge<
    Page<
      KnowledgeProduct | KnowledgeCard | KnowledgeMedia | KnowledgeGlossaryEntry
    >
  >(api, version ? browsePath(tab, version, params) : null);
  const heading = useRef<HTMLHeadingElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  function openProduct(id: string) {
    restoreFocus.current = document.activeElement as HTMLElement;
    setProductId(id);
  }
  function openCard(id: string) {
    restoreFocus.current = document.activeElement as HTMLElement;
    setCardId(id);
  }
  function returnToList() {
    setCardId(null);
    if (!cardId) setProductId(null);
    requestAnimationFrame(() => {
      if (restoreFocus.current?.isConnected) restoreFocus.current.focus();
      else heading.current?.focus();
    });
  }
  const detail = !!productId || !!cardId;
  return (
    <div className="kb-browser">
      <button onClick={detail ? returnToList : onBack}>
        {cardId
          ? productId
            ? "返回系列"
            : "返回浏览"
          : productId
            ? tab === "media" ? "返回图库" : "返回目录"
            : "‹ 返回我的"}
      </button>
      <div hidden={detail}>
        <p className="eyebrow">PUBLISHED KNOWLEDGE</p>
        <h1 tabIndex={-1} ref={heading}>
          卡牌知识库
        </h1>
        <p className="muted">
          按厂商、赛季与系列浏览已发布资料，查阅具体卡牌和图片证据。
        </p>
        <KnowledgeStatus resource={summary} />
        {summary.data && (
          <>
            <Summary data={summary.data} />
            <nav className="kb-tabs" aria-label="知识浏览">
              <div>
                {tabs.map(([key, label]) => (
                  <button
                    key={key}
                    aria-pressed={tab === key}
                    onClick={() => {
                      setTab(key);
                      setOffset(0);
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </nav>
            {!version ? (
              <p className="empty">尚未发布知识库，暂无可浏览内容。</p>
            ) : (
              <>
                {tab !== "media" && (
                  <form
                    role="search"
                    className="search"
                    onSubmit={(event) => {
                      event.preventDefault();
                      setSearch(query.trim());
                      setOffset(0);
                    }}
                  >
                    <input
                      aria-label="知识关键词"
                      maxLength={200}
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder={
                        tab === "cards"
                          ? "球员、卡号、系列、子系列…"
                          : "名称或已审核别名…"
                      }
                    />
                    <button>搜索</button>
                  </form>
                )}
                {(tab === "products" || tab === "cards") && (
                  <div className="kb-filters">
                    <label>
                      厂商
                      <select
                        aria-label="厂商"
                        value={manufacturer}
                        onChange={(event) => {
                          setManufacturer(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="">全部厂商</option>
                        {summary.data.manufacturers.map((item) => (
                          <option key={item.name} value={item.name}>
                            {item.name} · {item.product_count} 系列
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      赛季
                      <select
                        aria-label="赛季"
                        value={season}
                        onChange={(event) => {
                          setSeason(event.target.value);
                          setOffset(0);
                        }}
                      >
                        <option value="">全部赛季（含未知）</option>
                        {summary.data.seasons.map((year) => (
                          <option key={year} value={year}>
                            {year} 起始年
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                )}
                {tab === "products" && (
                  <label className="kb-checkbox">
                    <input
                      type="checkbox"
                      checked={hasImages}
                      onChange={(event) => {
                        setHasImages(event.target.checked);
                        setOffset(0);
                      }}
                    />
                    仅看有图片的系列
                  </label>
                )}
                {tab === "cards" && (
                  <ImageState
                    value={imageState}
                    onChange={(value) => {
                      setImageState(value);
                      setOffset(0);
                    }}
                  />
                )}
                {tab === "media" && (
                  <GalleryKind
                    value={kind}
                    onChange={(value) => {
                      setKind(value);
                      setOffset(0);
                    }}
                  />
                )}
                {tab === "entities" && (
                  <label>
                    词条类型
                    <select
                      aria-label="词条类型"
                      value={entityType}
                      onChange={(event) => {
                        setEntityType(event.target.value);
                        setOffset(0);
                      }}
                    >
                      <option value="">全部类型</option>
                      {summary.data.entity_types.map((item) => (
                        <option key={item.type} value={item.type}>
                          {entityTypeLabel(item.type)} · {item.count}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <KnowledgeStatus resource={list} />
                {list.data && (
                  <>
                    <p className="small muted" aria-live="polite">
                      已收录 {list.data.total.toLocaleString()}{" "}
                      {tab === "products"
                        ? "个系列"
                        : tab === "cards"
                          ? "条卡牌知识"
                          : tab === "media"
                            ? "张图片"
                            : "个词条"}
                    </p>
                    {tab === "products" && (
                      <ProductRows
                        products={list.data.items as KnowledgeProduct[]}
                        api={api}
                        version={version}
                        onProduct={openProduct}
                      />
                    )}
                    {tab === "cards" && (
                      <KnowledgeCardRows
                        cards={list.data.items as KnowledgeCard[]}
                        onCard={openCard}
                      />
                    )}
                    {tab === "media" && (
                      <KnowledgeGallery
                        api={api}
                        media={list.data.items as KnowledgeMedia[]}
                        version={version}
                        onProduct={openProduct}
                      />
                    )}
                    {tab === "entities" && (
                      <Glossary
                        entries={list.data.items as KnowledgeGlossaryEntry[]}
                      />
                    )}
                    <KnowledgePager page={list.data} onPage={setOffset} />
                  </>
                )}
              </>
            )}
          </>
        )}
      </div>
      {productId && version && (
        <div hidden={!!cardId}>
          <ProductDetail
            key={productId}
            api={api}
            id={productId}
            version={version}
            onCard={openCard}
          />
        </div>
      )}
      {cardId && version && (
        <CardDetail
          key={cardId}
          api={api}
          id={cardId}
          version={version}
          onCard={setCardId}
          onProduct={(id) => {
            setCardId(null);
            setProductId(id);
          }}
        />
      )}
    </div>
  );
}

function Summary({ data }: { data: KnowledgeBrowserSummary }) {
  return (
    <section className="panel kb-summary" aria-label="已收录知识概览">
      <p className="small muted">已发布版本：{data.version ?? "尚未发布"}</p>
      <div className="kb-stat-grid">
        {[
          ["厂商", data.summary.manufacturers],
          ["系列", data.summary.products],
          ["卡牌事实", data.summary.card_facts],
          ["图片", data.summary.images],
          ["实卡正反配对", data.summary.paired_cards],
        ].map(([label, count]) => (
          <div key={label}>
            <strong>{Number(count).toLocaleString()}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
      <p className="small muted">
        以上是目前已知、已发布内容，不代表完整收录；未知年份和缺图仍保留。
      </p>
      {data.issues.length > 0 && (
        <details className="kb-issues">
          <summary>来源缺口与异常（{data.issues.length}）</summary>
          <ul>
            {data.issues.map((issue, index) => (
              <li key={`${issue.source_key}-${index}`}>
                <span className="small muted">{issue.source_key}：</span>
                {issue.message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function ProductRows({
  products,
  api,
  version,
  onProduct,
}: {
  products: KnowledgeProduct[];
  api: MobileApi;
  version: string;
  onProduct: (id: string) => void;
}) {
  if (!products.length)
    return (
      <p className="empty">没有符合条件的系列，试试其他厂商、赛季或名称。</p>
    );
  return (
    <div className="kb-products">
      {products.map((product) => (
        <article className="panel kb-product" key={product.id}>
          {product.cover_image_url ? (
            <div className="kb-cover">
              <KnowledgeImage
                api={api}
                path={product.cover_image_url}
                version={version}
                alt={`${product.name} 参考图`}
              />
            </div>
          ) : (
            <div className="kb-no-cover" aria-hidden="true">
              暂无系列图片
            </div>
          )}
          <p className="small muted">
            {product.manufacturer || "厂商未知"} ·{" "}
            {seasonLabel(product.season_start, product.season_end)}
          </p>
          <button
            className="kb-title-button"
            onClick={() => onProduct(product.id)}
          >
            {product.name}
          </button>
          <p className="small">
            {product.card_count.toLocaleString()} 条卡牌知识 ·{" "}
            {product.image_count} 张图片 · {product.paired_card_count}{" "}
            组正反配对
          </p>
          <span className="kb-tag">
            {product.text_status === "available"
              ? "已收录文字"
              : product.text_status === "partial"
                ? "文字资料部分收录"
                : "文字资料缺失"}
          </span>
        </article>
      ))}
    </div>
  );
}

function ImageState({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      实卡图片
      <select
        aria-label="实卡图片"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="any">全部卡牌</option>
        <option value="has_image">已有逐卡图片</option>
        <option value="missing_front">缺少正面</option>
        <option value="missing_back">缺少背面</option>
      </select>
    </label>
  );
}
function GalleryKind({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      图片类型
      <select
        aria-label="图片类型"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="all">全部图片</option>
        <option value="design">官方设计示意</option>
        <option value="specimen">核实实卡图片</option>
      </select>
    </label>
  );
}

function Glossary({ entries }: { entries: KnowledgeGlossaryEntry[] }) {
  if (!entries.length)
    return <p className="empty">没有符合条件的已发布词条。</p>;
  return (
    <div>
      {entries.map((entry) => (
        <section className="panel" key={entry.id}>
          <p className="eyebrow">{entityTypeLabel(entry.entity_type)}</p>
          <h2>{entry.canonical_name}</h2>
          {!!entry.aliases.length && (
            <p className="small">别名：{entry.aliases.join("、")}</p>
          )}
          <p>{entry.description || "暂无已发布说明。"}</p>
          {entry.source_url && /^https?:\/\//i.test(entry.source_url) && (
            <a href={entry.source_url} target="_blank" rel="noreferrer">
              查看词条来源
            </a>
          )}
        </section>
      ))}
    </div>
  );
}

function ProductDetail({
  api,
  id,
  version,
  onCard,
}: {
  api: MobileApi;
  id: string;
  version: string;
  onCard: (id: string) => void;
}) {
  const resource = useKnowledge<KnowledgeProductDetail>(
    api,
    browsePath(`products/${encodeURIComponent(id)}`, version),
  );
  const [tab, setTab] = useState("overview");
  const [section, setSection] = useState("");
  const [parallel, setParallel] = useState("");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [imageState, setImageState] = useState("any");
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState("all");
  const [mediaOffset, setMediaOffset] = useState(0);
  const cards = useKnowledge<Page<KnowledgeCard>>(
    api,
    tab === "cards"
      ? browsePath("cards", version, {
          product_id: id,
          section,
          parallel,
          q,
          image_state: imageState,
          offset,
          limit: 40,
        })
      : null,
  );
  const media = useKnowledge<Page<KnowledgeMedia>>(
    api,
    tab === "gallery"
      ? browsePath("media", version, {
          product_id: id,
          kind,
          offset: mediaOffset,
          limit: 24,
        })
      : null,
  );
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (resource.data) title.current?.focus();
  }, [resource.data]);
  if (!resource.data) return <KnowledgeStatus resource={resource} />;
  const { product, sections, parallels } = resource.data;
  return (
    <section className="kb-detail">
      <p className="eyebrow">
        {product.manufacturer || "厂商未知"} ·{" "}
        {seasonLabel(product.season_start, product.season_end)}
      </p>
      <h1 tabIndex={-1} ref={title}>
        {product.name}
      </h1>
      <p className="muted">
        {product.card_count.toLocaleString()} 条卡牌知识 · {product.image_count}{" "}
        张图片
      </p>
      <nav className="kb-tabs" aria-label="系列详情">
        <div>
          {[
            ["overview", "系列概览"],
            ["cards", "清单卡牌"],
            ["knowledge", "子系列与平行"],
            ["gallery", "系列图库"],
            ["sources", "系列来源"],
          ].map(([key, label]) => (
            <button
              key={key}
              aria-pressed={tab === key}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </nav>
      {tab === "overview" && (
        <section className="panel">
          <h2>系列概览</h2>
          <p>{product.description || "暂无已发布系列说明。"}</p>
          <dl className="kb-facts">
            <dt>品牌</dt>
            <dd>{product.brand || "未知"}</dd>
            <dt>项目 / 联盟</dt>
            <dd>
              {product.sport || "未知"} / {product.league || "未知"}
            </dd>
            <dt>已审核别名</dt>
            <dd>{product.aliases.join("、") || "无"}</dd>
            <dt>文字资料</dt>
            <dd>
              {product.text_status === "available"
                ? "已收录"
                : product.text_status === "partial"
                  ? "部分收录"
                  : "缺失"}
            </dd>
            <dt>正反面配对</dt>
            <dd>{product.paired_card_count} 组</dd>
          </dl>
          <p className="small muted">
            设计示意、实卡单面和已核实的正反面配对分别保留，少量图片不代表全套图齐全。
          </p>
        </section>
      )}
      {tab === "knowledge" && (
        <section className="panel">
          <h2>已确认子系列</h2>
          {sections.length ? (
            <div className="kb-facet-buttons">
              {sections.map((item) => (
                <button
                  key={item.name}
                  onClick={() => {
                    setSection(item.name);
                    setParallel("");
                    setOffset(0);
                    setTab("cards");
                  }}
                >
                  {item.name} · {item.card_count}
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">尚无已确认子系列资料。</p>
          )}
          <h2>已确认平行版本</h2>
          {parallels.length ? (
            <div className="kb-facet-buttons">
              {parallels.map((item) => (
                <button
                  key={item.name}
                  onClick={() => {
                    setParallel(item.name);
                    setSection("");
                    setOffset(0);
                    setTab("cards");
                  }}
                >
                  {item.name} · {item.card_count}
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">尚无已确认平行资料。</p>
          )}
          <p className="small muted">
            以上来自已发布卡牌记录，不能据此推断任意子系列与平行组合存在。
          </p>
        </section>
      )}
      {tab === "cards" && (
        <>
          <form
            role="search"
            className="search"
            onSubmit={(event) => {
              event.preventDefault();
              setQ(query.trim());
              setOffset(0);
            }}
          >
            <input
              aria-label="系列内卡牌关键词"
              value={query}
              maxLength={200}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="球员、卡号、版本…"
            />
            <button>搜索</button>
          </form>
          <div className="kb-filters">
            <label>
              子系列
              <select
                aria-label="子系列"
                value={section}
                onChange={(event) => {
                  setSection(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">全部子系列</option>
                {sections.map((item) => (
                  <option key={item.name}>{item.name}</option>
                ))}
              </select>
            </label>
            <label>
              平行版本
              <select
                aria-label="平行版本"
                value={parallel}
                onChange={(event) => {
                  setParallel(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">全部平行</option>
                {parallels.map((item) => (
                  <option key={item.name}>{item.name}</option>
                ))}
              </select>
            </label>
          </div>
          <ImageState
            value={imageState}
            onChange={(value) => {
              setImageState(value);
              setOffset(0);
            }}
          />
          <KnowledgeStatus resource={cards} />
          {cards.data && (
            <>
              <KnowledgeCardRows cards={cards.data.items} onCard={onCard} />
              <KnowledgePager page={cards.data} onPage={setOffset} />
            </>
          )}
        </>
      )}
      {tab === "gallery" && (
        <>
          <GalleryKind
            value={kind}
            onChange={(value) => {
              setKind(value);
              setMediaOffset(0);
            }}
          />
          <KnowledgeStatus resource={media} />
          {media.data && (
            <>
              <KnowledgeGallery
                api={api}
                media={media.data.items}
                version={version}
              />
              <KnowledgePager page={media.data} onPage={setMediaOffset} />
            </>
          )}
        </>
      )}
      {tab === "sources" && (
        <section className="panel">
          <h2>系列来源</h2>
          <KnowledgeSources
            sources={
              product.source_url || product.source_authority
                ? [
                    {
                      url: product.source_url,
                      authority: product.source_authority,
                      locator: null,
                    },
                  ]
                : []
            }
          />
          <p className="small muted">具体卡牌和图片的来源在对应详情中查看。</p>
        </section>
      )}
    </section>
  );
}

function CardDetail({
  api,
  id,
  version,
  onCard,
  onProduct,
}: {
  api: MobileApi;
  id: string;
  version: string;
  onCard: (id: string) => void;
  onProduct: (id: string) => void;
}) {
  const resource = useKnowledge<KnowledgeCardDetail>(
    api,
    browsePath(`cards/${encodeURIComponent(id)}`, version),
  );
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (resource.data) title.current?.focus();
  }, [resource.data]);
  if (!resource.data) return <KnowledgeStatus resource={resource} />;
  const { card, images, related_versions } = resource.data;
  const exact = images.filter(
    (image) => image.card_id === card.id && isVerifiedMedia(image),
  );
  const paired = exact.some(
    (image) =>
      image.side === "front" &&
      image.pair_id &&
      exact.some(
        (back) => back.side === "back" && back.pair_id === image.pair_id,
      ),
  );
  return (
    <section className="kb-detail">
      <p className="eyebrow">
        {seasonLabel(card.season_start, card.season_end)} ·{" "}
        {card.manufacturer || "厂商未知"}
      </p>
      <h1 ref={title} tabIndex={-1}>
        {card.name}
      </h1>
      {card.product_id && (
        <button
          className="kb-series-link"
          onClick={() => onProduct(card.product_id!)}
        >
          {card.product_name || "查看所属系列"} ›
        </button>
      )}
      <section className="panel">
        <dl className="kb-facts">
          <dt>球员</dt>
          <dd>{card.player || "未知"}</dd>
          <dt>卡号</dt>
          <dd>{card.card_number || "未知"}</dd>
          <dt>子系列</dt>
          <dd>{card.section || "未知"}</dd>
          <dt>平行版本</dt>
          <dd>{card.parallel || "未知"}</dd>
          <dt>限量总数</dt>
          <dd>{card.serial_total ?? "未知"}</dd>
          <dt>新秀 / 签字 / 物料</dt>
          <dd>
            {flagLabel(card.is_rookie)} / {flagLabel(card.is_autograph)} /{" "}
            {flagLabel(card.is_patch)}
          </dd>
        </dl>
      </section>
      <section className="panel">
        <h2>该卡图片证据</h2>
        {!exact.some((image) => image.side === "front") && (
          <p className="notice">未收录该卡正面图片</p>
        )}
        {!exact.some((image) => image.side === "back") && (
          <p className="notice">未收录该卡背面图片</p>
        )}
        <p className="small muted">
          {paired
            ? "已收录来源确认的同组正反面。"
            : "尚无已确认的该卡正反面配对。"}{" "}
          设计示意不能代替该卡实图。
        </p>
        {!!images.length && (
          <KnowledgeGallery api={api} media={images} version={version} />
        )}
      </section>
      <section className="panel">
        <h2>同卡相关版本</h2>
        <p className="small muted">
          仅列同产品、赛季、球员、卡号和子系列的已发布版本（最多 30 条）。
        </p>
        {related_versions.length ? (
          <KnowledgeCardRows cards={related_versions} onCard={onCard} />
        ) : (
          <p className="muted">暂无其他已确认版本。</p>
        )}
      </section>
      <section className="panel">
        <h2>卡牌来源</h2>
        <KnowledgeSources sources={card.sources} />
      </section>
    </section>
  );
}
