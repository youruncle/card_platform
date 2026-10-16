import React, { useEffect, useState } from "react";
import { App as NativeApp } from "@capacitor/app";
import type { MobileApi } from "./api";
import type { Job, User } from "./types";
import { PrivateImage } from "./PrivateImage";
import { SupplementConfirmation } from "./SupplementConfirmation";
import { ConfirmationForm } from "./ConfirmationForm";
import {
  LEGACY_CAPABILITIES,
  type ClientCapabilities,
} from "../../../packages/card-core/src/capture";
import { statusName } from "./LibraryPage";
export function JobsPage({
  api,
  user,
  onOpen,
  capabilities = LEGACY_CAPABILITIES,
}: {
  capabilities?: ClientCapabilities;
  api: MobileApi;
  user: User;
  onOpen: (id: string) => void;
}) {
  const [jobs, setJobs] = useState<Job[]>([]),
    [selected, setSelected] = useState<string | null>(null),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0);
  const jobsPath = capabilities.capture_jobs
    ? "/v1/capture/jobs"
    : "/v1/mobile/jobs";
  useEffect(() => {
    let active = true,
      inflight = false;
    const refresh = async () => {
      if (inflight || document.hidden) return;
      inflight = true;
      try {
        const value = await api.get<Job[]>(jobsPath);
        if (active) {
          setJobs(value);
          setError("");
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "读取任务失败");
      } finally {
        inflight = false;
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
  }, [api, revision, jobsPath]);
  const job = jobs.find((value) => value.id === selected);
  return (
    <>
      <div className="heading">
        <h1>录卡任务</h1>
        <button onClick={() => setRevision((v) => v + 1)}>刷新</button>
      </div>
      <p className="muted">最近 100 条录卡任务 · 上传完成后会在后台处理</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {job ? (
        <>
          <button onClick={() => setSelected(null)}>‹ 返回任务</button>
          <h2>
            {job.collection_item_id
              ? "已入库"
              : job.status === "succeeded"
                ? "待你确认"
                : statusName(job.status)}
          </h2>
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
          ) : job.status === "succeeded" && job.target_item_id ? (
            <SupplementConfirmation
              key={job.id}
              api={api}
              job={job}
              onSaved={onOpen}
            />
          ) : job.status === "succeeded" && job.result ? (
            <ConfirmationForm
              key={job.id}
              owner={user.id}
              result={job.result}
              onConfirm={async (payload) => {
                const saved = await api.post<{ id: string }>(
                  `${jobsPath}/${job.id}/confirm`,
                  payload,
                );
                onOpen(saved.id);
              }}
            />
          ) : job.status === "failed" ? (
            <section className="panel">
              <p role="alert">{job.error_message ?? "识别失败，请稍后重试"}</p>
              <button
                disabled={job.attempt_count >= 3}
                onClick={async () => {
                  try {
                    await api.post(
                      `${capabilities.capture_jobs ? "/v1/capture/jobs" : "/v1/scanner/jobs"}/${job.id}/retry`,
                    );
                    setRevision((v) => v + 1);
                  } catch (e) {
                    setError(e instanceof Error ? e.message : "重试失败");
                  }
                }}
              >
                重新识别
              </button>
            </section>
          ) : (
            <p className="notice">任务正在处理中，可稍后回来查看。</p>
          )}
        </>
      ) : jobs.length ? (
        jobs.map((value) => (
          <button
            className="job-row"
            key={value.id}
            onClick={() => setSelected(value.id)}
          >
            <span>
              <strong>{value.result?.player_name ?? "新的录卡任务"}</strong>
              <small>
                {new Date(value.created_at).toLocaleString("zh-CN")}
              </small>
            </span>
            <span className="badge">
              {value.collection_item_id
                ? "已入库"
                : value.status === "succeeded"
                  ? "待确认"
                  : statusName(value.status)}
            </span>
          </button>
        ))
      ) : (
        !error && (
          <section className="empty">
            <h2>还没有录卡任务</h2>
            <p>拍摄正反面后，这里会显示识别进度。</p>
          </section>
        )
      )}
    </>
  );
}
