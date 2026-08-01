"use client";

import { ArrowLeft, CheckCircle2, Play, RefreshCw, Target } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
  createRecognitionBenchmark,
  createRecognitionKnowledgeProposals,
  acceptRecognitionAssistedLabel,
  getRecognitionBenchmarks,
  getRecognitionBenchmarkSamples,
  getRecognitionFeedbackReport,
  getRecognitionPredictionJob,
  getRecognitionSamplingAudit,
  getRecognitionVersionMetrics,
  labelRecognitionBenchmarkSample,
  predictRecognitionBenchmark,
  type RecognitionBenchmark,
  type RecognitionFeedbackReport,
  type RecognitionBenchmarkSample,
  type RecognitionSamplingAudit,
  type RecognitionVersionMetrics,
} from "@/lib/api";

const correctionFields = [
  ["player_name", "球员"],
  ["season", "赛季，如 2023-24"],
  ["brand_name", "厂商"],
  ["product_name", "产品"],
  ["insert_name", "特卡"],
  ["parallel_name", "平行"],
  ["card_number", "卡号"],
  ["is_numbered", "是否带编：true / false"],
  ["serial", "限编，如 3/10"],
  ["is_rookie", "是否RC：true / false"],
  ["rookie_evidence", "RC依据，如卡面RC标志"],
  ["is_autograph", "是否签字：true / false"],
  ["autograph_type", "签字类型：on_card / sticker / cut / unknown / none"],
  ["has_inscription", "是否带签字铭文：true / false"],
  ["inscription_text", "签字铭文原文，如 14X NBA ALL-STAR"],
  ["is_memorabilia", "是否带物料：true / false"],
  ["memorabilia_types", "物料类型，逗号分隔：jersey, patch, ball…"],
  ["memorabilia_usage", "物料属性：game_used / player_worn / event_worn / not_from_specific_game_or_event / manufactured / unknown / none"],
  ["memorabilia_player", "物料所属球员"],
  ["is_patch", "物料是否为 Patch：true / false"],
  ["grade_company", "评级公司，如 PSA"],
  ["grade_value", "评级分数，如 10"],
] as const;

const labels = [
  ["top1_correct", "Top-1 正确"],
  ["top2_correct", "Top-2 正确"],
  ["top3_correct", "Top-3 正确"],
  ["none_correct", "现有候选都不对"],
  ["insufficient_image", "图片不足"],
  ["source_error", "来源答案可疑"],
] as const;

const booleanCorrectionFields = new Set([
  "is_numbered",
  "is_rookie",
  "is_autograph",
  "has_inscription",
  "is_memorabilia",
  "is_patch",
]);

const enumCorrectionOptions: Record<string, Array<[string, string]>> = {
  autograph_type: [
    ["", "签字类型：未填写"],
    ["on_card", "卡签"],
    ["sticker", "贴签"],
    ["cut", "剪签"],
    ["unknown", "有签字，类型未知"],
    ["none", "无签字"],
  ],
  memorabilia_usage: [
    ["", "物料属性：未填写"],
    ["game_used", "比赛使用"],
    ["player_worn", "球员亲穿"],
    ["event_worn", "活动穿着"],
    ["not_from_specific_game_or_event", "非特定比赛/活动"],
    ["manufactured", "人造物料"],
    ["unknown", "有物料，来源未知"],
    ["none", "无物料"],
  ],
};

