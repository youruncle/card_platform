(() => {
  if (globalThis.cardPlatformCostBridge) return;
  globalThis.cardPlatformCostBridge = true;
  const origin = location.origin;
  const pending = new Set();
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== origin) return;
    const message = event.data;
    if (!['CARD_PLATFORM_COST_PING','CARD_PLATFORM_COST_SEARCH','CARD_PLATFORM_COST_CANCEL'].includes(message?.type)) return;
    if (typeof message.requestId !== 'string' || !/^[a-f0-9-]{36}$/.test(message.requestId)) return;
    if (message.type === 'CARD_PLATFORM_COST_CANCEL') {
      if (pending.has(message.requestId)) chrome.runtime.sendMessage(message).catch(() => {});
      return;
    }
    if (pending.has(message.requestId)) return;
    pending.add(message.requestId);
    chrome.runtime.sendMessage(message).then(
      result => window.postMessage({type:'CARD_PLATFORM_COST_RESPONSE', requestId:message.requestId, ...result}, origin),
      () => window.postMessage({type:'CARD_PLATFORM_COST_RESPONSE', requestId:message.requestId, error:'卡淘连接已更新，请刷新卡库页面后重试'}, origin),
    ).finally(() => pending.delete(message.requestId));
  });
})();
