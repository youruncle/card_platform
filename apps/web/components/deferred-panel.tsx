"use client";

import { Component, type ComponentType, type CSSProperties, type ReactNode, useMemo, useState } from "react";

const noticeStyle: CSSProperties = {
  position: "fixed", right: 20, bottom: 20, zIndex: 100,
  maxWidth: "min(420px, calc(100vw - 40px))", padding: 20,
  border: "1px solid var(--line)", borderRadius: 16,
  background: "var(--panel)", color: "var(--text)",
  boxShadow: "0 8px 32px #243b3320",
};

export function DeferredPanelLoading() {
  return <p style={noticeStyle} role="status">正在加载功能面板…</p>;
}

class PanelBoundary extends Component<
  { children: ReactNode; onRetry: () => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <p style={noticeStyle} role="alert">
        功能面板加载失败，卡库仍可继续浏览。
        <button className="text-button" onClick={this.props.onRetry}>重新加载面板</button>
      </p>;
    }
    return this.props.children;
  }
}

/** Recreate React.lazy on retry; a rejected lazy promise cannot retry itself. */
export function deferredPanel<Props extends object>(createPanel: () => ComponentType<Props>) {
  return function DeferredPanel(props: Props) {
    const [attempt, setAttempt] = useState(0);
    const Panel = useMemo(createPanel, [attempt]);
    return <PanelBoundary key={attempt} onRetry={() => setAttempt(value => value + 1)}>
      <Panel {...props} />
    </PanelBoundary>;
  };
}
