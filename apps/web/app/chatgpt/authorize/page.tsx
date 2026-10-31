"use client";

import { useCallback, useEffect, useState } from "react";
import { getCurrentUser, loginUser, type AuthUser } from "../../../lib/api";
import { chatgptRequest, scopeLabels } from "../../../lib/chatgpt";

type Consent = { client_name: string; scopes: string[]; resource: string; csrf_token: string };

export default function ChatgptAuthorizePage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [requestId, setRequestId] = useState("");
  const [consent, setConsent] = useState<Consent | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (id: string) => {
    try {
      setUser(await getCurrentUser());
      setConsent(await chatgptRequest<Consent>(`/authorizations/${encodeURIComponent(id)}`));
      setMessage("");
    } catch (error) { setMessage(error instanceof Error ? error.message : "请先登录"); }
  }, []);

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("request_id") ?? "";
    setRequestId(id);
    if (/^[0-9a-f-]{36}$/i.test(id)) void load(id);
    else setMessage("授权链接无效，请从 ChatGPT 重新连接卡牌管家。");
  }, [load]);

  async function approve() {
    if (!consent) return;
    setBusy(true);
    try {
      const result = await chatgptRequest<{ redirect_url: string }>(
        `/authorizations/${encodeURIComponent(requestId)}/approve`,
        { method: "POST", body: JSON.stringify({ csrf_token: consent.csrf_token }) },
      );
      window.location.assign(result.redirect_url);
    } catch (error) { setMessage(error instanceof Error ? error.message : "授权失败"); }
    finally { setBusy(false); }
  }

  return <main style={{ maxWidth: 560, margin: "64px auto", padding: 24 }}>
    <h1>连接 ChatGPT 卡牌管家</h1>
    {message && <p role="alert">{message}</p>}
    {!user && <form onSubmit={async event => {
      event.preventDefault(); setBusy(true);
      try {
        await loginUser({ email, password }); setPassword(""); await load(requestId);
      } catch (error) { setMessage(error instanceof Error ? error.message : "登录失败"); }
      finally { setBusy(false); }
    }}>
      <p>使用现有卡牌平台账户登录并核对授权。</p>
      <p><label>邮箱 <input type="email" autoComplete="username" required value={email}
        onChange={event => setEmail(event.target.value)} /></label></p>
      <p><label>密码 <input type="password" autoComplete="current-password" required value={password}
        onChange={event => setPassword(event.target.value)} /></label></p>
      <button disabled={busy || !requestId}>登录</button>
    </form>}
    {user && consent && <>
      <p>{consent.client_name} 请求访问 {user.display_name} 的云端卡库。</p>
      <ul>{consent.scopes.map(scope => <li key={scope}>{scopeLabels[scope] ?? scope}</li>)}</ul>
      <p>你可以在<a href="/chatgpt/connections">连接管理</a>撤销连接。录卡、收藏修改及记账会先显示具体内容，另行确认后才保存。</p>
      <button disabled={busy} onClick={() => void approve()}>同意并返回 ChatGPT</button>
    </>}
    <p><a href="/">取消并返回卡库</a></p>
  </main>;
}
