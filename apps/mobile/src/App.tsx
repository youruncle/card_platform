import React, { useEffect, useMemo, useState } from "react";
import { ApiError, MobileApi } from "./api";
import { Login } from "./login";
import { serverOrigin } from "./server";
import { sessionVault } from "./session";
import type { Session, User } from "./types";
import { DraftStore } from "./drafts";
import { LibraryPage, DetailPage } from "./LibraryPage";
import { CapturePage } from "./CapturePage";
import { KnowledgePage } from "./KnowledgePage";
import { loadCapabilities } from "./capabilities";
import { type ClientCapabilities } from "../../../packages/card-core/src/capture";
import { JobsPage } from "./JobsPage";
import { PlayerPricesPage } from "./PlayerPricesPage";

function AccountPage({
  api,
  user,
  onLogout,
}: {
  api: MobileApi;
  user: User;
  onLogout: () => Promise<void>;
}) {
  const [knowledge, setKnowledge] = useState(false);
  const [prices, setPrices] = useState(false);
  const [sessions, setSessions] = useState<
      {
        id: string;
        current: boolean;
        user_agent: string;
        last_seen_at: string;
      }[]
    >([]),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .get<typeof sessions>("/v1/auth/sessions")
      .then((v) => {
        if (active) setSessions(v);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api]);
  if (prices)
    return <PlayerPricesPage api={api} onBack={() => setPrices(false)} />;
  if (knowledge)
    return <KnowledgePage api={api} onBack={() => setKnowledge(false)} />;
  return (
    <>
      <h1>我的</h1>
      <button className="wide" onClick={() => setPrices(true)}>
        球员卡价涨跌
      </button>
      <button className="wide" onClick={() => setKnowledge(true)}>
        知识查询
      </button>
      <section className="panel">
        <h2>{user.display_name}</h2>
        <p>{user.email}</p>
        <p className="muted">基准币种 {user.base_currency}</p>
      </section>
      <section className="panel">
        <h2>登录设备</h2>
        {sessions.map((session) => (
          <div className="session-row" key={session.id}>
            <span>
              {session.current
                ? "当前设备"
                : session.user_agent?.slice(0, 65) || "其他设备"}
              <small>
                {new Date(session.last_seen_at).toLocaleString("zh-CN")}
              </small>
            </span>
            {!session.current && (
              <button
                onClick={async () => {
                  try {
                    await api.request(`/v1/auth/sessions/${session.id}`, {
                      method: "DELETE",
                    });
                    setSessions((list) =>
                      list.filter((s) => s.id !== session.id),
                    );
                  } catch (e) {
                    setError(e instanceof Error ? e.message : "撤销失败");
                  }
                }}
              >
                撤销
              </button>
            )}
          </div>
        ))}
      </section>
      {error && <p className="error">{error}</p>}
      <button className="wide" onClick={() => void onLogout()}>
        退出登录
      </button>
      <p className="muted small">
        退出时清除当前设备上的登录信息和未上传草稿。
      </p>
    </>
  );
}
function Workspace({
  session,
  onExit,
}: {
  session: Session;
  onExit: (message?: string) => void;
}) {
  const [tab, setTab] = useState("library"),
    [detail, setDetail] = useState<string | null>(null);
  const store = useMemo(
    () => new DraftStore(session.origin, session.user.id),
    [session.origin, session.user.id],
  );
  const api = useMemo(
    () =>
      new MobileApi(session.origin, session.token, () => {
        void sessionVault.clear().catch(() => {});
        onExit("登录已过期，请重新登录");
      }),
    [session, onExit],
  );
  useEffect(
    () => () => {
      api.dispose();
      store.dispose();
    },
    [api, store],
  );
  const [capabilities, setCapabilities] = useState<ClientCapabilities | null>(
    null,
  );
  const [capabilityError, setCapabilityError] = useState("");
  const [capabilityRetry, setCapabilityRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setCapabilityError("");
    setCapabilities(null);
    loadCapabilities(api)
      .then((v) => {
        if (active) setCapabilities(v);
      })
      .catch((e) => {
        if (active) setCapabilityError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api, capabilityRetry]);
  const open = (id: string) => {
    setDetail(id);
    setTab("library");
  };
  async function logout() {
    let message = "";
    try {
      await api.post("/v1/auth/logout");
    } catch {
      message = "设备已退出；云端会话撤销未确认，可从其他设备撤销。";
    } finally {
      api.dispose();
      try {
        await Promise.all([store.clear(), sessionVault.clear()]);
      } catch {
        message = "退出时清理设备存储失败，请重试或清除应用数据。";
      }
      onExit(message);
    }
  }
  return (
    <div className="app-shell">
      <header>
        <span className="wordmark">◇ CARD PLATFORM</span>
        <span className="private-label">私人卡库</span>
      </header>
      <main>
        {capabilities && <div hidden={tab !== "library" || !!detail}>
          <LibraryPage api={api} onOpen={open} capabilities={capabilities} active={tab === "library" && !detail} />
        </div>}
        {capabilityError && (
          <p role="alert" className="error">
            读取服务器功能失败：{capabilityError}{" "}
            <button onClick={() => setCapabilityRetry((n) => n + 1)}>
              重新连接
            </button>
          </p>
        )}
        {!capabilities ? (
          tab === "account" ? (
            <AccountPage api={api} user={session.user} onLogout={logout} />
          ) : (
            <p>
              {capabilityError
                ? "连接恢复后点击重新连接。"
                : "正在读取服务器功能…"}
            </p>
          )
        ) : detail ? (
          <DetailPage
            key={detail}
            api={api}
            id={detail}
            onBack={() => setDetail(null)}
            capabilities={capabilities}
            onSupplement={async (side) => {
              const target = await api.get<{ revision: string }>(
                `/v1/capture/items/${detail}`,
              );
              const current = await store.loadAll();
              const newDraft = {
                requestId: crypto.randomUUID(),
                targetItemId: detail,
                expectedRevision: target.revision,
                missingSide: side,
                singleSide: true,
              };
              await store.saveAll(
                [newDraft, ...current],
                current
                  .filter((d) => d.front || d.back)
                  .map((d) => d.requestId),
              );
              setDetail(null);
              setTab("capture");
            }}
          />
        ) : tab === "library" ? (
          null
        ) : tab === "capture" ? (
          <CapturePage
            api={api}
            store={store}
            capabilities={capabilities}
            onSubmitted={() => setTab("jobs")}
          />
        ) : tab === "jobs" ? (
          <JobsPage
            api={api}
            user={session.user}
            onOpen={open}
            capabilities={capabilities}
          />
        ) : (
          <AccountPage api={api} user={session.user} onLogout={logout} />
        )}
      </main>
      <nav aria-label="主导航">
        {[
          ["library", "▦", "卡库"],
          ["capture", "＋", "录卡"],
          ["jobs", "◷", "任务"],
          ["account", "○", "我的"],
        ].map(([key, icon, label]) => (
          <button
            key={key}
            className={tab === key ? "active" : ""}
            aria-current={tab === key ? "page" : undefined}
            onClick={() => {
              setDetail(null);
              setTab(key);
            }}
          >
            <span>{icon}</span>
            {label}
          </button>
        ))}
      </nav>
    </div>
  );
}
export default function App() {
  const [session, setSession] = useState<Session | null>(null),
    [ready, setReady] = useState(false),
    [message, setMessage] = useState("");
  const exit = React.useCallback((text = "") => {
    setSession(null);
    setMessage(text);
  }, []);
  useEffect(() => {
    let active = true;
    let api: MobileApi | undefined;
    sessionVault
      .load()
      .then(async (stored) => {
        if (stored) {
          if (stored.origin !== serverOrigin()) {
            await sessionVault.clear();
            if (active) setMessage("云端服务已固定为 cardsp.cn，请重新登录。");
            return;
          }
          api = new MobileApi(stored.origin, stored.token, () => {
            void sessionVault.clear().catch(() => {});
          });
          const user = await api.get<User>("/v1/auth/me");
          if (active) setSession({ ...stored, user });
        }
      })
      .catch((e) => {
        if (active)
          setMessage(
            e instanceof ApiError && e.status === 401
              ? "登录已过期，请重新登录。"
              : "无法恢复登录，请检查网络后重新登录。",
          );
      })
      .finally(() => {
        api?.dispose();
        if (active) setReady(true);
      });
    return () => {
      active = false;
      api?.dispose();
    };
  }, []);
  return !ready ? (
    <main className="login">
      <h1>正在打开卡库…</h1>
    </main>
  ) : session ? (
    <Workspace
      key={`${session.origin}:${session.user.id}`}
      session={session}
      onExit={exit}
    />
  ) : (
    <Login onLogin={setSession} message={message} />
  );
}
