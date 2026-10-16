import React, { useState } from "react";
import type { Recognition } from "./types";
import { confirmationPayload } from "./confirmation";
const labels: Record<string, string> = {
  player_name: "球员",
  brand_name: "品牌",
  product_name: "产品",
  season_start: "赛季年份",
  card_number: "卡号",
  parallel_name: "平行版本",
  insert_name: "特卡系列",
  serial_number: "卡片编号",
  serial_total: "限编总数",
  grade_company: "评级公司",
  grade_value: "评级分数",
};
export function ConfirmationForm({
  owner,
  result,
  onConfirm,
}: {
  owner: string;
  result: Recognition;
  onConfirm: (payload: unknown) => Promise<void>;
}) {
  const [fields, setFields] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.keys(labels).map((key) => [
        key,
        String(result[key as keyof Recognition] ?? ""),
      ]),
    ),
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <form
      className="panel"
      onSubmit={async (event) => {
        event.preventDefault();
        if (busy) return;
        setError("");
        try {
          const payload = confirmationPayload(owner, result, fields);
          setBusy(true);
          await onConfirm(payload);
        } catch (e) {
          setError(e instanceof Error ? e.message : "保存失败");
        } finally {
          setBusy(false);
        }
      }}
    >
      <h2>确认卡片信息</h2>
      <p className="muted">
        识别置信度 {Math.round(Number(result.confidence) * 100)}% · 请核对后入库
      </p>
      {[
        ...(result.caveats ?? []),
        ...(result.recognition_quality_issues ?? []),
      ].map((text, i) => (
        <p className="notice" key={i}>
          {text}
        </p>
      ))}
      <fieldset disabled={busy} className="fields">
        {Object.entries(labels).map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              value={fields[key]}
              onChange={(e) => setFields({ ...fields, [key]: e.target.value })}
              inputMode={
                [
                  "season_start",
                  "serial_number",
                  "serial_total",
                  "grade_value",
                ].includes(key)
                  ? "decimal"
                  : "text"
              }
            />
          </label>
        ))}
        {(
          [
            ["is_rookie", "新秀卡"],
            ["is_autograph", "签字卡"],
            ["is_memorabilia", "物料卡"],
            ["is_patch", "Patch"],
          ] as const
        ).map(([key, label]) => (
          <label className="check" key={key}>
            <input
              type="checkbox"
              checked={
                key in fields ? fields[key] === "true" : Boolean(result[key])
              }
              onChange={(e) =>
                setFields({ ...fields, [key]: String(e.target.checked) })
              }
            />
            {label}
          </label>
        ))}
      </fieldset>
      {Object.keys(result.field_evidence ?? {}).length > 0 && (
        <details>
          <summary>识别依据</summary>
          {Object.entries(result.field_evidence ?? {}).map(([key, values]) => (
            <p key={key}>
              <strong>{labels[key] ?? key}</strong>：{values.join("；")}
            </p>
          ))}
        </details>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button className="primary" disabled={busy}>
        {busy ? "正在保存…" : "确认入库"}
      </button>
    </form>
  );
}
