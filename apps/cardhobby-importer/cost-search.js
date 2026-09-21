/* Uses the same GET search as MyOrders' advanced keyword form. Authentication
 * stays in the signed-in CardHobby tab; only parsed order evidence is returned. */
(() => {
  if (globalThis.CardHobbyCostSearch) return;
  const origin = location.origin;
  const allowed = new Set(['https://cardhobby.com.cn', 'https://www.cardhobby.com.cn']);
  function searchUrl(keyword, now = new Date()) {
    if (!allowed.has(origin)) throw new Error('请在卡淘页面连接');
    const url = new URL('/Order/MyOrders', origin);
    url.search = new URLSearchParams({status:'0', code:'', idcardName:'', searchTxt:keyword,
      Order_Tag:'', beginDate:'2000-01-01 00:00:00', endDate:`${now.getFullYear()}-12-31 23:59:59`});
    return url.href;
  }
  function safeUrl(value, kind) {
    const url = new URL(value, origin);
    const pattern = kind === 'detail' ? /^\/order\/detail\/\d+\/?$/i : /^\/order\/myorders\/?$/i;
    if (url.origin !== origin || url.username || url.password || !pattern.test(url.pathname)) throw new Error('卡淘返回了异常订单链接');
    return url.href;
  }
  async function search(keywords, {signal, maxPages=10, maxOrders=100} = {}) {
    if (!Array.isArray(keywords) || !keywords.length || keywords.length > 3 || keywords.some(k => typeof k !== 'string' || !k.trim() || k.length > 120)) throw new Error('搜索关键字无效');
    const orders = new Map(), seen = new Set();
    let pages = 0, truncated = false;
    async function read(url, kind) {
      safeUrl(url, kind);
      signal?.throwIfAborted();
      const response = await fetch(url, {credentials:'same-origin', signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000)});
      if (response.status === 401 || response.status === 403) throw new Error('卡淘登录已过期，请在卡淘订单页重新登录后重试');
      if (!response.ok) throw new Error('卡淘订单读取失败，请稍后重试');
      if (response.url) {
        try { safeUrl(response.url, kind); } catch { throw new Error('请先在此 Chrome 登录卡淘，再重试获取成本'); }
      }
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (doc.querySelector('input[type="password"]')) throw new Error('请先在此 Chrome 登录卡淘，再重试获取成本');
      return doc;
    }
    for (const keyword of [...new Set(keywords.map(k => k.trim()))]) {
      let url = searchUrl(keyword);
      while (url) {
        if (pages >= maxPages || orders.size >= maxOrders) { truncated = true; break; }
        if (seen.has(url)) throw new Error('卡淘订单分页异常，自动搜索未完成');
        seen.add(url);
        const doc = await read(url, 'list'); pages++;
        // A recognized empty search is different from a login/error response.
        if (!doc.querySelector('input[placeholder="商品编号/商品名称"]')) throw new Error('未识别到卡淘订单搜索页，请登录卡淘或稍后重试');
        const captures = CardHobbyCostCapture.parseOrders(doc, url);
        const headers = [...doc.querySelectorAll('tr')].filter(row => /订单号\s*[:：]\s*\d+/.test(row.textContent));
        if (headers.length !== captures.length) throw new Error('部分订单未完整读取，未自动填入成本');
        for (const capture of captures) {
          if (orders.has(capture.external_order_id)) continue;
          if (orders.size >= maxOrders) { truncated = true; break; }
          const detail = await read(safeUrl(capture.source_url, 'detail'), 'detail');
          Object.assign(capture, CardHobbyCostCapture.detailAmounts(detail));
          if (capture.paid_total === null) capture.parser_errors.push('详情实付金额未核实');
          orders.set(capture.external_order_id, capture);
        }
        const next = [...doc.querySelectorAll('a[href]')].find(a => ['»','下一页','›'].includes(a.textContent.trim()) && !a.closest('.disabled'));
        url = next ? safeUrl(new URL(next.getAttribute('href'), url).href, 'list') : null;
        if (url && new URL(url).searchParams.get('searchTxt') !== keyword) throw new Error('卡淘分页丢失关键字，未继续读取');
        if (truncated) break;
      }
      if (truncated) break;
    }
    return {orders:[...orders.values()], complete:!truncated, keywords, pages};
  }
  globalThis.CardHobbyCostSearch = {searchUrl, search};
  const requests = new Map();
  if (typeof chrome !== 'undefined' && chrome.runtime?.onMessage) chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type === 'CARDHOBBY_SEARCH_CANCEL') { requests.get(message.requestId)?.abort(); return; }
    if (message?.type !== 'CARDHOBBY_SEARCH_ORDERS') return;
    if (requests.has(message.requestId) || requests.size) { respond({error:'已有卡淘搜索正在进行，请稍后重试'}); return; }
    const controller = new AbortController(); requests.set(message.requestId, controller);
    search(message.keywords, {signal:AbortSignal.any([controller.signal, AbortSignal.timeout(150000)])})
      .then(result => respond({result}), error => respond({error:error.name === 'AbortError' ? '已取消搜索' : error.message}))
      .finally(() => requests.delete(message.requestId));
    return true;
  });
})();
