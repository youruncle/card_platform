import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { KnowledgeMedia } from "../../../packages/card-core/src/knowledge-browser";
import type { MobileApi } from "./api";
import { sourceAuthorityLabel, seasonLabel } from "./knowledge-browser";

export const isDesignMedia = (media: KnowledgeMedia) =>
  media.image_type.toLowerCase().includes("design") ||
  media.image_type === "official_product_image";
const specimenTypes = new Set([
  "verified_card_scan",
  "card_scan",
  "reviewed_card_scan",
  "specimen",
  "verified_card",
  "verified_specimen",
]);
export const isVerifiedMedia = (media: KnowledgeMedia) =>
  specimenTypes.has(media.image_type);
export const mediaKind = (media: KnowledgeMedia) =>
  isDesignMedia(media)
    ? "官方设计示意 · 非逐卡实图"
    : isVerifiedMedia(media)
      ? "已核实实卡图片"
      : "图片类型待核对";
export const sideLabel = (side: KnowledgeMedia["side"]) =>
  side === "front" ? "正面" : side === "back" ? "背面" : "侧别未知";
export const mediaLabel = (media: KnowledgeMedia) =>
  media.caption ||
  `${media.player || "参考图"} ${media.card_number ? `#${media.card_number}` : ""} ${sideLabel(media.side)}`;

function mediaTitle(media: KnowledgeMedia) {
  if (!media.caption || media.caption.length <= 80) return mediaLabel(media);
  const page = media.caption.match(/page\s*(\d+)/i)?.[1];
  const type = isDesignMedia(media)
    ? "官方设计示意"
    : isVerifiedMedia(media)
      ? "核实实卡图片"
      : "参考图片";
  return [
    type,
    media.player,
    media.card_number ? `#${media.card_number}` : null,
    page ? `第 ${page} 页` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function imagePath(path: string, version: string | null) {
  // The server supplies a protected relative route, never a remote source URL.
  const [base, query] = path.split("?");
  if (!base.startsWith("/v1/knowledge/browse/media/") || base.includes("\\"))
    throw new Error("无效的知识图片地址");
  const params = new URLSearchParams(query);
  if (version) params.set("version_id", version);
  return `${base}${params.size ? `?${params}` : ""}`;
}

export function KnowledgeImage({
  api,
  path,
  version,
  alt,
  style,
  retryable = true,
  onLoad,
}: {
  api: MobileApi;
  path: string;
  version: string | null;
  alt: string;
  style?: React.CSSProperties;
  retryable?: boolean;
  onLoad?: React.ReactEventHandler<HTMLImageElement>;
}) {
  const [state, setState] = useState<{
    key: string;
    url?: string;
    error?: string;
  } | null>(null);
  const [revision, setRevision] = useState(0);
  const key = `${version}:${path}:${revision}`;
  useEffect(() => {
    let active = true;
    let objectUrl: string | undefined;
    setState(null);
    const load = async () => {
      try {
        const blob = await api.image(imagePath(path, version));
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ key, url: objectUrl });
      } catch (error) {
        if (active)
          setState({
            key,
            error: error instanceof Error ? error.message : "图片加载失败",
          });
      }
    };
    void load();
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, path, version, revision, key]);
  if (state?.key === key && state.error)
    return (
      <span className="kb-image-failure" role="status">
        图片不可用：{state.error}
        {retryable && (
          <button type="button" onClick={() => setRevision((v) => v + 1)}>
            重试图片
          </button>
        )}
      </span>
    );
  if (state?.key !== key || !state.url)
    return (
      <span className="kb-image-loading" role="status">
        图片加载中…
      </span>
    );
  return (
    <img
      src={state.url}
      alt={alt}
      style={style}
      onLoad={onLoad}
      onError={() => setState({ key, error: "图片无法显示" })}
    />
  );
}

