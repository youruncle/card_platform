"use client";
import React, { useEffect, useRef, useState } from "react";
import { actCostJob, confirmCost, createCostJob, finishCostSearchStep, getCostAudit, getCostJob, getCostSearchPlan, listCostJobs, type CostAudit, type CostJob, type CostSearchResult, type CostCandidate } from "../lib/api";
import { CardHobbyConnectionError, CardHobbyLoginError, connectCostSearch, searchCardHobbyOrders, resolveCostSearchWithFallback } from "../lib/cardhobby-cost-search";
import { CardHobbyAccountForm } from "./cardhobby-account-form";

const errorText = (error: unknown) => error instanceof Error ? error.message : "处理失败，请重试";
const active = (job: CostJob | null) => !!job && ["queued", "running"].includes(job.status);
const statusText: Record<string, string> = {queued:"等待搜索",running:"正在搜索卡淘订单",completed:"达到目标",exhausted:"已到扫描上限或没有更多卡片",cancelled:"已取消",failed:"搜索中断，可重试",recorded:"已录入",not_found:"未找到",incomplete:"搜索未完成",unmatched:"未能确定匹配",needs_confirmation:"相同卡片有多笔购买记录",skipped:"已跳过"};

export function PurchaseCostPanel({ itemId, onClose, onRecorded, initialResult, initialNeedsConnection = false, initialNeedsLogin = false, initialLoginCode, initialLabel = "", onRetry }: {
  itemId?: string;
  onClose: () => void;
  onRecorded: () => void;
  initialResult?: CostSearchResult;
  initialNeedsConnection?: boolean;
  initialNeedsLogin?: boolean;
  initialLoginCode?: string;
  initialLabel?: string;
  onRetry?: (itemId: string) => void;
}) {
  const [target, setTarget] = useState(10);
  const [job, setJob] = useState<CostJob | null>(null);
  const [preview, setPreview] = useState<CostSearchResult | null>(null);
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [label, setLabel] = useState("");
  const [needsConnection, setNeedsConnection] = useState(false);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [loginCode, setLoginCode] = useState<string>();
  const [batchItem, setBatchItem] = useState<string>();
  const currentItem = itemId ?? batchItem;
  const [confirmed, setConfirmed] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const requestKey = useRef<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const actionControllerRef = useRef<AbortController | null>(null);
  const confirmationRef = useRef<symbol | null>(null);
  const ignoredSeedRef = useRef<{itemId?: string; result?: CostSearchResult; needsConnection: boolean; label: string} | null>(null);
  const recordedRef = useRef(onRecorded);
  recordedRef.current = onRecorded;

  useEffect(() => () => actionControllerRef.current?.abort(), []);

  function report(error: unknown) {
    setMessage(errorText(error));
    if (error instanceof CardHobbyConnectionError) setNeedsConnection(true);
    if (error instanceof CardHobbyLoginError) { setNeedsLogin(true); setLoginCode(error.code); }
  }
  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    let alive = true;
    confirmationRef.current = null;
    setSelected(""); setPreview(null); setConfirmed(false); setMessage(""); setLabel(""); setNeedsConnection(false); setNeedsLogin(false);
    if (currentItem) {
      const ignoredSeed = ignoredSeedRef.current;
      const useSeed = itemId === currentItem && (initialResult || initialNeedsConnection || initialNeedsLogin)
        && !(ignoredSeed && ignoredSeed.itemId === itemId && ignoredSeed.result === initialResult && ignoredSeed.needsConnection === initialNeedsConnection && ignoredSeed.label === initialLabel);
      if (useSeed) {
        setBusy(false);
        setLabel(initialLabel);
        setNeedsConnection(initialNeedsConnection);
        setNeedsLogin(initialNeedsLogin);
        setLoginCode(initialLoginCode);
        setPreview(initialResult ?? null);
        setMessage(initialResult?.reason ?? "");
        setConfirmed(initialResult?.status === "recorded" || initialResult?.status === "skipped");
        return () => { alive = false; controller.abort(); };
      }
      setBusy(true);
      void (async () => {
        const plan = await getCostSearchPlan(currentItem);
        if (!alive) return;
        setLabel(plan.label);
        if (plan.status === "skipped") { setMessage(plan.reason); setConfirmed(true); return; }
        setMessage(`已根据卡牌信息提取关键词，正在搜索卡淘订单：${plan.keywords.join(" / ")}…`);
        const capture = await searchCardHobbyOrders(plan.keywords, controller.signal);
        if (!alive) return;
        setMessage("正在核对商品与运费…");
        const result = await resolveCostSearchWithFallback(currentItem, capture, plan, controller.signal,
          {keepWriteResponseOnClose: true});
        // A confirmed write still refreshes the collection if the modal was closed.
        if (result.status === "recorded") recordedRef.current();
        if (!alive) return;
        setPreview(result);
        if (result.status === "recorded" && result.recorded) {
          setConfirmed(true);
          setMessage(`${result.recorded.match_method === "inferred" ? "已推测匹配，请核对订单快照；购入成本" : "已自动填入购入成本"} ¥${result.recorded.total_cost}（卡价 ¥${result.recorded.card_price} + 运费 ¥${result.recorded.shipping}）`);
        } else {
          setMessage(result.reason);
          if (result.status === "skipped") setConfirmed(true);
        }
      })().catch(error => { if (alive && !controller.signal.aborted) report(error); }).finally(() => { if (alive) setBusy(false); });
    } else {
      setBusy(false);
      listCostJobs().then(jobs => { if (alive) setJob(jobs[0] ?? null); }).catch(error => { if (alive) report(error); });
    }
    return () => { alive = false; controller.abort(); };
  }, [currentItem, itemId, initialResult, initialNeedsConnection, initialNeedsLogin, initialLoginCode, initialLabel, retryToken]);

  useEffect(() => {
    if (!job || !active(job) || currentItem) return;
    let alive = true;
    let pollBusy = false;
    const controller = new AbortController();
    controllerRef.current = controller;
    if (job.source_mode === "browser_search") {
      void (async () => {
        const item = job.item_ids?.[job.scanned];
        if (!item) throw new Error("任务未返回待搜索卡片，请刷新重试");
        const plan = await getCostSearchPlan(item);
        if (!alive) return;
        setMessage(`正在搜索 ${job.scanned + 1} / ${job.item_ids?.length}：${plan.label}`);
        const capture = plan.status === "skipped" ? {orders:[], complete:true} : await searchCardHobbyOrders(plan.keywords, controller.signal, plan.fallback_keywords);
        if (!alive) return;
        const next = await finishCostSearchStep(job.id, item, capture);
        if (next.succeeded > job.succeeded) recordedRef.current();
        if (alive) setJob(next);
      })().catch(async error => {
        if (!alive || controller.signal.aborted) return;
        report(error);
        try { const next = await actCostJob(job.id, "search-failed"); if (alive) setJob(next); }
        catch (reason) { if (alive) report(reason); }
      });
    }
    const timer = window.setInterval(async () => {
      if (pollBusy) return;
      pollBusy = true;
      try {
        const next = await getCostJob(job.id);
        if (alive) { setJob(next); if (!active(next)) recordedRef.current(); }
      } catch (error) { if (alive) report(error); }
      finally { pollBusy = false; }
    }, 2000);
    return () => { alive = false; controller.abort(); window.clearInterval(timer); };
  }, [job?.id, job?.status, job?.scanned, currentItem]);

  async function run(operation: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await operation(); } catch (error) { report(error); }
    finally { setBusy(false); }
  }
  async function activateBrowserJob(operation: () => Promise<CostJob>) {
    const controller = new AbortController();
    actionControllerRef.current?.abort();
    actionControllerRef.current = controller;
    try {
      await connectCostSearch(controller.signal);
      if (controller.signal.aborted) return;
      setNeedsConnection(false);
      setNeedsLogin(false);
      const next = await operation();
      if (controller.signal.aborted) {
        // Closing during a request must not leave a newly activated job running.
        if (active(next)) await actCostJob(next.id, "cancel");
        return;
      }
      setJob(next);
    } catch (error) {
      if (!controller.signal.aborted) throw error;
    } finally {
      if (actionControllerRef.current === controller) actionControllerRef.current = null;
    }
  }
  async function close() {
    actionControllerRef.current?.abort();
    controllerRef.current?.abort();
    if (active(job) && job?.source_mode === "browser_search") {
      try { await actCostJob(job.id, "cancel"); } catch (error) { report(error); return; }
    }
    onClose();
  }
  function retry() {
    if (!currentItem) return;
    if (onRetry) {
      onRetry(currentItem);
      onClose();
      return;
    }
    ignoredSeedRef.current = {itemId, result:initialResult, needsConnection:initialNeedsConnection, label:initialLabel};
    setRetryToken(value => value + 1);
  }
  const duplicateCandidates = preview?.candidates.filter(candidate => candidate.automatic) ?? [];
  const needsDuplicateChoice = !confirmed && preview?.status === "needs_confirmation" && duplicateCandidates.length > 1;
  async function confirmSelection() {
    const candidate = duplicateCandidates.find(row => row.unit_id === selected);
    if (!currentItem || !needsDuplicateChoice || !candidate || confirmationRef.current) return;
    const token = Symbol("confirmation");
    confirmationRef.current = token;
    const controller = controllerRef.current;
    const notifyRecorded = recordedRef.current;
    const closeAfterConfirm = itemId === currentItem && !!initialResult;
    setBusy(true); setMessage("");
    try {
      await confirmCost(currentItem, candidate);
      // Refresh a completed write even if the user has since closed or switched cards.
      notifyRecorded();
      if (controller?.signal.aborted) return;
      setConfirmed(true); setMessage("购入成本已录入。");
      if (closeAfterConfirm) onClose();
    } catch (error) {
      if (!controller?.signal.aborted) report(error);
    } finally {
      if (confirmationRef.current === token) confirmationRef.current = null;
      if (!controller?.signal.aborted) setBusy(false);
    }
  }
  return <div className="modal-backdrop"><section className="modal cost-modal" role="dialog" aria-modal="true" aria-labelledby="cost-title">
    <div className="modal-heading"><h2 id="cost-title">{currentItem ? "获取卡淘购入成本" : "批量获取卡淘成本"}</h2><button className="text-button" onClick={() => void close()} aria-label="关闭成本识别">关闭</button></div>
    <p>每次获取都会重新搜索卡淘购买订单。核心信息与图片相符时允许推测匹配，并保留订单快照供核对。多笔相似购买记录需要选择订单；明显冲突、未付款或金额不可信时保留原成本。</p>
    {!busy && !active(job) && !needsLogin && <button className="text-button" onClick={()=>{setNeedsLogin(true);setLoginCode(undefined);}}>管理卡淘登录账号</button>}
    {needsLogin && <CardHobbyAccountForm verification={loginCode === "VERIFICATION_REQUIRED"} onConnected={()=>{
      setNeedsLogin(false); setMessage("");
      if (currentItem) retry();
      else void run(()=>activateBrowserJob(()=>{
        if (job && ["failed","cancelled"].includes(job.status)) return actCostJob(job.id,"retry");
        requestKey.current ??= crypto.randomUUID(); return createCostJob(target,requestKey.current,"browser_search");
      }));
    }}/>}
    {needsConnection && <section className="cost-connection"><h3>首次连接卡淘</h3><p>在 Chrome 启用卡淘连接扩展，并保持卡淘已登录。之后点击“获取成本”即可自动搜索。</p>
      <a href="/cardhobby-connector.zip" download>下载卡淘连接扩展</a> · <a href="https://cardhobby.com.cn/Order/MyOrders" target="_blank" rel="noreferrer">打开卡淘订单页</a>
      <details><summary>安装方法（只需一次）</summary><p>解压下载文件，在 Chrome 的扩展程序页面启用开发者模式，点击“加载已解压的扩展程序”选择解压后的文件夹。刷新卡库页面后重试。</p></details>
    </section>}
    <p role="status" aria-live="polite">{message}</p>
    {currentItem ? <>
      {label && <p>{label}</p>}
      {preview?.recorded && <details className="cost-audit"><summary>查看订单快照{preview.recorded.match_method === "inferred" ? " · 推测匹配" : ""}</summary><OrderEvidenceSnapshot candidate={preview.recorded}/></details>}
      {!confirmed && !busy && !needsLogin && <button className="text-button" onClick={retry}>重新自动获取成本</button>}
      {needsDuplicateChoice && <div className="cost-candidates">{duplicateCandidates.map(candidate => <label className="cost-candidate" key={candidate.unit_id}>
        <input type="radio" name="cost-candidate" value={candidate.unit_id} checked={selected===candidate.unit_id} disabled={busy} onChange={() => setSelected(candidate.unit_id)}/>
        {candidate.image_urls[0] && <img src={candidate.image_urls[0]} alt="订单商品图片" referrerPolicy="no-referrer"/>}
        <div><strong>{candidate.title}</strong><p>{candidate.ordered_at} · {candidate.seller} · {candidate.status}</p>
          <p>订单 {candidate.order_id} · 商品 {candidate.item_id}</p>
          <p>卡价 ¥{candidate.card_price} + 运费 ¥{candidate.shipping} = <b>¥{candidate.total_cost}</b></p>
          <p>{candidate.reasons.join("；")}</p>
          {candidate.match_method === "inferred" && <p>推测匹配 · {(candidate.inference_warnings ?? []).join("；")}</p>}
          <a href={candidate.source_url} target="_blank" rel="noreferrer">查看来源订单</a>
        </div>
      </label>)}</div>}
      {needsDuplicateChoice && <button className="primary-button" disabled={!selected || busy} onClick={() => void confirmSelection()}>确认所选商品并录入成本</button>}
      {!itemId && <button className="text-button" onClick={() => setBatchItem(undefined)}>返回批量进度</button>}
    </> : <>
      <label>最多成功录入<input aria-label="最大成功录入个数 X" type="number" min="1" max="500" value={target} disabled={busy || active(job)} onChange={event=>{setTarget(Number(event.target.value));requestKey.current=null;}}/> 张</label>
      <p>逐张提取关键词并搜索卡淘订单，匹配后自动录入。相同卡片有多笔购买记录时留待选择订单；未找到或无法确定匹配的卡会跳过。关闭窗口会暂停，可稍后继续。</p>
      <button className="primary-button" disabled={busy || active(job) || !Number.isInteger(target) || target<1 || target>500} onClick={()=>void run(()=>activateBrowserJob(()=>{
        requestKey.current ??= crypto.randomUUID(); return createCostJob(target,requestKey.current,"browser_search");
      }))}>开始批量获取成本</button>
      {job && <section><h3>{statusText[job.status] ?? job.status}</h3><progress value={job.succeeded} max={job.target}/><p>成功 {job.succeeded} / {job.target} · 已扫描 {job.scanned} / {job.scan_limit}</p>
        {job.error && <p role="alert">{job.error}</p>}
        {active(job) && <button className="text-button" disabled={busy} onClick={()=>void run(async()=>{controllerRef.current?.abort();setJob(await actCostJob(job.id,"cancel"));})}>取消任务</button>}
        {["failed","cancelled"].includes(job.status) && <button className="primary-button" disabled={busy} onClick={()=>void run(()=>activateBrowserJob(()=>actCostJob(job.id,"retry")))}>从中断处继续</button>}
        {!active(job) && <button className="text-button" onClick={()=>{requestKey.current=null;setJob(null);}}>准备新任务</button>}
        <div className="cost-results">{job.results.map(row=><p key={row.item_id}>卡片 {row.item_id.slice(0,8)} · {statusText[row.status] ?? row.status} · {row.reason} {row.status==="needs_confirmation" && <button className="text-button" onClick={()=>setBatchItem(row.item_id)}>选择对应订单</button>}</p>)}</div>
      </section>}
    </>}
  </section></div>;
}
export function OrderEvidenceSnapshot({candidate}: {candidate:CostCandidate}) {
  return <section className="cost-order-snapshot">
    <strong>{candidate.title}</strong>
    <p>{candidate.match_method === "image_title_correction" ? "图片核验纠正卖家标题（请核对）" : candidate.match_method === "inferred" ? "推测匹配（请核对）" : candidate.match_method === "bundle_equal_split" ? "打包订单按实体张数均摊" : "严格匹配 / 已核验来源"} · 匹配置信度 {Math.round(Number(candidate.confidence)*100)}%</p>
    {(candidate.inference_warnings ?? []).length > 0 && <p role="note">{candidate.inference_warnings?.join("；")}</p>}
    <div style={{display:"flex",gap:12,flexWrap:"wrap"}}>{candidate.image_urls.map(url=><a key={url} href={url} target="_blank" rel="noreferrer"><img src={url} alt="订单商品快照" referrerPolicy="no-referrer" style={{maxWidth:200,maxHeight:280,objectFit:"contain"}}/></a>)}</div>
    {!candidate.image_urls.length && <p>该订单没有商品图片，标题和价格证据仍保留。</p>}
    <p>卡价 ¥{candidate.card_price} + 分摊运费 ¥{candidate.shipping} = 总成本 ¥{candidate.total_cost}</p>
    {candidate.shipping_basis === "owner_default" && <p role="note">顺丰到付运费按本账户指定的整单默认金额、依实物卡张数均摊；不是卡淘线上实付运费，实际到付金额尚未核实。</p>}
    <p>{candidate.ordered_at} · {candidate.seller} · {candidate.status}</p>
    {candidate.visual_inference && <p>图片模型推测（可能有误，不代表人工核验）：{candidate.visual_inference.reasons.join("；")}</p>}
    <p>标题、价格与图片链接按录入时保存；点击图片查看原图，远端图片失效时不影响已保存的金额与标题。</p>
    {candidate.item_url && <a href={candidate.item_url} target="_blank" rel="noreferrer">查看卡淘商品</a>}
  </section>;
}

