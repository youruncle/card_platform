import React, { useEffect, useRef, useState } from "react";
import type { MobileApi } from "./api";
import type { Job } from "./types";
import {
  identitySuggestions,
  acceptIdentitySuggestions,
  type Identity,
} from "../../../packages/card-core/src/supplement";
const display = (value: unknown) =>
  value === null || value === undefined
    ? "未记录"
    : typeof value === "boolean"
      ? value
        ? "是"
        : "否"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
export function SupplementConfirmation({
  api,
  job,
  onSaved,
}: {
  api: MobileApi;
  job: Job;
  onSaved: (id: string) => void;
}) {
  const [identity, setIdentity] = useState<Identity | null>(null),
    [chosen, setChosen] = useState<string[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [canAccept, setCanAccept] = useState(false),
    [acceptanceReason, setAcceptanceReason] = useState("");
  const active = useRef(true),
    locked = useRef(false);
  useEffect(() => {
    active.current = true;
    api
      .get<{
        identity: Identity;
        can_accept_identity?: boolean;
        identity_acceptance_reason?: string | null;
      }>(`/v1/capture/items/${job.target_item_id}`)
      .then((t) => {
        if (active.current) {
          setIdentity(t.identity);
          setCanAccept(t.can_accept_identity === true);
          setAcceptanceReason(
            t.identity_acceptance_reason ??
              (t.can_accept_identity
                ? ""
                : "请从网页身份审核修改已确认的身份。"),
          );
        }
      })
      .catch((e) => {
        if (active.current) setError(e.message);
      });
    return () => {
      active.current = false;
    };
  }, [api, job.target_item_id]);
  const suggestions = identity
    ? identitySuggestions(identity, job.result ?? {})
    : [];
  async function confirm(accept: boolean) {
    if (locked.current || (accept && !canAccept)) return;
    locked.current = true;
    setBusy(true);
    setError("");
    try {
      const body =
        accept && identity
          ? {
              accepted_identity: acceptIdentitySuggestions(
                identity,
                suggestions,
                chosen,
              ),
            }
          : {};
      const saved = await api.post<{ id: string }>(
        `/v1/capture/jobs/${job.id}/confirm`,
        body,
      );
      if (active.current) onSaved(saved.id);
    } catch (e) {
      if (active.current) setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      locked.current = false;
      if (active.current) setBusy(false);
    }
  }
  return (
    <section className="panel">
      <p>默认只补充照片，保留原来已确认的身份。</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button
        className="primary"
        disabled={busy}
        onClick={() => void confirm(false)}
      >
        确认补充到原卡片
      </button>
      <details>
        <summary>查看识别差异（可选）</summary>
        <p>
          只修改你勾选的球员、品牌、赛季、产品或卡号等主身份字段；其余字段保持原值。
        </p>
        {acceptanceReason && <p className="notice">{acceptanceReason}</p>}
        {!identity ? (
          <p>
            {error
              ? "暂时无法读取差异，可返回卡片重试。"
              : "正在读取原卡片信息…"}
          </p>
        ) : suggestions.length === 0 ? (
          <p>没有可接受的新身份字段。</p>
        ) : (
          suggestions.map((s) => (
            <label className="check" key={s.key}>
              <input
                type="checkbox"
                disabled={busy || !canAccept}
                checked={chosen.includes(s.key)}
                onChange={(e) =>
                  setChosen((v) =>
                    e.target.checked
                      ? [...v, s.key]
                      : v.filter((k) => k !== s.key),
                  )
                }
              />
              <span>
                {s.label}：{display(s.current)} → {display(s.suggested)}
              </span>
            </label>
          ))
        )}
        <button
          disabled={busy || !identity || !canAccept || chosen.length === 0}
          onClick={() => void confirm(true)}
        >
          接受所选差异并补充照片
        </button>
      </details>
    </section>
  );
}
