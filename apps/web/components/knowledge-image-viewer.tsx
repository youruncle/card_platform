"use client";

import React, { useEffect, useRef, useState } from "react";
import { assetUrl } from "../lib/api";
import { mediaCategory, mediaKind, mediaTitle, seasonLabel, sideLabel, sourceLabel, type KnowledgeMedia } from "../lib/knowledge-browser";

export type KnowledgeViewerState = { images: KnowledgeMedia[]; index: number; compare: boolean; opener: HTMLElement | null };

export function KnowledgeImageViewer({ viewer, onClose }: { viewer: KnowledgeViewerState; onClose: () => void }) {
  const [index, setIndex] = useState(viewer.index);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [compare, setCompare] = useState(viewer.compare);
  const [comparison, setComparison] = useState(viewer.images.length > 1 ? (viewer.index + 1) % viewer.images.length : 0);
  const dialog = useRef<HTMLDivElement>(null);
  const image = viewer.images[index];
  const reset = () => { setZoom(1); setRotation(0); };
  const change = (next: number) => { if (next === comparison) setComparison(index); setIndex(next); reset(); };

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      viewer.opener?.focus();
    };
  }, [viewer.opener]);

  function keyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") { event.preventDefault(); onClose(); }
    if (event.key === "ArrowLeft") { event.preventDefault(); change((index - 1 + viewer.images.length) % viewer.images.length); }
    if (event.key === "ArrowRight") { event.preventDefault(); change((index + 1) % viewer.images.length); }
    if (event.key === "+" || event.key === "=") setZoom(value => Math.min(4, value + .25));
    if (event.key === "-") setZoom(value => Math.max(.5, value - .25));
    if (event.key === "0") reset();
    if (event.key === "Tab") {
      const elements = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],select,summary') ?? []);
      const first = elements[0], last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }
  function imagePane(item: KnowledgeMedia) {
    return <figure className="kb-viewer-pane" key={item.id}>
      <ScrollableKnowledgeImage image={item} zoom={zoom} rotation={rotation} />
      <figcaption><strong>{mediaTitle(item)}</strong><span>{mediaKind(item)} · {sideLabel(item.side)} · {sourceLabel(item.source_authority)}{!item.card_id && mediaCategory(item) !== "design" && " · 未绑定具体卡牌"}</span>{item.product_id && <span>{item.product_name ?? "系列名称待补充"} · {item.manufacturer ?? "制造商未知"} · {seasonLabel(item.season_start ?? null, item.season_end)}</span>}{item.caption && mediaTitle(item) !== item.caption && <details><summary>原始图片说明</summary><p>{item.caption}</p></details>}</figcaption>
    </figure>;
  }
  return <div className="kb-viewer-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="kb-viewer" role="dialog" aria-modal="true" aria-label="知识图片查看器" ref={dialog} onKeyDown={keyDown}>
      <header><div><strong>图片查看</strong><small>{index + 1} / {viewer.images.length} · {mediaKind(image)}</small></div><button onClick={onClose} aria-label="关闭图片查看器">关闭 ✕</button></header>
      <div className="kb-viewer-tools">
        <button aria-label="缩小图片" onClick={() => setZoom(value => Math.max(.5, value - .25))}>−</button><span aria-live="polite">{Math.round(zoom * 100)}%</span>
        <button aria-label="放大图片" onClick={() => setZoom(value => Math.min(4, value + .25))}>＋</button>
        <button aria-label="向左旋转图片" onClick={() => setRotation(value => value - 90)}>左转</button><button aria-label="向右旋转图片" onClick={() => setRotation(value => value + 90)}>右转</button>
        <button onClick={reset}>还原</button><a href={assetUrl(image.image_url) ?? undefined} target="_blank" rel="noreferrer">查看大图</a>
        {viewer.images.length > 1 && <button aria-pressed={compare} onClick={() => setCompare(value => !value)}>图片对照</button>}
      </div>
      <div className="kb-viewer-switch">
        <button disabled={viewer.images.length < 2} onClick={() => change((index - 1 + viewer.images.length) % viewer.images.length)}>上一张</button>
        <select aria-label="当前图片" value={index} onChange={event => change(Number(event.target.value))}>{viewer.images.map((item, i) => <option key={item.id} value={i}>{mediaTitle(item, i)} · {sideLabel(item.side)}</option>)}</select>
        <button disabled={viewer.images.length < 2} onClick={() => change((index + 1) % viewer.images.length)}>下一张</button>
        {image.card_id && viewer.images.filter(item => item.card_id === image.card_id && item.side !== "unknown").map(item => <button key={item.id} aria-pressed={item.id === image.id} onClick={() => change(viewer.images.indexOf(item))}>{sideLabel(item.side)}</button>)}
        {compare && <label>对照图片<select aria-label="对照图片" value={comparison} onChange={event => { const next = Number(event.target.value); if (next !== index) setComparison(next); }}>{viewer.images.map((item, i) => <option key={item.id} value={i} disabled={i === index}>{mediaTitle(item, i)} · {sideLabel(item.side)}</option>)}</select></label>}
      </div>
      <div className={`kb-viewer-stage ${compare ? "kb-comparison" : ""}`}>{imagePane(image)}{compare && imagePane(viewer.images[comparison])}</div>
      <footer>滚动图片区域查看放大细节 · ← → 切换 · + / − 缩放 · 0 还原 · Esc 关闭{image.source_url && <> · <a href={image.source_url} target="_blank" rel="noreferrer">图片来源</a></>}</footer>
    </div>
  </div>;
}

