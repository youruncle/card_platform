"use client";
import React, { useEffect, useRef, useState } from "react";
import { ApiError, CardHobbyVerification, getCardHobbyVerification, restartCardHobbyVerification, sendCardHobbySms } from "../lib/api";

export function CardHobbySmsVerification({ onReady, onConnected, onLoginRequired, disabled = false }: {onReady: (ready: boolean) => void; onConnected?: () => void; onLoginRequired?: () => void; disabled?: boolean}) {
  const [details, setDetails] = useState<CardHobbyVerification | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const request = useRef<AbortController | null>(null);
  const callback = useRef(onReady);
  callback.current = onReady;

  function showError(reason: unknown) {
    setError(reason instanceof Error ? reason.message : "无法读取验证状态，请重试");
    if (reason instanceof ApiError && reason.code === "VERIFICATION_EXPIRED") {
      setExpired(true); setDetails(null); setSeconds(0); setOpen(false); callback.current(false);
    }
    if (reason instanceof ApiError && ["LOGIN_REQUIRED", "LOGIN_REJECTED"].includes(reason.code ?? "")) onLoginRequired?.();
  }

  function apply(value: CardHobbyVerification) {
    const url = new URL(value.captcha_url);
    if (url.origin !== "https://cms.cardhobby.com.cn" || url.pathname !== "/checkcode/index") throw new Error("卡淘验证地址异常，请重新登录");
    setDetails(value); setSeconds(value.retry_after_seconds); setExpired(false);
    callback.current(value.sms_sent === true);
  }

  async function load(restart = false) {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      if (restart) {
        try {
          await restartCardHobbyVerification(controller.signal);
          if (!controller.signal.aborted) onConnected?.();
          return;
        } catch (reason) {
          if (!(reason instanceof ApiError) || reason.code !== "VERIFICATION_REQUIRED") throw reason;
        }
      }
      const value = await getCardHobbyVerification(controller.signal);
      if (!controller.signal.aborted) apply(value);
    } catch (reason) {
      if (!controller.signal.aborted) showError(reason);
    } finally {
      if (request.current === controller) request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    callback.current(false);
    void load();
    return () => { request.current?.abort(); request.current = null; };
  }, []);
  useEffect(() => {
    if (seconds <= 0) return;
    const timer = window.setTimeout(() => setSeconds(value => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [seconds]);

  useEffect(() => {
    if (!open || disabled) return;
    async function receive(event: MessageEvent) {
      if (event.origin !== "https://cms.cardhobby.com.cn" || !frame.current?.contentWindow || event.source !== frame.current.contentWindow || event.data !== "checkcode" || request.current) return;
      const controller = new AbortController(); request.current = controller;
      setOpen(false); setBusy(true); setError(""); callback.current(false);
      setDetails(value => value ? {...value, sms_sent: false} : value);
      try {
        const value = await sendCardHobbySms(controller.signal);
        if (!controller.signal.aborted) apply(value);
      } catch (reason) {
        if (!controller.signal.aborted) {
          showError(reason);
          // A failed/ambiguous POST also starts the server cooldown. Do not retry it.
          try {
            const value = await getCardHobbyVerification(controller.signal);
            if (!controller.signal.aborted) apply(value);
          } catch (statusError) {
            if (!controller.signal.aborted) {
              if (statusError instanceof ApiError && ["VERIFICATION_EXPIRED", "LOGIN_REQUIRED"].includes(statusError.code ?? "")) showError(statusError);
              else setSeconds(60);
            }
          }
        }
      } finally {
        if (request.current === controller) request.current = null;
        if (!controller.signal.aborted) setBusy(false);
      }
    }
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [open, disabled]);

  return <div className="cardhobby-sms-verification">
    <p role="status">{busy ? "正在确认短信状态…" : expired ? "验证已过期，可以使用已输入的账号重新发起验证。" : details?.sms_sent ? "卡淘已确认发送短信，请查看手机。" : "尚未确认发送短信，请先完成卡淘滑块验证。"}</p>
    <button type="button" className="text-button" disabled={busy || disabled || seconds > 0} onClick={() => {
      if (!details) { void load(expired); return; }
      setError(""); setOpen(true);
    }}>{seconds > 0 ? `${seconds}秒后可重新获取` : expired ? "重新发起验证" : !details ? "重新读取验证状态" : details.sms_sent ? "重新获取短信验证码" : "获取短信验证码"}</button>
    {open && details && <div>
      <p>请手动完成下方卡淘官方滑块，完成后将请求发送短信。</p>
      <iframe ref={frame} title="卡淘官方滑块验证" src={details.captcha_url} sandbox="allow-scripts allow-same-origin" referrerPolicy="no-referrer" style={{width:"100%", maxWidth:420, height:320, border:0}} />
      <button type="button" className="text-button" onClick={() => setOpen(false)}>取消滑块验证</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
