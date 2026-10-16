"use client";

import { ArrowLeft, Check, Database, GitBranch, Pencil, Plus, RefreshCw, RotateCcw, Search, Trash2 } from "lucide-react";
import { OfficialChecklistProgressPanel } from "@/components/official-checklist-progress";
import { FormEvent, useCallback, useEffect, useState } from "react";

import {
  addKnowledgeAlias,
  deleteKnowledgeAlias,
  bootstrapKnowledge,
  createKnowledgeEntity,
  createKnowledgeRelation,
  getKnowledgeEntities,
  getKnowledgeBatches,
  getKnowledgeBatchCatalog,
  getKnowledgeCoverage,
  getKnowledgeImpact,
  getKnowledgeObservations,
  getKnowledgeRelations,
  getKnowledgeVersions,
  getKnowledgeWorkspace,
  initializeMarketplaceAliases,
  publishKnowledge,
  promoteKnowledgeObservation,
  revalueKnowledgeImpact,
  reviewKnowledge,
  updateKnowledgeAlias,
  reviewKnowledgeObservation,
  rollbackKnowledge,
  applyKnowledgeCatalogBatch,
  type KnowledgeEntity,
  type KnowledgeBatch,
  type KnowledgeBatchCatalogItem,
  type KnowledgeCoverage,
  type KnowledgeImpact,
  type KnowledgeObservation,
  type KnowledgeRelation,
  type KnowledgeVersion,
  type KnowledgeWorkspace,
} from "@/lib/api";

const entityLabels: Record<string, string> = {
  manufacturer: "制造商",
  brand: "厂牌",
  license: "授权",
  product: "产品 / 系列",
  insert: "Insert",
  parallel: "Parallel",
  attribute: "领域属性",
  player: "球员",
  card: "具体卡片",
};

