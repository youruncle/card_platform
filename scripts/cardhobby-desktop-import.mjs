#!/usr/bin/env node

import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(SCRIPT_DIR, "..");
const CONTROL_HOST = "127.0.0.1";
const CONTROL_PORT = Number(process.env.CARDHOBBY_HELPER_PORT || 8765);
const DEBUG_PORT = Number(process.env.CARDHOBBY_DEBUG_PORT || 9223);
const API_URL = (process.env.CARD_PLATFORM_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const ORDER_URL = "https://www.cardhobby.com.cn/order/myorders?status=0";
const PROFILE_DIR = resolve(
  process.env.CARDHOBBY_PROFILE_DIR ||
    join(PROJECT_ROOT, "data", "cardhobby-browser-profile"),
);
const SCANNER_PATH = join(
  PROJECT_ROOT,
  "apps",
  "cardhobby-importer",
  "content.js",
);
const ALLOWED_ORIGINS = new Set([
  "http://127.0.0.1:3000",
  "http://localhost:3000",
]);

const chromeCandidates = [
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
];

function json(response, status, payload, origin = null) {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  };
  if (origin && ALLOWED_ORIGINS.has(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers.Vary = "Origin";
  }
  response.writeHead(status, {
    ...headers,
  });
  response.end(JSON.stringify(payload));
}

async function body(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function chromePath() {
  const configured = process.env.CARDHOBBY_CHROME_PATH;
  if (configured && existsSync(configured)) return configured;
  return chromeCandidates.find(existsSync);
}

function chromeAppName(executable) {
  if (executable.includes("Google Chrome Beta.app")) return "Google Chrome Beta";
  if (executable.includes("Chromium.app")) return "Chromium";
  return "Google Chrome";
}

async function debuggingAvailable() {
  try {
    const response = await fetch(`http://${CONTROL_HOST}:${DEBUG_PORT}/json/version`);
    return response.ok;
  } catch {
    return false;
  }
}

async function launchCardHobby() {
  const executable = chromePath();
  if (!executable) throw new Error("未找到 Google Chrome 或 Chromium");
  if (!(await debuggingAvailable())) {
    const child = spawn(
      "open",
      [
        "-na",
        chromeAppName(executable),
        "--args",
        `--user-data-dir=${PROFILE_DIR}`,
        `--remote-debugging-port=${DEBUG_PORT}`,
        `--remote-debugging-address=${CONTROL_HOST}`,
        "--no-first-run",
        "--no-default-browser-check",
        `--app=${ORDER_URL}`,
      ],
      { detached: true, stdio: "ignore" },
    );
    child.unref();
  }
  return {
    order_url: ORDER_URL,
    profile_dir: PROFILE_DIR,
    reused_session: await debuggingAvailable(),
  };
}

async function targets() {
  const response = await fetch(`http://${CONTROL_HOST}:${DEBUG_PORT}/json/list`);
  if (!response.ok) throw new Error("独立浏览器尚未就绪");
  return response.json();
}

async function cardHobbyTarget() {
  const pages = await targets();
  const page = pages.find(
    (entry) =>
      entry.type === "page" &&
      /^https:\/\/www\.cardhobby\.com\.cn\//i.test(entry.url || ""),
  );
  if (!page) throw new Error("没有找到卡淘窗口，请先点击“打开卡淘登录窗口”");
  return page;
}

async function cdpEvaluate(webSocketUrl, expression) {
  const socket = new WebSocket(webSocketUrl);
  await new Promise((resolvePromise, reject) => {
    socket.addEventListener("open", resolvePromise, { once: true });
    socket.addEventListener("error", () => reject(new Error("无法连接独立浏览器")), {
      once: true,
    });
  });
  const id = 1;
  socket.send(
    JSON.stringify({
      id,
      method: "Runtime.evaluate",
      params: { expression, returnByValue: true, awaitPromise: true },
    }),
  );
  const message = await new Promise((resolvePromise, reject) => {
    const timer = setTimeout(() => reject(new Error("读取卡淘页面超时")), 15000);
    socket.addEventListener("message", (event) => {
      const parsed = JSON.parse(String(event.data));
      if (parsed.id !== id) return;
      clearTimeout(timer);
      resolvePromise(parsed);
    });
  });
  socket.close();
  if (message.error) throw new Error(message.error.message);
  if (message.result?.exceptionDetails) {
    throw new Error(message.result.exceptionDetails.text || "订单页面解析失败");
  }
  return message.result?.result?.value;
}

function scannerExpression() {
  const source = readFileSync(SCANNER_PATH, "utf8");
  const listenerIndex = source.indexOf("chrome.runtime.onMessage");
  const scanner = listenerIndex >= 0 ? source.slice(0, listenerIndex) : source;
  return `(() => { ${scanner}; return scanOrders(); })()`;
}

async function scan(ownerId) {
  if (!ownerId) throw new Error("请填写收藏者 ID");
  const target = await cardHobbyTarget();
  if (!/\/order\/myorders/i.test(target.url || "")) {
    throw new Error("请在独立窗口完成登录并进入“我买到的”订单页");
  }
  const captured = await cdpEvaluate(target.webSocketDebuggerUrl, scannerExpression());
  if (captured?.error) throw new Error(captured.error);
  if (!captured?.orders?.length) {
    throw new Error("当前订单页没有识别到卡片，请确认订单列表已经加载");
  }
  const response = await fetch(`${API_URL}/v1/integrations/cardhobby/import-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner_id: ownerId, ...captured }),
  });
  if (!response.ok) throw new Error(`生成预览失败：${await response.text()}`);
  return response.json();
}

async function confirm(batchId, payload) {
  const response = await fetch(
    `${API_URL}/v1/integrations/cardhobby/import-batches/${batchId}/confirm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) throw new Error(`确认导入失败：${await response.text()}`);
  return response.json();
}

const CONTROL_HTML = String.raw`<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Card Platform · 卡淘桌面导入</title>
<style>
:root{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#17202a;background:#f4f6f8}
body{max-width:1100px;margin:0 auto;padding:28px}h1{margin:0 0 4px}.sub{color:#667085;margin:0 0 24px}
.panel,.draft{background:#fff;border:1px solid #dde2e8;border-radius:12px;padding:16px;margin:12px 0}
.toolbar{display:flex;gap:10px;align-items:end;flex-wrap:wrap}label{display:grid;gap:5px;color:#667085}
input{padding:8px;border:1px solid #cbd2da;border-radius:7px;min-width:180px}button{padding:9px 14px;border:0;border-radius:8px;background:#991f32;color:#fff;cursor:pointer}
button:disabled{opacity:.45}.secondary{background:#344054}.status{color:#667085}.error{color:#b42318}
.title{display:flex;gap:10px;align-items:start}.title strong{flex:1}.fields{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:12px}.wide{grid-column:span 2}
.warning{color:#a15c00}.duplicate{opacity:.55}@media(max-width:760px){.fields{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<h1>卡淘桌面导入助手</h1>
<p class="sub">卡淘账号、密码和 Cookie 只保存在独立的本机 Chrome 配置中。</p>
<section class="panel toolbar">
  <label>收藏者 ID<input id="owner" placeholder="Card Platform UUID"></label>
  <button id="launch" class="secondary">打开卡淘登录窗口</button>
  <button id="scan">扫描当前订单页</button>
  <button id="confirm" disabled>导入已选卡片</button>
</section>
<p id="status" class="status">先打开独立窗口并完成卡淘登录。</p>
<main id="drafts"></main>
<script>
const state={batchId:null,drafts:[]};const $=s=>document.querySelector(s);
const esc=v=>String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#39;");
const status=(m,e=false)=>{$("#status").textContent=m;$("#status").className=e?"error":"status"};
const field=(l,k,v,c="")=>'<label class="'+c+'">'+l+'<input data-field="'+k+'" value="'+esc(v||"")+'"></label>';
async function request(path,payload){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload||{})});const data=await r.json();if(!r.ok)throw new Error(data.error||"请求失败");return data}
function render(){$("#drafts").innerHTML=state.drafts.map(d=>{const item=d.proposed_item||{},card=item.card||{},cap=d.capture;return '<article class="draft '+(d.duplicate?"duplicate":"")+'" data-id="'+d.id+'"><div class="title"><input type="checkbox" data-select '+(d.duplicate?"disabled":"")+' '+(item.card?"checked":"")+'><strong>'+esc(cap.title)+'</strong></div><small>商品价 ¥'+esc(cap.sale_price??"?")+' · 运费 ¥'+esc(cap.shipping_price)+'</small>'+d.warnings.map(w=>'<p class="warning">'+esc(w)+'</p>').join("")+'<div class="fields">'+field("球员","player_name",card.player_name,"wide")+field("品牌","brand_name",card.brand_name||"Panini")+field("赛季","season_start",card.season_start)+field("产品","product_name",card.product_name,"wide")+field("卡号","card_number",card.card_number)+field("Insert","insert_name",card.insert_name)+field("平行","parallel_name",card.parallel_name)+field("限编","serial_total",item.serial_total)+'</div></article>'}).join("");$("#confirm").disabled=state.drafts.every(d=>d.duplicate)}
function collect(){const owner=$("#owner").value.trim();return [...document.querySelectorAll(".draft")].flatMap(el=>{if(!el.querySelector("[data-select]").checked)return[];const v=k=>el.querySelector('[data-field="'+k+'"]').value.trim()||null,d=state.drafts.find(x=>x.id===el.dataset.id),season=Number(v("season_start"));if(!v("player_name")||!v("brand_name")||!season||!v("product_name"))throw new Error("存在未补齐的必填身份字段");return[{draft_id:d.id,item:{owner_id:owner,card:{player_name:v("player_name"),brand_name:v("brand_name"),season_start:season,season_end:season+1,product_name:v("product_name"),card_number:v("card_number"),insert_name:v("insert_name"),parallel_name:v("parallel_name"),print_run:v("serial_total")?Number(v("serial_total")):null},serial_total:v("serial_total")?Number(v("serial_total")):null,notes:"卡淘商品："+d.capture.item_url,purchase:{transacted_at:d.capture.purchased_at||new Date().toISOString(),price:d.capture.sale_price||"0",fee_amount:d.capture.shipping_price||"0",currency:"CNY",venue:"CardHobby",external_order_id:d.capture.external_order_id,notes:d.capture.title+"\\n"+d.capture.item_url}}}]})}
$("#owner").value=localStorage.getItem("card-platform-owner-id")||"";
$("#launch").onclick=()=>request("/launch").then(()=>status("独立窗口已打开，请登录后进入“我买到的”。")).catch(e=>status(e.message,true));
$("#scan").onclick=async()=>{try{const owner=$("#owner").value.trim();if(!owner)throw new Error("请填写收藏者 ID");localStorage.setItem("card-platform-owner-id",owner);status("正在读取订单并生成预览…");const p=await request("/scan",{owner_id:owner});state.batchId=p.batch_id;state.drafts=p.drafts;render();status("已生成 "+p.drafts.length+" 条预览。")}catch(e){status(e.message,true)}};
$("#confirm").onclick=async()=>{try{const items=collect();if(!items.length)throw new Error("没有选择卡片");const result=await request("/confirm",{batch_id:state.batchId,owner_id:$("#owner").value.trim(),items});status("成功导入 "+result.imported_item_ids.length+" 张卡片。");$("#confirm").disabled=true}catch(e){status(e.message,true)}};
</script>
</body></html>`;

const server = createServer(async (request, response) => {
  try {
    const origin = request.headers.origin || null;
    if (request.method === "OPTIONS") {
      if (!origin || !ALLOWED_ORIGINS.has(origin)) {
        json(response, 403, { error: "origin not allowed" });
        return;
      }
      response.writeHead(204, {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        Vary: "Origin",
      });
      response.end();
      return;
    }
    if (origin && !ALLOWED_ORIGINS.has(origin)) {
      json(response, 403, { error: "origin not allowed" });
      return;
    }
    if (request.method === "GET" && request.url === "/") {
      json(response, 200, {
        status: "ready",
        message: "请在 Card Platform 主页面使用卡淘导入功能",
      }, origin);
      return;
    }
    if (request.method === "POST" && request.url === "/launch") {
      json(response, 200, await launchCardHobby(), origin);
      return;
    }
    if (request.method === "POST" && request.url === "/scan") {
      const payload = await body(request);
      json(response, 200, await scan(payload.owner_id), origin);
      return;
    }
    if (request.method === "POST" && request.url === "/confirm") {
      const payload = await body(request);
      json(
        response,
        200,
        await confirm(payload.batch_id, {
          owner_id: payload.owner_id,
          items: payload.items,
        }),
        origin,
      );
      return;
    }
    json(response, 404, { error: "not found" }, origin);
  } catch (error) {
    json(response, 400, {
      error: error instanceof Error ? error.message : String(error),
    }, request.headers.origin || null);
  }
});

server.listen(CONTROL_PORT, CONTROL_HOST, () => {
  const controlUrl = `http://${CONTROL_HOST}:${CONTROL_PORT}`;
  console.log(`卡淘桌面导入桥接服务：${controlUrl}`);
  console.log(`独立会话目录：${PROFILE_DIR}`);
});
