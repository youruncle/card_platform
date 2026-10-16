import React, { useState } from "react";
import { LocalImage } from "./PrivateImage";
import type { Crop } from "./capture";
export function PhotoCrop({
  blob,
  onApply,
  onCancel,
  busy,
}: {
  blob: Blob;
  onApply: (crop: Crop) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [edges, setEdges] = useState({ left: 0, right: 0, top: 0, bottom: 0 });
  return (
    <section className="panel" aria-label="裁切照片">
      <h2>裁切照片</h2>
      <p className="muted">调整四边，保留完整卡面和边框。</p>
      <div className="crop-preview">
        <LocalImage blob={blob} alt="裁切预览" />
        <div
          className="crop-window"
          style={{
            left: `${edges.left}%`,
            right: `${edges.right}%`,
            top: `${edges.top}%`,
            bottom: `${edges.bottom}%`,
          }}
        />
      </div>
      {(
        [
          ["left", "左边"],
          ["right", "右边"],
          ["top", "上边"],
          ["bottom", "下边"],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label} {edges[key]}%
          <input
            type="range"
            min="0"
            max="45"
            value={edges[key]}
            disabled={busy}
            onChange={(e) =>
              setEdges({ ...edges, [key]: Number(e.target.value) })
            }
          />
        </label>
      ))}
      <div className="actions">
        <button disabled={busy} onClick={onCancel}>
          取消裁切
        </button>
        <button
          className="primary"
          disabled={busy}
          onClick={() =>
            onApply({
              x: edges.left / 100,
              y: edges.top / 100,
              width: (100 - edges.left - edges.right) / 100,
              height: (100 - edges.top - edges.bottom) / 100,
            })
          }
        >
          应用裁切
        </button>
      </div>
    </section>
  );
}
