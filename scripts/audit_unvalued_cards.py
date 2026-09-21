#!/usr/bin/env python3
"""Create a reproducible, read-only audit of valuation coverage and failures.

The main ``cards`` collection remains the complete list of active cards without a
usable formal valuation.  ``--display-id`` adds an ``affected_cards`` collection
that can also contain cards whose valuation was restored by a remediation run.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "postgresql://card_platform:card_platform@127.0.0.1:5432/card_platform"

ROOT_CAUSE_LABELS = {
    "identity_fields_missing_or_role_misplaced": "身份字段缺失、错误或角色错置",
    "product_brand_normalization_or_recall": "产品/品牌归一化或卡淘召回不足",
    "title_normalization_or_comparable_hard_conflict": "标题归一化或可比分级硬冲突",
    "sparse_comparable_samples": "可比成交样本稀疏",
    "grade_condition_currency_or_time": "评级、品相、币种或时间窗口问题",
    "refresh_retry_or_job_state": "刷新、重试或任务状态问题",
    "model_analysis_missing_or_not_persisted": "模型分析缺失、失败或结论未落库",
    "unclassified_insufficient_evidence": "尚未归类的证据不足",
}

ROOT_CAUSE_REMEDIATIONS = {
    "identity_fields_missing_or_role_misplaced": "身份字段角色校验、确认版身份回填",
    "product_brand_normalization_or_recall": "统一品牌/产品别名、子产品边界和卡淘查询扩展",
    "title_normalization_or_comparable_hard_conflict": "统一标题归一化并重做可比分层/硬冲突校验",
    "sparse_comparable_samples": "稀疏成交保留宽区间、低置信度和可编辑原因",
    "grade_condition_currency_or_time": "评级/品相/币种/时效分层并按风险降权",
    "refresh_retry_or_job_state": "刷新前置、安全重试和任务状态回填",
    "model_analysis_missing_or_not_persisted": "模型分析/回退估值执行与落库检查",
    "unclassified_insufficient_evidence": "保留诊断上下文并转人工核验",
}


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _strings(item)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _failure_summaries(row: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for value in (row.get("result_summary"), row.get("previous_failure_result_summary")):
        summary = _mapping(value)
        if summary and summary not in summaries:
            summaries.append(summary)
    return summaries


def _diagnostic_codes(row: dict[str, Any]) -> list[str]:
    return _unique_strings(
        code
        for summary in _failure_summaries(row)
        for code in _list(
            _mapping(summary.get("valuation_failure_diagnostics")).get("root_cause_codes")
        )
    )


def _risk_flags(row: dict[str, Any]) -> list[str]:
    flags: list[Any] = []
    for evidence_key in ("valuation_evidence", "shadow_evidence"):
        evidence = _mapping(row.get(evidence_key))
        flags.extend(_list(evidence.get("risk_flags")))
        flags.extend(_list(evidence.get("review_reasons")))
    flags.extend(_list(row.get("shadow_review_reasons")))
    return _unique_strings(flags)


def root_cause_assessment(
    row: dict[str, Any],
) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """Map low-level diagnostics to stable, multi-select audit categories.

    The details intentionally retain the observable reason for each classification;
    the category counts alone must not be mistaken for a model diagnosis.
    """

    details: dict[str, list[str]] = {}

    def add(category: str, reason: str) -> None:
        bucket = details.setdefault(category, [])
        if reason not in bucket:
            bucket.append(reason)

    summaries = _failure_summaries(row)
    diagnostics = [
        _mapping(summary.get("valuation_failure_diagnostics")) for summary in summaries
    ]
    raw_codes = _diagnostic_codes(row)
    risks = _risk_flags(row)
    diagnostic_text = " ".join(
        [*raw_codes, *risks, *(_strings(diagnostics)), *(_strings(row.get("latest_identity_conflicts")))]
    ).lower()
    unresolved = row.get("valuation_snapshot_id") is None

    missing_fields = _unique_strings(
        field
        for summary in summaries
        for field in _list(summary.get("missing_identity_fields"))
    )
    identity_conflicts = _list(row.get("latest_identity_conflicts"))
    if missing_fields:
        add(
            "identity_fields_missing_or_role_misplaced",
            f"missing_identity_fields={','.join(missing_fields)}",
        )
    if identity_conflicts:
        add(
            "identity_fields_missing_or_role_misplaced",
            f"latest_identity_conflicts={len(identity_conflicts)}",
        )
    if unresolved and row.get("confirmed_identity") is None:
        add("identity_fields_missing_or_role_misplaced", "identity_not_confirmed")
    if any(
        token in diagnostic_text
        for token in (
            "missing_card_anchor",
            "identity_missing",
            "identity_uncertain",
            "role_misplaced",
            "role_conflict",
            "field_role",
            "字段错置",
            "身份字段缺失",
        )
    ):
        add("identity_fields_missing_or_role_misplaced", "identity_diagnostic_or_risk")

    recall_stage = any(
        diagnostic.get("stage") == "cardhobby_recall_and_similarity_gate"
        for diagnostic in diagnostics
    )
    if recall_stage or any(
        token in diagnostic_text
        for token in (
            "no_candidate_reached_minimum_similarity",
            "product_mismatch",
            "brand_mismatch",
            "series_mismatch",
            "product_conflict",
            "brand_conflict",
            "标题未命中目标产品",
            "产品系列",
            "品牌归一",
            "product normalization",
            "zero_results_relax",
            "too_broad_refine",
        )
    ):
        add("product_brand_normalization_or_recall", "cardhobby_recall_or_product_match")
    if unresolved and int(row.get("market_evidence_count") or 0) == 0:
        add("product_brand_normalization_or_recall", "no_durable_market_evidence")

    rejected_count = max(
        [int(diagnostic.get("rejected_candidate_count") or 0) for diagnostic in diagnostics]
        or [0]
    )
    hard_conflict_tokens = (
        "hard_conflict",
        "identity_conflict",
        "no_exact_t0_candidate",
        "parallel_mismatch",
        "card_number_mismatch",
        "insert_mismatch",
        "season_mismatch",
        "serial_total_mismatch",
        "标题明确",
        "标题未命中",
        "可比",
    )
    if rejected_count:
        add(
            "title_normalization_or_comparable_hard_conflict",
            f"rejected_candidates={rejected_count}",
        )
    if any(token in diagnostic_text for token in hard_conflict_tokens):
        add(
            "title_normalization_or_comparable_hard_conflict",
            "title_or_identity_hard_gate",
        )

    evidence_count = int(row.get("market_evidence_count") or 0)
    sparse_tokens = (
        "fewer_than_two",
        "single_",
        "sparse",
        "insufficient_evidence",
        "no_exact_t0",
        "no_eligible_candidate",
        "no_candidate_reached",
        "样本不足",
        "证据不足",
    )
    if unresolved and evidence_count < 2:
        add("sparse_comparable_samples", f"durable_market_evidence={evidence_count}")
    if any(token in diagnostic_text for token in sparse_tokens):
        add("sparse_comparable_samples", "sparse_sample_diagnostic_or_risk")

    market_constraint_tokens = (
        "grade",
        "graded",
        "condition",
        "currency",
        "fx_",
        "stale",
        "time_window",
        "too_old",
        "评级",
        "品相",
        "币种",
        "时间窗口",
        "过期",
    )
    if any(token in diagnostic_text for token in market_constraint_tokens):
        add(
            "grade_condition_currency_or_time",
            "grade_condition_currency_or_freshness_constraint",
        )

    refresh_status = row.get("refresh_status")
    job_status = row.get("job_status")
    if refresh_status in {"failed", "source_limited", "insufficient"}:
        add("refresh_retry_or_job_state", f"market_refresh_{refresh_status}")
    if job_status in {"failed", "queued", "running"}:
        add("refresh_retry_or_job_state", f"valuation_job_{job_status}")
    if any(
        token in diagnostic_text
        for token in ("retry", "refresh", "job_state", "provider_unavailable", "circuit_open")
    ):
        add("refresh_retry_or_job_state", "refresh_or_retry_diagnostic")

    if unresolved:
        analysis_status = row.get("analysis_status")
        if analysis_status is None:
            add("model_analysis_missing_or_not_persisted", "model_analysis_missing")
        elif analysis_status != "succeeded":
            add("model_analysis_missing_or_not_persisted", "model_analysis_failed")
        elif row.get("analysis_fair_value") is None:
            add("model_analysis_missing_or_not_persisted", "model_conclusion_not_priced")
        elif row.get("shadow_fair_value") is None:
            add("model_analysis_missing_or_not_persisted", "priced_analysis_not_persisted")

    if not details and unresolved:
        add("unclassified_insufficient_evidence", "no_structured_failure_diagnostic")
    causes = [category for category in ROOT_CAUSE_LABELS if category in details]
    return causes, details, raw_codes


def root_causes(row: dict[str, Any]) -> list[str]:
    """Backward-compatible category-only view used by callers and tests."""

    causes, _, _ = root_cause_assessment(row)
    return causes


def _used_comparables(evidence: Any) -> list[dict[str, Any]]:
    data = _mapping(evidence)
    for key in ("used_comparables", "comparables", "references"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    return []


def valuation_result(row: dict[str, Any]) -> dict[str, Any]:
    """Prefer a formal snapshot, then shadow, then expert-model analysis."""

    if row.get("valuation_snapshot_id") is not None:
        evidence = _mapping(row.get("valuation_evidence"))
        return {
            "source": "valuation_snapshot",
            "is_formal": True,
            "id": row.get("valuation_snapshot_id"),
            "status": "published",
            "currency": row.get("valuation_currency"),
            "low_value": row.get("valuation_low_value"),
            "fair_value": row.get("valuation_fair_value"),
            "high_value": row.get("valuation_high_value"),
            "confidence": row.get("valuation_confidence"),
            "model_version": row.get("valuation_model_version"),
            "valued_at": row.get("valuation_valued_at"),
            "used_comparables": _used_comparables(evidence),
            "risk_flags": _unique_strings(_list(evidence.get("risk_flags"))),
            "evidence": evidence,
        }
    if row.get("shadow_fair_value") is not None:
        evidence = _mapping(row.get("shadow_evidence"))
        return {
            "source": "valuation_shadow_result",
            "is_formal": False,
            "id": row.get("shadow_result_id"),
            "status": row.get("shadow_status"),
            "currency": row.get("shadow_currency"),
            "low_value": row.get("shadow_low_value"),
            "fair_value": row.get("shadow_fair_value"),
            "high_value": row.get("shadow_high_value"),
            "confidence": row.get("shadow_confidence"),
            "model_version": row.get("shadow_model_version"),
            "valued_at": row.get("shadow_created_at"),
            "used_comparables": _used_comparables(evidence),
            "risk_flags": _unique_strings(
                [*_list(row.get("shadow_review_reasons")), *_list(evidence.get("risk_flags"))]
            ),
            "evidence": evidence,
        }
    if row.get("analysis_fair_value") is not None:
        return {
            "source": "card_analysis_snapshot",
            "is_formal": False,
            "id": row.get("analysis_snapshot_id"),
            "status": row.get("analysis_status"),
            "currency": row.get("analysis_currency"),
            "low_value": row.get("analysis_low_value"),
            "fair_value": row.get("analysis_fair_value"),
            "high_value": row.get("analysis_high_value"),
            "confidence": row.get("analysis_confidence"),
            "model_version": row.get("analysis_model_name"),
            "valued_at": row.get("analysis_created_at"),
            "used_comparables": _list(row.get("analysis_evidence_summary")),
            "risk_flags": _unique_strings(_list(row.get("analysis_risks"))),
            "evidence": {
                "evidence_summary": _list(row.get("analysis_evidence_summary")),
                "risks_and_conflicts": _list(row.get("analysis_risks")),
            },
        }
    return {
        "source": "none",
        "is_formal": False,
        "id": None,
        "status": "unresolved",
        "currency": None,
        "low_value": None,
        "fair_value": None,
        "high_value": None,
        "confidence": None,
        "model_version": None,
        "valued_at": None,
        "used_comparables": [],
        "risk_flags": [],
        "evidence": {},
    }


AUDIT_SQL = """
WITH latest_job AS (
    SELECT DISTINCT ON (collection_item_id) *
    FROM valuation_job ORDER BY collection_item_id, created_at DESC
), latest_failed_job AS (
    SELECT DISTINCT ON (collection_item_id) *
    FROM valuation_job
    WHERE status IN ('failed', 'insufficient_evidence')
    ORDER BY collection_item_id, created_at DESC
), usable_valuation AS (
    SELECT DISTINCT ON (collection_item_id) *
    FROM valuation_snapshot
    WHERE identity_compatibility <> 'incompatible'
      AND low_value IS NOT NULL AND fair_value IS NOT NULL AND high_value IS NOT NULL
      AND low_value <= fair_value AND fair_value <= high_value
    ORDER BY collection_item_id, valued_at DESC
), latest_identity AS (
    SELECT DISTINCT ON (collection_item_id) collection_item_id, id, identity,
           review_status, conflicts, created_at
    FROM card_identity_version ORDER BY collection_item_id, version DESC
), confirmed_identity AS (
    SELECT DISTINCT ON (collection_item_id) collection_item_id, id, identity, created_at
    FROM card_identity_version WHERE review_status = 'confirmed'
    ORDER BY collection_item_id, version DESC
), latest_shadow AS (
    SELECT DISTINCT ON (collection_item_id) * FROM valuation_shadow_result
    ORDER BY collection_item_id, created_at DESC
), latest_refresh AS (
    SELECT DISTINCT ON (b.collection_item_id) b.collection_item_id, r.*
    FROM market_identity_binding b
    JOIN market_refresh_job r USING (identity_fingerprint)
    ORDER BY b.collection_item_id, r.created_at DESC
), latest_analysis AS (
    SELECT DISTINCT ON (collection_item_id) * FROM card_analysis_snapshot
    ORDER BY collection_item_id, created_at DESC
), evidence AS (
    SELECT b.collection_item_id, count(*) AS evidence_count,
           jsonb_agg(jsonb_build_object(
             'id', e.id, 'external_sale_id', e.external_sale_id,
             'provider', e.provider_code, 'title', e.raw_title,
             'sale_price', e.sale_price, 'shipping_price', e.shipping_price,
             'currency', e.currency, 'occurred_at', e.occurred_at,
             'evidence_level', e.evidence_level, 'match_confidence', e.match_confidence,
             'source_url', e.source_url, 'identity_classification', e.identity_classification
           ) ORDER BY e.occurred_at DESC) AS rows
    FROM market_identity_binding b
    JOIN market_evidence e USING (identity_fingerprint)
    GROUP BY b.collection_item_id
)
SELECT ci.id AS collection_item_id, ci.display_id, p.full_name AS player_name,
       cs.season_start, cs.season_end, b.name AS brand_name, cs.product_name,
       cs.set_name, cd.card_number, cd.insert_name, cd.parallel_name,
       ci.serial_number, ci.serial_total, ci.grade_company, ci.grade_value,
       cd.is_rookie, cd.is_autograph, cd.is_memorabilia, cd.is_patch,
       j.id AS valuation_job_id, j.status AS job_status, j.attempt_count,
       j.error_message AS job_error, j.result_summary, j.created_at AS job_created_at,
       fj.id AS previous_failure_job_id, fj.status AS previous_failure_job_status,
       fj.error_message AS previous_failure_job_error,
       fj.result_summary AS previous_failure_result_summary,
       fj.created_at AS previous_failure_job_created_at,
       li.review_status AS latest_identity_status, li.identity AS latest_identity,
       li.conflicts AS latest_identity_conflicts,
       ci2.id AS confirmed_identity_version_id, ci2.identity AS confirmed_identity,
       v.id AS valuation_snapshot_id, v.method AS valuation_method,
       v.currency AS valuation_currency, v.low_value AS valuation_low_value,
       v.fair_value AS valuation_fair_value, v.high_value AS valuation_high_value,
       v.confidence AS valuation_confidence, v.model_version AS valuation_model_version,
       v.valued_at AS valuation_valued_at, v.evidence AS valuation_evidence,
       v.identity_compatibility AS valuation_identity_compatibility,
       v.identity_compatibility_reasons AS valuation_identity_compatibility_reasons,
       s.id AS shadow_result_id, s.status AS shadow_status, s.currency AS shadow_currency,
       s.low_value AS shadow_low_value, s.fair_value AS shadow_fair_value,
       s.high_value AS shadow_high_value, s.confidence AS shadow_confidence,
       s.model_version AS shadow_model_version, s.created_at AS shadow_created_at,
       s.review_reasons AS shadow_review_reasons, s.evidence AS shadow_evidence,
       r.id AS refresh_job_id, r.status AS refresh_status,
       r.attempt_count AS refresh_attempt_count, r.created_at AS refresh_created_at,
       r.error_message AS refresh_error, r.result_summary AS refresh_result_summary,
       a.id AS analysis_snapshot_id, a.status AS analysis_status,
       a.currency AS analysis_currency, a.model_name AS analysis_model_name,
       a.created_at AS analysis_created_at, a.fair_value AS analysis_fair_value,
       a.fair_value_low AS analysis_low_value, a.fair_value_high AS analysis_high_value,
       a.valuation_confidence AS analysis_confidence,
       a.evidence_summary AS analysis_evidence_summary,
       a.risks_and_conflicts AS analysis_risks,
       COALESCE(e.evidence_count, 0) AS market_evidence_count,
       COALESCE(e.rows, '[]'::jsonb) AS market_evidence
