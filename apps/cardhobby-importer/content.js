function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function parseMoney(text, labels) {
  for (const label of labels) {
    const pattern = new RegExp(`${label}\\s*[：:]?\\s*(?:¥|￥)?\\s*(\\d+(?:\\.\\d{1,2})?)`, "i");
    const match = text.match(pattern);
    if (match) return match[1];
  }
  return null;
}

function parseSalePrice(text) {
  const labeled = parseMoney(text, ["成交价", "商品金额", "实付款", "价格"]);
  if (labeled) return labeled;
  const unitPrice = text.match(
    /(?:¥|￥)\s*(\d+(?:\.\d{1,2})?)\s*[xX×]\s*\d+/,
  );
  if (unitPrice) return unitPrice[1];
  const total = parseMoney(text, ["总额", "合计"]);
  const shipping = parseMoney(text, ["含运费", "运费", "邮费"]);
  if (!total) return null;
  return Math.max(0, Number(total) - Number(shipping || 0)).toFixed(2);
}

function parseDate(text) {
  const match = text.match(/\b(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
  if (!match) return null;
  const [, year, month, day, hour = "0", minute = "0", second = "0"] = match;
  return new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
  ).toISOString();
}

function orderContainer(anchor) {
  return (
    anchor.closest("[data-order-id]") ||
    anchor.closest(".order-item") ||
    anchor.closest(".order-info") ||
    anchor.closest("tr") ||
    anchor.closest("li") ||
    anchor.parentElement
  );
}

function scanOrders() {
  if (location.pathname.toLowerCase() !== "/order/myorders") {
    return { error: "请先打开卡淘“我买到的”订单页面。" };
  }
  const anchors = [...document.querySelectorAll('a[href*="/market/item/"]')];
  const found = new Map();
  for (const anchor of anchors) {
    const url = new URL(anchor.href, location.origin);
    const itemMatch = url.pathname.match(/^\/market\/item\/(\d+)\/?$/i);
    if (!itemMatch) continue;
    const itemId = itemMatch[1];
    const container = orderContainer(anchor);
    const rawText = cleanText(container?.innerText || anchor.innerText);
    const title =
      cleanText(anchor.getAttribute("title")) ||
      cleanText(anchor.innerText) ||
      cleanText(container?.querySelector("[title]")?.getAttribute("title"));
    if (!title) continue;
    const orderMatch = rawText.match(/(?:订单号|订单编号|Order\s*ID)\s*[：:]?\s*([A-Za-z0-9_-]+)/i);
    const images = [...(container?.querySelectorAll("img") || [])]
      .map((image) => image.currentSrc || image.src)
      .filter((src) => /^https:\/\/images\.cardhobby\.com\//i.test(src))
      .slice(0, 10);
    found.set(itemId, {
      external_order_id: orderMatch?.[1] || null,
      cardhobby_item_id: itemId,
      item_url: `${url.origin}${url.pathname}`,
      title,
      image_urls: [...new Set(images)],
      purchased_at: parseDate(rawText),
      sale_price: parseSalePrice(rawText),
      shipping_price: parseMoney(rawText, ["运费", "邮费"]) || "0",
      currency: "CNY",
      raw_text: rawText.slice(0, 10000),
    });
  }
  return {
    source_url: location.href,
    orders: [...found.values()],
    page_title: document.title,
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "CARDHOBBY_SCAN_ORDERS") return;
  try {
    sendResponse(scanOrders());
  } catch (error) {
    sendResponse({ error: error instanceof Error ? error.message : String(error) });
  }
});
