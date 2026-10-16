"use client";
import React, { useEffect, useRef, useState } from "react";
import { ApiError, forgetCardHobbyAccount, loginCardHobbyAccount, verifyCardHobbyAccount } from "../lib/api";
import { CardHobbySmsVerification } from "./cardhobby-sms-verification";

export function CardHobbyAccountForm({ verification = false, onConnected }: {verification?: boolean; onConnected: () => void}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState(verification);
  const [smsReady, setSmsReady] = useState(false);
  const [verificationVersion, setVerificationVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => setChallenge(verification), [verification]);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (challenge && !smsReady) return;
    if (request.current) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      if (challenge) await verifyCardHobbyAccount(code, controller.signal);
      else await loginCardHobbyAccount(username, password, controller.signal);
      if (!controller.signal.aborted) { setUsername(""); setPassword(""); setCode(""); onConnected(); }
    } catch (reason) {
      if (!controller.signal.aborted) {
        if (reason instanceof ApiError && reason.code === "VERIFICATION_REQUIRED") { setChallenge(true); setPassword(""); }
        if (reason instanceof ApiError && ["LOGIN_REQUIRED", "LOGIN_REJECTED"].includes(reason.code ?? "")) setChallenge(false);
        if (reason instanceof ApiError && reason.code === "VERIFICATION_EXPIRED") { setSmsReady(false); setVerificationVersion(value => value + 1); }
        setCode(""); setError(reason instanceof Error ? reason.message : "登录失败，请重试");
      }
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  async function forget() {
    if (request.current) return;
    const controller = new AbortController(); request.current = controller; setBusy(true);
    try {
      await forgetCardHobbyAccount(controller.signal);
      if (!controller.signal.aborted) { setChallenge(false); setUsername(""); setPassword(""); setCode(""); setError("已移除当前用户保存的卡淘账号和登录会话。已有购入成本不受影响。"); }
    } catch (reason) { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "移除失败"); }
    finally { request.current = null; if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="cost-connection">
    <h3>{challenge ? "卡淘短信验证" : "登录并绑定卡淘账号"}</h3>
    <p>仅绑定当前产品用户。登录成功后，账号、密码和会话将在后台加密保存；下次获取成本时自动检查登录状态，失效后自动续登。无需安装扩展。</p>
    <form onSubmit={event => void submit(event)}>
      {challenge ? <><CardHobbySmsVerification key={verificationVersion} onReady={setSmsReady} onConnected={onConnected} onLoginRequired={() => { setSmsReady(false); setChallenge(false); }} disabled={busy} />
        <label>短信验证码<input name="cardhobby-code" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{4,6}" title="请输入4至6位数字短信验证码" maxLength={6} required value={code} disabled={busy} onChange={e=>setCode(e.target.value)}/></label></> : <>
        <label>卡淘用户名 / 手机号<input name="cardhobby-username" autoComplete="username" required maxLength={120} value={username} disabled={busy} onChange={e=>setUsername(e.target.value)}/></label>
        <label>卡淘密码<input name="cardhobby-password" type="password" autoComplete="current-password" required maxLength={512} value={password} disabled={busy} onChange={e=>setPassword(e.target.value)}/></label>
      </>}
      <button className="primary-button" type="submit" disabled={busy || (challenge && !smsReady)}>{busy ? "正在验证…" : challenge ? "验证并继续获取成本" : "登录、保存并继续获取成本"}</button>
      <button className="text-button" type="button" disabled={busy} onClick={()=>void forget()}>移除已保存账号 / 重新绑定</button>
    </form>
    {error && <p role="alert">{error}</p>}
  </section>;
}