FROM collection_item ci
JOIN card_definition cd ON cd.id = ci.card_definition_id
JOIN card_set cs ON cs.id = cd.card_set_id
JOIN brand b ON b.id = cs.brand_id
JOIN player p ON p.id = cd.player_id
LEFT JOIN latest_job j ON j.collection_item_id = ci.id
LEFT JOIN latest_failed_job fj ON fj.collection_item_id = ci.id
LEFT JOIN usable_valuation v ON v.collection_item_id = ci.id
LEFT JOIN latest_identity li ON li.collection_item_id = ci.id
LEFT JOIN confirmed_identity ci2 ON ci2.collection_item_id = ci.id
LEFT JOIN latest_shadow s ON s.collection_item_id = ci.id
LEFT JOIN latest_refresh r ON r.collection_item_id = ci.id
LEFT JOIN latest_analysis a ON a.collection_item_id = ci.id
LEFT JOIN evidence e ON e.collection_item_id = ci.id
WHERE ci.deleted_at IS NULL
ORDER BY ci.display_id
"""


def audit_card(row: dict[str, Any]) -> dict[str, Any]:
    causes, cause_details, raw_codes = root_cause_assessment(row)
    result = valuation_result(row)
    if result["source"] == "valuation_snapshot":
        repair_outcome = "formal_valuation_published"
    elif result["source"] == "valuation_shadow_result":
        repair_outcome = "editable_low_confidence_shadow_only"
    elif result["source"] == "card_analysis_snapshot":
        repair_outcome = "model_analysis_only_not_formally_published"
    else:
        repair_outcome = "unresolved"
    uncertainties = result["risk_flags"] or ([] if result["is_formal"] else causes)
    repair_actions = [ROOT_CAUSE_REMEDIATIONS[cause] for cause in causes]
    return json_value(
        {
            **row,
            "root_causes": causes,
            "root_cause_details": cause_details,
            "diagnostic_root_cause_codes": raw_codes,
            "repair_outcome": repair_outcome,
            "repair_action_status": (
                "applied_and_formalized"
                if result["is_formal"]
                else "applied_or_evaluated_but_unresolved"
            ),
            "repair_actions": repair_actions,
            "valuation_result": result,
            "remaining_uncertainties": uncertainties,
        }
    )


def _identity_text(card: dict[str, Any]) -> str:
    season = (
        f"{card.get('season_start')}-{str(card.get('season_end'))[-2:]}"
        if card.get("season_start") and card.get("season_end")
        else str(card.get("season_start") or "")
    )
    return " ".join(
        str(value)
        for value in (
            card.get("player_name"),
            season,
            card.get("brand_name"),
            card.get("product_name"),
            card.get("insert_name"),
            card.get("parallel_name"),
            f"#{card['card_number']}" if card.get("card_number") else None,
        )
        if value
    ).replace("|", "\\|")


def _value_text(result: dict[str, Any]) -> str:
    if result.get("fair_value") is None:
        return "—"
    return (
        f"{result.get('currency') or ''} {result['fair_value']} "
        f"[{result.get('low_value')}–{result.get('high_value')}]"
    ).strip()


def _evidence_text(result: dict[str, Any]) -> str:
    evidence: list[str] = []
    for comparable in result.get("used_comparables", [])[:3]:
        if not isinstance(comparable, dict):
            evidence.append(str(comparable))
            continue
        tier = comparable.get("tier") or comparable.get("evidence_type") or "成交"
        price = (
            comparable.get("landed_price")
            or comparable.get("sale_price")
            or comparable.get("normalized_price")
        )
        currency = comparable.get("currency") or ""
        title = comparable.get("title") or comparable.get("source") or comparable.get("id")
        evidence.append(" ".join(str(value) for value in (tier, currency, price, title) if value))
    return "；".join(evidence).replace("|", "\\|") or "无已采用证据"


def _repair_text(card: dict[str, Any]) -> str:
    result = card["valuation_result"]
    outcomes = {
        "formal_valuation_published": "正式估值已落库",
        "editable_low_confidence_shadow_only": "保留可编辑低置信估值",
        "model_analysis_only_not_formally_published": "仅模型分析，未正式落库",
        "unresolved": "仍未形成估值",
    }
    model = result.get("model_version")
    outcome = f"{outcomes[card['repair_outcome']]}{f'（{model}）' if model else ''}"
    actions = "；".join(card["repair_actions"])
    return f"{actions} → {outcome}" if actions else outcome


def _cards_markdown(cards: Sequence[dict[str, Any]]) -> list[str]:
    lines = [
        "| 卡片 | 身份 | 最新任务 | 根因（可多选） | 修复/结果 | 估值 | 置信度 | 正式估值采用证据 | 仍有不确定性 |",
        "|---:|---|---|---|---|---:|---:|---|---|",
    ]
    for card in cards:
        result = card["valuation_result"]
        causes = "；".join(ROOT_CAUSE_LABELS[cause] for cause in card["root_causes"])
        uncertainties = "；".join(card["remaining_uncertainties"]) or "无结构化风险标记"
        lines.append(
            f"| {card['display_id']} | {_identity_text(card)} | "
            f"{card.get('job_status') or '无任务'} | {causes.replace('|', '\\|')} | "
            f"{_repair_text(card).replace('|', '\\|')} | {_value_text(result)} | "
            f"{result.get('confidence') or '—'} | {_evidence_text(result)} | "
            f"{uncertainties.replace('|', '\\|')} |"
        )
    if not cards:
        lines.append("| — | 无 | — | — | — | — | — | — | — |")
    return lines


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 估值覆盖与失败根因审计",
        "",
        f"生成时间：{report['generated_at']}",
        f"数据库：{report['database']}",
        (
            f"当前卡片：{report['baseline']['active_items']}；"
            f"可用正式估值：{report['baseline']['valued_items']}；"
            f"未估值：{report['baseline']['unvalued_items']}；"
            f"覆盖率：{report['baseline']['coverage_percent']}%"
        ),
        "",
        "说明：根因允许多选；“市场证据”是检索池，“正式估值采用证据”仅来自最新可用估值快照。",
        "",
        "## 当前未估值卡片根因数量（可多选）",
        "",
        "| 根因 | 数量 |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {ROOT_CAUSE_LABELS.get(key, key)} | {count} |"
        for key, count in report["root_cause_counts"].items()
    )
    lines.extend(["", "## 当前未估值逐卡清单", "", *_cards_markdown(report["cards"])])
    if report["selection"]["requested_display_ids"]:
        lines.extend(
            [
                "",
                "## 指定受影响卡片（含已恢复正式估值）",
                "",
                (
                    "请求 display_id："
                    + ", ".join(str(value) for value in report["selection"]["requested_display_ids"])
                ),
                (
                    "未找到："
                    + (
                        ", ".join(
                            str(value) for value in report["selection"]["missing_display_ids"]
                        )
                        or "无"
                    )
                ),
                "",
                "| 根因 | 数量 |",
                "|---|---:|",
            ]
        )
        lines.extend(
            f"| {ROOT_CAUSE_LABELS.get(key, key)} | {count} |"
            for key, count in report["affected_root_cause_counts"].items()
        )
        lines.extend(["", *_cards_markdown(report["affected_cards"])])
    return "\n".join(lines) + "\n"


def build_report(
    rows: Sequence[dict[str, Any]],
    *,
    database: str,
    active: int,
    valued: int,
    requested_display_ids: Sequence[int] = (),
) -> dict[str, Any]:
    audited = [audit_card(dict(row)) for row in rows]
    unvalued = [card for card in audited if not card["valuation_result"]["is_formal"]]
    requested = list(dict.fromkeys(requested_display_ids))
    requested_set = set(requested)
    affected = [card for card in audited if card["display_id"] in requested_set]
    found = {card["display_id"] for card in affected}

    cause_counts = Counter(cause for card in unvalued for cause in card["root_causes"])
    affected_counts = Counter(cause for card in affected for cause in card["root_causes"])
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "database": database,
        "baseline": {
            "active_items": active,
            "valued_items": valued,
            "unvalued_items": active - valued,
            "coverage_percent": round(100 * valued / active, 2) if active else 100,
        },
        "root_cause_labels": ROOT_CAUSE_LABELS,
        "root_cause_remediations": ROOT_CAUSE_REMEDIATIONS,
        "root_cause_counts": dict(cause_counts.most_common()),
        "cards": unvalued,
        "selection": {
            "requested_display_ids": requested,
            "found_display_ids": [card["display_id"] for card in affected],
            "missing_display_ids": [value for value in requested if value not in found],
        },
        "affected_root_cause_counts": dict(affected_counts.most_common()),
        "affected_cards": affected,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument(
        "--display-id",
        dest="display_ids",
        type=int,
        action="append",
        default=[],
        help="Include this active card in the affected-card section; repeat as needed.",
    )
    parser.add_argument("--json", type=Path, default=ROOT / "data" / "valuation-audit-current.json")
    parser.add_argument(
        "--markdown", type=Path, default=ROOT / "docs" / "valuation-audit-current.md"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    database_url = (
        args.database_url
        or dotenv_values(ROOT / ".env").get("DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        db = connection.execute("SELECT current_database() AS name").fetchone()["name"]
        counts = connection.execute(
            """SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS active,
                      count(*) FILTER (WHERE deleted_at IS NULL AND EXISTS (
                        SELECT 1 FROM valuation_snapshot v
                        WHERE v.collection_item_id=collection_item.id
                          AND v.identity_compatibility <> 'incompatible'
                          AND v.low_value <= v.fair_value AND v.fair_value <= v.high_value
                      )) AS valued
               FROM collection_item"""
        ).fetchone()
        rows = [dict(row) for row in connection.execute(AUDIT_SQL).fetchall()]
    report = build_report(
        rows,
        database=db,
        active=counts["active"],
        valued=counts["valued"],
        requested_display_ids=args.display_ids,
    )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    args.markdown.write_text(markdown(report))
    print(
        json.dumps(
            {
                "baseline": report["baseline"],
                "root_cause_counts": report["root_cause_counts"],
                "selection": report["selection"],
                "affected_root_cause_counts": report["affected_root_cause_counts"],
                "json": str(args.json),
                "markdown": str(args.markdown),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
