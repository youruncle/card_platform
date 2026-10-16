import React, { useEffect, useRef, useState } from "react";
import type { MobileApi } from "./api";
import { normalizePhoto } from "./capture";
import { LocalImage } from "./PrivateImage";
import type {
  CropRecipe,
  CropProposal,
  Quad,
} from "../../../packages/card-core/src/capture";
type Proposal = CropProposal;
const full: Quad = [
  [0, 0],
  [1, 0],
  [1, 1],
  [0, 1],
];
export function QuadCropEditor({
  api,
  source,
  onApply,
  onCancel,
  rotation = 0,
}: {
  api: MobileApi;
  source: Blob;
  rotation?: number;
  onApply: (blob: Blob, recipe: CropRecipe) => Promise<void>;
  onCancel: () => void;
}) {
  const [proposal, setProposal] = useState<Proposal | null>(null),
    [quad, setQuad] = useState<Quad>(full),
    [preview, setPreview] = useState<Blob | null>(null),
    [busy, setBusy] = useState(true),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0),
    [target, setTarget] = useState<"card" | "slab">("card");
  const mounted = useRef(true),
    lock = useRef(false),
    drag = useRef<number | null>(null),
    svg = useRef<SVGSVGElement>(null);
  useEffect(() => {
    mounted.current = true;
    let active = true;
    setBusy(true);
    setError("");
    setProposal(null);
    setPreview(null);
    const form = new FormData();
    form.append("image", source, "source.jpg");
    form.append("target", target);
    api
      .request<Proposal>("/v1/capture/crop-proposals", {
        method: "POST",
        body: form,
      })
      .then((p) => {
        if (active) {
          setProposal(p);
          setQuad(p.quad ?? full);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
      mounted.current = false;
    };
  }, [api, source, retry, target]);
  async function apply(useOriginal = false, confirm = false) {
    if (!proposal || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const p = await api.post<Proposal>(
        `/v1/capture/crop-proposals/${proposal.proposal_id}/apply`,
        { quad: useOriginal ? null : quad },
      );
      const raw = await api.image(p.preview_url);
      const blob = rotation ? await normalizePhoto(raw, rotation) : raw;
      if (!mounted.current) return;
      if (confirm)
        await onApply(blob, {
          quad: useOriginal ? null : quad,
          source_hash: p.source_hash,
          rotation,
          confirmed: true,
          target,
          algorithm_version: p.algorithm_version,
        });
      else setPreview(blob);
    } catch (e) {
      if (mounted.current)
        setError(e instanceof Error ? e.message : "裁切失败");
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function move(index: number, axis: number, value: number) {
    setQuad(
      (q) =>
        q.map((p, i) =>
          i === index
            ? p.map((n, a) =>
                a === axis ? Math.max(0, Math.min(1, value)) : n,
              )
            : p,
        ) as Quad,
    );
    setPreview(null);
  }
  return (
    <section className="panel" aria-label="自动取景裁切">
      <h2>核对卡片四角</h2>
      <p className="muted">
        拖动四角或调整坐标，预览后确认。每次都从保留的源照片重新裁切。
      </p>
      <label>
        保留范围
        <select
          value={target}
          disabled={busy}
          onChange={(e) => setTarget(e.target.value as typeof target)}
        >
          <option value="card">卡面与边框</option>
          <option value="slab">评级盒与标签（手动核对）</option>
        </select>
      </label>
      {rotation !== 0 && (
        <p>结果将保留 {rotation}° 旋转；在原照片上调整四角。</p>
      )}
      {busy && <p role="status">正在处理图片…</p>}
      {error && (
        <p className="error" role="alert">
          {error}{" "}
          <button disabled={busy} onClick={() => setRetry((n) => n + 1)}>
            重新检测
          </button>
        </p>
      )}
      {proposal && (
        <>
          <p>
            {target === "slab"
              ? "请手动核对评级盒与标签，当前建议保留整张。"
              : proposal.quad
                ? "已找到卡片边缘，请检查是否完整。"
                : "未找到可靠边缘，请手动调整或保留整张。"}
          </p>
          <div
            className="crop-preview"
            style={{
              position: "relative",
              aspectRatio: `${proposal.width}/${proposal.height}`,
            }}
          >
            <LocalImage blob={source} alt="源照片" />
            <svg
              ref={svg}
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                touchAction: "none",
              }}
              aria-label="拖动卡片四角"
              onPointerMove={(e) => {
                if (drag.current === null || busy) return;
                const r = svg.current!.getBoundingClientRect();
                const i = drag.current;
                setQuad(
                  (q) =>
                    q.map((p, j) =>
                      j === i
                        ? [
                            Math.max(
                              0,
                              Math.min(1, (e.clientX - r.left) / r.width),
                            ),
                            Math.max(
                              0,
                              Math.min(1, (e.clientY - r.top) / r.height),
                            ),
                          ]
                        : p,
                    ) as Quad,
                );
                setPreview(null);
              }}
              onPointerUp={() => {
                drag.current = null;
              }}
              onPointerCancel={() => {
                drag.current = null;
              }}
            >
              <polygon
                points={quad
                  .map((p) => `${p[0] * 100},${p[1] * 100}`)
                  .join(" ")}
                fill="#00bb7722"
                stroke="#00bb77"
                strokeWidth=".6"
              />
              {quad.map((p, i) => (
                <circle
                  key={i}
                  cx={p[0] * 100}
                  cy={p[1] * 100}
                  r="2.5"
                  fill="white"
                  stroke="#087755"
                  strokeWidth=".8"
                  onPointerDown={(e) => {
                    if (busy) return;
                    drag.current = i;
                    svg.current?.setPointerCapture(e.pointerId);
                  }}
                />
              ))}
            </svg>
          </div>
          <details>
            <summary>精确调整四角</summary>
            {quad.map((p, i) => (
              <div key={i}>
                {["左上", "右上", "右下", "左下"][i]}
                {p.map((n, a) => (
                  <label key={a}>
                    {a ? "纵向" : "横向"} {Math.round(n * 100)}%
                    <input
                      aria-label={`${i + 1}角${a ? "纵" : "横"}坐标`}
                      type="range"
                      min="0"
                      max="100"
                      step="0.5"
                      value={n * 100}
                      disabled={busy}
                      onChange={(e) => move(i, a, Number(e.target.value) / 100)}
                    />
                  </label>
                ))}
              </div>
            ))}
          </details>
          {preview && <LocalImage blob={preview} alt="透视裁切预览" />}
          <div className="actions">
            <button disabled={busy} onClick={() => void apply()}>
              预览裁切
            </button>
            <button disabled={busy} onClick={() => void apply(false, true)}>
              确认裁切
            </button>
            <button disabled={busy} onClick={() => void apply(true, true)}>
              保留整张
            </button>
          </div>
        </>
      )}
      <button disabled={busy} onClick={onCancel}>
        取消裁切
      </button>
    </section>
  );
}
