import React, { useEffect, useState, useRef } from "react";
import { Capacitor } from "@capacitor/core";
import { CameraSource } from "@capacitor/camera";
import type { MobileApi } from "./api";
import { DraftStore, type Draft } from "./drafts";
import { normalizePhoto, takePhoto, pickPhotos } from "./capture";
import { LocalImage } from "./PrivateImage";
import { renderPhotoEdit } from "./photoEdits";
import { QuadCropEditor } from "./QuadCropEditor";
import {
  canSubmitCapture,
  batchDrafts,
  swapDraftSides,
  LEGACY_CAPABILITIES,
  type ClientCapabilities,
  type CropRecipe,
} from "../../../packages/card-core/src/capture";
import { PhotoCrop } from "./PhotoCrop";
import type { Job } from "./types";
const emptyDraft = (): Draft => ({ requestId: crypto.randomUUID() });
export function CapturePage({
  api,
  store,
  onSubmitted,
  capabilities = LEGACY_CAPABILITIES,
}: {
  capabilities?: ClientCapabilities;
  api: MobileApi;
  store: DraftStore;
  onSubmitted: (job: Job) => void;
}) {
  const mounted = useRef(true),
    locked = useRef(false);
  const [drafts, setDrafts] = useState<Draft[]>([]),
    [index, setIndex] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [batchMode, setBatchMode] = useState<"pairs" | "front" | "back">("pairs"),
    [batch, setBatch] = useState<Blob[]>([]),
    [cropSide, setCropSide] = useState<"front" | "back" | null>(null);
  const draft = drafts[index];
  useEffect(() => {
    mounted.current = true;
    let generation = 0;
    const refresh = () => {
      const ticket = ++generation;
      void store
        .loadAll()
        .then((values) => {
          if (mounted.current && ticket === generation) {
            setDrafts(values.length ? values : [emptyDraft()]);
            setIndex((i) => Math.min(i, Math.max(0, values.length - 1)));
          }
        })
        .catch(() => {
          if (mounted.current) setError("无法读取草稿，请检查设备存储");
        });
    };
    const unsubscribe = store.subscribe(refresh);
    refresh();
    return () => {
      mounted.current = false;
      generation++;
      unsubscribe();
    };
  }, [store]);
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      if (mounted.current)
        setError(e instanceof Error ? e.message : "处理失败，请重试");
    } finally {
      locked.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function save(next: Draft[]) {
    if (!mounted.current) return;
    await store.saveAll(
      next,
      drafts.filter((d) => d.front || d.back).map((d) => d.requestId),
    );
    if (mounted.current) {
      setDrafts(next);
      setNotice("草稿已保存在此设备");
    }
  }
  async function replace(
    side: "front" | "back",
    photo: () => Promise<Blob>,
    recipe?: CropRecipe,
  ) {
    if (!draft) return;
    await run(async () => {
      const blob = await photo();
      if (!mounted.current) return;
      await save(
        drafts.map((d, i) =>
          i === index
            ? {
                ...d,
                [side]: blob,
                [side === "front" ? "frontSource" : "backSource"]: recipe
                  ? (d[side === "front" ? "frontSource" : "backSource"] ??
                    d[side])
                  : blob,
                recipes: { ...d.recipes, [side]: recipe },
                requestId: crypto.randomUUID(),
              }
            : d,
        ),
      );
      setCropSide(!recipe && capabilities.crop_proposals ? side : null);
    });
  }
  async function chooseBatch(source: () => Promise<Blob[]>) {
    await run(async () => {
      const photos = await source();
      if (mounted.current) setBatch(photos);
    });
  }
  const accept = "image/jpeg,image/png,image/webp,image/heic,image/heif";
  return (
    <>
      <h1>{draft?.targetItemId ? "补充卡片照片" : "录入新卡"}</h1>
      {draft?.targetItemId && (
        <p className="notice">
          补拍会更新原卡片，不会新建卡片。身份信息会保留。
        </p>
      )}
      <p className="muted">拍照或从相册选图，核对正反面后提交识别。</p>
      <details className="panel">
        <summary>拍摄小提示</summary>
        <p>
          让卡片占满画面，保留四条边；镜头正对卡片，避开反光。先拍正面，再拍背面。
        </p>
      </details>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {Capacitor.isNativePlatform() ? (
        <button
          disabled={busy || !!cropSide || !draft}
          onClick={() => void chooseBatch(pickPhotos)}
        >
          相册批量选图
        </button>
      ) : (
        <label className="button">
          相册批量选图
          <input
            aria-label="批量选择相册照片"
            type="file"
            accept={accept}
            multiple
            disabled={busy || !!cropSide || !draft}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (files.length > 20) {
                setError("每次最多选择 20 张照片");
                return;
              }
              void chooseBatch(async () => {
                const images: Blob[] = [];
                for (const file of files)
                  images.push(await normalizePhoto(file));
                return images;
              });
            }}
          />
        </label>
      )}
      {batch.length > 0 && (
        <section className="panel" aria-label="配对照片">
          <h2>核对配对顺序</h2>
          {capabilities.single_side && (
            <label>
              照片分组
              <select
                value={batchMode}
                onChange={(e) =>
                  setBatchMode(e.target.value as typeof batchMode)
                }
              >
                <option value="pairs">按正面、背面配对</option>
                <option value="front">每张都是正面</option>
                <option value="back">每张都是背面</option>
              </select>
            </label>
          )}
          <p>
            每两张为一张卡的正面、背面。调整位置后加入草稿；单张照片可稍后补背面。
          </p>
          <div className="batch-grid">
            {batch.map((photo, i) => (
              <div key={i}>
                <LocalImage blob={photo} alt={`照片 ${i + 1}`} />
                <label>
                  照片 {i + 1} 放到
                  <select
                    aria-label={`照片 ${i + 1} 的位置`}
                    value={i}
                    disabled={busy || !!cropSide}
                    onChange={(e) => {
                      const to = Number(e.target.value),
                        next = [...batch];
                      [next[i], next[to]] = [next[to], next[i]];
                      setBatch(next);
                    }}
                  >
                    {batch.map((_, j) => (
                      <option key={j} value={j}>
                        第{" "}
                        {batchMode === "pairs" ? Math.floor(j / 2) + 1 : j + 1}{" "}
                        张卡 ·{" "}
                        {batchMode === "pairs"
                          ? j % 2
                            ? "背面"
                            : "正面"
                          : batchMode === "front"
                            ? "正面"
                            : "背面"}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ))}
          </div>
          <div className="actions">
            <button disabled={busy || !!cropSide} onClick={() => setBatch([])}>
              取消选图
            </button>
            <button
              disabled={busy || !!cropSide}
              className="primary"
              onClick={() =>
                void run(async () => {
                  const existing = drafts.filter((d) => d.front || d.back);
                  await save([...existing, ...batchDrafts(batch, batchMode)]);
                  if (mounted.current) {
                    setIndex(existing.length);
                    setBatch([]);
                    setCropSide(null);
                  }
                })
              }
            >
              加入草稿
            </button>
          </div>
        </section>
      )}
      {draft && (
        <>
          <div className="heading">
            <h2>待录入 {drafts.length} 张</h2>
            <button
              disabled={busy || !!cropSide}
              onClick={() =>
                void run(async () => {
                  await save([...drafts, emptyDraft()]);
                  if (mounted.current) {
                    setIndex(drafts.length);
                    setCropSide(null);
                  }
                })
              }
            >
              新增一张
            </button>
          </div>
          <div className="draft-tabs" aria-label="选择草稿">
            {drafts.map((d, i) => (
              <button
                disabled={busy || !!cropSide}
                aria-pressed={i === index}
                key={d.requestId}
                onClick={() => {
                  setIndex(i);
                  setCropSide(null);
                }}
              >
                第 {i + 1} 张{d.front && d.back ? " · 双面齐全" : ""}
              </button>
            ))}
          </div>
          <div className="photo-grid">
            {(["front", "back"] as const)
              .filter(
                (side) => !draft.missingSide || side === draft.missingSide,
              )
              .map((side, i) => (
                <section className="panel" key={side}>
                  <h2>{side === "front" ? "01 · 正面" : "02 · 背面"}</h2>
                  <LocalImage
                    blob={draft[side]}
                    alt={side === "front" ? "卡片正面" : "卡片背面"}
                  />
                  <div className="actions">
                    {Capacitor.isNativePlatform() ? (
                      <>
                        <button
                          disabled={busy || !!cropSide}
                          onClick={() =>
                            void replace(side, () =>
                              takePhoto(CameraSource.Camera),
                            )
                          }
                        >
                          {draft[side] ? "重拍" : "拍照"}
                        </button>
                        <button
                          disabled={busy || !!cropSide}
                          onClick={() =>
                            void replace(side, () =>
                              takePhoto(CameraSource.Photos),
                            )
                          }
                        >
                          相册
                        </button>
                      </>
                    ) : (
                      <>
                        {(["camera", "album"] as const).map((source) => (
                          <label className="button" key={source}>
                            {source === "camera"
                              ? draft[side]
                                ? "重拍"
                                : "拍照"
                              : "相册"}
                            <input
                              aria-label={`${source === "camera" ? "拍摄" : "选择"}${side === "front" ? "正面" : "背面"}照片`}
                              type="file"
                              accept={accept}
                              capture={
                                source === "camera" ? "environment" : undefined
                              }
                              disabled={busy || !!cropSide}
                              onChange={(e) => {
                                if (busy || cropSide) return;
                                const file = e.target.files?.[0];
                                e.target.value = "";
                                if (file)
                                  void replace(side, () =>
                                    normalizePhoto(file),
                                  );
                              }}
                            />
                          </label>
                        ))}
                      </>
                    )}
                    {draft[side] && (
                      <>
                        <button
                          disabled={busy || !!cropSide}
                          onClick={() =>
                            void replace(
                              side,
                              () =>
                                renderPhotoEdit(
                                  api,
                                  draft[
                                    side === "front"
                                      ? "frontSource"
                                      : "backSource"
                                  ] ?? draft[side]!,
                                  {
                                    ...draft.recipes?.[side],
                                    rotation:
                                      ((draft.recipes?.[side]?.rotation ?? 0) +
                                        90) %
                                      360,
                                  },
                                  capabilities.crop_proposals,
                                ),
                              {
                                ...draft.recipes?.[side],
                                rotation:
                                  ((draft.recipes?.[side]?.rotation ?? 0) +
                                    90) %
                                  360,
                                confirmed:
                                  draft.recipes?.[side]?.confirmed ?? false,
                              },
                            )
                          }
                        >
                          旋转
                        </button>
                        <button
                          disabled={busy || !!cropSide}
                          onClick={() => setCropSide(side)}
                        >
                          裁切{side === "front" ? "正面" : "背面"}
                        </button>
                      </>
                    )}
                  </div>
                </section>
              ))}
          </div>
          {cropSide &&
            draft[cropSide] &&
            (capabilities.crop_proposals ? (
              <QuadCropEditor
                key={`${draft.requestId}:${cropSide}`}
                api={api}
                source={
                  draft[cropSide === "front" ? "frontSource" : "backSource"] ??
                  draft[cropSide]!
                }
                rotation={draft.recipes?.[cropSide]?.rotation ?? 0}
                onCancel={() => setCropSide(null)}
                onApply={async (blob, recipe) => {
                  await replace(cropSide, async () => blob, recipe);
                }}
              />
            ) : (
              <PhotoCrop
                blob={
                  draft[cropSide === "front" ? "frontSource" : "backSource"] ??
                  draft[cropSide]!
                }
                busy={busy}
                onCancel={() => setCropSide(null)}
                onApply={(crop) =>
                  void replace(
                    cropSide,
                    () =>
                      normalizePhoto(
                        draft[
                          cropSide === "front" ? "frontSource" : "backSource"
                        ] ?? draft[cropSide]!,
                        draft.recipes?.[cropSide]?.rotation ?? 0,
                        crop,
                      ),
                    {
                      rotation: draft.recipes?.[cropSide]?.rotation ?? 0,
                      quad: [
                        [crop.x, crop.y],
                        [crop.x + crop.width, crop.y],
                        [crop.x + crop.width, crop.y + crop.height],
                        [crop.x, crop.y + crop.height],
                      ],
                      confirmed: true,
                    },
                  )
                }
              />
            ))}
          <div className="actions">
            <button
              disabled={
                busy ||
                !!cropSide ||
                !!draft.targetItemId ||
                (!draft.front && !draft.back)
              }
              onClick={() =>
                void run(() =>
                  save(
                    drafts.map((d, i) => (i === index ? swapDraftSides(d) : d)),
                  ),
                )
              }
            >
              交换正反面
            </button>
            <button
              disabled={busy || !!cropSide}
              onClick={() =>
                void run(async () => {
                  const next = drafts.filter((_, i) => i !== index);
                  await save(next.length ? next : [emptyDraft()]);
                  if (mounted.current) {
                    setIndex(0);
                    setCropSide(null);
                  }
                })
              }
            >
              移除此草稿
            </button>
          </div>
          {(!draft.front || !draft.back) && (
            <p className="muted small">
              {capabilities.single_side
                ? "单面可能缺少身份字段，确认时需要补充，入库后可补拍另一面。"
                : "当前服务器需要正反两面；可先保存单面，稍后补拍。"}
            </p>
          )}
          {capabilities.single_side &&
            !draft.targetItemId &&
            (!draft.front || !draft.back) && (
              <label>
                <input
                  type="checkbox"
                  checked={!!draft.singleSide}
                  disabled={busy || !!cropSide}
                  onChange={(e) => {
                    const checked = e.currentTarget.checked;
                    const next = drafts.map((d, i) =>
                      i === index ? { ...d, singleSide: checked } : d,
                    );
                    setDrafts(next);
                    void run(async () => {
                      try {
                        await save(next);
                      } catch (error) {
                        const persisted = await store.loadAll();
                        if (mounted.current)
                          setDrafts(
                            persisted.length ? persisted : [emptyDraft()],
                          );
                        throw error;
                      }
                    });
                  }}
                />
                先识别这一面
              </label>
            )}
          <button
            className="primary wide"
            disabled={
              busy ||
              !!cropSide ||
              !canSubmitCapture(draft, capabilities.single_side) ||
              batch.length > 0
            }
            onClick={() =>
              void run(async () => {
                if (!canSubmitCapture(draft, capabilities.single_side)) return;
                if (capabilities.crop_proposals) {
                  const pendingSide = (["front", "back"] as const).find(
                    (side) => draft[side] && !draft.recipes?.[side]?.confirmed,
                  );
                  if (pendingSide) {
                    setCropSide(pendingSide);
                    setNotice("请先确认这一面的裁切范围，或选择保留整张。");
                    return;
                  }
                }
                const job = capabilities.capture_jobs
                  ? await api.capture<Job>(draft)
                  : await api.upload<Job>(
                      "/v1/mobile/jobs",
                      draft.front!,
                      draft.back!,
                      draft.requestId,
                    );
                await store.clearIfRequestId(draft.requestId);
                const remaining = await store.loadAll();
                if (mounted.current) {
                  setIndex(0);
                  setCropSide(null);
                  setDrafts(remaining.length ? remaining : [emptyDraft()]);
                  if (remaining.length)
                    setNotice(
                      "已提交识别，可继续下一张。结果在「任务」中查看。",
                    );
                  else onSubmitted(job);
                }
              })
            }
          >
            {busy ? "正在处理…" : "提交识别"}
          </button>
        </>
      )}
      <p className="muted small">
        未上传的照片保存在当前账户草稿中。上传中断可重试；切回应用后到「任务」查看已提交的识别结果。
      </p>
    </>
  );
}
