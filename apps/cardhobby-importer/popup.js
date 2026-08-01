const state = { batchId: null, drafts: [] };
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

function setStatus(message, isError = false) {
  $("#status").textContent = message;
  $("#status").style.color = isError ? "#a00020" : "#596273";
}

async function loadSettings() {
  const saved = await chrome.storage.local.get(["apiUrl", "ownerId"]);
  if (saved.apiUrl) $("#apiUrl").value = saved.apiUrl;
  if (saved.ownerId) {
    $("#ownerId").value = saved.ownerId;
    return;
  }
  const appTabs = await chrome.tabs.query({
    url: ["http://127.0.0.1:3000/*", "http://localhost:3000/*"],
  });
  if (!appTabs.length) return;
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: appTabs[0].id },
    func: () => window.localStorage.getItem("card-alpha-owner-id"),
  });
  if (result) {
    $("#ownerId").value = result;
    await chrome.storage.local.set({ ownerId: result });
  }
}

async function saveSettings() {
  await chrome.storage.local.set({
    apiUrl: $("#apiUrl").value.replace(/\/+$/, ""),
    ownerId: $("#ownerId").value.trim(),
  });
  setStatus("设置已保存。");
}

function field(label, key, value, className = "") {
  return `<label class="${className}">${label}<input data-field="${key}" value="${escapeHtml(value)}" /></label>`;
}

function renderDrafts() {
  $("#drafts").innerHTML = state.drafts.map((draft) => {
    const item = draft.proposed_item || {};
    const card = item.card || {};
    const capture = draft.capture;
    const disabled = draft.duplicate ? "disabled" : "";
    return `
      <article class="draft ${draft.duplicate ? "duplicate" : ""}" data-id="${draft.id}">
        <div class="draft-title">
          <input type="checkbox" data-select ${disabled} ${item.card ? "checked" : ""} />
          <strong>${escapeHtml(capture.title)}</strong>
        </div>
        <small>商品价 ¥${capture.sale_price ?? "?"} · 运费 ¥${capture.shipping_price}</small>
        ${draft.warnings.map((warning) => `<p class="warning">${escapeHtml(warning)}</p>`).join("")}
        <div class="fields">
          ${field("球员", "player_name", card.player_name, "wide")}
          ${field("品牌", "brand_name", card.brand_name || "Panini")}
          ${field("赛季", "season_start", card.season_start)}
          ${field("产品", "product_name", card.product_name, "wide")}
          ${field("卡号", "card_number", card.card_number)}
          ${field("Insert", "insert_name", card.insert_name)}
          ${field("平行", "parallel_name", card.parallel_name)}
          ${field("限编", "serial_total", item.serial_total)}
        </div>
      </article>`;
  }).join("");
  $("#confirm").disabled = state.drafts.every((draft) => draft.duplicate);
}

function collectConfirmation() {
  const ownerId = $("#ownerId").value.trim();
  return [...document.querySelectorAll(".draft")].flatMap((element) => {
    if (!element.querySelector("[data-select]").checked) return [];
    const value = (key) => element.querySelector(`[data-field="${key}"]`)?.value.trim() || null;
    const draft = state.drafts.find((entry) => entry.id === element.dataset.id);
    const season = Number(value("season_start"));
    if (!value("player_name") || !value("brand_name") || !season || !value("product_name")) {
      throw new Error(`“${draft.capture.title}”缺少球员、品牌、赛季或产品`);
    }
    return [{
      draft_id: draft.id,
      item: {
        owner_id: ownerId,
        card: {
          player_name: value("player_name"),
          brand_name: value("brand_name"),
          season_start: season,
          season_end: season + 1,
          product_name: value("product_name"),
          card_number: value("card_number"),
          insert_name: value("insert_name"),
          parallel_name: value("parallel_name"),
          print_run: value("serial_total") ? Number(value("serial_total")) : null,
        },
        serial_total: value("serial_total") ? Number(value("serial_total")) : null,
        notes: `卡淘商品：${draft.capture.item_url}`,
        purchase: {
          transacted_at: draft.capture.purchased_at || new Date().toISOString(),
          price: draft.capture.sale_price || "0",
          fee_amount: draft.capture.shipping_price || "0",
          currency: "CNY",
          venue: "CardHobby",
          external_order_id: draft.capture.external_order_id,
          notes: `${draft.capture.title}\n${draft.capture.item_url}`,
        },
      },
    }];
  });
}

async function scan() {
  await saveSettings();
  const ownerId = $("#ownerId").value.trim();
  if (!ownerId) throw new Error("请先填写收藏者 ID");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const captured = await chrome.tabs.sendMessage(tab.id, { type: "CARDHOBBY_SCAN_ORDERS" });
  if (captured.error) throw new Error(captured.error);
  if (!captured.orders.length) throw new Error("当前页没有识别到卡片商品，请确认已登录且订单已显示");
  setStatus(`识别到 ${captured.orders.length} 个商品，正在生成预览…`);
  const response = await fetch(`${$("#apiUrl").value}/v1/integrations/cardhobby/import-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner_id: ownerId, ...captured }),
  });
  if (!response.ok) throw new Error(`预览失败：${await response.text()}`);
  const preview = await response.json();
  state.batchId = preview.batch_id;
  state.drafts = preview.drafts;
  renderDrafts();
  setStatus(`已生成 ${preview.drafts.length} 条预览，请核对后导入。`);
}

async function confirmImport() {
  const items = collectConfirmation();
  if (!items.length) throw new Error("没有选择可导入的卡片");
  const response = await fetch(
    `${$("#apiUrl").value}/v1/integrations/cardhobby/import-batches/${state.batchId}/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner_id: $("#ownerId").value.trim(), items }),
    },
  );
  if (!response.ok) throw new Error(`导入失败：${await response.text()}`);
  const result = await response.json();
  setStatus(`成功导入 ${result.imported_item_ids.length} 张卡片。`);
  $("#confirm").disabled = true;
}

$("#saveSettings").addEventListener("click", () => saveSettings().catch((error) => setStatus(error.message, true)));
$("#scan").addEventListener("click", () => scan().catch((error) => setStatus(error.message, true)));
$("#confirm").addEventListener("click", () => confirmImport().catch((error) => setStatus(error.message, true)));
loadSettings();
