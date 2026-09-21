import { ApiError, getCardHobbyAccountStatus, searchSavedCardHobbyOrders } from "./api";
export type OrderSearchCapture = {orders: unknown[]; complete: boolean; keywords?: string[]; pages?: number};
export class CardHobbyConnectionError extends Error {}
export class CardHobbyLoginError extends Error {
  constructor(message: string, public code = "LOGIN_REQUIRED") { super(message); }
}
function bridgeError(value: Record<string, unknown>): Error {
  const message = String(value.error);
  // Also recognize v0.3.0 responses: installed connectors can outlive a web deploy.
  if (value.code === 'AUTH_REQUIRED' || /^(请先在此 Chrome 登录卡淘|卡淘登录已过期)/.test(message)) {
    return new CardHobbyLoginError('卡淘登录已过期，请在此 Chrome 重新登录卡淘，再点击获取成本。本次未修改已有成本。');
  }
  if (value.code === 'CONNECTION_REQUIRED' || /^(卡淘连接已更新|无法连接卡淘页面|请先在此 Chrome 打开并登录卡淘订单页)/.test(message)) {
    return new CardHobbyConnectionError(message);
  }
  return new Error(message);
}
function callBridge(type: string, keywords: string[], signal?: AbortSignal): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const requestId = crypto.randomUUID();
    const ping = type === 'CARD_PLATFORM_COST_PING';
    const finish = (error?: Error, value?: Record<string, unknown>) => {
      window.clearTimeout(timer);
      window.removeEventListener('message', receive);
      signal?.removeEventListener('abort', cancel);
      if (error) reject(error); else resolve(value ?? {});
    };
    const cancel = () => {
      window.postMessage({type:'CARD_PLATFORM_COST_CANCEL', requestId}, window.location.origin);
      finish(new DOMException('已取消搜索', 'AbortError'));
    };
    const receive = (event: MessageEvent) => {
      if (event.source !== window || event.origin !== window.location.origin || event.data?.type !== 'CARD_PLATFORM_COST_RESPONSE' || event.data.requestId !== requestId) return;
      if (event.data.error) finish(bridgeError(event.data));
      else finish(undefined, event.data);
    };
    const timer = window.setTimeout(() => {
      window.postMessage({type:'CARD_PLATFORM_COST_CANCEL', requestId}, window.location.origin);
      finish(ping ? new CardHobbyConnectionError('首次使用请连接卡淘；连接后无需再导出或上传订单文件。') : new Error('卡淘自动搜索暂时未完成，本次未填写成本'));
    }, ping ? 1500 : 160000);
    window.addEventListener('message', receive);
    signal?.addEventListener('abort', cancel, {once:true});
    if (signal?.aborted) { cancel(); return; }
    window.postMessage({type, requestId, keywords}, window.location.origin);
  });
}
export async function connectLegacyCostSearch(signal?: AbortSignal) {
  return callBridge('CARD_PLATFORM_COST_PING', [], signal);
}
export async function searchLegacyCardHobbyOrders(keywords: string[], signal?: AbortSignal): Promise<OrderSearchCapture> {
  await connectLegacyCostSearch(signal);
  const value = await callBridge('CARD_PLATFORM_COST_SEARCH', keywords, signal);
  const result = value.result as OrderSearchCapture;
  if (!result || !Array.isArray(result.orders) || typeof result.complete !== 'boolean') throw new Error('卡淘未返回有效搜索结果，请重试');
  return {orders:result.orders, complete:result.complete};
}

export async function connectCostSearch(signal?: AbortSignal) {
  const state = await getCardHobbyAccountStatus(signal);
  if (state.state === "verification_required") throw new CardHobbyLoginError("卡淘要求短信验证，请完成验证后继续", "VERIFICATION_REQUIRED");
  if (!state.saved || state.state === "login_required") throw new CardHobbyLoginError("请登录卡淘。登录成功后会加密保存账号，并自动继续获取成本。");
  return state;
}
export async function searchCardHobbyOrders(keywords: string[], signal?: AbortSignal): Promise<OrderSearchCapture> {
  try {
    // The backend checks the actual session and automatically reauthenticates with saved credentials.
    const result = await searchSavedCardHobbyOrders(keywords, signal);
    if (!result || !Array.isArray(result.orders) || typeof result.complete !== "boolean") throw new Error("卡淘未返回有效搜索结果，请重试");
    return {orders:result.orders, complete:result.complete};
  } catch (error) {
    if (error instanceof ApiError && ["LOGIN_REQUIRED", "LOGIN_REJECTED", "VERIFICATION_REQUIRED"].includes(error.code ?? "")) throw new CardHobbyLoginError(error.message, error.code);
    throw error;
  }
}
