import React, { useEffect, useRef, useState } from "react";
import { App as NativeApp } from "@capacitor/app";
import { Capacitor } from "@capacitor/core";
import { CameraSource } from "@capacitor/camera";
import { ApiError, type MobileApi } from "./api";
import type { Job, Recognition, User } from "./types";
import { PrivateImage, LocalImage } from "./PrivateImage";
import { normalizePhoto, takePhoto } from "./capture";
import { SupplementConfirmation } from "./SupplementConfirmation";
import { ConfirmationForm } from "./ConfirmationForm";
import {
  LEGACY_CAPABILITIES,
  type ClientCapabilities,
} from "../../../packages/card-core/src/capture";
import {
  captureTaskLabels,
  type CaptureTask,
  type CaptureTaskFilter,
  type CaptureTaskPage,
  type CaptureTaskState,
} from "../../../packages/card-core/src/capture-tasks";
import { pairReviewState } from "../../../packages/card-core/src/recognition";

const stateOf = (job: Job | CaptureTask): CaptureTaskState =>
  "display_status" in job
    ? job.display_status
    : job.collection_item_id
      ? "imported"
      : job.status === "succeeded"
        ? "needs_review"
        : job.status === "failed"
          ? "failed"
          : job.status === "cancelled"
            ? "cancelled"
            : "processing";
const taskOf = (job: Job | CaptureTask): CaptureTask | null =>
  "display_status" in job ? job : null;
const messageOf = (error: unknown, fallback: string) =>
  error instanceof Error ? error.message : fallback;
const formatTime = (value: string) => new Date(value).toLocaleString("zh-CN");

function ReplacementPhotos({
  api,
  job,
  onReplaced,
}: {
  api: MobileApi;
  job: CaptureTask;
  onReplaced: (task: CaptureTask) => void;
}) {
  const [photos, setPhotos] = useState<Partial<Record<"front" | "back", Blob>>>(
    {},
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const locked = useRef(false),
    requestId = useRef(crypto.randomUUID());
  const active = useRef(true);
  useEffect(
    () => () => {
      active.current = false;
    },
    [],
  );
  async function choose(side: "front" | "back", read: () => Promise<Blob>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      const photo = await read();
      if (active.current) {
        requestId.current = crypto.randomUUID();
        setPhotos((previous) => ({ ...previous, [side]: photo }));
      }
    } catch (e) {
      if (active.current) setError(messageOf(e, "读取照片失败"));
    } finally {
      locked.current = false;
      if (active.current) setBusy(false);
    }
  }
  return (
    <section className="panel task-replacement" aria-label="更换任务照片">
      <h2>更换照片</h2>
      <p className="muted">
        重新核对正反面。更换后创建关联的新任务，保留原任务与历史。
      </p>
      <div className="photo-grid">
        {(["front", "back"] as const).map((side) => {
          const label = side === "front" ? "正面" : "背面";
          return (
            <div key={side}>
              <LocalImage blob={photos[side]} alt={`替换${label}预览`} />
              {Capacitor.isNativePlatform() ? (
                <div className="actions">
                  <button
                    disabled={busy}
                    onClick={() =>
                      void choose(side, () => takePhoto(CameraSource.Camera))
                    }
                  >
                    重拍{label}
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void choose(side, () => takePhoto(CameraSource.Photos))
                    }
                  >
                    相册选择{label}
                  </button>
                </div>
              ) : (
                <label className="button">
                  更换{label}照片
                  <input
                    type="file"
                    accept="image/jpeg,image/png,image/webp,image/heic,image/heif"
                    aria-label={`更换${label}照片`}
                    disabled={busy}
                    onChange={(event) => {
                      const file = event.currentTarget.files?.[0];
                      event.currentTarget.value = "";
                      if (file) void choose(side, () => normalizePhoto(file));
                    }}
                  />
                </label>
              )}
            </div>
          );
        })}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button
        disabled={busy || !photos.front || !photos.back}
        onClick={async () => {
          if (locked.current || !photos.front || !photos.back) return;
          locked.current = true;
          setBusy(true);
          setError("");
          try {
            const replacement = await api.upload<CaptureTask>(
              `/v1/capture/tasks/${job.id}/images`,
              photos.front,
              photos.back,
              requestId.current,
            );
            if (active.current) onReplaced(replacement);
          } catch (e) {
            if (active.current) setError(messageOf(e, "更换照片失败"));
          } finally {
            locked.current = false;
            if (active.current) setBusy(false);
          }
        }}
      >
        {busy ? "正在处理…" : "更换照片并重新识别"}
      </button>
    </section>
  );
}