/** A physical canvas keeps enlarged and rotated corners in the nonnegative scroll area. */
function ScrollableKnowledgeImage({ image, zoom, rotation }: { image: KnowledgeMedia; zoom: number; rotation: number }) {
  const port = useRef<HTMLDivElement>(null);
  const [bounds, setBounds] = useState({ width: 320, height: 360 });
  const [natural, setNatural] = useState({ width: 400, height: 600 });
  useEffect(() => {
    const element = port.current;
    if (!element) return;
    const measure = () => setBounds(current => {
      const width = element.clientWidth || 320, height = element.clientHeight || 360;
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
  const fit = Math.min(1, Math.max(1, bounds.width - 32) / (sideways ? natural.height : natural.width), Math.max(1, bounds.height - 32) / (sideways ? natural.width : natural.height));
  const width = natural.width * fit, height = natural.height * fit;
  const canvasWidth = (sideways ? height : width) * zoom, canvasHeight = (sideways ? width : height) * zoom;
  const x = angle === 90 ? height * zoom : angle === 180 ? width * zoom : 0;
  const y = angle === 180 ? height * zoom : angle === 270 ? width * zoom : 0;
  return <div className="kb-viewer-image" ref={port}>
    <div className="kb-viewer-canvas" style={{ width: canvasWidth, height: canvasHeight, marginTop: Math.max(0, (bounds.height - canvasHeight) / 2), marginBottom: Math.max(0, (bounds.height - canvasHeight) / 2) }}>
      <KnowledgeImage retryable src={image.image_url} alt={mediaTitle(image)} onLoad={event => {
        const loaded = event.currentTarget;
        if (loaded.naturalWidth && loaded.naturalHeight) setNatural({ width: loaded.naturalWidth, height: loaded.naturalHeight });
      }} style={{ width, height, transformOrigin: "0 0", transform: `translate(${x}px, ${y}px) rotate(${rotation}deg) scale(${zoom})` }} />
    </div>
  </div>;
}

export function KnowledgeImage({ src, alt, style, className, retryable = false, onLoad }: { src: string; alt: string; style?: React.CSSProperties; className?: string; retryable?: boolean; onLoad?: React.ReactEventHandler<HTMLImageElement> }) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { setFailed(false); setAttempt(0); }, [src]);
  if (failed) return <span className="kb-image-error" role="status">图片暂不可用{retryable && <button onClick={event => { event.stopPropagation(); setAttempt(value => value + 1); setFailed(false); }}>重试图片</button>}</span>;
  const resolved = assetUrl(src) ?? src;
  return <img className={className} src={attempt ? `${resolved}${resolved.includes("?") ? "&" : "?"}_retry=${attempt}` : resolved} alt={alt} loading="lazy" decoding="async" style={style} onLoad={onLoad} onError={() => setFailed(true)} />;
}
