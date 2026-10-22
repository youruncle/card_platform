import React, { useEffect, useState } from "react";
import type {
  KnowledgeCard,
  KnowledgePage,
  KnowledgeSource,
} from "../../../packages/card-core/src/knowledge-browser";
import { ApiError, type MobileApi } from "./api";

const entityLabels: Record<string, string> = {
  manufacturer: "厂商", brand: "厂牌", license: "授权", product: "产品 / 系列",
  insert: "特卡 / 子系列", parallel: "平行版本", attribute: "卡牌属性",
  player: "球员", card: "具体卡牌",
};
export const entityTypeLabel = (type: string) => entityLabels[type] ?? "其他知识";

export function browsePath(
  resource: string,
  version: string | null,
  params: Record<string, string | number | boolean | null | undefined> = {},
) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value !== undefined && value !== null && value !== "")
      query.set(key, String(value));
  if (version) query.set("version_id", version);
  return `/v1/knowledge/browse/${resource}${query.size ? `?${query}` : ""}`;
}

export function useKnowledge<T>(api: MobileApi, path: string | null) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{
    key: string | null;
    data: T | null;
    error: string;
    loading: boolean;
    preparing: boolean;
    retryExhausted?: boolean;
  }>({ key: null, data: null, error: "", loading: false, preparing: false });
  useEffect(() => {
    if (!path) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const request = async (attempt: number) => {
      setState({
        key: path,
        data: null,
        error: "",
        loading: true,
        preparing: false,
      });
      try {
        const data = await api.get<T>(path);
        if (active)
          setState({
            key: path,
            data,
            error: "",
            loading: false,
            preparing: false,
          });
      } catch (error) {
        if (!active) return;
        const preparing = error instanceof ApiError && error.status === 503;
        setState({
          key: path,
          data: null,
          error: error instanceof Error ? error.message : "知识库加载失败",
          loading: false,
          preparing,
          retryExhausted: preparing && attempt >= 10,
        });
        if (preparing && attempt < 10)
          timer = setTimeout(
            () => {
              if (active) void request(attempt + 1);
            },
            Math.min(30, error.retryAfter ?? 3) * 1000,
          );
      }
    };
    void request(0);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [api, path, revision]);
  const visible =
    state.key === path
      ? state
      : { data: null, error: "", loading: !!path, preparing: false };
  return { ...visible, retry: () => setRevision((v) => v + 1) };
}

export function KnowledgeStatus({
  resource,
}: {
  resource: {
    loading: boolean;
    error: string;
    preparing?: boolean;
    retryExhausted?: boolean;
    retry: () => void;
  };
}) {
  if (resource.error)
    return (
      <div role="alert" className={resource.preparing ? "notice" : "error"}>
        <p>{resource.error}</p>
        {resource.preparing && (
          <p className="small">{resource.retryExhausted
            ? "自动重试已停止，请点击重试继续浏览。"
            : "目录准备完成后可继续浏览，正在等待自动重试。"}</p>
        )}
        <button onClick={resource.retry}>重试</button>
      </div>
    );
  if (resource.loading)
    return (
      <p role="status" className="muted">
        正在加载已发布知识…
      </p>
    );
  return null;
}

export function KnowledgePager({
  page,
  onPage,
}: {
  page: KnowledgePage<unknown>;
  onPage: (offset: number) => void;
}) {
  if (!page.total) return null;
  return (
    <nav className="kb-pager" aria-label="分页">
      <button
        disabled={page.offset === 0}
        onClick={() => onPage(Math.max(0, page.offset - page.limit))}
      >
        上一页
      </button>
      <span aria-live="polite">
        {page.offset + 1}–{Math.min(page.offset + page.limit, page.total)} /{" "}
        {page.total}
      </span>
      <button
        disabled={page.offset + page.limit >= page.total}
        onClick={() => onPage(page.offset + page.limit)}
      >
        下一页
      </button>
    </nav>
  );
}

export function sourceAuthorityLabel(value: string | null) {
  return (
    (
      {
        official_host: "官方站点",
        manufacturer_document_third_party_host: "经销商托管原厂资料",
        official_checklist: "官方清单",
        official_product: "官方产品资料",
        official: "官方资料",
        verified: "已核实来源",
        reviewed: "已核实来源",
        manufacturer: "厂商资料",
        third_party: "第三方资料",
      } as Record<string, string>
    )[value ?? ""] ?? "来源类型待核对"
  );
}

export function KnowledgeSources({ sources }: { sources: KnowledgeSource[] }) {
  if (!sources.length) return <p className="muted">未提供来源。</p>;
  return (
    <ul className="kb-sources">
      {sources.map((source, i) => (
        <li key={`${source.url}-${i}`}>
          {source.url && /^https?:\/\//i.test(source.url) ? (
            <a href={source.url} target="_blank" rel="noreferrer">
              {sourceAuthorityLabel(source.authority)}
              {source.locator ? ` · ${source.locator}` : ""}
            </a>
          ) : (
            <span>
              {sourceAuthorityLabel(source.authority)}
              {source.locator ? ` · ${source.locator}` : ""}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

export const seasonLabel = (start: number | null, end: number | null) =>
  start === null
    ? "赛季未知"
    : end !== null && end !== start
      ? `${start}–${end}`
      : String(start);
export const flagLabel = (value: boolean | null) =>
  value === null ? "未知" : value ? "是" : "否";

export function KnowledgeCardRows({
  cards,
  onCard,
}: {
  cards: KnowledgeCard[];
  onCard: (id: string) => void;
}) {
  if (!cards.length) return <p className="empty">没有符合条件的卡牌知识。</p>;
  return (
    <div className="kb-card-list">
      {cards.map((card) => (
        <button
          className="kb-card-row"
          key={card.id}
          onClick={() => onCard(card.id)}
        >
          <strong>{card.name}</strong>
          <span>
            {card.player || "球员未知"} · #{card.card_number || "未知"}
          </span>
          <span className="small muted">
            {card.product_name || "系列未知"} ·{" "}
            {seasonLabel(card.season_start, card.season_end)}
          </span>
          <span className="small">
            {card.section || "子系列未知"} · {card.parallel || "平行未知"} ·{" "}
            {card.image_count ? `${card.image_count} 张实图` : "逐卡图缺失"}
          </span>
        </button>
      ))}
    </div>
  );
}