export function JobsPage({
  api,
  user,
  onOpen,
  onCapture,
  capabilities = LEGACY_CAPABILITIES,
}: {
  capabilities?: ClientCapabilities;
  api: MobileApi;
  user: User;
  onOpen: (id: string) => void;
  onCapture?: () => void;
}) {
  const [jobs, setJobs] = useState<Array<Job | CaptureTask>>([]);
  const [selected, setSelected] = useState<string | null>(null),
    [detail, setDetail] = useState<CaptureTask | null>(null);
  const [error, setError] = useState(""),
    [detailError, setDetailError] = useState(""),
    [actionError, setActionError] = useState("");
  const [revision, setRevision] = useState(0),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const [filter, setFilter] = useState<CaptureTaskFilter>("attention"),
    [page, setPage] = useState(1);
  const [taskPage, setTaskPage] = useState<CaptureTaskPage | null>(null),
    [loading, setLoading] = useState(true);
  const [legacy, setLegacy] = useState(!capabilities.capture_jobs);
  const locked = useRef(false);
  const jobsPath = capabilities.capture_jobs
    ? "/v1/capture/jobs"
    : "/v1/mobile/jobs";
  const unified = capabilities.capture_jobs && !legacy;
  const listPath = unified
    ? `/v1/capture/tasks?state=${filter}&page=${page}&page_size=20`
    : jobsPath;
  useEffect(() => {
    let active = true,
      inflight = false;
    setLoading(true);
    setError("");
    const refresh = async () => {
      if (inflight || document.hidden) return;
      inflight = true;
      try {
        const value = await api.get<CaptureTaskPage | Job[]>(listPath);
        if (!active) return;
        if (Array.isArray(value)) {
          setJobs(value);
          setTaskPage(null);
          if (unified) setLegacy(true);
        } else {
          setTaskPage(value);
          setJobs(value.items);
        }
        setError("");
      } catch (e) {
        if (!active) return;
        if (unified && e instanceof ApiError && [404, 405].includes(e.status))
          setLegacy(true);
        else setError(messageOf(e, "读取任务失败"));
      } finally {
        inflight = false;
        if (active) setLoading(false);
      }
    };
    void refresh();
    const timer = setInterval(refresh, 5000);
    const visibility = () => {
      void refresh();
    };
    document.addEventListener("visibilitychange", visibility);
    const listener = NativeApp.addListener("appStateChange", (state) => {
      if (state.isActive) void refresh();
    });
    return () => {
      active = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
      void listener.then((handle) => handle.remove());
    };
  }, [api, revision, listPath, unified]);

  useEffect(() => {
    if (!selected || !unified) return;
    let active = true,
      inflight = false;
    setDetailError("");
    async function refreshDetail() {
      if (inflight || document.hidden) return;
      inflight = true;
      try {
        const value = await api.get<CaptureTask>(
          `/v1/capture/tasks/${selected}`,
        );
        if (active) {
          setDetail(value);
          setDetailError("");
        }
      } catch (e) {
        if (active) setDetailError(messageOf(e, "读取任务详情失败"));
      } finally {
        inflight = false;
      }
    }
    void refreshDetail();
    const timer = setInterval(refreshDetail, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [api, selected, unified, revision]);

  const job = selected
    ? unified
      ? detail?.id === selected
        ? detail
        : null
      : jobs.find((value) => value.id === selected)
    : null;
  const task = job ? taskOf(job) : null;
  const state = job ? stateOf(job) : null;
  // Failed tasks can be completed manually; the review is bound to the current photos.
  let result: Recognition | null = job?.result ?? null;
  if (
    task &&
    (!result?.pair_validation?.image_content_hashes?.front ||
      !result?.pair_validation?.image_content_hashes?.back)
  ) {
    const validation = result?.pair_validation;
    result = {
      ...(result ?? {}),
      confidence: result?.confidence ?? 0,
      pair_validation: {
        status: validation?.status === "conflict" ? "conflict" : "unknown",
        reason_codes: validation?.reason_codes ?? ["identity_unverified"],
        evidence: validation?.evidence ?? [],
        image_content_hashes: task.image_content_hashes,
        version: validation?.version ?? 1,
      },
    };
  }
  const attentionCount = taskPage
    ? taskPage.counts.failed + taskPage.counts.needs_review
    : 0;
  function openTask(id: string, next: CaptureTask | null = null) {
    setDetail(next);
    setSelected(id);
    setDetailError("");
    setActionError("");
    setNotice("");
  }
  return (
    <>
      <div className="heading">
        <h1>
          录卡任务
          {attentionCount > 0 && (
            <span className="badge task-attention">
              {attentionCount} 待处理
            </span>
          )}
        </h1>
        <button onClick={() => setRevision((v) => v + 1)}>刷新</button>
      </div>
      <p className="muted">
        上传完成后会在后台处理，自动扫描任务也会显示在这里。
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {actionError && (
        <p className="error" role="alert">
          {actionError}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {selected ? (
        <>
          <button
            onClick={() => {
              setSelected(null);
              setDetail(null);
              setActionError("");
              setNotice("");
            }}
          >
            ‹ 返回任务
          </button>
          {detailError && (
            <p className="error" role="alert">
              {detailError}
            </p>
          )}
          {job && state ? (
            <>
              <h2>{captureTaskLabels[state]}</h2>
              <p className="muted">
                {formatTime(job.created_at)} · 已尝试 {job.attempt_count ?? 0}{" "}
                次
              </p>
              {(task?.attention_reasons.length
                ? task.attention_reasons
                : job.error_message
                  ? [job.error_message]
                  : []
              ).map((reason, index) => (
                <p className="notice" key={index}>
                  {reason}
                </p>
              ))}
              {(!job.front_image_url || !job.back_image_url) && (
                <p className="notice">
                  本次只上传了{job.front_image_url ? "正面" : "背面"}
                  ，未提供的字段需要确认。
                </p>
              )}
              <div className="photo-grid">
                <PrivateImage
                  api={api}
                  path={job.images?.front?.preview_url ?? job.front_image_url}
                  originalPath={job.front_image_url}
                  alt="任务正面"
                />
                <PrivateImage
                  api={api}
                  path={job.images?.back?.preview_url ?? job.back_image_url}
                  originalPath={job.back_image_url}
                  alt="任务背面"
                />
              </div>
              {job.collection_item_id ? (
                <button
                  className="primary"
                  onClick={() => onOpen(job.collection_item_id!)}
                >
                  查看卡片
                </button>
              ) : (
                <>
                  {(state === "failed" || state === "needs_review") && (
                    <>
                      {(task?.can_retry ??
                        (job.status === "failed" && job.attempt_count < 3)) && (
                        <button
                          disabled={busy}
                          onClick={async () => {
                            if (locked.current) return;
                            locked.current = true;
                            setBusy(true);
                            setActionError("");
                            try {
                              const next = await api.post<CaptureTask>(
                                `${unified ? "/v1/capture/tasks" : capabilities.capture_jobs ? "/v1/capture/jobs" : "/v1/scanner/jobs"}/${job.id}/retry`,
                              );
                              if (unified && next?.display_status)
                                setDetail(next);
                              setRevision((v) => v + 1);
                            } catch (e) {
                              setActionError(messageOf(e, "重试失败"));
                            } finally {
                              locked.current = false;
                              setBusy(false);
                            }
                          }}
                        >
                          {busy ? "正在重试…" : "重新识别"}
                        </button>
                      )}
                      {job.target_item_id &&
                      (unified || job.status === "succeeded") ? (
                        <SupplementConfirmation
                          key={`supplement-${job.id}`}
                          api={api}
                          job={{ ...job, result }}
                          onSaved={onOpen}
                          confirmPath={
                            unified
                              ? `/v1/capture/tasks/${job.id}/confirm`
                              : undefined
                          }
                        />
                      ) : (
                        result &&
                        (unified || job.status === "succeeded") && (
                          <ConfirmationForm
                            key={`confirm-${job.id}`}
                            owner={user.id}
                            result={result}
                            paired={
                              !!job.front_image_url && !!job.back_image_url
                            }
                            onConfirm={async (payload) => {
                              const saved = await api.post<{ id: string }>(
                                `${unified ? "/v1/capture/tasks" : jobsPath}/${job.id}/confirm`,
                                payload,
                              );
                              setRevision((v) => v + 1);
                              onOpen(saved.id);
                            }}
                          />
                        )
                      )}
                      {task ? (
                        <ReplacementPhotos
                          key={`replace-${job.id}`}
                          api={api}
                          job={task}
                          onReplaced={(replacement) => {
                            openTask(replacement.id, replacement);
                            setRevision((v) => v + 1);
                            setNotice("照片已更换，新任务保留与原任务的关联。");
                          }}
                        />
                      ) : (
                        result &&
                        pairReviewState(
                          result,
                          !!job.front_image_url && !!job.back_image_url,
                        ).blocked &&
                        onCapture && (
                          <button onClick={onCapture}>
                            重新拍摄 / 选择照片
                          </button>
                        )
                      )}
                    </>
                  )}
                  {state === "processing" && (
                    <p className="notice">任务正在处理中，可稍后回来查看。</p>
                  )}
                  {state === "cancelled" && (
                    <p className="notice">
                      此任务已取消，原有照片和处理历史仍保留。
                    </p>
                  )}
                </>
              )}
              {task?.replacement_job_id && (
                <button onClick={() => openTask(task.replacement_job_id!)}>
                  查看更换后的任务
                </button>
              )}
              {task && (
                <section className="panel" aria-label="任务历史">
                  <h2>处理历史</h2>
                  {task.history.length ? (
                    <ol className="task-history">
                      {task.history.map((event) => (
                        <li key={event.id}>
                          <strong>
                            {captureTaskLabels[event.display_status]} · 第{" "}
                            {event.attempt_count} 次尝试
                          </strong>
                          <p>{event.message}</p>
                          <small className="muted">
                            {formatTime(event.occurred_at)} · {event.source}
                          </small>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="muted">
                      暂无已记录的历史；旧任务只展示实际保留的记录。
                    </p>
                  )}
                </section>
              )}
            </>
          ) : (
            !detailError && <p role="status">正在读取任务详情…</p>
          )}
        </>
      ) : (
        <>
          {unified && (
            <>
              {taskPage && (
                <div className="task-counts" aria-label="任务计数">
                  {(
                    [
                      "processing",
                      "needs_review",
                      "failed",
                      "imported",
                      "cancelled",
                    ] as const
                  ).map((key) => (
                    <span key={key}>
                      {captureTaskLabels[key]} {taskPage.counts[key]}
                    </span>
                  ))}
                </div>
              )}
              <label className="task-filter">
                任务状态
                <select
                  value={filter}
                  onChange={(event) => {
                    setFilter(event.currentTarget.value as CaptureTaskFilter);
                    setPage(1);
                  }}
                >
                  <option value="attention">待处理</option>
                  <option value="all">全部历史</option>
                  {Object.entries(captureTaskLabels).map(([key, label]) => (
                    <option value={key} key={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          {loading ? (
            <p role="status">正在读取任务…</p>
          ) : jobs.length ? (
            jobs.map((value) => (
              <article className="task-list-item" key={value.id}>
                {unified && (
                  <div className="task-thumbnail">
                    <PrivateImage
                      api={api}
                      path={
                        value.images?.front?.thumb_url ??
                        value.images?.front?.preview_url ??
                        value.front_image_url ??
                        value.back_image_url
                      }
                      alt="任务照片预览"
                    />
                  </div>
                )}
                <button className="job-row" onClick={() => openTask(value.id)}>
                  <span>
                    <strong>
                      {value.result?.player_name ?? "新的录卡任务"}
                    </strong>
                    <small>{formatTime(value.created_at)}</small>
                    {(taskOf(value)?.attention_reasons.length
                      ? taskOf(value)!.attention_reasons
                      : value.error_message
                        ? [value.error_message]
                        : []
                    ).map((reason, index) => (
                      <small className="task-reason" key={index}>
                        {reason}
                      </small>
                    ))}
                  </span>
                  <span className="badge">
                    {captureTaskLabels[stateOf(value)]}
                  </span>
                </button>
              </article>
            ))
          ) : (
            !error && (
              <section className="empty">
                <h2>{unified ? "当前筛选下没有任务" : "还没有录卡任务"}</h2>
                <p>
                  拍摄正反面后，这里会显示识别进度。可选择全部历史查看过去的任务。
                </p>
              </section>
            )
          )}
          {unified && taskPage && (
            <div className="actions task-pagination" aria-label="任务分页">
              <button
                disabled={loading || page <= 1}
                onClick={() => setPage((v) => v - 1)}
              >
                上一页
              </button>
              <span>
                第 {page} / {Math.max(1, taskPage.total_pages)} 页 · 共{" "}
                {taskPage.total} 条
              </span>
              <button
                disabled={loading || page >= taskPage.total_pages}
                onClick={() => setPage((v) => v + 1)}
              >
                下一页
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}
