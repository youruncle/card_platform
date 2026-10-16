import React, { useEffect, useState } from "react";
import type {
  KnowledgeEntity,
  KnowledgeWorkspace,
} from "../../../packages/card-core/src/contracts";
import type { MobileApi } from "./api";
export function KnowledgePage({
  api,
  onBack,
}: {
  api: MobileApi;
  onBack: () => void;
}) {
  const [query, setQuery] = useState(""),
    [search, setSearch] = useState<string | null>(null),
    [rows, setRows] = useState<KnowledgeEntity[] | null>(null),
    [workspace, setWorkspace] = useState<KnowledgeWorkspace | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .get<KnowledgeWorkspace>("/v1/knowledge/workspace")
      .then((w) => {
        if (active) setWorkspace(w);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api]);
  useEffect(() => {
    if (search === null) return;
    let active = true;
    setRows(null);
    setError("");
    api
      .get<KnowledgeEntity[]>(
        `/v1/knowledge/entities?${new URLSearchParams({ q: search })}`,
      )
      .then((v) => {
        if (active) setRows(v);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [api, search]);
  return (
    <>
      <button onClick={onBack}>‹ 返回我的</button>
      <h1>知识查询</h1>
      <p className="muted">
        查阅球员、品牌和卡牌系列。编辑、导入和发布请使用网页版。
      </p>
      {workspace && (
        <p className="small muted">
          当前识别使用版本：{workspace.runtime_version ?? "尚未发布"}
          。查询结果来自知识工作区，可能包含待审核条目。
        </p>
      )}
      <form
        className="search"
        onSubmit={(e) => {
          e.preventDefault();
          setSearch(query.trim());
        }}
      >
        <input
          aria-label="知识关键词"
          required
          maxLength={200}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="球员、品牌、系列名称"
        />
        <button>查询</button>
      </form>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {search !== null && !rows && !error && <p>正在查询…</p>}
      {rows?.length === 0 && (
        <p className="empty">未找到相关知识，试试其他名称。</p>
      )}
      {rows?.map((row) => (
        <section className="panel" key={row.id}>
          <h2>{row.canonical_name}</h2>
          <p className="small muted">
            {row.entity_type} ·{" "}
            {row.review_status === "approved"
              ? "已审核"
              : row.review_status === "reviewed"
                ? "已审核"
                : "待核对"}
          </p>
          {row.aliases.length > 0 && (
            <p>别名：{row.aliases.map((a) => a.alias).join("、")}</p>
          )}
          {typeof row.attributes.description === "string" && (
            <p>{row.attributes.description}</p>
          )}
        </section>
      ))}
    </>
  );
}