export function PurchaseCostAudit({itemId,defaultOpen=false}: {itemId:string;defaultOpen?:boolean}) {
  const [audit,setAudit]=useState<CostAudit|null>(null);
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(true);
  useEffect(()=>{let alive=true;setAudit(null);setError("");setLoading(true);getCostAudit(itemId).then(value=>{if(alive)setAudit(value);}).catch(e=>{if(alive)setError(errorText(e));}).finally(()=>{if(alive)setLoading(false);});return()=>{alive=false;};},[itemId]);
  if(error) return <p role="alert">成本来源暂不可用：{error}</p>;
  if(!audit) return defaultOpen ? <p role="status">{loading ? "正在读取订单快照…" : "暂无卡淘订单快照；此成本可能是手工录入。"}</p> : null;
  return <details className="cost-audit" open={defaultOpen || undefined}><summary>卡淘购入成本来源 · {audit.manual_override ? "已人工修改" : audit.evidence.match_method === "inferred" ? "推测匹配（请核对）" : audit.confirmation==="automatic" ? "自动匹配" : "用户已确认"}</summary>
    <OrderEvidenceSnapshot candidate={audit.evidence}/>
    <p>原始卡价 ¥{audit.card_price} · 分摊运费 ¥{audit.shipping} · 原始总成本 ¥{audit.evidence.total_cost}</p>
    <p>订单 {audit.evidence.order_id} · 商品 {audit.evidence.item_id} · {audit.evidence.ordered_at}</p><p>{audit.evidence.reasons.join("；")}</p>
    {audit.source_changed && <p role="alert">来源订单已变化。现有成本保留，请核对退款或金额变化。</p>}
    {audit.manual_override && <p>账本已人工调整，当前卡价 {audit.current_currency} {audit.current_price}，费用 {audit.current_shipping}；原始证据保留。</p>}
    <a href={audit.evidence.source_url} target="_blank" rel="noreferrer">查看来源订单</a>
  </details>;
}

export function PurchaseCostEvidenceModal({itemId,onClose}:{itemId:string;onClose:()=>void}) {
  return <div className="modal-backdrop"><section className="modal cost-modal" role="dialog" aria-modal="true" aria-label="购入成本订单快照">
    <div className="modal-heading"><h2>购入成本订单快照</h2><button className="text-button" onClick={onClose}>关闭快照</button></div>
    <PurchaseCostAudit itemId={itemId} defaultOpen/>
  </section></div>;
}
