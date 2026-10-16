"use client";

import React, {useState} from "react";

export function HoldingStatusControl({status = "held", onChange}: {
  status?: "held" | "sold";
  onChange: (status: "held" | "sold") => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function change() {
    setBusy(true);
    setError("");
    try { await onChange(status === "sold" ? "held" : "sold"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "状态保存失败"); }
    finally { setBusy(false); }
  }
  return <div className="holding-status-control">
    <span className="tag muted">{status === "sold" ? "已卖出" : "持有"}</span>
    <button type="button" className="text-button" disabled={busy} onClick={() => void change()}>
      {busy ? "保存中…" : status === "sold" ? "恢复持有" : "标记为已卖出"}
    </button>
    {error && <small role="alert">{error}</small>}
  </div>;
}