type ViewerSelection = {
  media: KnowledgeMedia[];
  index: number;
  opener: HTMLElement | null;
};
export function KnowledgeGallery({
  api,
  media,
  version,
  onProduct,
}: {
  api: MobileApi;
  media: KnowledgeMedia[];
  version: string | null;
  onProduct?: (id: string) => void;
}) {
  const [selection, setSelection] = useState<ViewerSelection | null>(null);
  return (
    <>
      {!media.length ? (
        <p className="empty">
          当前条件下未收录图片。设计示意和逐卡实图分开统计。
        </p>
      ) : (
        <div className="kb-gallery">
          {media.map((item, index) => (
            <article className="kb-media-tile" key={item.id}>
              <button
                className="kb-image-open"
                aria-label={`查看图片：${mediaTitle(item)}`}
                onClick={(event) =>
                  setSelection({ media, index, opener: event.currentTarget })
                }
              >
                <KnowledgeImage
                  api={api}
                  path={item.thumbnail_url}
                  version={version}
                  alt={mediaLabel(item)}
                  retryable={false}
                />
              </button>
              <p className="kb-media-caption">{mediaTitle(item)}</p>
              {item.product_name && <p className="small muted">
                {item.product_name} · {seasonLabel(item.season_start ?? null, item.season_end ?? null)}
              </p>}
              {item.product_id && onProduct && <button type="button"
                aria-label={`查看所属系列：${item.product_name || "系列详情"}`}
                onClick={() => onProduct(item.product_id!)}>查看所属系列</button>}
              <p className="small">{mediaKind(item)}</p>
              <p className="small muted">
                {sideLabel(item.side)}
                {item.pair_id ? " · 有配对记录" : " · 无确认配对"}
              </p>
              {item.source_url && /^https?:\/\//i.test(item.source_url) && (
                <a
                  className="small"
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看图片来源
                </a>
              )}
            </article>
          ))}
        </div>
      )}
      {selection && (
        <KnowledgeViewer
          api={api}
          selection={selection}
          version={version}
          onClose={() => setSelection(null)}
        />
      )}
    </>
  );
}

function KnowledgeViewer({
  api,
  selection,
  version,
  onClose,
}: {
  api: MobileApi;
  selection: ViewerSelection;
  version: string | null;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(selection.index);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [compare, setCompare] = useState("");
  const dialog = useRef<HTMLDivElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const item = selection.media[index];
  const comparison = selection.media.find((media) => media.id === compare);
  const pair =
    isVerifiedMedia(item) &&
    item.pair_id &&
    item.card_id &&
    item.side !== "unknown"
      ? selection.media.find(
          (media) =>
            isVerifiedMedia(media) &&
            media.id !== item.id &&
            media.pair_id === item.pair_id &&
            media.card_id === item.card_id &&
            media.product_id === item.product_id &&
            media.side !== "unknown" &&
            media.side !== item.side,
        )
      : undefined;
  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    close.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
      }
      if (event.key !== "Tab") return;
      const controls = Array.from(
        dialog.current?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),select,a[href],[tabindex='0']",
        ) || [],
      );
      const first = controls[0],
        last = controls[controls.length - 1];
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === dialog.current)
      ) {
        event.preventDefault();
        last?.focus();
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    const back = () => onCloseRef.current();
    document.addEventListener("keydown", keydown);
    window.addEventListener("popstate", back);
    const marker = `knowledge-media-${Date.now()}`;
    window.history.pushState(
      { ...window.history.state, knowledgeViewer: marker },
      "",
    );
    return () => {
      document.body.style.overflow = originalOverflow;
      document.removeEventListener("keydown", keydown);
      window.removeEventListener("popstate", back);
      if (window.history.state?.knowledgeViewer === marker)
        window.history.back();
      selection.opener?.focus();
    };
  }, [selection]);
  function switchTo(next: number) {
    setIndex(next);
    setZoom(1);
    setRotation(0);
    setCompare("");
  }
  return createPortal(
    <div
      className="kb-viewer"
      role="dialog"
      aria-modal="true"
      aria-label="图片查看器"
      ref={dialog}
      tabIndex={-1}
    >
      <header>
        <strong>{mediaTitle(item)}</strong>
        <button ref={close} onClick={onClose}>
          关闭图片
        </button>
      </header>
      <div className="kb-viewer-tools">
        <button disabled={index === 0} onClick={() => switchTo(index - 1)}>
          上一张
        </button>
        <span>
          {index + 1} / {selection.media.length}
        </span>
        <button
          disabled={index === selection.media.length - 1}
          onClick={() => switchTo(index + 1)}
        >
          下一张
        </button>
        {pair && (
          <button onClick={() => switchTo(selection.media.indexOf(pair))}>
            {sideLabel(pair.side)}
          </button>
        )}
        <button
          disabled={zoom >= 3}
          onClick={() => setZoom((v) => Math.min(3, v + 0.25))}
        >
          放大
        </button>
        <button
          disabled={zoom <= 0.5}
          onClick={() => setZoom((v) => Math.max(0.5, v - 0.25))}
        >
          缩小
        </button>
        <button onClick={() => setRotation((v) => (v + 90) % 360)}>旋转</button>
        <button
          onClick={() => {
            setZoom(1);
            setRotation(0);
          }}
        >
          复位
        </button>
      </div>
      <p className="small" aria-live="polite">
        {mediaKind(item)} · {sideLabel(item.side)} · {Math.round(zoom * 100)}%
        {pair ? " · 已核实同组正反面" : " · 无确认的另一面"}
      </p>
      <label className="kb-compare-label">
        对照图片
        <select
          aria-label="对照图片"
          value={compare}
          onChange={(event) => setCompare(event.target.value)}
        >
          <option value="">不对照</option>
          {selection.media
            .filter((media) => media.id !== item.id)
            .map((media) => (
              <option value={media.id} key={media.id}>
                {mediaLabel(media)} · {sideLabel(media.side)}
              </option>
            ))}
        </select>
      </label>
      <div className={`kb-viewer-images${comparison ? " kb-comparing" : ""}`}>
        <ScrollableKnowledgeImage api={api} image={item} version={version}
          alt={mediaLabel(item)} zoom={zoom} rotation={rotation} />
        {comparison && (
          <ScrollableKnowledgeImage api={api} image={comparison} version={version}
            alt={`对照：${mediaLabel(comparison)}`} zoom={1} rotation={0} />
        )}
      </div>
      <footer>
        {item.caption && <p className="small">{item.caption}</p>}
        <p className="small">{sourceAuthorityLabel(item.source_authority)}</p>
        {item.source_url && /^https?:\/\//i.test(item.source_url) && (
          <a href={item.source_url} target="_blank" rel="noreferrer">
            查看原始来源
          </a>
        )}
        <p className="small">并列查看不代表同一张卡或已确认配对。</p>
      </footer>
    </div>,
    document.body,
  );
}

