"use client";
import React, {useEffect, useRef, useState} from "react";
import {getAutomaticCostAttention} from "../lib/api";
import {watchAutomaticCostAttention, createAutomaticCostNoticeGate, type AutomaticCostAttention} from "../../../packages/card-core/src/automatic-cost-attention";
import {CardHobbyAccountForm} from "./cardhobby-account-form";

export function AutomaticCostNotice({ownerId}: {ownerId: string}) {
  return <OwnerNotice key={ownerId} />;
}

function OwnerNotice() {
  const [notice, setNotice] = useState<AutomaticCostAttention | null>(null);
  const [open, setOpen] = useState(false);
  const shouldAnnounce = useRef(createAutomaticCostNoticeGate());
  const watcher = useRef<ReturnType<typeof watchAutomaticCostAttention> | null>(null);
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const value = watchAutomaticCostAttention(getAutomaticCostAttention, next => {
      setNotice(next);
      if (!next.blocked_count) setOpen(false);
      else if (shouldAnnounce.current(next)) setOpen(true);
    });
    watcher.current = value;
    return () => { value.dispose(); watcher.current = null; };
  }, []);
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, [open]);
  if (!notice?.blocked_count) return null;
  return <>
    {!open && <aside className="automatic-cost-reminder" role="status">
      <p>{notice.blocked_count} 张卡的自动成本获取需要登录或验证卡淘。</p>
      <button onClick={() => setOpen(true)}>登录或验证卡淘</button>
    </aside>}
    {open && <div className="modal-backdrop"><section ref={dialog} tabIndex={-1} className="modal cost-modal" role="dialog" aria-modal="true" aria-labelledby="automatic-cost-account-title" onKeyDown={event => {if (event.key === "Escape") setOpen(false);}}>
      <div className="modal-heading"><h2 id="automatic-cost-account-title">自动获取成本需要{notice.code === "VERIFICATION_REQUIRED" ? "验证" : "登录"}卡淘</h2><button className="text-button" onClick={() => setOpen(false)}>稍后处理</button></div>
      <p>{notice.blocked_count} 张卡的买入成本因卡淘账号状态而暂停。完成登录或验证后会继续自动获取；已有成本保持不变。</p>
      <CardHobbyAccountForm verification={notice.code === "VERIFICATION_REQUIRED"} onConnected={() => {setOpen(false); void watcher.current?.refresh();}} />
    </section></div>}
  </>;
}
