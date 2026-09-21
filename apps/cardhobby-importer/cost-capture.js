/* Order-only evidence capture. Runs in the user's signed-in page; never reads cookies,
 * storage, forms, addresses or full HTML. No requests outside CardHobby. */
(() => {
  if (globalThis.CardHobbyCostCapture) return;
  function text(node) { return (node?.textContent || "").replace(/\s+/g," ").trim(); }
  function money(value, label) {
    return value.match(new RegExp(label+"\\s*[:：]?\\s*[￥¥]\\s*(\\d+(?:\\.\\d{1,2})?)(?![\\d.])"))?.[1] ?? null;
  }
  function canonical(value) {
    const url=new URL(value,location.origin);
    if(url.protocol!=="https:" || !["cardhobby.com.cn","www.cardhobby.com.cn"].includes(url.hostname) || url.username || url.password) throw new Error("来源不属于卡淘");
    url.search="";url.hash="";return url.href;
  }
  function parseOrders(doc, sourceUrl, fetchedAt=new Date().toISOString()) {
    const orders=[];
    for(const table of doc.querySelectorAll("table")) {
      const rows=[...table.querySelectorAll("tr")].filter(row=>row.closest("table")===table);
      const header=rows.find(row=>/订单号\s*[:：]\s*\d+/.test(text(row)));
      if(!header) continue;
      const headerText=text(header);
      const external_order_id=headerText.match(/订单号\s*[:：]\s*(\d+)/)[1];
      const ordered_at=headerText.match(/\b20\d{2}-\d{2}-\d{2}\b/)?.[0];
      const seller=[...header.querySelectorAll("a")].find(a=>a.getAttribute("href")?.toLowerCase().includes("/market/") && text(a));
      const lines=[], parser_errors=[];
      let shipping=null, displayed_total=null, status="未知", source_url=canonical(sourceUrl);
      for(const row of rows) {
        const itemId=text(row).match(/商品编号\s*[:：]\s*(\d+)/)?.[1];
        if(!itemId) continue;
        const anchors=[...row.querySelectorAll('a[href]')].filter(a=>/\/market\/item\/\d+\/?$/i.test(new URL(a.getAttribute('href'),sourceUrl).pathname));
        const anchor=anchors.find(a=>text(a)) || anchors[0];
        const priceCell=[...row.cells].find(cell=>/[￥¥]\s*\d+(?:\.\d{1,2})?\s*[xX×]\s*\d+/.test(text(cell)));
        const unit=text(priceCell).match(/[￥¥]\s*(\d+(?:\.\d{1,2})?)\s*[xX×]\s*(\d+)/);
        const title=anchor?.getAttribute('title')?.trim() || text(anchor);
        if(!anchor || !unit || !title) {parser_errors.push('商品行字段不完整');continue;}
        const image_urls=[...row.querySelectorAll('img')].flatMap(img=>{
          const source=img.getAttribute('src') || img.getAttribute('data-src');
          if(!source) return [];
          const url=new URL(source,sourceUrl);
          if(url.protocol!=='https:' || url.hostname!=='images.cardhobby.com') return [];
          url.search='';url.hash='';return [url.href];
        });
        lines.push({item_id:itemId,title,item_url:canonical(new URL(anchor.getAttribute('href'),sourceUrl).href),image_urls:[...new Set(image_urls)],unit_price:unit[1],quantity:Number(unit[2])});
        for(const cell of row.cells) {
          const value=text(cell);
          // Only cells containing totals/status, never item title promotional language.
          if(!cell.querySelector('a[href*="/market/item/"]') && /含运费/.test(value)) {
            shipping=money(value,'含运费');displayed_total=money(value,'总额');
          }
          const detail=[...cell.querySelectorAll('a')].find(a=>text(a)==='订单详情');
          if(detail) {
            source_url=canonical(new URL(detail.getAttribute('href'),sourceUrl).href);
            status=value.match(/退款中|已关闭|待支付|等待卖家发货|等待买家收货|待发货|待收货|待评价|已完成/)?.[0] ||
              ([...cell.querySelectorAll('button,input')].some(button=>text(button)==='确认收货' || button.value==='确认收货') ? '待收货' : '未知');
          }
        }
      }
      if(!lines.length) continue;
      if(!ordered_at || !seller) parser_errors.push('订单日期或商家缺失');
      if(new Set(lines.map(line=>line.item_id)).size!==lines.length) parser_errors.push('重复商品行');
      orders.push({external_order_id,ordered_at:ordered_at || null,seller:text(seller) || '未知',status,source_url,fetched_at:fetchedAt,
        currency:'CNY',shipping,displayed_total,paid_total:null,discount:null,refund:null,extra_fee:null,
        complete:!parser_errors.length,parser_errors,lines});
    }
    return orders;
  }
  function detailAmounts(doc) {
    // Only read leaf text blocks labelled as amounts. Excludes buyer/address containers.
    const values=[...doc.querySelectorAll('li,p,td,div')].map(text).filter(value=>value.length<100);
    const find=label=>values.map(value=>money(value,label)).find(value=>value!==null) ?? null;
    return {paid_total:find('实付总额'),discount:find('优惠(?:金额)?'),refund:find('退款(?:金额)?'),extra_fee:find('额外费用')};
  }
  async function exportEvidence(maxPages=20, signal, progress=()=>{}) {
    if(!/^\/order\/myorders\/?$/i.test(location.pathname)) throw new Error('请先打开已登录的卡淘购买订单页');
    const seen=new Set(), orders=new Map(); let url=location.href, doc=document, pages=0;
    while(url && pages<maxPages) {
      if(signal?.aborted) throw new Error('已取消；未发送或保存订单');
      if(seen.has(url)) break;
      seen.add(url); pages++;
      const captures=parseOrders(doc,url);
      if(!captures.length) throw new Error('未读到订单：请确认登录与页面结构，未猜测空页为结束');
      for(const capture of captures) {
        if(signal?.aborted) throw new Error('已取消');
        if(/\/order\/detail\/\d+/i.test(capture.source_url)) {
          try {
            const response=await fetch(capture.source_url,{credentials:'same-origin',signal:signal ? AbortSignal.any([signal,AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000)});
            if(!response.ok) throw new Error('detail');
            const detail=new DOMParser().parseFromString(await response.text(),'text/html');
            Object.assign(capture,detailAmounts(detail));
            if(capture.paid_total===null) capture.parser_errors.push('详情实付金额未核实');
          } catch(error) {
            if(signal?.aborted) throw new Error('已取消');
            capture.parser_errors.push('订单详情读取失败，重新导出可重试');
          }
        }
        orders.set(capture.external_order_id,capture);
        progress(`正在读取第 ${pages} 页，已读取 ${orders.size} 个订单`);
      }
      progress(`已读取 ${pages} 页 / ${orders.size} 个订单`);
      const next=[...doc.querySelectorAll('a[href]')].find(a=>['»','下一页','›'].includes(text(a)));
      if(!next) {url=null;break;}
      const nextUrl=new URL(next.getAttribute('href'),url);
      if(nextUrl.origin!==location.origin || !/^\/order\/myorders\/?$/i.test(nextUrl.pathname)) throw new Error('分页链接异常');
      url=nextUrl.href;
      if(pages>=maxPages || seen.has(url)) break;
      const response=await fetch(url,{credentials:'same-origin',signal:signal ? AbortSignal.any([signal,AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000)});
      if(!response.ok) throw new Error('分页读取失败，可重新导出');
      doc=new DOMParser().parseFromString(await response.text(),'text/html');
    }
    return {orders:[...orders.values()],pages,truncated:!!url,next_url:url};
  }
  globalThis.CardHobbyCostCapture={parseOrders,detailAmounts,exportEvidence};
  if(typeof chrome!=='undefined' && chrome.runtime?.onMessage) chrome.runtime.onMessage.addListener((message,_sender,respond)=>{
    if(message?.type!=='CARDHOBBY_COST_EXPORT') return;
    if(document.getElementById('card-platform-cost-export')) {respond({ok:true});return;}
    const panel=document.createElement('section');panel.id='card-platform-cost-export';
    panel.style.cssText='position:fixed;right:20px;top:60px;z-index:2147483647;padding:18px;background:white;border:2px solid #881133;max-width:360px;box-shadow:0 4px 20px #888;color:#222';
    const label=document.createElement('label');label.textContent='最多读取页数（1–20）：';
    const input=document.createElement('input');input.type='number';input.min='1';input.max='20';input.value='5';label.append(input);
    const start=document.createElement('button');start.textContent='导出成本订单证据';
    const cancel=document.createElement('button');cancel.textContent='取消／关闭';
    const status=document.createElement('p');status.textContent='仅保存订单字段，不包含 Cookie、密码、收件地址。JSON 含私人购买记录，请妥善保管。';
    let controller;
    start.onclick=async()=>{
      const max=Number(input.value);if(!Number.isInteger(max)||max<1||max>20) {status.textContent='请输入 1–20';return;}
      controller=new AbortController();start.disabled=true;
      try {
        const capture=await exportEvidence(max,controller.signal,value=>status.textContent=value);
        const blob=new Blob([JSON.stringify(capture)],{type:'application/json'}),href=URL.createObjectURL(blob);
        const a=document.createElement('a');a.href=href;a.download='cardhobby-cost-evidence.json';a.click();URL.revokeObjectURL(href);
        status.textContent=`已导出 ${capture.orders.length} 个订单。${capture.truncated?'达到页数上限；后续请从下一页继续。':'分页读取完成。'}在 Card Platform 成本窗口选择文件即可同步。`;
      } catch(error) {status.textContent=error.message || '读取失败';}
      finally {start.disabled=false;}
    };
    cancel.onclick=()=>{controller?.abort();panel.remove();};
    panel.append(label,start,cancel,status);document.body.append(panel);respond({ok:true});
  });
})();
