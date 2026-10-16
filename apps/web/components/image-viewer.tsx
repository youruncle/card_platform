"use client";

import {
  ChevronLeft,
  ChevronRight,
  Download,
  Maximize2,
  RotateCcw,
  RotateCw,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

type ViewerImage = { url: string; alt: string };
type ImageViewerDetail = { images: ViewerImage[]; index: number };

function openImageViewer(
  url: string | null | undefined,
  alt: string,
  gallery?: Array<{ src: string | null | undefined; alt: string }>,
) {
  if (!url) return;
  const images = (gallery ?? [{ src: url, alt }])
    .filter((item): item is { src: string; alt: string } => Boolean(item.src))
    .map((item) => ({ url: item.src, alt: item.alt }));
  const index = Math.max(0, images.findIndex((item) => item.url === url && item.alt === alt));
  window.dispatchEvent(
    new CustomEvent<ImageViewerDetail>("card-platform:open-image", {
      detail: { images, index },
    }),
  );
}

export function OpenableImage({
  src,
  alt,
  className,
  gallery,
  displaySrc,
}: {
  src: string | undefined;
  displaySrc?: string | null;
  alt: string;
  className?: string;
  gallery?: Array<{ src: string | null | undefined; alt: string }>;
}) {
  const [attempt,setAttempt]=useState(0),[failed,setFailed]=useState(false);
  useEffect(()=>{setAttempt(0);setFailed(false);},[src,displaySrc]);
  useEffect(()=>{if(!failed || attempt>=2 || !displaySrc)return;const timer=setTimeout(()=>{setAttempt(n=>n+1);setFailed(false);},750*(attempt+1));return()=>clearTimeout(timer);},[failed,attempt,displaySrc]);
  if(failed)return <button type="button" aria-label={`重新加载${alt}`} onClick={()=>{setAttempt(0);setFailed(false);}}>图片暂不可用 · 重试</button>;
  const imageSrc=displaySrc ?? src;
  return (
    <img
      src={imageSrc && attempt ? `${imageSrc}${imageSrc.includes("?") ? "&" : "?"}_retry=${attempt}` : imageSrc}
      onError={()=>setFailed(true)}
      loading="lazy"
      decoding="async"
      alt={alt}
      className={className}
      role="button"
      tabIndex={0}
      title="点击查看高清大图"
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        openImageViewer(src, alt, gallery);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openImageViewer(src, alt, gallery);
        }
      }}
    />
  );
}

