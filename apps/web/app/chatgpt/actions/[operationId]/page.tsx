"use client";

import { use, useEffect, useState } from "react";
import { chatgptRequest } from "../../../../lib/chatgpt";

type Operation = {
  operation_id: string; kind: string; status: string; payload: Record<string, unknown>;
  payload_hash: string; expires_at: string; result: unknown;
};

const labels: Record<string, string> = {
  import: "确认录入收藏", update: "校正收藏信息", transaction: "记录买卖或费用",
  trash: "移入回收站", restore: "恢复收藏", publish_research: "发布成交研究结果",
};

const fieldLabels: Record<string, string> = {
  target: "当前卡片", changes: "本次修改", identity: "校正后的身份", corrected_fields: "录入信息", card: "卡片身份",
  item_id: "实卡编号", job_id: "识别任务编号", display_id: "卡库编号", player_name: "球员", brand_name: "品牌",
  product_name: "产品", season_start: "起始赛季", season_end: "结束赛季", set_name: "系列", card_number: "卡号",
  insert_name: "特卡", parallel_name: "平行版本", is_rookie: "新秀卡", is_autograph: "签字卡", autograph_type: "签字类型",
  is_memorabilia: "含实物", is_patch: "Patch", memorabilia_types: "实物类型", memorabilia_usage: "实物使用情况",
  memorabilia_player: "实物所属球员", has_inscription: "含题字", inscription_text: "题字", is_numbered: "限量编号",
  serial_number: "实卡编号分子", serial_total: "限量总数", grade_company: "评级公司", grade_value: "评级分数",
  grade_cert_number: "评级证书编号", strategy_tags: "标签", holding_status: "持有状态", notes: "备注", reason: "修改依据",
  quantity_held: "账面持仓数量", remaining_cost: "剩余成本", realized_profit: "已实现盈亏", purchase: "采购记录",
  transaction: "本次交易", transaction_type: "交易类型", transacted_at: "交易时间", quantity: "数量", unit_price: "单价",
  price: "采购单价", fee_amount: "费用金额", fee_direction: "费用承担方", currency: "币种", fx_rate_to_base: "折算到账户币种的汇率",
  fx_basis: "汇率依据", venue: "交易渠道", action: "操作", purchase_price: "原采购单价", purchase_currency: "原采购币种",
};
const valueLabels: Record<string, string> = {
  held: "持有", sold: "已售出", buy: "买入", sell: "卖出", fee: "独立费用", owner_paid: "本人承担",
  counterparty_paid: "对方承担（不计入本人费用）", none: "无费用", trash: "移入回收站（保留交易记录）", restore: "恢复收藏",
};
function PreviewFields({ values }: { values: Record<string, unknown> }) {
  return <dl>{Object.entries(values).filter(([key]) => fieldLabels[key]).map(([key, value]) => <div key={key} style={{ marginBottom: 10 }}>
    <dt style={{ fontWeight: 600 }}>{fieldLabels[key]}</dt><dd style={{ marginLeft: 16, overflowWrap: "anywhere" }}>
      {value === null ? "未填写" : typeof value === "boolean" ? (value ? "是" : "否") : Array.isArray(value) ?
        (value.length ? value.map(String).join("、") : "无") : typeof value === "object" ?
        <PreviewFields values={value as Record<string, unknown>} /> : valueLabels[String(value)] ?? String(value)}
    </dd>
  </div>)}</dl>;
}

export default function OperationPage({ params }: { params: Promise<{ operationId: string }> }) {
  const { operationId } = use(params);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pairChecked, setPairChecked] = useState(false);
  useEffect(() => {
    chatgptRequest<Operation>(`/operations/${encodeURIComponent(operationId)}`)
      .then(setOperation).catch(reason => setError(reason instanceof Error ? reason.message : "加载失败"));
  }, [operationId]);

  return <main style={{ maxWidth: 680, margin: "48px auto", padding: 24 }}>
    <h1>{labels[operation?.kind ?? ""] ?? "核对卡牌操作"}</h1>
    {error && <p role="alert">{error}</p>}
    {!operation && <p>请先在<a href="/">卡牌平台</a>登录，再打开此确认链接。</p>}
    {operation && <>
      <p>操作编号：{operation.operation_id}</p>
      {Array.isArray(operation.payload.preview_images) && <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {(operation.payload.preview_images as { side: string; url: string }[]).map(image =>
          /^\/v1\/capture\/jobs\/[0-9a-f-]+\/images\/(front|back)\?revision=\d+$/.test(image.url) &&
          <figure key={image.side} style={{ margin: 0 }}>
            <img src={image.url} alt={image.side === "front" ? "待确认正面" : "待确认背面"}
              style={{ maxWidth: "100%", width: 240, maxHeight: 360, objectFit: "contain" }} />
            <figcaption>{image.side === "front" ? "正面" : "背面"}</figcaption>
          </figure>)}
      </div>}
      {operation.payload.target && typeof operation.payload.target === "object" && (() => {
        const target = operation.payload.target as { id?: string; images?: Record<string,boolean> };
        return target.id && /^[0-9a-f-]{36}$/.test(target.id) && <div style={{display:"flex",gap:12,flexWrap:"wrap"}}>
          {(["front","back"] as const).filter(side=>target.images?.[side]).map(side=><img key={side}
            src={`/v1/portfolio/items/${target.id}/images/${side}?variant=preview`} alt={side==="front"?"目标实卡正面":"目标实卡背面"}
            style={{maxWidth:240,maxHeight:360,objectFit:"contain"}} />)}
        </div>;
      })()}
      <PreviewFields values={operation.payload} />
      {Array.isArray(operation.payload.required_acknowledgements) && operation.payload.required_acknowledgements.includes("pair_images_checked") &&
        <p><label><input type="checkbox" checked={pairChecked} onChange={event => setPairChecked(event.target.checked)} />
          我已查看当前正背照片，确认它们属于同一张实卡。</label></p>}
      <p>有效期至 {new Date(operation.expires_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}（北京时间）</p>
      {operation.status === "prepared" && <button disabled={busy || (
        Array.isArray(operation.payload.required_acknowledgements) && operation.payload.required_acknowledgements.includes("pair_images_checked") && !pairChecked
      )} onClick={async () => {
        setBusy(true); setError("");
        try {
          setOperation(await chatgptRequest<Operation>(`/operations/${encodeURIComponent(operationId)}/approve`, {
            method: "POST", body: JSON.stringify({ payload_hash: operation.payload_hash,
              acknowledgements: pairChecked ? ["pair_images_checked"] : [] }),
          }));
        } catch (reason) { setError(reason instanceof Error ? reason.message : "批准失败"); }
        finally { setBusy(false); }
      }}>核对无误，批准此操作</button>}
      {operation.status === "approved" && <p role="status">已批准。请返回 ChatGPT 执行并查看保存结果。</p>}
      {operation.status === "succeeded" && <p role="status">操作已保存，可返回 ChatGPT 回读结果。</p>}
    </>}
    <p><a href="/">返回卡库</a> · <a href="/chatgpt/connections">管理 ChatGPT 连接</a></p>
  </main>;
}
