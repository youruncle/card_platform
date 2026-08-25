"use client";

import { useEffect, useState } from "react";
import {
  captureFlatbedSheet, confirmFlatbedLayout, confirmFlatbedPairs, createFlatbedSession, getFlatbedDevice, getFlatbedSession, getFlatbedSessions,
  proposeFlatbedPairs, submitFlatbedSession, uploadFlatbedSheet, assetUrl,
  type FlatbedPair, type FlatbedRegion, type FlatbedSession, type FlatbedSheet,
} from "../lib/api";

export function FlatbedBatchPanel({ onCompleted }: { onCompleted?: (jobIds: string[]) => void | Promise<void> }) {
  const [device, setDevice] = useState("正在探测 Canon 扫描仪…");
  const [dpi, setDpi] = useState<300 | 600>(300);
  const [session, setSession] = useState<FlatbedSession | null>(null);
  const [fronts, setFronts] = useState<FlatbedRegion[]>([]);
  const [backs, setBacks] = useState<FlatbedRegion[]>([]);
  const [sheets, setSheets] = useState<Partial<Record<"front" | "back", FlatbedSheet>>>({});
  const [pairs, setPairs] = useState<FlatbedPair[]>([]);
  const [originalPairs, setOriginalPairs] = useState<FlatbedPair[]>([]);
  const [missingFront, setMissingFront] = useState<string[]>([]);
  const [missingBack, setMissingBack] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [lightbox, setLightbox] = useState<{ url: string; label: string } | null>(null);
  const [editing, setEditing] = useState<"front" | "back" | null>(null);
  const [draft, setDraft] = useState<Array<Array<{ x: number; y: number }>>>([]);
  const [undo, setUndo] = useState<Array<Array<Array<{ x: number; y: number }>>>>([]);
  const [selected, setSelected] = useState(0);

  useEffect(() => {
    getFlatbedDevice().then((value) => setDevice(`${value.name} · ${value.serial} · 已就绪`)).catch((error) => setDevice(`设备不可用：${error.message}`));
    getFlatbedSessions().then(async (items) => {
      if (!items[0] || items[0].submitted_at) return;
      const restored = await getFlatbedSession(items[0].id);
      setSession(restored);
      const restoredFronts = (restored.regions ?? []).filter((item) => item.side === "front");
      const restoredBacks = (restored.regions ?? []).filter((item) => item.side === "back");
      setFronts(restoredFronts);
      setBacks(restoredBacks);
      setSheets(Object.fromEntries((restored.sheets ?? []).map((item) => [item.side, item])));
      const restoredPairs = (restored.pairs ?? []).filter((item) => item.selected).map((item) => ({
        front_id: String(item.front_region_id), back_id: String(item.back_region_id),
        confidence: Number(item.score), confidence_level: String(item.confidence_level) as "high" | "medium" | "low",
        conflicts: Array.isArray(item.conflicts) ? item.conflicts.map(String) : [],
        signals: typeof item.signals === "object" && item.signals ? item.signals as Record<string, number> : {},
        requires_review: String(item.confidence_level) !== "high" || (Array.isArray(item.conflicts) && item.conflicts.length > 0),
      }));
      setPairs(restoredPairs); setOriginalPairs(restoredPairs);
      if (restoredPairs.length > 0 || restoredFronts.length === 0 || restoredBacks.length === 0) {
        setMessage("已恢复上次未完成的 Canon 扫描会话。");
        return;
      }
      const proposal = await proposeFlatbedPairs(restored.id);
      applyProposal(proposal);
      setMessage(proposal.requires_review
        ? "已恢复扫描会话并重新生成配对，请核对后录入。"
        : "已恢复扫描会话并重新生成全部高置信度配对，可直接录入。");
    }).catch((error) => setMessage(error instanceof Error ? `恢复扫描会话失败：${error.message}` : "恢复扫描会话失败"));
  }, []);

  useEffect(() => {
    if (!lightbox) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setLightbox(null); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [lightbox]);

  async function start() {
    setBusy(true); setMessage("");
    try { setSession(await createFlatbedSession(dpi)); setFronts([]); setBacks([]); setSheets({}); setPairs([]); setMissingFront([]); setMissingBack([]); }
    catch (error) { setMessage(error instanceof Error ? error.message : "无法创建会话"); }
    finally { setBusy(false); }
  }

  async function sheet(side: "front" | "back", file?: File) {
    if (!session || !file) return;
    setBusy(true); setMessage("");
    try {
      await acceptSheet(side, await uploadFlatbedSheet(session.id, side, file));
    } catch (error) { setMessage(error instanceof Error ? error.message : "扫描处理失败"); }
    finally { setBusy(false); }
  }

  async function scan(side: "front" | "back", preview: boolean) {
    if (!session) return;
    setBusy(true); setMessage(preview ? "正在进行低风险概览扫描…" : `正在以 ${dpi} DPI 扫描整版…`);
    try { await acceptSheet(side, await captureFlatbedSheet(session.id, side, preview)); }
    catch (error) { setMessage(error instanceof Error ? error.message : "扫描仪采集失败"); }
    finally { setBusy(false); }
  }

  async function acceptSheet(side: "front" | "back", result: { id: string; regions: FlatbedRegion[]; failures: string[] }) {
    setSheets((current) => ({ ...current, [side]: { id: result.id, side, revision: 0 } }));
    const warning = result.failures.length ? ` 检测警告：${result.failures.join("、")}。` : "";
    if (side === "front") {
      setFronts(result.regions);
      if (backs.length === 0) {
        setMessage(`检测到 ${result.regions.length} 张正面。${warning}确认后请保持位置并翻面。`);
        return;
      }
      const proposal = await proposeFlatbedPairs(session!.id);
      applyProposal(proposal);
      setMessage(proposal.requires_review ? `${warning}正面已更新并重新配对，请核对标记项。` : `${warning}正面已更新，全部配对可直接确认。`);
      return;
    }
    setBacks(result.regions);
    const proposal = await proposeFlatbedPairs(session!.id);
    applyProposal(proposal);
    setMessage(proposal.requires_review ? `${warning}存在低置信度、冲突或数量不一致，请人工核对或补扫。` : `${warning}全部为高置信度配对，可直接确认。`);
  }

  function applyProposal(proposal: Awaited<ReturnType<typeof proposeFlatbedPairs>>) {
    setPairs(proposal.pairs);
    setOriginalPairs(proposal.pairs);
    setMissingFront(proposal.missing_front);
    setMissingBack(proposal.missing_back);
  }

  async function retryPairing() {
    if (!session) return;
    setBusy(true); setMessage("正在重新生成正反面配对…");
    try {
      const proposal = await proposeFlatbedPairs(session.id);
      applyProposal(proposal);
      setMessage(proposal.requires_review ? "配对已重建，请核对标记项后录入。" : "配对已重建，可直接录入。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "重新生成配对失败"); }
    finally { setBusy(false); }
  }

  function SheetPreview({ side, regions }: { side: "front" | "back"; regions: FlatbedRegion[] }) {
    const current = sheets[side];
    if (!current) return null;
    const original = assetUrl(`/v1/flatbed/sheets/${current.id}/image`) ?? undefined;
    const label = `${side === "front" ? "正面" : "背面"}整版扫描`;
    return <div className="flatbed-sheet-preview">
      <div className="flatbed-sheet-heading"><strong>{label}</strong><span><button type="button" className="ghost-button" onClick={() => { const next = regions.map((r) => r.bounds.points?.length === 4 ? r.bounds.points.map((p) => ({...p})) : [{x:r.bounds.x,y:r.bounds.y},{x:r.bounds.x+r.bounds.width,y:r.bounds.y},{x:r.bounds.x+r.bounds.width,y:r.bounds.y+r.bounds.height},{x:r.bounds.x,y:r.bounds.y+r.bounds.height}]); setDraft(next); setUndo([]); setSelected(0); setEditing(side); }}>校正布局</button><button type="button" className="link-button" onClick={() => original && setLightbox({ url: original, label })}>查看原图</button></span></div>
      <button type="button" className="flatbed-sheet-image" onClick={() => original && setLightbox({ url: original, label })}>
        <span className="flatbed-sheet-canvas">
          <img src={original} alt={`${side === "front" ? "正面" : "背面"}整版扫描缩略图`} />
          <svg className="flatbed-sheet-polygons" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">{regions.map((region) => {
            const points = region.bounds.points?.length === 4 ? region.bounds.points : [
              { x: region.bounds.x, y: region.bounds.y }, { x: region.bounds.x + region.bounds.width, y: region.bounds.y },
              { x: region.bounds.x + region.bounds.width, y: region.bounds.y + region.bounds.height }, { x: region.bounds.x, y: region.bounds.y + region.bounds.height },
            ];
            return <polygon key={region.id} points={points.map((point) => `${point.x},${point.y}`).join(" ")} vectorEffect="non-scaling-stroke" />;
          })}</svg>
          {regions.map((region) => { const points = region.bounds.points ?? [{ x: region.bounds.x, y: region.bounds.y }]; return <i key={region.id} title={`卡片 ${region.ordinal + 1}，置信度 ${Math.round(region.confidence * 100)}%`} style={{ left: `${Math.min(...points.map((point) => point.x)) * 100}%`, top: `${Math.min(...points.map((point) => point.y)) * 100}%` }}>{region.ordinal + 1}</i>; })}
        </span>
      </button>
      <div className="flatbed-region-thumbs">{regions.map((region) => { const url = assetUrl(`/v1/flatbed/regions/${region.id}/image`) ?? ""; return <button type="button" key={region.id} onClick={() => setLightbox({ url, label: `${side === "front" ? "正面" : "背面"}卡片 ${region.ordinal + 1}` })}><img src={url} alt={`卡片 ${region.ordinal + 1}`} /><span>{region.ordinal + 1}</span></button>; })}</div>
    </div>;
  }

  async function saveLayout() {
    if (!editing || !sheets[editing]) return;
    setBusy(true);
    try {
      const result = await confirmFlatbedLayout(sheets[editing]!.id, draft.map((points) => ({ points })));
      setSheets((current) => ({ ...current, [editing]: { id: result.id, side: editing, revision: (current[editing]?.revision ?? 0) + 1 } }));
      if (editing === "front") setFronts(result.regions); else setBacks(result.regions);
      setEditing(null);
      const hasBothSides = editing === "front" ? backs.length > 0 : fronts.length > 0;
      if (hasBothSides) {
        const proposal = await proposeFlatbedPairs(session!.id);
        applyProposal(proposal);
        setMessage(`已保存 ${result.regions.length} 个校正框、重建派生裁切及正反面配对，原始扫描未改变。`);
      } else {
        setPairs([]); setOriginalPairs([]); setMissingFront([]); setMissingBack([]);
        setMessage(`已保存 ${result.regions.length} 个校正框并重建派生裁切，原始扫描未改变。`);
      }
    } catch (error) { setMessage(error instanceof Error ? error.message : "布局保存失败"); }
    finally { setBusy(false); }
  }

  function swapBack(index: number, backId: string) {
    setPairs((current) => {
      const other = current.findIndex((pair) => pair.back_id === backId);
      if (other < 0 || other === index) return current;
      const next = current.map((pair) => ({ ...pair }));
      [next[index].back_id, next[other].back_id] = [next[other].back_id, next[index].back_id];
      return next;
    });
  }

  function conflictLabel(value: string) {
    return ({
      ocr_identity_conflict: "OCR 身份信息不一致",
      geometry_conflict: "摆放位置存在冲突",
      visual_conflict: "画面特征不一致",
      low_signal: "可用特征不足",
    } as Record<string, string>)[value] ?? value;
  }

  async function confirmAndSubmit() {
    if (!session) return;
    setBusy(true);
    try {
      await confirmFlatbedPairs(session.id, pairs.map((pair) => ({ front_region_id: pair.front_id, back_region_id: pair.back_id })));
      const result = await submitFlatbedSession(session.id);
      await onCompleted?.(result.job_ids);
    } catch (error) { setMessage(error instanceof Error ? error.message : "提交失败"); }
    finally { setBusy(false); }
  }

  return <section className="flatbed-panel">
    <div className="flatbed-heading"><div><strong>Canon 扫描仪批量录入</strong><small>{device}</small></div><label>DPI <select value={dpi} onChange={(event) => setDpi(Number(event.target.value) as 300 | 600)} disabled={!!session}><option>300</option><option>600</option></select></label></div>
    {!session && <button type="button" className="primary-button" onClick={start} disabled={busy}>新建扫描会话</button>}
    {session && <div className="flatbed-steps">
      <label><b>1. 扫描正面</b><small>支持单张、多行多列、卡片与卡砖混合</small><span className="flatbed-scan-actions"><button type="button" onClick={() => void scan("front", true)} disabled={busy}>概览</button><button type="button" onClick={() => void scan("front", false)} disabled={busy}>正式扫描</button></span><input title="也可导入已有整版图" type="file" accept="image/jpeg,image/png" onChange={(e) => void sheet("front", e.target.files?.[0])} disabled={busy} /></label>
      <span className={fronts.length ? "done" : ""}>{fronts.length} 张</span>
      <SheetPreview side="front" regions={fronts} />
      <label><b>2. 原位翻面后扫描背面</b><small>系统会评估镜像、180° 和行列变化</small><span className="flatbed-scan-actions"><button type="button" onClick={() => void scan("back", true)} disabled={busy || !fronts.length}>概览</button><button type="button" onClick={() => void scan("back", false)} disabled={busy || !fronts.length}>正式扫描</button></span><input title="也可导入已有整版图" type="file" accept="image/jpeg,image/png" onChange={(e) => void sheet("back", e.target.files?.[0])} disabled={busy || !fronts.length} /></label>
      <span className={backs.length ? "done" : ""}>{backs.length} 张</span>
      <SheetPreview side="back" regions={backs} />
    </div>}
    {fronts.length > 0 && backs.length > 0 && pairs.length === 0 && missingFront.length === 0 && missingBack.length === 0 && <div className="flatbed-pairs">
      <p className="recognition-error">正反面都已扫描，但尚未生成配对。</p>
      <button type="button" className="primary-button" onClick={() => void retryPairing()} disabled={busy}>重新生成配对并继续录入</button>
    </div>}
    {(pairs.length > 0 || missingFront.length > 0 || missingBack.length > 0) && <div className="flatbed-pairs"><div className="flatbed-pair-title"><strong>正反面配对</strong><button type="button" className="ghost-button" onClick={() => setPairs(originalPairs)}>撤销调整</button></div>
      {(missingFront.length > 0 || missingBack.length > 0) && <p className="recognition-error">数量不一致：缺正面 {missingFront.length} 张，缺背面 {missingBack.length} 张。请使用上方对应按钮补扫后重新配对。</p>}
      <div className="flatbed-pair-grid">{pairs.map((pair, index) => {
        const front = fronts.find((item) => item.id === pair.front_id);
        const back = backs.find((item) => item.id === pair.back_id);
        const frontNumber = (front?.ordinal ?? index) + 1;
        const backNumber = (back?.ordinal ?? index) + 1;
        const frontUrl = assetUrl(`/v1/flatbed/regions/${pair.front_id}/image`) ?? "";
        const backUrl = assetUrl(`/v1/flatbed/regions/${pair.back_id}/image`) ?? "";
        return <article key={pair.front_id} className={`flatbed-pair-card ${pair.requires_review ? "review" : ""}`}>
          <div className="flatbed-pair-card-heading"><strong>卡片 {index + 1}</strong><span className={`flatbed-confidence ${pair.confidence_level}`}>{Math.round(pair.confidence * 100)}% · {pair.confidence_level === "high" ? "高置信度" : pair.confidence_level === "medium" ? "需核对" : "低置信度"}</span></div>
          <div className="flatbed-pair-visuals">
            <button type="button" onClick={() => setLightbox({ url: frontUrl, label: `卡片 ${index + 1} 正面` })}><img src={frontUrl} alt={`卡片 ${index + 1} 正面`} /><span>正面 {frontNumber}</span><small>点击放大</small></button>
            <div className="flatbed-pair-switch"><span aria-hidden="true">⇄</span><label>匹配背面<select value={pair.back_id} onChange={(event) => swapBack(index, event.target.value)}>{backs.map((candidate) => <option key={candidate.id} value={candidate.id}>背面 {candidate.ordinal + 1}</option>)}</select></label></div>
            <button type="button" onClick={() => setLightbox({ url: backUrl, label: `卡片 ${index + 1} 背面` })}><img src={backUrl} alt={`卡片 ${index + 1} 背面`} /><span>背面 {backNumber}</span><small>点击放大</small></button>
          </div>
          <div className={`flatbed-pair-note ${pair.conflicts.length ? "conflict" : ""}`}>{pair.conflicts.length ? pair.conflicts.map(conflictLabel).join("；") : "未发现配对冲突"}</div>
        </article>;
      })}</div>
      <button type="button" className="primary-button" onClick={confirmAndSubmit} disabled={busy || missingFront.length > 0 || missingBack.length > 0}>确认配对并录入 {pairs.length} 张卡片</button>
    </div>}
    {message && <p className="recognition-error">{message}</p>}
    {editing && <div className="flatbed-layout-editor" role="dialog" aria-modal="true"><div><header><strong>校正{editing === "front" ? "正面" : "背面"}布局</strong><small>拖动四角；可增加、删除、撤销后确认。绿色为当前框。</small></header><div className="flatbed-editor-canvas"><img src={assetUrl(`/v1/flatbed/sheets/${sheets[editing]!.id}/image`) ?? ""} alt="整版布局校正" /><svg viewBox="0 0 1 1" preserveAspectRatio="none" onPointerMove={(event) => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; const target = event.currentTarget.getBoundingClientRect(); const encoded = event.currentTarget.dataset.drag?.split(":").map(Number); if (!encoded) return; const [box, point] = encoded; const x = Math.max(0, Math.min(1, (event.clientX-target.left)/target.width)); const y = Math.max(0, Math.min(1, (event.clientY-target.top)/target.height)); setDraft((current) => current.map((p,i) => i === box ? p.map((v,j) => j === point ? {x,y} : v) : p)); }} onPointerUp={(event) => { event.currentTarget.releasePointerCapture(event.pointerId); delete event.currentTarget.dataset.drag; }}>{draft.map((points, box) => <g key={box} onPointerDown={() => setSelected(box)}><polygon points={points.map((p) => `${p.x},${p.y}`).join(" ")} />{points.map((p, point) => <circle key={point} cx={p.x} cy={p.y} r=".012" onPointerDown={(event) => { event.stopPropagation(); setUndo((h) => [...h, draft.map((q) => q.map((v) => ({...v})))].slice(-30)); const svg=event.currentTarget.ownerSVGElement!; svg.dataset.drag=`${box}:${point}`; svg.setPointerCapture(event.pointerId); }} />)}</g>)}</svg></div><footer><button type="button" onClick={() => { if (!undo.length) return; setDraft(undo[undo.length-1]); setUndo((h) => h.slice(0,-1)); }} disabled={!undo.length}>撤销</button><button type="button" onClick={() => { setUndo((h) => [...h,draft]); setDraft((d) => [...d,[{x:.35,y:.35},{x:.65,y:.35},{x:.65,y:.65},{x:.35,y:.65}]]); setSelected(draft.length); }}>增加框</button><button type="button" onClick={() => { if (draft.length <= 1) return; setUndo((h) => [...h,draft]); setDraft((d) => d.filter((_,i) => i !== selected)); setSelected(0); }}>删除框</button><button type="button" onClick={() => setEditing(null)}>取消</button><button type="button" className="primary-button" onClick={() => void saveLayout()} disabled={busy}>确认并重建裁切</button></footer></div></div>}
    {lightbox && <div className="flatbed-lightbox" role="dialog" aria-modal="true" aria-label={lightbox.label} onMouseDown={(event) => { if (event.target === event.currentTarget) setLightbox(null); }}><div><header><strong>{lightbox.label}</strong><button type="button" aria-label="关闭大图" onClick={() => setLightbox(null)}>×</button></header><img src={lightbox.url} alt={lightbox.label} /></div></div>}
  </section>;
}
