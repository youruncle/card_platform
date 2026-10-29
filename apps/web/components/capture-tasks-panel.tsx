"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { captureTaskLabels, type CaptureTask, type CaptureTaskFilter, type CaptureTaskPage } from "../../../packages/card-core/src/capture-tasks";
import { confirmationPayload } from "../../../packages/card-core/src/confirmation";
import { pairReviewPayload, pairReviewState, type PairReview, type RecognitionInput } from "../../../packages/card-core/src/recognition";
import { createPortal } from "react-dom";
import { PairReviewFields } from "./pair-review-fields";

const base = process.env.NEXT_PUBLIC_API_URL ?? "";
const imageUrl = (url: string) => /^https?:/.test(url) ? url : `${base}${url}`;
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}/v1/capture/tasks${path}`, { ...init, credentials: "include" });
  if (response.status === 401) window.dispatchEvent(new Event("card-platform:unauthorized"));
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw Error(typeof body.detail === "string" ? body.detail : body.detail?.message ?? `任务请求失败 (${response.status})`);
  }
  return response.json();
}
const message = (error: unknown) => error instanceof Error ? error.message : "任务请求失败，请重试";
const timestamp = (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN") : "时间未知";
const filters: Record<CaptureTaskFilter, string> = { attention: "待处理", all: "全部历史", ...captureTaskLabels };
const fields = {
  player_name: "球员 / 主体", brand_name: "品牌", product_name: "产品", season_start: "发行年",
  season_end: "赛季结束年", card_number: "卡号", parallel_name: "平行", insert_name: "特卡",
  serial_number: "编号", serial_total: "限编", grade_company: "评级公司", grade_value: "评级分数",
};
const retainedFields = { is_rookie: "新秀", is_autograph: "签名", is_memorabilia: "实物材质", is_patch: "Patch", has_inscription: "题字" };

/** Only source-backed suggestions prefill a new recognition draft; typed edits remain explicit. */
export function recognitionDraftValue<T extends object, K extends keyof T>(result: T | null | undefined, key: K): T[K] | undefined {
  const assessments = (result as { field_assessments?: Record<string, { status?: string }> } | null | undefined)?.field_assessments;
  const status = assessments?.[String(key)]?.status;
  return status === "unknown" || status === "conflicted" ? undefined : result?.[key];
}


export function CaptureTasksCenter({ ownerId, open, onOpen, onClose, onSaved, onOpenItem, initialTaskIds }: {
  ownerId: string; open: boolean; onOpen: () => void; onClose: () => void;
  onSaved: () => void | Promise<void>; onOpenItem?: (id: string) => void;
  initialTaskIds?: string[];
}) {
  const [counts, setCounts] = useState<CaptureTaskPage["counts"] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    let running = false;
    const refresh = async () => {
      if (running || document.visibilityState === "hidden") return;
      running = true;
      try {
        const data = await request<CaptureTaskPage>("?state=attention&page=1&page_size=1", { signal: abort.signal });
        if (!abort.signal.aborted) { setCounts(data.counts); setError(""); }
      } catch (e) { if (!abort.signal.aborted) setError(message(e)); }
      finally { running = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { abort.abort(); window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [ownerId, open]);
  const attention = counts ? counts.failed + counts.needs_review : null;
  return <>
    <button type="button" className="ghost-button capture-task-entry" onClick={onOpen} title={error || "查看所有录入任务与历史"}
      aria-label={`录入任务${attention ? ` · ${attention} 项待处理` : ""}${error ? " · 状态读取失败" : ""}`}>
      录入任务 {attention !== null && attention > 0 && <span className="capture-task-badge">{attention}</span>}
      {error && <span aria-hidden="true"> !</span>}
    </button>
    {open && createPortal(<CaptureTasksPanel ownerId={ownerId} onClose={onClose} onSaved={onSaved} onOpenItem={onOpenItem} initialTaskIds={initialTaskIds} onCounts={setCounts} />, document.body)}
  </>;
}

function CaptureTasksPanel({ ownerId, onClose, onSaved, onOpenItem, initialTaskIds, onCounts }: {
  ownerId: string; onClose: () => void; onSaved: () => void | Promise<void>;
  onOpenItem?: (id: string) => void; initialTaskIds?: string[]; onCounts: (counts: CaptureTaskPage["counts"]) => void;
}) {
  const [filter, setFilter] = useState<CaptureTaskFilter>("attention");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<CaptureTaskPage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(initialTaskIds?.[0] ?? null);
  const [selected, setSelected] = useState<CaptureTask | null>(null);
  const detailRegion = useRef<HTMLElement>(null);
  useEffect(() => {
    if (selected && window.matchMedia?.("(max-width: 760px)").matches) {
      detailRegion.current?.scrollIntoView?.({ block: "start" });
    }
  }, [selected?.id]);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRevision, setDetailRevision] = useState(0);
  const currentSelection = useRef(selectedId);
  currentSelection.current = selectedId;
  const detailRequest = useRef(0);
  useEffect(() => {
    const abort = new AbortController();
    let running = false;
    const refresh = async (background = false) => {
      if (running || (background && document.visibilityState === "hidden")) return;
      running = true;
      if (!background) setLoading(true);
      try {
        const next = await request<CaptureTaskPage>(`?state=${filter}&page=${page}&page_size=20`, { signal: abort.signal });
        if (!abort.signal.aborted) { setData(next); onCounts(next.counts); setError(""); }
      } catch (e) { if (!abort.signal.aborted) setError(message(e)); }
      finally { if (!abort.signal.aborted) setLoading(false); running = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(true), 10000);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, [filter, page, revision, onCounts]);
  useEffect(() => {
    setSelected(null); setDetailError("");
    if (!selectedId) return;
    const abort = new AbortController();
    setDetailLoading(true);
    let running = false;
    const load = async (background = false) => {
      if (running || (background && document.visibilityState === "hidden")) return;
      running = true;
      const sequence = ++detailRequest.current;
      try {
        const next = await request<CaptureTask>(`/${selectedId}`, { signal: abort.signal });
        if (!abort.signal.aborted && sequence === detailRequest.current) { setSelected(next); setDetailError(""); }
      } catch (e) { if (!abort.signal.aborted && sequence === detailRequest.current) setDetailError(message(e)); }
      finally { if (!abort.signal.aborted && sequence === detailRequest.current) setDetailLoading(false); running = false; }
    };
    void load();
    const timer = window.setInterval(() => void load(true), 10000);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, [selectedId, detailRevision]);
  const changed = useCallback((next: CaptureTask) => {
    if (currentSelection.current === next.id) { ++detailRequest.current; setDetailLoading(false); setSelected(next); }
    setRevision(value => value + 1);
  }, []);
  return <div className="modal-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="modal capture-tasks-modal" role="dialog" aria-modal="true" aria-label="录入任务中心">
      <header className="capture-tasks-header"><div><h2>录入任务</h2><p>识别成功后仍需完成入库；失败任务可重试、补充身份或更换照片。</p></div><button type="button" className="ghost-button" onClick={onClose}>关闭</button></header>
      {initialTaskIds?.length ? <nav className="capture-task-batch" aria-label="本批录入任务"><strong>本批 {initialTaskIds.length} 项</strong>{initialTaskIds.map((id, index) => <button type="button" key={id} onClick={() => setSelectedId(id)}>任务 {index + 1}</button>)}</nav> : null}
      <div className="capture-task-filters"><label>任务状态 <select aria-label="任务状态" value={filter} onChange={event => { setFilter(event.target.value as CaptureTaskFilter); setPage(1); setData(null); setError(""); }}>
        {Object.entries(filters).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label><button type="button" className="ghost-button" disabled={loading} onClick={() => setRevision(value => value + 1)}>刷新任务</button></div>
      {data && <p className="capture-task-counts">处理中 {data.counts.processing} · 待确认 / 待补充 {data.counts.needs_review} · 识别失败 {data.counts.failed} · 已入库 {data.counts.imported} · 已取消 {data.counts.cancelled}</p>}
      {error && <p role="alert">{error}。请刷新任务重试。</p>}
      {loading && <p role="status">正在读取任务…</p>}
      {!loading && !error && data?.items.length === 0 && <p>当前筛选没有任务</p>}
      <div className="capture-task-layout"><div>
        <ul className="capture-task-list">{data?.items.map(task => <li key={task.id} className={selectedId === task.id ? "selected" : ""}>
          <span className={`capture-task-state ${task.display_status}`}>{captureTaskLabels[task.display_status]}</span>
          <strong>{task.result?.player_name || task.id}</strong>
          <small>{timestamp(task.created_at)} · 已尝试 {task.attempt_count} 次</small>
          {task.attention_reasons.map(reason => <p key={reason}>{reason}</p>)}
          {task.error_message && <p>{task.error_message}</p>}
          <button type="button" className="ghost-button" aria-label={`查看任务 ${task.id}`} onClick={() => setSelectedId(task.id)}>查看任务</button>
        </li>)}</ul>
        {data && <nav className="capture-task-pagination" aria-label="任务分页"><button type="button" disabled={loading || page <= 1} onClick={() => { setPage(value => value - 1); setData(null); }} aria-label="任务上一页">上一页</button><span>第 {data.page} / {Math.max(1, data.total_pages)} 页 · 共 {data.total} 项</span><button type="button" disabled={loading || page >= data.total_pages} onClick={() => { setPage(value => value + 1); setData(null); }} aria-label="任务下一页">下一页</button></nav>}
      </div><aside ref={detailRegion} className="capture-task-detail" aria-label="任务详情">
        {detailLoading && <p role="status">正在读取任务详情…</p>}
        {detailError && <p role="alert">详情读取失败：{detailError} <button type="button" onClick={() => setDetailRevision(value => value + 1)}>重读详情</button></p>}
        {!selectedId && <p>选择任务查看照片、处理结果和尝试历史。</p>}
        {selected && <TaskDetail key={`${selected.id}:${selected.image_content_hashes.front}:${selected.image_content_hashes.back}:${selected.result ? "recognized" : "manual"}`} task={selected} ownerId={ownerId} onChanged={changed} onSaved={onSaved} onOpenItem={onOpenItem} onSelect={id => setSelectedId(current => current === selected.id ? id : current)} />}
      </aside></div>
    </section>
  </div>;
}

function TaskDetail({ task, ownerId, onChanged, onSaved, onOpenItem, onSelect }: {
  task: CaptureTask; ownerId: string; onChanged: (task: CaptureTask) => void;
  onSaved: () => void | Promise<void>; onOpenItem?: (id: string) => void; onSelect: (id: string) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(Object.keys(fields).map(key => [key, String(recognitionDraftValue(task.result, key as keyof RecognitionInput) ?? "")])));
  const [flags, setFlags] = useState<Record<string, string>>(() => Object.fromEntries(Object.keys(retainedFields).map(key => [key, recognitionDraftValue(task.result, key as keyof RecognitionInput) == null ? "unknown" : String(recognitionDraftValue(task.result, key as keyof RecognitionInput))])));
  const [review, setReview] = useState<PairReview | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [front, setFront] = useState<Blob | null>(null), [back, setBack] = useState<Blob | null>(null);
  const replacementId = useRef(crypto.randomUUID());
  const paired = task.available_sides.includes("front") && task.available_sides.includes("back");
  const result: RecognitionInput = { ...(task.result ?? { confidence: 0 }), pair_validation: task.result?.pair_validation ?? { status: "unknown", image_content_hashes: task.image_content_hashes, reason_codes: [], evidence: [], version: 1 } };
  const correctedResult: RecognitionInput = { ...result, is_numbered: recognitionDraftValue(result, "is_numbered") ?? null, ...Object.fromEntries(Object.entries(flags).map(([key, value]) => [key, value === "unknown" ? null : value === "true"])) };
  const pairState = pairReviewState(result, paired, review);
  const informationPending = ["player_name", "brand_name", "product_name", "season_start"].some(key => !values[key]?.trim())
    || Object.values(flags).some(value => value === "unknown")
    || (result.is_numbered == null && !values.serial_number?.trim() && !values.serial_total?.trim())
    || (pairState.status === "unknown" && !pairState.reviewed);
  const terminal = task.display_status === "failed" || task.display_status === "needs_review";
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(message(e)); } finally { setBusy(false); }
  };
  const confirm = (event: FormEvent) => {
    event.preventDefault();
    if (!terminal || pairState.blocked) return;
    void run(async () => {
      const payload = { ...(task.target_item_id ? {} : confirmationPayload(ownerId, correctedResult, { ...values, ...flags })), ...pairReviewPayload(result, paired, review) };
      const saved = await request<{ id: string }>(`/${task.id}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      onChanged({ ...task, display_status: "imported", collection_item_id: saved.id, can_retry: false });
      setNotice("已保存到卡库"); await onSaved();
    });
  };
  return <>
    <h3>{captureTaskLabels[task.display_status]}</h3>{informationPending && <span className="identity-pending-badge" title="识别信息待核对；未知信息保留为空，不影响入库">信息待确认</span>}<small>任务 {task.id} · 已尝试 {task.attempt_count} 次</small>
    <p>创建于 {timestamp(task.created_at)} · 更新于 {timestamp(task.updated_at)}</p>
    <div className="capture-task-photos">{(["front", "back"] as const).map(side => {
      const photo = task.images?.[side]; const url = photo?.preview_url || photo?.url || task[side === "front" ? "front_image_url" : "back_image_url"];
      return <figure key={side}>{url ? <a href={imageUrl(photo?.original_url || url)} target="_blank" rel="noreferrer"><img src={imageUrl(url)} alt={`任务${side === "front" ? "正面" : "背面"}`} /></a> : <p>未提供{side === "front" ? "正面" : "背面"}照片</p>}<figcaption>{side === "front" ? "正面" : "背面"}</figcaption></figure>;
    })}</div>
    {task.attention_reasons.map(reason => <p key={reason}>{reason}</p>)}
    {task.error_message && <p className="capture-task-error">{task.error_message}</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {task.display_status === "imported" && task.collection_item_id && onOpenItem && <button type="button" className="primary-button" onClick={() => onOpenItem(task.collection_item_id!)}>查看已入库卡片</button>}
    {task.replacement_job_id && <button type="button" onClick={() => onSelect(task.replacement_job_id!)}>查看更正后的任务</button>}
    {task.can_retry && <button type="button" className="ghost-button" disabled={busy} onClick={() => void run(async () => { onChanged(await request<CaptureTask>(`/${task.id}/retry`, { method: "POST" })); setReview(null); setNotice("已重新排队；之前的失败历史仍保留"); })}>重新尝试识别</button>}
    {terminal && <>
      <form onSubmit={confirm} className="capture-task-confirm">
        <h4>{task.target_item_id ? "补拍确认" : "补充 / 确认身份"}</h4>
        {!task.target_item_id && <><p>无法确认的字段可以留空并直接入库，保存后标记“信息待确认”。不能以版权年代替发行年。</p><div className="capture-task-fields">{Object.entries(fields).map(([key, label]) => <label key={key}>{label}<input value={values[key]} disabled={busy} inputMode={["season_start", "season_end", "serial_number", "serial_total"].includes(key) ? "numeric" : undefined} onChange={event => setValues(old => ({ ...old, [key]: event.target.value }))} /></label>)}</div>
        <div className="capture-task-fields">{Object.entries(retainedFields).map(([key, label]) => <label key={key}>{label}<select value={flags[key]} disabled={busy} onChange={event => setFlags(old => ({ ...old, [key]: event.target.value }))}><option value="unknown">未知</option><option value="true">是</option><option value="false">否</option></select></label>)}</div></>}
        <PairReviewFields result={result} paired={paired} review={review} onChange={setReview} disabled={busy} />
        <button type="submit" className="primary-button" disabled={busy || pairState.blocked}>{task.target_item_id ? "确认补拍（保留身份）" : "确认入库"}</button>
      </form>
      <section className="capture-task-replace"><h4>更正照片</h4><p>替换后创建关联的新任务，保留此任务及历史；请提供完整的正反面。</p>
        <label>替换正面照片<input type="file" accept="image/*" disabled={busy} onChange={event => { setFront(event.target.files?.[0] ?? null); replacementId.current = crypto.randomUUID(); }} /></label>
        <label>替换背面照片<input type="file" accept="image/*" disabled={busy} onChange={event => { setBack(event.target.files?.[0] ?? null); replacementId.current = crypto.randomUUID(); }} /></label>
        <button type="button" className="ghost-button" disabled={busy || !front || !back} onClick={() => void run(async () => {
          const form = new FormData(); form.append("front", front!, "front.jpg"); form.append("back", back!, "back.jpg");
          const next = await request<CaptureTask>(`/${task.id}/images`, { method: "POST", headers: { "Idempotency-Key": replacementId.current }, body: form });
          setReview(null); onChanged({ ...task, display_status: "cancelled", can_retry: false, replacement_job_id: next.id }); onSelect(next.id);
        })}>替换照片并重新识别</button>
      </section>
    </>}
    <section className="capture-task-history"><h4>尝试与状态历史</h4>{task.history.length === 0 ? <p>此任务没有保留下来的历史记录。</p> : <ol>{task.history.map(event => <li key={event.id}><strong>{captureTaskLabels[event.display_status]} · 第 {event.attempt_count} 次</strong><time>{timestamp(event.occurred_at)}</time><p>{event.message}</p><small>来源：{event.source}</small></li>)}</ol>}</section>
  </>;
}
