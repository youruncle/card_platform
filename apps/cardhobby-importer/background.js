// Only the local Card Platform UI can initiate an order search. Never accepts a
// caller-supplied destination, Cookie, script, or arbitrary HTTP operation.
const localOrigins = new Set(['http://127.0.0.1:3000', 'http://localhost:3000']);
const requests = new Map();
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  let origin;
  try { origin = new URL(sender.url).origin; } catch { return; }
  if (!sender.tab || !localOrigins.has(origin) || !message || typeof message.requestId !== 'string' || !/^[a-f0-9-]{36}$/.test(message.requestId)) return;
  const key = `${sender.tab.id}:${message.requestId}`;
  if (message.type === 'CARD_PLATFORM_COST_CANCEL') {
    const tabId = requests.get(key);
    if (tabId !== undefined) void chrome.tabs.sendMessage(tabId, {type:'CARDHOBBY_SEARCH_CANCEL', requestId:key}).catch(() => {});
    return;
  }
  if (message.type === 'CARD_PLATFORM_COST_PING') { respond({version:'0.5.0'}); return; }
  if (message.type !== 'CARD_PLATFORM_COST_SEARCH') return;
  (async () => {
    const tabs = await chrome.tabs.query({url:['https://cardhobby.com.cn/*','https://www.cardhobby.com.cn/*']});
    const tab = tabs.find(tab => /\/order\/myorders/i.test(tab.url)) || tabs[0];
    if (!tab) throw new Error('请先在此 Chrome 打开并登录卡淘订单页，然后重试');
    requests.set(key, tab.id);
    // Injecting also handles tabs opened before an extension update.
    await chrome.scripting.executeScript({target:{tabId:tab.id}, files:['cost-capture.js','cost-search.js']});
    return chrome.tabs.sendMessage(tab.id, {type:'CARDHOBBY_SEARCH_ORDERS', requestId:key, keywords:message.keywords});
  })().then(respond, () => respond({error:'无法连接卡淘页面，请打开并登录卡淘订单页后重试'}))
    .finally(() => requests.delete(key));
  return true;
});