export default function KnowledgePage() {
  const [workspace, setWorkspace] = useState<KnowledgeWorkspace | null>(null);
  const [entities, setEntities] = useState<KnowledgeEntity[]>([]);
  const [versions, setVersions] = useState<KnowledgeVersion[]>([]);
  const [relations, setRelations] = useState<KnowledgeRelation[]>([]);
  const [observations, setObservations] = useState<KnowledgeObservation[]>([]);
  const [impact, setImpact] = useState<KnowledgeImpact[]>([]);
  const [coverage, setCoverage] = useState<KnowledgeCoverage[]>([]);
  const [coverageCounts, setCoverageCounts] = useState<Record<string, number>>({});
  const [batches, setBatches] = useState<KnowledgeBatch[]>([]);
  const [batchCatalog, setBatchCatalog] = useState<KnowledgeBatchCatalogItem[]>([]);
  const [batchReport, setBatchReport] = useState<KnowledgeBatch | null>(null);
  const [selectedVersion, setSelectedVersion] = useState("");
  const [entityType, setEntityType] = useState("");
  const [query, setQuery] = useState("");
  const [actorId, setActorId] = useState("");
  const [canonicalName, setCanonicalName] = useState("");
  const [newType, setNewType] = useState("insert");
  const [releaseNotes, setReleaseNotes] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [relationSubject, setRelationSubject] = useState("");
  const [relationObject, setRelationObject] = useState("");
  const [relationType, setRelationType] = useState("conflicts_with");
  const [showCreateEntity, setShowCreateEntity] = useState(false);

  const load = useCallback(async () => {
    const [
      nextWorkspace, nextEntities, nextVersions, nextRelations, nextObservations,
      nextCoverage, nextBatches, nextBatchCatalog,
    ] = await Promise.all([
      getKnowledgeWorkspace(),
      getKnowledgeEntities(entityType, query),
      getKnowledgeVersions(),
      getKnowledgeRelations(),
      getKnowledgeObservations(),
      getKnowledgeCoverage(),
      getKnowledgeBatches(),
      getKnowledgeBatchCatalog(),
    ]);
    setWorkspace(nextWorkspace);
    setEntities(nextEntities);
    setVersions(nextVersions);
    setRelations(nextRelations);
    setObservations(nextObservations);
    setCoverage(nextCoverage.rows);
    setCoverageCounts(nextCoverage.counts);
    setBatches(nextBatches);
    setBatchCatalog(nextBatchCatalog);
    const versionId = selectedVersion || nextWorkspace.published_version_id || nextVersions[0]?.id;
    if (versionId) {
      setSelectedVersion(versionId);
      setImpact(await getKnowledgeImpact(versionId));
    } else {
      setImpact([]);
    }
  }, [entityType, query, selectedVersion]);

  useEffect(() => {
    setActorId(window.localStorage.getItem("knowledgeActorId") ?? "");
    void load().catch((error) => setMessage(error.message));
  }, [load]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setMessage("");
    try {
      await action();
      await load();
      setMessage(success);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function runCatalogBatch(batchKey: string, dryRun: boolean) {
    setBusy(true);
    setMessage("");
    try {
      const report = await applyKnowledgeCatalogBatch(actorId, batchKey, dryRun);
      setBatchReport(report);
      await load();
      setMessage(dryRun
        ? `已完成 ${batchKey} 的 dry-run；请核对摘要、冲突与历史标题回放后再应用。`
        : `已应用 ${batchKey}，尚未发布；请填写发布说明并发布新版本。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "批次操作失败");
    } finally {
      setBusy(false);
    }
  }

  function saveActor(value: string) {
    setActorId(value);
    window.localStorage.setItem("knowledgeActorId", value);
  }

  function createEntity(event: FormEvent) {
    event.preventDefault();
    if (!canonicalName.trim()) return;
    void run(
      () => createKnowledgeEntity(actorId, { entity_type: newType, canonical_name: canonicalName }),
      "实体已创建，需审核后才能发布。",
    );
    setCanonicalName("");
  }

  return (
    <main className="knowledge-shell">
      <header className="knowledge-header">
        <div>
          <a href="/" className="knowledge-back"><ArrowLeft size={16} /> 返回资产台</a>
          <p className="eyebrow">KNOWLEDGE CONTROL</p>
          <h1>卡牌知识库</h1>
          <p>草稿、审核、版本发布与影响重估集中在这里管理。</p>
        </div>
        <div className="knowledge-status">
          <Database size={18} />
          <div>
            <small>当前发布版本</small>
            <strong>v{workspace?.published_version ?? "—"} · rev {workspace?.revision ?? "—"}</strong>
            <small>运行时 v{workspace?.runtime_version ?? "兼容回退"}</small>
          </div>
        </div>
      </header>
      <OfficialChecklistProgressPanel />

      {message && <div className="knowledge-message">{message}</div>}

      <section className="knowledge-toolbar panel">
        <label>
          操作用户 UUID
          <input value={actorId} onChange={(event) => saveActor(event.target.value)} placeholder="已有管理员用户 ID" />
        </label>
        <button disabled title="v2 模型词表已在 v5 证据审计中停用">
          旧词表已停用
        </button>
        <button
          disabled={busy || !actorId}
          onClick={() => void run(
            () => initializeMarketplaceAliases(actorId),
            "已初始化卡淘卖家词候选：均为待审核草稿，未进入线上检索。",
          )}
        >初始化卖家同义词候选</button>
        {workspace?.member_count === 0 && (
          <button disabled={busy || !actorId} onClick={() => void run(
            () => bootstrapKnowledge(actorId),
            "已显式认领知识库管理员。",
          )}>认领管理员</button>
        )}
        <input value={releaseNotes} onChange={(event) => setReleaseNotes(event.target.value)} placeholder="发布说明" />
        <button
          className="primary-button"
          disabled={busy || !actorId || !workspace}
          onClick={() => void run(
            () => publishKnowledge(actorId, workspace!.revision, releaseNotes),
            "新知识版本已发布，影响清单已生成。",
          )}
        >
          <GitBranch size={16} /> 发布版本
        </button>
      </section>

      <div className="knowledge-grid">
        <section className="panel knowledge-main">
          <div className="section-heading">
            <div>
              <p className="eyebrow">ENTITIES & ALIASES</p>
              <h2>实体与别名</h2>
            </div>
            <button
              className="knowledge-create-toggle"
              type="button"
              onClick={() => setShowCreateEntity(true)}
            >
              <Plus size={16} /> 新建实体
            </button>
          </div>
          <div className="knowledge-filter-bar" aria-label="筛选知识实体">
            <div className="knowledge-filter-heading">
              <Search size={16} />
              <span>搜索已有知识</span>
            </div>
            <label>
              <span>实体类型</span>
              <select value={entityType} onChange={(event) => setEntityType(event.target.value)}>
                <option value="">全部类型</option>
                {Object.entries(entityLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="knowledge-search-field">
              <span>名称或别名</span>
              <div className="knowledge-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：Donruss、国宝、鞋灯" /></div>
            </label>
          </div>
          <div className="knowledge-entities">
            {entities.map((entity) => (
              <article key={entity.id} className="knowledge-entity">
                <div>
                  <span className="knowledge-type">{entityLabels[entity.entity_type]}</span>
                  <h3>{entity.canonical_name}</h3>
                  <div className="knowledge-aliases">
                    {entity.aliases.map((alias) => (
                      <span key={alias.id}>
                        {alias.alias}
                        {typeof alias.source?.source_url === "string" && (
                          <a
                            href={alias.source.source_url}
                            target="_blank"
                            rel="noreferrer"
                            title="查看该别名的公开检索来源"
                          >来源</a>
                        )}
                        {alias.review_status !== "approved" && (
                          <button title="审核通过" onClick={() => void run(
                            () => reviewKnowledge(actorId, "alias", alias.id, "approved"),
                            "别名已审核。",
                          )}><Check size={12} /></button>
                        )}
                        <button title="编辑别名" onClick={() => {
                          const value = window.prompt("编辑别名", alias.alias);
                          if (value && value.trim() !== alias.alias) void run(
                            () => updateKnowledgeAlias(actorId, alias.id, {
                              alias: value.trim(), language: /[\u4e00-\u9fff]/.test(value) ? "zh" : "en",
                              alias_type: alias.alias_type,
                            }),
                            "别名已更新；内容变更后需重新审核。",
                          );
                        }}><Pencil size={12} /></button>
                        <button title="删除别名" onClick={() => {
                          if (window.confirm(`删除别名“${alias.alias}”？会保留审计记录和已发布版本。`)) void run(
                            () => deleteKnowledgeAlias(actorId, alias.id), "别名已删除（软删除）。",
                          );
                        }}><Trash2 size={12} /></button>
                      </span>
                    ))}
                  </div>
                </div>
                <div className="knowledge-actions">
                  <span className={`review-state ${entity.review_status}`}>{entity.review_status}</span>
                  {entity.review_status !== "approved" && (
                    <button onClick={() => void run(
                      () => reviewKnowledge(actorId, "entity", entity.id, "approved"),
                      "实体已审核。",
                    )}>审核通过</button>
                  )}
                  <button onClick={() => {
                    const alias = window.prompt("输入新别名");
                    if (alias) void run(
                      () => addKnowledgeAlias(actorId, entity.id, {
                        alias,
                        language: /[\u4e00-\u9fff]/.test(alias) ? "zh" : "en",
                        alias_type: "synonym",
                      }),
                      "别名已创建，待审核。",
                    );
                  }}>添加别名</button>
                </div>
              </article>
            ))}
            {!entities.length && <p className="muted-copy">没有匹配的知识实体。</p>}
          </div>
        </section>

        <aside className="panel knowledge-versions">
          <p className="eyebrow">VERSIONS</p>
          <h2>版本与回滚</h2>
          {versions.map((version) => (
            <article key={version.id}>
              <div><strong>v{version.version_number}</strong><span>{version.status}</span></div>
              <p>{version.release_notes || "无发布说明"}</p>
              <small>{new Date(version.published_at).toLocaleString("zh-CN")}</small>
              {workspace?.published_version_id !== version.id && (
                <button disabled={busy || !actorId} onClick={() => {
                  if (window.confirm(`回滚到 v${version.version_number}？将生成新的影响清单。`)) {
                    void run(
                      () => rollbackKnowledge(actorId, workspace!.revision, version.id),
                      `已回滚到 v${version.version_number}。`,
                    );
                  }
                }}><RotateCcw size={14} /> 回滚至此版本</button>
              )}
            </article>
          ))}
          {!versions.length && <p className="muted-copy">尚未发布版本。</p>}
        </aside>
      </div>

      <div className="knowledge-secondary-grid">
        <section className="panel knowledge-secondary">
          <div className="section-heading">
            <div><p className="eyebrow">COVERAGE MATRIX</p><h2>覆盖矩阵</h2></div>
          </div>
          <p className="muted-copy">
            已覆盖 {coverageCounts.covered ?? 0} · 部分 {coverageCounts.partial ?? 0} ·
            待验证 {coverageCounts.needs_review ?? 0} · 待来源 {coverageCounts.pending_source ?? 0} ·
            来源缺失 {coverageCounts.source_missing ?? 0}
          </p>
          <div className="knowledge-compact-list">
            {coverage.map((row) => (
              <article key={row.id}>
                <span>{row.manufacturer_name ?? "—"} · {row.product_name ?? "—"}</span>
                <strong>{row.coverage_status}</strong>
                <span>{row.season_start ? `${row.season_start}-${String(row.season_end ?? "").slice(-2)}` : "赛季待补"}</span>
                <small>{row.notes ?? "—"}</small>
                {row.source_url && <a href={row.source_url} target="_blank" rel="noreferrer">来源</a>}
              </article>
            ))}
          </div>
          {batches[0] && (
            <p className="muted-copy">
              最近批次：{batches[0].batch_key} · {batches[0].status} ·
              冲突 {batches[0].conflict_report.length}
            </p>
          )}
        </section>

        <section className="panel knowledge-secondary">
          <div className="section-heading">
            <div><p className="eyebrow">VERIFIED IMPORTS</p><h2>可运行知识批次</h2></div>
          </div>
          <p className="muted-copy">批次先 dry-run，检查来源、冲突与历史标题回放；应用后仍需单独发布版本。</p>
          <div className="knowledge-compact-list">
            {batchCatalog.map((batch) => {
              const previouslyApplied = batches.some((item) => item.batch_key === batch.batch_key && !item.dry_run && ["applied", "published"].includes(item.status));
              return (
                <article key={batch.batch_key}>
                  <span>{batch.batch_key}</span>
                  <strong>{batch.entity_count} 实体</strong>
                  <small>清单 v{batch.manifest_version} · {batch.path}</small>
                  <small>{batch.source_count} 个来源；应用前会执行结构、重复、冲突与回放校验。</small>
                  <div className="knowledge-batch-actions">
                    <button disabled={busy || !actorId} onClick={() => void runCatalogBatch(batch.batch_key, true)}>dry-run</button>
                    <button disabled={busy || !actorId || previouslyApplied} onClick={() => {
                      if (window.confirm(`应用 ${batch.batch_key}？应用后仍需手动发布版本。`)) void runCatalogBatch(batch.batch_key, false);
                    }}>{previouslyApplied ? "已应用" : "应用"}</button>
                  </div>
                </article>
              );
            })}
            {!batchCatalog.length && <p className="muted-copy">暂无通过目录校验的可运行批次。</p>}
          </div>
          {batchReport && (
            <p className="muted-copy">
              最近报告：{batchReport.batch_key} · {batchReport.dry_run ? "dry-run" : batchReport.status} ·
              冲突 {batchReport.conflict_report.length} · 回放 {Object.values(batchReport.replay_result).filter(Boolean).length}/{Object.keys(batchReport.replay_result).length}
            </p>
          )}
        </section>

        <section className="panel knowledge-secondary">
          <div className="section-heading">
            <div><p className="eyebrow">RELATIONS</p><h2>排斥与约束关系</h2></div>
          </div>
          <div className="knowledge-relation-form">
            <select value={relationSubject} onChange={(event) => setRelationSubject(event.target.value)}>
              <option value="">选择主体实体</option>
              {entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.canonical_name}</option>)}
            </select>
            <select value={relationType} onChange={(event) => setRelationType(event.target.value)}>
              <option value="conflicts_with">互相冲突</option>
              <option value="excludes">排斥</option>
              <option value="requires">依赖</option>
              <option value="belongs_to">属于</option>
              <option value="equivalent_to">等价</option>
            </select>
            <select value={relationObject} onChange={(event) => setRelationObject(event.target.value)}>
              <option value="">选择目标实体</option>
              {entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.canonical_name}</option>)}
            </select>
            <button disabled={busy || !actorId || !relationSubject || !relationObject} onClick={() => {
              const reason = window.prompt("关系说明") ?? "";
              void run(
                () => createKnowledgeRelation(actorId, {
                  subject_entity_id: relationSubject,
                  relation_type: relationType,
                  object_entity_id: relationObject,
                  reason,
                }),
                "关系已创建，待审核。",
              );
            }}><Plus size={15} /> 新建关系</button>
          </div>
          <div className="knowledge-compact-list">
            {relations.map((relation) => (
              <article key={relation.id}>
                <span>{relation.subject_name}</span>
                <strong>{relation.relation_type}</strong>
                <span>{relation.object_name}</span>
                <small>{relation.reason || "无说明"}</small>
                {relation.review_status !== "approved" && (
                  <button onClick={() => void run(
                    () => reviewKnowledge(actorId, "relation", relation.id, "approved"),
                    "关系已审核。",
                  )}>审核</button>
                )}
              </article>
            ))}
            {!relations.length && <p className="muted-copy">暂无关系。</p>}
          </div>
        </section>

        <section className="panel knowledge-secondary">
          <div className="section-heading">
            <div><p className="eyebrow">OBSERVATIONS</p><h2>未知词候选</h2></div>
          </div>
          <div className="knowledge-compact-list">
            {observations.map((observation) => (
              <article key={observation.id}>
                <span>{observation.observed_text}</span>
                <strong>{observation.occurrence_count} 次</strong>
                <span>{observation.status}</span>
                <small>{observation.confidence ? `置信度 ${observation.confidence}` : "尚未回放验证"}</small>
                {observation.context?.applicability && (
                  <small>
                    适用范围：{Object.entries(observation.context.applicability)
                      .map(([key, value]) => `${key}=${String(value)}`).join(" · ")}
                  </small>
                )}
                {observation.context?.source_samples?.map((sample) => (
                  <a key={sample.url} href={sample.url} target="_blank" rel="noreferrer">
                    来源样本：{sample.title}
                  </a>
                ))}
                {["observed", "proposed"].includes(observation.status) && (
                  <button onClick={() => {
                    const entityId = window.prompt("关联的实体 UUID");
                    if (!entityId) return;
                    const alias = window.prompt("建议别名", observation.observed_text);
                    if (!alias) return;
                    const evidenceCount = Number(window.prompt("历史回放命中数", "1") || "0");
                    const confidence = Number(window.prompt("验证置信度（0-1）", "0.90") || "0");
                    void run(
                      () => reviewKnowledgeObservation(actorId, observation.id, {
                        proposed_entity_id: entityId,
                        proposed_alias: alias,
                        replay_result: { matched_records: evidenceCount, reviewed_at: new Date().toISOString() },
                        confidence,
                        decision: "validated",
                      }),
                      "候选已完成人工回放验证。",
                    );
                  }}>验证候选</button>
                )}
                {observation.status === "validated" && (
                  <button onClick={() => void run(
                    () => promoteKnowledgeObservation(actorId, observation.id),
                    "候选已晋级为待审核别名，不会直接进入正式版本。",
                  )}>高门槛晋级</button>
                )}
              </article>
            ))}
            {!observations.length && <p className="muted-copy">暂无未知词观察。</p>}
          </div>
        </section>

        <section className="panel knowledge-secondary knowledge-impact">
          <div className="section-heading">
            <div><p className="eyebrow">IMPACT & REVALUATION</p><h2>版本影响与重估</h2></div>
            <select value={selectedVersion} onChange={(event) => setSelectedVersion(event.target.value)}>
              <option value="">选择版本</option>
              {versions.map((version) => <option key={version.id} value={version.id}>v{version.version_number}</option>)}
            </select>
          </div>
          <div className="knowledge-impact-summary">
            <strong>{impact.filter((item) => item.object_type === "collection_item").length}</strong><span>受影响持仓</span>
            <strong>{impact.filter((item) => item.object_type === "market_comp").length}</strong><span>受影响成交</span>
            <button disabled={!selectedVersion || !actorId || busy} onClick={() => void run(
              () => revalueKnowledgeImpact(actorId, selectedVersion),
              "受影响持仓已幂等加入重估队列。",
            )}><RefreshCw size={15} /> 批量重估</button>
          </div>
          <div className="knowledge-impact-items">
            {impact.slice(0, 30).map((item) => (
              <span key={`${item.object_type}-${item.object_id}`}>
                {item.object_type} · {item.object_id.slice(0, 8)} · {item.revaluation_status}
              </span>
            ))}
          </div>
        </section>
      </div>

      {showCreateEntity && (
        <div
          className="knowledge-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setShowCreateEntity(false);
          }}
        >
          <section
            className="knowledge-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-knowledge-title"
          >
            <div className="knowledge-modal-header">
              <div>
                <p className="eyebrow">NEW KNOWLEDGE ENTITY</p>
                <h2 id="create-knowledge-title">新建知识实体</h2>
              </div>
              <button
                type="button"
                aria-label="关闭"
                onClick={() => setShowCreateEntity(false)}
              >×</button>
            </div>
            <p className="knowledge-modal-note">
              新实体会先进入待审核状态；审核并发布新版本后，才会用于搜索、分类与估值。
            </p>
            <form className="knowledge-modal-form" onSubmit={(event) => {
              createEntity(event);
              if (canonicalName.trim()) setShowCreateEntity(false);
            }}>
              <label>
                <span>实体类型</span>
                <select value={newType} onChange={(event) => setNewType(event.target.value)} autoFocus>
                  {Object.entries(entityLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                <small>产品、Insert、Parallel 和领域属性应分别建模。</small>
              </label>
              <label>
                <span>规范名称</span>
                <input value={canonicalName} onChange={(event) => setCanonicalName(event.target.value)} placeholder="例如：Donruss" />
                <small>填写官方或行业通用英文名称；中文名和误写稍后作为别名添加。</small>
              </label>
              <div className="knowledge-modal-actions">
                <button type="button" onClick={() => setShowCreateEntity(false)}>取消</button>
                <button className="primary-button" disabled={busy || !actorId || !canonicalName.trim()}>
                  <Plus size={16} /> 创建并进入待审核
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </main>
  );
}