export function GlobalImageViewer() {
  const [viewer, setViewer] = useState<ImageViewerDetail | null>(null);
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [hoverPoint, setHoverPoint] = useState({ x: 0, y: 0 });
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 });
  const [hovering, setHovering] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [hoverMagnifier, setHoverMagnifier] = useState(true);
  const drag = useRef<{
    pointerX: number;
    pointerY: number;
    panX: number;
    panY: number;
    startedOnEmptyStage: boolean;
    moved: boolean;
  } | null>(null);

  const resetView = useCallback(() => {
    drag.current = null;
    setZoom(1);
    setRotation(0);
    setPan({ x: 0, y: 0 });
    setHoverPoint({ x: 0, y: 0 });
    setStageSize({ width: 0, height: 0 });
    setDragging(false);
  }, []);

  useEffect(() => {
    const open = (event: Event) => {
      const detail = (event as CustomEvent<ImageViewerDetail>).detail;
      if (!detail?.images?.length) return;
      setViewer(detail);
      resetView();
    };
    window.addEventListener("card-platform:open-image", open);
    return () => window.removeEventListener("card-platform:open-image", open);
  }, [resetView]);

  useEffect(() => {
    if (!viewer) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setViewer(null);
      if (event.key === "ArrowLeft" && viewer.images.length > 1) {
        setViewer((current) => current && ({
          ...current,
          index: (current.index - 1 + current.images.length) % current.images.length,
        }));
        resetView();
      }
      if (event.key === "ArrowRight" && viewer.images.length > 1) {
        setViewer((current) => current && ({
          ...current,
          index: (current.index + 1) % current.images.length,
        }));
        resetView();
      }
      if (event.key === "+" || event.key === "=") {
        setZoom((value) => Math.min(6, value + 0.25));
      }
      if (event.key === "-") setZoom((value) => Math.max(0.5, value - 0.25));
      if (event.key === "0") resetView();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [viewer, resetView]);

  if (!viewer) return null;
  const image = viewer.images[viewer.index];
  const hasGallery = viewer.images.length > 1;
  const changeImage = (direction: -1 | 1) => {
    setViewer((current) => current && ({
      ...current,
      index: (current.index + direction + current.images.length) % current.images.length,
    }));
    resetView();
  };
  const magnifierScale = 2.2;
  const imageTransform = `translate(${pan.x}px, ${pan.y}px) rotate(${rotation}deg) scale(${zoom})`;

  return (
    <div
      className="image-viewer-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={image.alt}
      onClick={(event) => {
        if (event.target === event.currentTarget) setViewer(null);
      }}
    >
      <div className="image-viewer-toolbar">
        <strong>{image.alt}</strong>
        <div>
          <button type="button" aria-label="缩小" title="缩小" onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}>
            <ZoomOut size={17} />
          </button>
          <span aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button type="button" aria-label="放大" title="放大" onClick={() => setZoom((value) => Math.min(6, value + 0.25))}>
            <ZoomIn size={17} />
          </button>
          <button type="button" aria-label="向左旋转" title="向左旋转" onClick={() => setRotation((value) => value - 90)}>
            <RotateCcw size={17} />
          </button>
          <button type="button" aria-label="向右旋转" title="向右旋转" onClick={() => setRotation((value) => value + 90)}>
            <RotateCw size={17} />
          </button>
          <button
            type="button"
            className={hoverMagnifier ? "active" : ""}
            aria-label="切换悬停局部放大"
            aria-pressed={hoverMagnifier}
            title="切换悬停局部放大"
            onClick={() => setHoverMagnifier((value) => !value)}
          >
            <Maximize2 size={17} />
          </button>
          <button type="button" aria-label="还原视图" title="还原视图（快捷键 0）" onClick={resetView}>还原</button>
          <a href={image.url} target="_blank" rel="noreferrer" aria-label="在新窗口打开原图" title="在新窗口打开原图">
            <Download size={17} />
          </a>
          <button type="button" aria-label="关闭图片查看器" title="关闭（Esc）" onClick={() => setViewer(null)}>
            <X size={18} />
          </button>
        </div>
      </div>
      <div
        className="image-viewer-stage"
        onWheel={(event) => {
          event.preventDefault();
          const delta = event.deltaY < 0 ? 0.2 : -0.2;
          setZoom((value) => Math.max(0.5, Math.min(6, value + delta)));
        }}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          drag.current = {
            pointerX: event.clientX,
            pointerY: event.clientY,
            panX: pan.x,
            panY: pan.y,
            startedOnEmptyStage: event.target === event.currentTarget,
            moved: false,
          };
          setDragging(true);
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const activeDrag = drag.current;
          if (activeDrag) {
            const deltaX = event.clientX - activeDrag.pointerX;
            const deltaY = event.clientY - activeDrag.pointerY;
            if (deltaX * deltaX + deltaY * deltaY > 100) activeDrag.moved = true;
            setPan({
              x: activeDrag.panX + deltaX,
              y: activeDrag.panY + deltaY,
            });
            return;
          }
          const bounds = event.currentTarget.getBoundingClientRect();
          setHoverPoint({
            x: event.clientX - bounds.left,
            y: event.clientY - bounds.top,
          });
          setStageSize({ width: bounds.width, height: bounds.height });
        }}
        onPointerUp={(event) => {
          const shouldClose = Boolean(
            drag.current?.startedOnEmptyStage && !drag.current.moved,
          );
          drag.current = null;
          setDragging(false);
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
          }
          if (shouldClose) setViewer(null);
        }}
        onPointerCancel={() => {
          drag.current = null;
          setDragging(false);
        }}
        onLostPointerCapture={() => {
          drag.current = null;
          setDragging(false);
        }}
        onPointerEnter={() => setHovering(true)}
        onPointerLeave={() => {
          setHovering(false);
        }}
      >
        {hasGallery && (
          <button
            type="button"
            className="image-viewer-nav previous"
            aria-label="查看上一张图片"
            title="上一张（方向键 ←）"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              changeImage(-1);
            }}
          >
            <ChevronLeft size={34} />
          </button>
        )}
        <img
          src={image.url}
          alt={image.alt}
          draggable={false}
          style={{
            transform: imageTransform,
            transformOrigin: "center",
          }}
        />
        {hovering && hoverMagnifier && !dragging && stageSize.width > 0 && (
          <div
            className="image-viewer-magnifier"
            aria-hidden="true"
            style={{ left: hoverPoint.x, top: hoverPoint.y }}
          >
            <div
              className="image-viewer-magnifier-canvas"
              style={{
                width: stageSize.width,
                height: stageSize.height,
                left: 90 - hoverPoint.x * magnifierScale,
                top: 90 - hoverPoint.y * magnifierScale,
                transform: `scale(${magnifierScale})`,
              }}
            >
              <img
                src={image.url}
                alt=""
                draggable={false}
                style={{ transform: imageTransform, transformOrigin: "center" }}
              />
            </div>
          </div>
        )}
        {hasGallery && (
          <button
            type="button"
            className="image-viewer-nav next"
            aria-label="查看下一张图片"
            title="下一张（方向键 →）"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.stopPropagation();
              changeImage(1);
            }}
          >
            <ChevronRight size={34} />
          </button>
        )}
      </div>
      <small className="image-viewer-help">
        {hasGallery ? `${viewer.index + 1} / ${viewer.images.length} · 左右方向键切换 · ` : ""}
        点击空白区域关闭 · 悬停放大镜不会移动原图 · 按住鼠标左键拖拽平移 · 滚轮缩放 · Esc 关闭
      </small>
    </div>
  );
}
