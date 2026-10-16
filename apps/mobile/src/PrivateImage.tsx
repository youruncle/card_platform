import React, { useEffect, useRef, useState } from "react";
import type { MobileApi } from "./api";
function ImageViewer({
  src,
  alt,
  onClose,
}: {
  src: string;
  alt: string;
  onClose: () => void;
}) {
  const [zoom, setZoom] = useState(1);
  const initial = useRef<{ distance: number; zoom: number } | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const clamp = (value: number) => Math.min(4, Math.max(1, value));
  useEffect(() => {
    const prior = document.activeElement as HTMLElement | null,
      overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      document.body.style.overflow = overflow;
      prior?.focus();
    };
  }, []);
  return (
    <div
      ref={dialog}
      className="image-viewer"
      role="dialog"
      aria-modal="true"
      aria-label={`${alt}大图`}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
        if (e.key === "Tab") {
          const buttons = Array.from(
            dialog.current?.querySelectorAll<HTMLButtonElement>("button") ?? [],
          );
          const first = buttons[0],
            last = buttons[buttons.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last?.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first?.focus();
          }
        }
      }}
    >
      <div className="viewer-toolbar">
        <button onClick={onClose}>关闭大图</button>
        <button
          aria-label="缩小"
          disabled={zoom <= 1}
          onClick={() => setZoom((v) => clamp(v - 0.25))}
        >
          −
        </button>
        <span>{Math.round(zoom * 100)}%</span>
        <button
          aria-label="放大"
          disabled={zoom >= 4}
          onClick={() => setZoom((v) => clamp(v + 0.25))}
        >
          ＋
        </button>
      </div>
      <div
        className="viewer-scroll"
        onTouchStart={(e) => {
          if (e.touches.length === 2)
            initial.current = {
              distance: Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY,
              ),
              zoom,
            };
        }}
        onTouchMove={(e) => {
          if (e.touches.length === 2 && initial.current?.distance) {
            const d = Math.hypot(
              e.touches[0].clientX - e.touches[1].clientX,
              e.touches[0].clientY - e.touches[1].clientY,
            );
            setZoom(
              clamp((initial.current.zoom * d) / initial.current.distance),
            );
          }
        }}
        onTouchEnd={() => {
          initial.current = null;
        }}
      >
        <img
          src={src}
          alt={`${alt}大图`}
          style={{ width: `${zoom * 100}%`, maxWidth: "none" }}
        />
      </div>
    </div>
  );
}
export function PrivateImage({
  api,
  path,
  alt,
  originalPath,
}: {
  originalPath?: string | null;
  api: MobileApi;
  path?: string | null;
  alt: string;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(typeof IntersectionObserver === "undefined"),
    [revision, setRevision] = useState(0),
    [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<{
      api: MobileApi;
      path: string;
      src: string;
    } | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setNear(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    if (holder.current) observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let active = true,
      url = "";
    setLoaded(null);
    setError("");
    setOpen(false);
    if (path && near)
      api
        .image(path)
        .then((blob) => {
          if (active) {
            url = URL.createObjectURL(blob);
            setLoaded({ api, path, src: url });
          }
        })
        .catch(() => {
          if (active) setError("图片暂不可用");
        });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, path, near, revision]);
  const [original, setOriginal] = useState<{
    api: MobileApi;
    path: string;
    src: string;
  } | null>(null);
  const [originalError, setOriginalError] = useState("");
  const [originalRetry, setOriginalRetry] = useState(0);
  useEffect(() => {
    let active = true,
      url = "";
    setOriginal(null);
    setOriginalError("");
    if (open && originalPath && originalPath !== path)
      api
        .image(originalPath)
        .then((blob) => {
          if (active) {
            url = URL.createObjectURL(blob);
            setOriginal({ api, path: originalPath, src: url });
          }
        })
        .catch(() => {
          if (active) setOriginalError("大图加载失败");
        });
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [api, originalPath, path, open, originalRetry]);
  const originalSrc =
    original?.api === api && original.path === originalPath ? original.src : "";
  const src = loaded?.api === api && loaded.path === path ? loaded.src : "";
  return (
    <div ref={holder} className="private-image">
      {src ? (
        <>
          <button
            className="image-open"
            aria-label={`查看${alt}`}
            onClick={() => setOpen(true)}
          >
            <img className="card-image" src={src} alt={alt} />
          </button>
          {open &&
            (originalPath && originalPath !== path && !originalSrc ? (
              <div
                role="dialog"
                aria-modal="true"
                aria-label="加载大图"
                className="image-viewer"
              >
                <p>{originalError || "正在加载大图…"}</p>
                {originalError && (
                  <button onClick={() => setOriginalRetry((n) => n + 1)}>
                    重试大图
                  </button>
                )}
                <button onClick={() => setOpen(false)}>关闭大图</button>
              </div>
            ) : (
              <ImageViewer
                src={originalSrc || src}
                alt={alt}
                onClose={() => setOpen(false)}
              />
            ))}
        </>
      ) : (
        <div className="image-placeholder">
          {error || (!path ? "暂无图片" : "加载图片…")}
          {error && (
            <button
              aria-label={`重新加载${alt}`}
              onClick={() => setRevision((v) => v + 1)}
            >
              重新加载
            </button>
          )}
        </div>
      )}
    </div>
  );
}
export function LocalImage({ blob, alt }: { blob?: Blob; alt: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!blob) {
      setUrl("");
      return;
    }
    const src = URL.createObjectURL(blob);
    setUrl(src);
    return () => URL.revokeObjectURL(src);
  }, [blob]);
  return url ? (
    <img className="card-image" src={url} alt={alt} />
  ) : (
    <div className="image-placeholder">{alt}</div>
  );
}
