import React, { useEffect, useRef, useState } from "react";
import { ApiError, MobileApi } from "./api";
import { serverOrigin } from "./server";
import { sessionVault } from "./session";
import type { Session } from "./types";

export function Login({
  onLogin,
  message,
}: {
  onLogin: (session: Session) => void;
  message: string;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [accountLocks, setAccountLocks] = useState<Record<string, number>>({});
  const [now, setNow] = useState(Date.now());
  const pending = useRef(false);
  const activeApi = useRef<MobileApi | null>(null);
  const normalizedEmail = email.trim().toLowerCase();
  const lockedUntil = accountLocks[normalizedEmail] ?? 0;
  const remaining = Math.max(0, Math.ceil((lockedUntil - now) / 1000));
  useEffect(() => () => activeApi.current?.dispose(), []);
  useEffect(() => {
    if (!Object.keys(accountLocks).length) return;
    const timer = setInterval(() => {
      const timestamp = Date.now();
      setNow(timestamp);
      setAccountLocks((current) => {
        const active = Object.entries(current).filter(([, until]) => until > timestamp);
        return active.length === Object.keys(current).length
          ? current
          : Object.fromEntries(active);
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [accountLocks]);
  return (
    <main className="login">
      <p className="eyebrow">CARD PLATFORM</p>
      <div className="brand-mark">◇</div>
      <h1>
        每一张卡，<br />都有它的故事。
      </h1>
      <p className="muted">随身收藏 · 拍照识别 · 证据估值</p>
      <form
        className="panel"
        aria-busy={busy}
        onSubmit={async (event) => {
          event.preventDefault();
          if (pending.current || Date.now() < lockedUntil) return;
          pending.current = true;
          setBusy(true);
          setError("");
          const origin = serverOrigin();
          const api = new MobileApi(origin, "", () => {});
          activeApi.current = api;
          try {
            const response = await api.post<Omit<Session, "origin">>(
              "/v1/mobile/auth/login",
              { email: normalizedEmail, password },
            );
            const session = { ...response, origin };
            await sessionVault.save(session);
            setPassword("");
            onLogin(session);
          } catch (failure) {
            if (failure instanceof ApiError && failure.status === 429) {
              const timestamp = Date.now();
              setNow(timestamp);
              setAccountLocks((current) => ({
                ...current,
                [normalizedEmail]: timestamp + (failure.retryAfter ?? 60) * 1000,
              }));
              setError("");
            } else {
              setError(
                failure instanceof Error
                  ? failure.message
                  : "无法连接云端，请检查网络后重试。",
              );
            }
          } finally {
            api.dispose();
            activeApi.current = null;
            pending.current = false;
            setBusy(false);
          }
        }}
      >
        <h2>登录你的卡库</h2>
        <p className="small muted">使用已有云端账户的邮箱和密码登录。</p>
        <label htmlFor="login-username">用户名（邮箱）</label>
        <input
          id="login-username"
          name="username"
          type="email"
          inputMode="email"
          required
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          disabled={busy}
        />
        <label htmlFor="login-password" className="login-password-label">
          密码
        </label>
        <div className="login-password-field">
          <input
            id="login-password"
            name="password"
            type={visible ? "text" : "password"}
            required
            autoComplete="current-password"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
          />
          <button
            type="button"
            aria-controls="login-password"
            aria-label={visible ? "隐藏密码" : "显示密码"}
            aria-pressed={visible}
            onClick={() => setVisible((value) => !value)}
          >
            {visible ? "隐藏" : "显示"}
          </button>
        </div>
        {(remaining > 0 || error || message) && (
          <p className="error" role="alert">
            {remaining > 0
              ? `登录尝试过多，账户已暂时锁定，请在 ${Math.floor(remaining / 60)} 分 ${remaining % 60} 秒后重试。`
              : error || message}
          </p>
        )}
        <button className="primary wide" disabled={busy || remaining > 0}>
          {busy ? "正在登录…" : remaining > 0 ? "请稍后重试" : "登录"}
        </button>
        <p className="small muted">安全连接 cardsp.cn 云端卡库</p>
      </form>
    </main>
  );
}