/** Keep every enlarged/rotated corner inside a nonnegative scroll canvas. */
function ScrollableKnowledgeImage({ api, image, version, alt, zoom, rotation }: {
  api: MobileApi; image: KnowledgeMedia; version: string | null;
  alt: string; zoom: number; rotation: number;
}) {
  const port = useRef<HTMLDivElement>(null);
  const [bounds, setBounds] = useState({ width: 320, height: 400 });
  const [natural, setNatural] = useState({ width: 240, height: 336 });
  useEffect(() => {
    const element = port.current;
    if (!element) return;
    const measure = () => setBounds(current => {
      const width = element.clientWidth || 320;
      const height = element.clientHeight || 400;
      return current.width === width && current.height === height ? current : { width, height };
    });
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(measure);
      observer.observe(element);
      return () => observer.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  const angle = ((rotation % 360) + 360) % 360;
  const sideways = angle === 90 || angle === 270;
  const fit = Math.min(1, Math.max(1, bounds.width - 24) / (sideways ? natural.height : natural.width),
    Math.max(1, bounds.height - 24) / (sideways ? natural.width : natural.height));
  const width = natural.width * fit, height = natural.height * fit;
  const canvasWidth = (sideways ? height : width) * zoom;
  const canvasHeight = (sideways ? width : height) * zoom;
  const x = angle === 90 ? height * zoom : angle === 180 ? width * zoom : 0;
  const y = angle === 180 ? height * zoom : angle === 270 ? width * zoom : 0;
  return <div className="kb-viewer-stage" ref={port}>
    <div className="kb-viewer-canvas" style={{ width: canvasWidth, height: canvasHeight }}>
      <KnowledgeImage api={api} path={image.image_url} version={version} alt={alt}
        onLoad={event => {
          const loaded = event.currentTarget;
          if (loaded.naturalWidth && loaded.naturalHeight)
            setNatural({ width: loaded.naturalWidth, height: loaded.naturalHeight });
        }}
        style={{ width, height, transformOrigin: "0 0",
          transform: `translate(${x}px, ${y}px) rotate(${rotation}deg) scale(${zoom})` }} />
    </div>
  </div>;
}
