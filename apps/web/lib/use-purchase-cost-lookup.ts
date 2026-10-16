"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getCostSearchPlan, type CostSearchResult } from "./api";
import { CardHobbyConnectionError, CardHobbyLoginError, searchCardHobbyOrders, resolveCostSearchWithFallback } from "./cardhobby-cost-search";

export type CostLookupState = {
  status: "loading" | "needs_confirmation" | "connection_required" | "login_required" | "recorded" | "not_found" | "incomplete" | "unmatched" | "skipped" | "error";
  message: string;
  result?: CostSearchResult;
  label?: string;
  loginCode?: string;
};

type LookupSession = {
  ownerId: string | null;
  active: boolean;
  controllers: Map<string, AbortController>;
  pending: Set<string>;
  queue: Promise<void>;
};

const completedStatuses = new Set(["recorded", "not_found", "incomplete", "unmatched", "skipped"]);

export function usePurchaseCostLookup(ownerId: string | null, onRecorded: (itemId: string, result: CostSearchResult) => void) {
  const [snapshot, setSnapshot] = useState<{ownerId: string | null; lookups: Record<string, CostLookupState>}>({ownerId, lookups: {}});
  const ownerRef = useRef(ownerId);
  const recordedRef = useRef(onRecorded);
  const sessionRef = useRef<LookupSession | null>(null);
  ownerRef.current = ownerId;
  recordedRef.current = onRecorded;

  useEffect(() => {
    const session: LookupSession = {ownerId, active: true, controllers: new Map(), pending: new Set(), queue: Promise.resolve()};
    sessionRef.current = session;
    setSnapshot({ownerId, lookups: {}});
    return () => {
      session.active = false;
      session.controllers.forEach(controller => controller.abort());
      session.controllers.clear();
      session.pending.clear();
      if (sessionRef.current === session) sessionRef.current = null;
    };
  }, [ownerId]);

  const start = useCallback((itemId: string) => {
    const session = sessionRef.current;
    if (!itemId || !session?.active || !session.ownerId || session.ownerId !== ownerRef.current || session.pending.has(itemId)) return;
    const controller = new AbortController();
    session.controllers.set(itemId, controller);
    session.pending.add(itemId);
    const current = () => session.active && sessionRef.current === session && ownerRef.current === session.ownerId && session.controllers.get(itemId) === controller && !controller.signal.aborted;
    const update = (value: CostLookupState) => {
      if (!current()) return;
      setSnapshot(previous => {
        if (!current()) return previous;
        return {ownerId: session.ownerId, lookups: {...(previous.ownerId === session.ownerId ? previous.lookups : {}), [itemId]: value}};
      });
    };
    update({status: "loading", message: "成本获取中…"});

    // Serialize account order reads. Every clicked row
    // shows pending immediately, while its whole lookup waits for this queue.
    session.queue = session.queue.then(async () => {
      if (!current()) return;
      let label: string | undefined;
      try {
        const plan = await getCostSearchPlan(itemId, controller.signal);
        if (!current()) return;
        label = plan.label;
        if (plan.status === "skipped") {
          update({status: "skipped", message: plan.reason, label});
          return;
        }
        update({status: "loading", message: "正在搜索卡淘购买订单…", label});
        const capture = await searchCardHobbyOrders(plan.keywords, controller.signal);
        if (!current()) return;
        update({status: "loading", message: "正在核对卡价与运费…", label});
        const received = await resolveCostSearchWithFallback(itemId, capture, plan, controller.signal);
        if (!current()) return;
        const eligible = received.candidates.filter(candidate => candidate.automatic);
        const status: CostLookupState["status"] = received.status === "needs_confirmation"
          ? (eligible.length > 1 ? "needs_confirmation" : "unmatched")
          : completedStatuses.has(received.status) ? received.status as CostLookupState["status"] : "error";
        const result = {...received, status, candidates: received.status === "needs_confirmation" ? eligible : received.candidates};
        const message = status === "recorded"
          ? (result.recorded ? `${result.recorded.match_method === "inferred" ? "已推测匹配，请核对订单快照；购入成本" : "已自动填入购入成本"} ¥${result.recorded.total_cost}` : "购入成本已录入")
          : result.reason || (status === "needs_confirmation" ? "发现相同卡片的多笔购买记录，请选择对应订单" : "本次未获取到购入成本");
        update({status, message, result, label});
        if (status === "recorded" && current()) {
          // A list refresh may fail while the successful write remains valid.
          try { Promise.resolve(recordedRef.current(itemId, result)).catch(() => {}); } catch { /* Keep the recorded result available to the row. */ }
        }
      } catch (error) {
        if (!current()) return;
        update({status: error instanceof CardHobbyLoginError ? "login_required" : error instanceof CardHobbyConnectionError ? "connection_required" : "error", message: error instanceof Error ? error.message : "成本获取失败，请重试", label, loginCode: error instanceof CardHobbyLoginError ? error.code : undefined});
      } finally {
        if (session.controllers.get(itemId) === controller) session.pending.delete(itemId);
      }
    });
  }, []);

  const clear = useCallback((itemId: string) => {
    const session = sessionRef.current;
    if (!session?.active || session.ownerId !== ownerRef.current) return;
    session.controllers.get(itemId)?.abort();
    session.controllers.delete(itemId);
    session.pending.delete(itemId);
    setSnapshot(previous => {
      if (!session.active || sessionRef.current !== session || session.ownerId !== ownerRef.current || previous.ownerId !== session.ownerId) return previous;
      const lookups = {...previous.lookups};
      delete lookups[itemId];
      return {...previous, lookups};
    });
  }, []);

  return {lookups: snapshot.ownerId === ownerId ? snapshot.lookups : {}, start, clear};
}