function percent(value: number | null | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function identityText(identity?: Record<string, unknown>) {
  if (!identity) return "";
  return [
    identity.player_name,
    identity.season_start && identity.season_end
      ? `${identity.season_start}-${String(identity.season_end).slice(-2)}`
      : identity.season_start,
    identity.brand_name,
    identity.product_name,
    identity.insert_name,
    identity.parallel_name,
    identity.card_number ? `#${identity.card_number}` : null,
    identity.serial_total ? `/${identity.serial_total}` : null,
    identity.grade_company && identity.grade_value
      ? `${identity.grade_company} ${identity.grade_value}`
      : identity.grade_company,
  ].filter(Boolean).join(" · ");
}

function attributeText(identity?: Record<string, unknown>) {
  if (!identity) return [];
  const yesNoUnknown = (value: unknown) =>
    value === true ? "是" : value === false ? "否" : "未知";
  const autographLabels: Record<string, string> = {
    on_card: "卡签",
    sticker: "贴签",
    cut: "剪签",
    unknown: "类型未知",
    none: "无",
  };
  const usageLabels: Record<string, string> = {
    game_used: "比赛使用",
    player_worn: "球员亲穿",
    event_worn: "活动穿着",
    not_from_specific_game_or_event: "非特定比赛/活动",
    manufactured: "人造物料",
    unknown: "来源未知",
    none: "无",
  };
  return [
    ["带编", yesNoUnknown(identity.is_numbered)],
    ["RC", yesNoUnknown(identity.is_rookie)],
    ["签字", `${yesNoUnknown(identity.is_autograph)} · ${
      autographLabels[String(identity.autograph_type)] ?? "类型未知"
    }`],
    ["铭文", `${yesNoUnknown(identity.has_inscription)}${
      identity.inscription_text ? ` · ${identity.inscription_text}` : ""
    }`],
    ["物料", `${yesNoUnknown(identity.is_memorabilia)} · ${
      Array.isArray(identity.memorabilia_types) && identity.memorabilia_types.length
        ? identity.memorabilia_types.join(", ")
        : "类型未知"
    } · ${usageLabels[String(identity.memorabilia_usage)] ?? "来源未知"}`],
  ];
}

function editableCorrection(identity?: Record<string, unknown> | null) {
  if (!identity) return {};
  const result: Record<string, string> = {};
  for (const [key] of correctionFields) {
    let value: unknown = identity[key];
    if (key === "season") {
      const start = identity.season_start;
      const end = identity.season_end;
      value = start
        ? `${start}${end ? `-${String(end).slice(-2)}` : ""}`
        : null;
    } else if (key === "serial") {
      value = identity.serial_number && identity.serial_total
        ? `${identity.serial_number}/${identity.serial_total}`
        : null;
    }
    if (Array.isArray(value)) value = value.join(", ");
    if (value !== null && value !== undefined && value !== "") {
      result[key] = String(value);
    }
  }
  return result;
}

export default function RecognitionBenchmarksPage() {
  const [batches, setBatches] = useState<RecognitionBenchmark[]>([]);
  const [batchId, setBatchId] = useState("");
  const [samples, setSamples] = useState<RecognitionBenchmarkSample[]>([]);
  const [feedback, setFeedback] = useState<RecognitionFeedbackReport | null>(null);
  const [versionMetrics, setVersionMetrics] = useState<RecognitionVersionMetrics | null>(null);
  const [samplingAudit, setSamplingAudit] = useState<RecognitionSamplingAudit | null>(null);
  const [reviewSampleId, setReviewSampleId] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [correction, setCorrection] = useState<Record<string, string>>({});
  const [labelNotes, setLabelNotes] = useState("");

  const load = useCallback(async (preferredId?: string) => {
    setLoading(true);
    setLoadError("");
    try {
      const nextBatches = await getRecognitionBenchmarks();
      setBatches(nextBatches);
      const nextId =
        preferredId ||
        batchId ||
        nextBatches.find((batch) => batch.status === "labeling")?.id ||
        nextBatches[0]?.id ||
        "";
      setBatchId(nextId);
      if (nextId) {
        const [nextSamples, nextFeedback, nextVersionMetrics, nextSamplingAudit] = await Promise.all([
          getRecognitionBenchmarkSamples(nextId),
          getRecognitionFeedbackReport(nextId).catch(() => null),
          getRecognitionVersionMetrics(nextId).catch(() => null),
          getRecognitionSamplingAudit(nextId).catch(() => null),
        ]);
        setSamples(nextSamples);
        setFeedback(nextFeedback);
        setVersionMetrics(nextVersionMetrics);
        setSamplingAudit(nextSamplingAudit);
      } else {
        setSamples([]);
        setFeedback(null);
        setVersionMetrics(null);
        setSamplingAudit(null);
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : "未知错误";
      setLoadError(`盲测数据加载失败：${detail}`);
      throw error;
    } finally {
      setLoading(false);
    }
  }, [batchId]);

  useEffect(() => {
    void load().catch((error) => setMessage(error.message));
  }, [load]);

  useEffect(() => {
    if (!batchId || samples.length === 0) return;
    const finishedCount = samples.filter(
      (sample) => sample.prediction || sample.prediction_error,
    ).length;
    if (finishedCount >= samples.length) return;
    const timer = window.setInterval(() => {
      void Promise.all([
        getRecognitionBenchmarks(),
        getRecognitionBenchmarkSamples(batchId),
        getRecognitionFeedbackReport(batchId).catch(() => null),
        getRecognitionVersionMetrics(batchId).catch(() => null),
      ])
        .then(([nextBatches, nextSamples, nextFeedback, nextVersionMetrics]) => {
          setBatches(nextBatches);
          setSamples(nextSamples);
          setFeedback(nextFeedback);
          setVersionMetrics(nextVersionMetrics);
        })
        .catch((error) => {
          setMessage(error instanceof Error ? error.message : "后台刷新失败");
        });
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [batchId, samples]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await load();
      setMessage(success);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function createBatch() {
    setBusy(true);
    try {
      const created = await createRecognitionBenchmark({
        name: `卡淘篮球单卡盲测 ${new Date().toLocaleDateString("zh-CN")}`,
        sample_size: 50,
        page_count: 8,
        seed: 20260729,
        max_per_seller: 4,
      });
      await load(created.id);
      setMessage("已采集首批盲测样本。商品标题不会进入预测模型。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "采集失败");
    } finally {
      setBusy(false);
    }
  }

  async function runPrediction(
    limit: number,
    options: { retryErrors?: boolean; replayAll?: boolean } = {},
  ) {
    setBusy(true);
    setMessage(
      options.replayAll
        ? "全量版本回放任务已提交，旧预测会保留在历史中。"
        : "预测任务已提交，可安全等待或刷新页面。",
    );
    try {
      const job = await predictRecognitionBenchmark(batchId, limit, options);
      for (;;) {
        const currentJob = await getRecognitionPredictionJob(batchId, job.id);
        setMessage(
          currentJob.status === "running"
            ? "正在识别图片并核验图鉴…"
            : `预测任务状态：${currentJob.status}`,
        );
        if (["succeeded", "partial", "failed"].includes(currentJob.status)) {
          if (currentJob.status === "failed") {
            throw new Error(currentJob.error_message || "预测任务失败");
          }
          await load();
          setMessage(
            `预测完成：成功 ${currentJob.predicted_count}，失败 ${currentJob.failed_count}`,
          );
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "预测失败");
    } finally {
      setBusy(false);
    }
  }

  const selected = batches.find((batch) => batch.id === batchId);
  const automaticCurrent =
    samples.find(
      (sample) => sample.label_status !== "labeled" && sample.prediction,
    ) ??
    samples.find((sample) => sample.label_status !== "labeled") ??
    samples[0];
  const current =
    samples.find((sample) => sample.id === reviewSampleId) ??
    automaticCurrent;
  const predictedCount = samples.filter((sample) => sample.prediction).length;
  const acceptanceReady =
    (selected?.metrics?.scored_count ?? 0) >= 50 &&
    selected?.metrics?.scored_count === selected?.sample_count;

  useEffect(() => {
    setCorrection(editableCorrection(current?.corrected_identity));
    setLabelNotes(current?.label_notes ?? "");
  }, [current?.id]);

  const structuredCorrection = useCallback(() => {
    const result: Record<string, unknown> = {};
    for (const [key] of correctionFields) {
      const value = correction[key]?.trim();
      if (!value) continue;
      if (key === "season") {
        const match = value.match(/^(\d{4})(?:\s*-\s*(\d{2}|\d{4}))?$/);
        if (match) {
          result.season_start = Number(match[1]);
          result.season_end = match[2]
            ? Number(match[2].length === 2 ? `${match[1].slice(0, 2)}${match[2]}` : match[2])
            : null;
        }
      } else if (key === "serial") {
        const match = value.match(/^0*(\d+)\s*\/\s*0*(\d+)$/);
        if (match) {
          result.serial_number = Number(match[1]);
          result.serial_total = Number(match[2]);
        }
      } else if (key === "grade_value") {
        const grade = Number(value);
        if (!Number.isNaN(grade)) result.grade_value = grade;
      } else if (
        [
          "is_numbered",
          "is_rookie",
          "is_autograph",
          "has_inscription",
          "is_memorabilia",
          "is_patch",
        ].includes(key)
      ) {
        if (value === "true" || value === "false") result[key] = value === "true";
      } else if (key === "memorabilia_types") {
        result[key] = value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
      } else {
        result[key] = value;
      }
    }
    return Object.keys(result).length ? result : undefined;
  }, [correction]);

  const submitLabel = useCallback(async (value: string, label: string) => {
    if (!current || busy) return;
    setBusy(true);
    setMessage("");
    try {
      const correctionPayload = structuredCorrection();
      const response = await labelRecognitionBenchmarkSample(
        batchId,
        current.id,
        value,
        correctionPayload,
        labelNotes,
      );
      const rank = value === "top1_correct"
        ? 1
        : value === "top2_correct"
          ? 2
          : value === "top3_correct"
            ? 3
            : null;
      const selectedIdentity =
        correctionPayload ??
        (rank ? current.prediction?.candidates[rank - 1]?.identity : undefined);
      const wasLabeled = current.label_status === "labeled";
      setSamples((previous) => previous.map((sample) => (
        sample.id === current.id
          ? {
              ...sample,
              label_status: "labeled",
              label_result: value,
              corrected_identity: selectedIdentity ?? null,
              label_notes: labelNotes || null,
            }
          : sample
      )));
      setBatches((previous) => previous.map((batch) => (
        batch.id === batchId
          ? {
              ...batch,
              metrics: response.metrics,
              labeled_count: batch.labeled_count + (wasLabeled ? 0 : 1),
            }
          : batch
      )));
      setReviewSampleId("");
      setMessage(`已标记：${label}，已自动进入下一张`);
      void Promise.all([
        getRecognitionFeedbackReport(batchId).catch(() => null),
        getRecognitionVersionMetrics(batchId).catch(() => null),
      ]).then(([nextFeedback, nextVersionMetrics]) => {
        setFeedback(nextFeedback);
        setVersionMetrics(nextVersionMetrics);
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "标注失败");
    } finally {
      setBusy(false);
    }
  }, [batchId, busy, current, labelNotes, structuredCorrection]);

  const acceptAssisted = useCallback(async () => {
    if (!current || busy || !current.assisted_label_result) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await acceptRecognitionAssistedLabel(batchId, current.id);
      const wasLabeled = current.label_status === "labeled";
      setSamples((previous) => previous.map((sample) => (
        sample.id === current.id
          ? {
              ...sample,
              label_status: "labeled",
              label_result: current.assisted_label_result,
              corrected_identity: current.assisted_identity,
              label_notes: "人工确认 Codex 辅助标注",
              assisted_review_status: "accepted",
            }
          : sample
      )));
      setBatches((previous) => previous.map((batch) => (
        batch.id === batchId
          ? {
              ...batch,
              metrics: response.metrics,
              labeled_count: batch.labeled_count + (wasLabeled ? 0 : 1),
            }
          : batch
      )));
      setReviewSampleId("");
      setMessage("已确认 Codex 建议，已自动进入下一张");
      void Promise.all([
        getRecognitionFeedbackReport(batchId).catch(() => null),
        getRecognitionVersionMetrics(batchId).catch(() => null),
      ]).then(([nextFeedback, nextVersionMetrics]) => {
        setFeedback(nextFeedback);
        setVersionMetrics(nextVersionMetrics);
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "辅助标注确认失败");
    } finally {
      setBusy(false);
    }
  }, [batchId, busy, current]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        target?.matches("input, textarea, select, button")
      ) {
        return;
      }
      const shortcuts: Record<string, readonly [string, string, number?]> = {
        "1": ["top1_correct", "Top-1 正确", 1],
        "2": ["top2_correct", "Top-2 正确", 2],
        "3": ["top3_correct", "Top-3 正确", 3],
        "0": ["none_correct", "现有候选都不对"],
        q: ["insufficient_image", "图片不足"],
        e: ["source_error", "来源答案可疑"],
      };
      const shortcut = shortcuts[event.key.toLowerCase()];
      if (!shortcut) return;
      const [value, label, requiredRank] = shortcut;
      if (
        requiredRank &&
        (current?.prediction?.candidates.length ?? 0) < requiredRank
      ) {
        return;
      }
      event.preventDefault();
      void submitLabel(value, label);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [current?.prediction?.candidates.length, submitLabel]);

  return (
    <main className="benchmark-shell">
      <header className="benchmark-header">
        <div>
          <a href="/" className="knowledge-back"><ArrowLeft size={16} /> 返回资产台</a>
          <p className="eyebrow">RECOGNITION BENCHMARK</p>
          <h1>真实卡片盲测</h1>
          <p>模型只看卡片图片；预测完成后才显示卡淘来源信息供人工打标。</p>
        </div>
        <div className="benchmark-target">
          <Target size={20} />
          <div><small>验收目标</small><strong>Top-1 ≥ 90% · Top-3 ≥ 97%</strong></div>
        </div>
      </header>

      {message && <div className="knowledge-message">{message}</div>}

      <section className="benchmark-toolbar panel">
        <select
          value={batchId}
          onChange={(event) => {
            setBatchId(event.target.value);
            void load(event.target.value);
          }}
        >
          <option value="">暂无批次</option>
          {batches.map((batch) => (
            <option key={batch.id} value={batch.id}>{batch.name}</option>
          ))}
        </select>
        <button disabled={busy} onClick={() => void createBatch()}>
          <RefreshCw size={16} /> 采集50张
        </button>
        <button
          disabled={busy || !batchId}
          onClick={() => void runPrediction(10)}
        >
          <Play size={16} /> 预测下一组
        </button>
        <button
          disabled={busy || !batchId || samples.length === 0}
          onClick={() => void runPrediction(
            Math.min(samples.length, 50),
            { retryErrors: true, replayAll: true },
          )}
          title="以当前流水线重新预测全部样本；旧版本结果保留用于对比"
        >
          <RefreshCw size={16} /> 全量版本回放
        </button>
      </section>

      <section className="benchmark-metrics">
        <article className="panel"><small>样本</small><strong>{selected?.sample_count ?? 0}</strong></article>
        <article className="panel"><small>已预测</small><strong>{predictedCount}</strong></article>
        <article className="panel"><small>已打标</small><strong>{selected?.labeled_count ?? 0}</strong></article>
        <article className="panel">
          <small>Top-1</small>
          <strong>{acceptanceReady ? percent(selected?.metrics?.top1_accuracy) : "待完整标注"}</strong>
        </article>
        <article className="panel">
          <small>Top-3</small>
          <strong>{acceptanceReady ? percent(selected?.metrics?.top3_recall) : "待完整标注"}</strong>
        </article>
      </section>

      {samplingAudit && (
        <section className="panel benchmark-feedback">
          <div>
            <h2>采样合规审计</h2>
            <span>{samplingAudit.conforms ? "通过" : "需要复核"}</span>
          </div>
          <div>
            <article>
              <strong>{samplingAudit.sample_count} 张真实已售单卡</strong>
              <span>
                去重商品 {samplingAudit.unique_item_count}
                {" · "}卖家 {samplingAudit.unique_seller_count}
                {" · "}单卖家最多 {samplingAudit.max_samples_per_seller}
                {" · "}成交价 ¥{samplingAudit.min_final_price}–¥{samplingAudit.max_final_price}
              </span>
            </article>
          </div>
        </section>
      )}

      {versionMetrics && versionMetrics.versions.length > 0 && (
        <section className="panel benchmark-feedback">
          <div>
            <h2>预测版本对比</h2>
            <span>
              可评分真值 {versionMetrics.scorable_ground_truth_count} 张
              {versionMetrics.incomplete_ground_truth_count > 0
                ? ` · ${versionMetrics.incomplete_ground_truth_count} 张真值字段不足`
                : ""}
              {versionMetrics.unresolved_labeled_count > 0
                ? ` · ${versionMetrics.unresolved_labeled_count} 张已标注但缺少正确身份`
                : ""}
            </span>
          </div>
          <div>
            {versionMetrics.versions.map((version) => (
              <article key={`${version.pipeline_version}:${version.knowledge_version ?? "none"}`}>
                <strong>
                  {version.pipeline_version}
                  {version.knowledge_version_number
                    ? ` · 知识库 v${version.knowledge_version_number}`
                    : ""}
                </strong>
                <span>
                  Top-1 {percent(version.top1_accuracy)}
                  {" · "}Top-3 {percent(version.top3_recall)}
                  {" · "}真值覆盖 {version.sample_count}
                  {" · "}验收 {
                    version.acceptance_status === "passed"
                      ? "通过"
                      : version.acceptance_status === "failed"
                        ? "未通过"
                        : `待满 ${versionMetrics.minimum_acceptance_samples} 张`
                  }
                  {" · "}失败 {version.prediction_error_count}
                  {" · "}多候选 {percent(version.multi_candidate_rate)}
                  {" · "}三候选 {percent(version.three_candidate_rate)}
                </span>
              </article>
            ))}
          </div>
        </section>
      )}

      {feedback && feedback.field_errors.length > 0 && (
        <section className="panel benchmark-feedback">
          <div>
            <h2>反馈修复队列</h2>
            <span>{feedback.knowledge_queue_count} 张待转为知识修复</span>
          </div>
          <div>
            {feedback.field_errors.map((item) => (
              <article key={item.field}>
                <strong>{item.field}</strong>
                <span>
                  准确率 {percent(item.accuracy)} · {item.errors}/{item.corrections} 次纠错
                </span>
              </article>
            ))}
          </div>
          {feedback.knowledge_queue_count > 0 && (
            <button
              disabled={busy}
              onClick={() => void run(
                () => createRecognitionKnowledgeProposals(batchId),
                "已生成知识修复草案，仍需审核后才能发布。",
              )}
            >
              生成知识修复草案
            </button>
          )}
        </section>
      )}

      {loading ? (
        <section className="panel benchmark-empty">正在加载盲测批次与图片…</section>
      ) : loadError ? (
        <section className="panel benchmark-empty">
          <p>{loadError}</p>
          <button onClick={() => void load()}>重新加载</button>
        </section>
      ) : current ? (
        <section className="benchmark-workspace">
          <div className="benchmark-images panel">
            <h2>盲测图片</h2>
            <div>
              {current.image_urls.slice(0, 2).map((url, index) => (
                <figure key={url}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={url} alt={index === 0 ? "卡片正面" : "卡片背面"} />
                  <figcaption>{index === 0 ? "图1" : "图2"}</figcaption>
                </figure>
              ))}
            </div>
          </div>

          <div className="benchmark-review panel">
            <div>
              <h2>Top-3预测</h2>
              <select
                value={current.id}
                onChange={(event) => setReviewSampleId(event.target.value)}
                aria-label="选择盲测样本"
              >
                {samples.map((sample, index) => (
                  <option key={sample.id} value={sample.id}>
                    {index + 1}/{samples.length} · {sample.source_item_id}
                    {sample.label_status === "labeled" ? " · 已标注" : " · 待标注"}
                    {sample.prediction_error ? " · 预测失败" : ""}
                  </option>
                ))}
              </select>
            </div>
            {current.prediction_pipeline_version && (
              <small className="benchmark-version">
                流水线 {current.prediction_pipeline_version} · 模型 {current.prediction_model}
              </small>
            )}
            <div className="benchmark-shortcuts" aria-label="标注快捷键">
              <span><kbd>1</kbd> Top-1正确</span>
              <span><kbd>2</kbd> Top-2正确</span>
              <span><kbd>3</kbd> Top-3正确</span>
              <span><kbd>0</kbd> 都不对</span>
              <span><kbd>Q</kbd> 图片不足</span>
              <span><kbd>E</kbd> 来源可疑</span>
            </div>
            {current.assisted_label_result &&
              current.assisted_review_status === "pending" && (
              <section className="benchmark-assisted">
                <div>
                  <small>
                    CODEX 辅助建议 · 置信度 {
                      current.assisted_confidence
                        ? `${Math.round(Number(current.assisted_confidence) * 100)}%`
                        : "—"
                    }
                  </small>
                  <strong>
                    {labels.find(([value]) => value === current.assisted_label_result)?.[1]
                      ?? current.assisted_label_result}
                  </strong>
                  {current.assisted_identity && (
                    <p>{identityText(current.assisted_identity)}</p>
                  )}
                  {current.assisted_evidence.length > 0 && (
                    <ul>
                      {current.assisted_evidence.map((item) => <li key={item}>{item}</li>)}
                    </ul>
                  )}
                </div>
                <button disabled={busy} onClick={() => void acceptAssisted()}>
                  <CheckCircle2 size={16} /> 确认建议并下一张
                </button>
              </section>
            )}
            <details className="benchmark-rubric">
              <summary>严格打标口径</summary>
              <p>
                “正确”表示球员、赛季、产品、特卡、平行、卡号、限编、评级、RC/签字/物料等
                可判定的关键身份均无实质冲突。签字还必须核对卡签/贴签，物料还必须核对
                类型及比赛使用/球员亲穿/活动穿着/非特定场次等背面声明。仅球员或系列相同、
                但平行/特卡/卡号或上述属性错误，
                仍应选择“三个都不对”或对应的其他正确候选。
              </p>
              <p>
                卡淘标题与图片冲突或不足以确认时选择“来源答案可疑”；图片无法辨认关键字段时
                选择“图片不足”。二者不进入准确率分母。
              </p>
            </details>
            {current.prediction ? (
              <>
                <div className="benchmark-candidates">
                  {current.prediction.candidates.map((candidate) => (
                    <article key={candidate.rank}>
                      <b>Top-{candidate.rank}</b>
                      <strong>{identityText(candidate.identity) || candidate.title}</strong>
                      <div className="benchmark-attributes">
                        {attributeText(candidate.identity).map(([label, value]) => (
                          <span key={label}><small>{label}</small>{value}</span>
                        ))}
                      </div>
                      <small>置信度 {candidate.confidence ?? candidate.match_confidence ?? "—"}</small>
                      <button
                        className="benchmark-candidate-confirm"
                        disabled={busy}
                        onClick={() => void submitLabel(
                          `top${candidate.rank}_correct`,
                          `Top-${candidate.rank} 正确`,
                        )}
                      >
                        <CheckCircle2 size={15} />
                        确认 Top-{candidate.rank} 正确
                        <kbd>{candidate.rank}</kbd>
                      </button>
                    </article>
                  ))}
                </div>
                {Object.keys(current.prediction.field_evidence ?? {}).length > 0 && (
                  <details className="benchmark-rubric">
                    <summary>查看关键字段的图片证据</summary>
                    {Object.entries(current.prediction.field_evidence).map(([field, evidence]) => (
                      <p key={field}><strong>{field}</strong>：{evidence.join("；")}</p>
                    ))}
                  </details>
                )}
                <div className="benchmark-reveal">
                  <small>预测后解锁的来源参考</small>
                  <p>{current.revealed_reference?.title ?? "来源未提供标题"}</p>
                  {current.source_url && (
                    <a href={current.source_url} target="_blank" rel="noreferrer">打开原始商品页</a>
                  )}
                  {current.final_price && <span>成交价：¥{current.final_price}</span>}
                </div>
                {current.source_audit?.status === "review" && (
                  <div className="benchmark-reveal">
                    <small>预测后弱证据预警（不计入评分）</small>
                    {current.source_audit.conflicts.map((conflict) => (
                      <p key={conflict.field}>
                        {conflict.reason}：预测 {String(conflict.prediction ?? "空")}；
                        标题提示 {JSON.stringify(conflict.source_hint)}
                      </p>
                    ))}
                    <small>{current.source_audit.disclaimer}</small>
                  </div>
                )}
                <details className="benchmark-correction">
                  <summary>补充完整正确身份（预测有字段错误或三个候选都不对时填写）</summary>
                  <p>
                    可先载入最接近的候选，再修改错误字段。人工修正至少需要球员、产品，
                    并包含赛季、卡号、特卡、平行、总限编或评级公司中的两项，才能计入正式指标。
                  </p>
                  <div className="benchmark-labels">
                    {current.prediction.candidates.map((candidate) => (
                      <button
                        type="button"
                        key={`draft-${candidate.rank}`}
                        disabled={busy}
                        onClick={() => setCorrection(editableCorrection(candidate.identity))}
                      >
                        载入 Top-{candidate.rank} 作为草稿
                      </button>
                    ))}
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setCorrection({})}
                    >
                      清空草稿
                    </button>
                  </div>
                  <div>
                    {correctionFields.map(([key, placeholder]) => {
                      const update = (value: string) => setCorrection((previous) => ({
                        ...previous,
                        [key]: value,
                      }));
                      if (booleanCorrectionFields.has(key)) {
                        return (
                          <label key={key}>
                            <span>{placeholder.split("：")[0]}</span>
                            <select
                              value={correction[key] ?? ""}
                              onChange={(event) => update(event.target.value)}
                            >
                              <option value="">未填写/未知</option>
                              <option value="true">是</option>
                              <option value="false">否</option>
                            </select>
                          </label>
                        );
                      }
                      if (enumCorrectionOptions[key]) {
                        return (
                          <label key={key}>
                            <span>{placeholder.split("：")[0]}</span>
                            <select
                              value={correction[key] ?? ""}
                              onChange={(event) => update(event.target.value)}
                            >
                              {enumCorrectionOptions[key].map(([value, text]) => (
                                <option key={value} value={value}>{text}</option>
                              ))}
                            </select>
                          </label>
                        );
                      }
                      return (
                        <label key={key}>
                          <span>{placeholder.split("，")[0]}</span>
                          <input
                            value={correction[key] ?? ""}
                            placeholder={placeholder}
                            onChange={(event) => update(event.target.value)}
                          />
                        </label>
                      );
                    })}
                  </div>
                  <textarea
                    value={labelNotes}
                    placeholder="判断依据或其他备注"
                    onChange={(event) => setLabelNotes(event.target.value)}
                  />
                </details>
                <div className="benchmark-labels">
                  {labels
                    .filter(([value]) => {
                      const rank = value === "top1_correct"
                        ? 1
                        : value === "top2_correct"
                          ? 2
                          : value === "top3_correct"
                            ? 3
                            : null;
                      return rank == null || (current.prediction?.candidates.length ?? 0) >= rank;
                    })
                    .map(([value, label]) => (
                    <button
                      key={value}
                      disabled={busy}
                      onClick={() => void submitLabel(value, label)}
                    >
                      <CheckCircle2 size={15} /> {label}
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <p>{current.prediction_error || "该样本尚未预测，请点击“预测下一组”。"}</p>
            )}
          </div>
        </section>
      ) : (
        <section className="panel benchmark-empty">
          当前批次确实没有样本，请切换批次或采集首批50张。
        </section>
      )}
    </main>
  );
}
