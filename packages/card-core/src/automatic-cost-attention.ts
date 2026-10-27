export type AutomaticCostAttention = {
  blocked_count: number;
  notice_key: string | null;
  latest_blocked_at?: string | null;
  code: string | null;
  worker_enabled: boolean;
};

/** A shrinking backlog is not a new failure, even if its newest member disappears. */
export function createAutomaticCostNoticeGate() {
  let latest = -Infinity;
  let codes = new Set<string>();
  const legacyKeys = new Set<string>();
  return (value: AutomaticCostAttention) => {
    if (!value.blocked_count || !value.notice_key) return false;
    const timestamp = Date.parse(value.latest_blocked_at ?? "");
    if (!Number.isFinite(timestamp)) {
      if (legacyKeys.has(value.notice_key)) return false;
      legacyKeys.add(value.notice_key); return true;
    }
    if (timestamp < latest) return false;
    if (timestamp > latest) {latest = timestamp; codes = new Set();}
    const code = value.code ?? "LOGIN_REQUIRED";
    if (codes.has(code)) return false;
    codes.add(code); return true;
  };
}

/** One read-only request at a time; background failures never initiate login/SMS. */
export function watchAutomaticCostAttention(
  read: (signal: AbortSignal) => Promise<AutomaticCostAttention>,
  receive: (value: AutomaticCostAttention) => void,
) {
  const controller = new AbortController();
  let busy = false;
  async function refresh() {
    if (controller.signal.aborted || busy) return;
    busy = true;
    try {
      const value = await read(controller.signal);
      if (!controller.signal.aborted && value && Number.isInteger(value.blocked_count)
        && value.blocked_count >= 0 && (value.blocked_count === 0 || typeof value.notice_key === "string")) receive(value);
    } catch {
      // Keep an existing actionable reminder through transient network errors.
      // Authentication failures are handled by the client's authenticated transport.
    } finally { busy = false; }
  }
  const focus = () => { void refresh(); };
  const visible = () => { if (!document.hidden) void refresh(); };
  const timer = window.setInterval(visible, 15000);
  window.addEventListener("focus", focus);
  document.addEventListener("visibilitychange", visible);
  void refresh();
  return {
    refresh,
    dispose() {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", visible);
    },
  };
}
