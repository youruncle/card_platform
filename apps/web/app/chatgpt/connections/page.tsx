"use client";

import { useEffect, useState } from "react";
import { chatgptRequest, scopeLabels } from "../../../lib/chatgpt";

type Connection = { id: string; scopes: string[]; created_at: string; expires_at: string; revoked_at: string | null };
const when = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
export default function ConnectionsPage() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const load = () => chatgptRequest<Connection[]>("/connections").then(setConnections);
  useEffect(() => { load().catch(reason => setError(String(reason))); }, []);
  return <main style={{ maxWidth: 760, padding: 24, margin: "40px auto" }}>
    <h1>ChatGPT 连接管理</h1>
    <p>只显示当前平台账户的连接。撤销后，该连接的工具和私有图片访问立即失效；已保存的卡片、交易和研究记录保留。恢复访问需要重新授权。</p>
    {error && <p role="alert">{error}</p>}
    {!connections.length && !error && <p>暂无连接。</p>}
    {connections.map(connection => <section key={connection.id} style={{ borderBottom: "1px solid #888", padding: "16px 0" }}>
      <p>连接编号：{connection.id}</p>
      <p>创建：{when(connection.created_at)}；到期：{when(connection.expires_at)}（北京时间）</p>
      <ul>{connection.scopes.map(scope => <li key={scope}>{scopeLabels[scope] ?? scope}</li>)}</ul>
      {connection.revoked_at ? <p>已于 {when(connection.revoked_at)} 撤销。</p> : <button disabled={busy !== null} onClick={async () => {
        setBusy(connection.id); setError("");
        try { await chatgptRequest(`/connections/${encodeURIComponent(connection.id)}/revoke`, { method: "POST", body: "{}" }); await load(); }
        catch (reason) { setError(String(reason)); } finally { setBusy(null); }
      }}>撤销此连接</button>}
    </section>)}
    <p><a href="/">返回卡库</a></p>
  </main>;
}
