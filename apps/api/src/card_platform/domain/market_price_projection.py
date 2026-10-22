"""Read-only market publication checks against current ownership provenance."""
from decimal import Decimal, InvalidOperation

from card_platform.purchase_source_roles import is_owned_purchase_source
from card_platform.domain.finish_evidence import explicit_visual_version_conflict

CONTRACT = "market-price-publication-read-v2"
HISTORICAL_CODEX_MODELS = {"codex-independent-fallback-v1", "codex-researched-fallback-v2"}


def _qualified_research_snapshot(row, snapshot, evidence):
    if (snapshot.get('model_version') != 'codex-researched-fallback-v2'
            or evidence.get('pricing_basis') != 'codex_researched_observation'):
        return False
    from card_platform.domain.codex_sales_research import (
        assess_sales_research, research_target_binding_reasons, research_backed_prices,
    )
    from card_platform.codex_joint_context import current_image_hashes
    try:
        research = evidence.get('market_research') or {}
        # The owner/item-scoped SQL join already supplies the original analysis.
        # A research-shaped JSON blob alone cannot attest local capture checks.
        history = row.get('synthesis_codex_history') or []
        source = next((entry for entry in history if isinstance(entry, dict)
            and evidence.get('source_analysis_id')
            and str(entry.get('id')) == str(evidence['source_analysis_id'])), None)
        if (not source or not research_backed_prices(source)
                or (source.get('raw_result') or {}).get('market_research') != research):
            return False
        assessed = assess_sales_research(research)
        return (assessed['status'] == 'supported_observation'
            and assessed['currency'] == snapshot.get('currency')
            and not research_target_binding_reasons(research, row, current_image_hashes(row))
            and all(Decimal(str(snapshot.get(key))) == Decimal(str(assessed.get(value)))
                    for key, value in [('low_value', 'low_value'), ('fair_value', 'fair_value'),
                                       ('high_value', 'high_value')]))
    except (TypeError, ValueError, KeyError, AttributeError, ArithmeticError):
        # Historical data can predate the structured contract. One malformed
        # reference must not fail the entire account's portfolio read.
        return False


def apply_market_snapshot_read_contract(row: dict, snapshot: dict | None):
    """Withhold a contaminated market projection, retaining its immutable record.

    Only the owner/item-scoped assignment join is authority. A persisted role
    boolean, notes link or another holding's purchase does not establish ownership.
    Manual prices and qualified new research retain their authority. Historical
    references keep their original values in diagnostics, outside market totals.
    """
    purchase_sources = row.pop("market_purchase_sources", None) or []
    if not snapshot or snapshot.get("method") in {"manual", "manual_override"}:
        return row, snapshot
    evidence = snapshot.get("evidence") or {}
    if not isinstance(evidence, dict):
        if snapshot.get('model_version') not in HISTORICAL_CODEX_MODELS:
            return row, snapshot
        evidence = {}
    legacy = (snapshot.get('model_version') in HISTORICAL_CODEX_MODELS
              and not _qualified_research_snapshot(row, snapshot, evidence))
    used = []
    for key in ("used_comparables", "comparables"):
        entries = evidence.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("used_for_center") is False:
                continue
            if "final_weight" in entry:
                try:
                    weight = Decimal(str(entry["final_weight"]))
                    if weight.is_finite() and weight <= 0:
                        continue
                except (InvalidOperation, ValueError):
                    pass
            used.append(entry)
    identity = {"owner_id": row.get("owner_id"), "collection_item_id": row.get("id"),
                "purchase_sources": purchase_sources}
    owned = [entry["source_url"] for entry in used if isinstance(entry, dict)
             and is_owned_purchase_source(identity, entry)]
    conflicts = [entry.get('source_url') for entry in used
                 if explicit_visual_version_conflict(entry)]
    if not owned and not conflicts and not legacy:
        return row, snapshot
    prior_status = row.get("valuation_status")
    summary = row.get('valuation_result_summary')
    summary = dict(summary) if isinstance(summary, dict) else {}
    codes = []
    if owned:
        codes.append('owned_copy_history_not_independent_market_price')
    if conflicts:
        codes.append('explicit_visual_version_conflict_in_market_center')
    if legacy:
        codes.append('legacy_codex_reference_not_current_market_price')
        summary['historical_reference'] = dict(
            pricing_basis=evidence.get('pricing_basis') or 'codex_independent_fallback',
            source_analysis_id=evidence.get('source_analysis_id'),
            source_analyzed_at=evidence.get('source_analyzed_at'),
            identity_compatibility=snapshot.get('identity_compatibility', 'unknown'),
            current_job_verified=False, current_job_status=prior_status,
            note='历史Codex参考值保留；缺少当前商品级成交研究，不计入独立市场估值')
    if prior_status in {None, "succeeded"}:
        summary["reason"] = ('持仓本件成交已保留为历史参考；缺少可独立支持的市场估值' if owned else
                             '原估值依据存在版本冲突或缺少当前研究；保留历史参考，市场估值待复核')
    summary.update(publication_validation={"contract": CONTRACT, "status": "withheld",
                       "reason_codes": codes,
                       "snapshot_id": str(snapshot.get("id")), "source_urls": list(dict.fromkeys(
                           url for url in [*owned, *conflicts] if isinstance(url, str))),
                       "persisted_job_status": prior_status,
                       "historical_value": {key: snapshot.get(key) for key in
                           ("currency", "low_value", "fair_value", "high_value")}})
    row.update(valuation_currency=None, valuation_low=None, fair_value=None,
               valuation_high=None,
               valuation_status=("insufficient_evidence" if prior_status in {None, "succeeded"}
                                 else prior_status),
               valuation_result_summary=summary,
               shadow_valuation_currency=None, shadow_valuation_low=None,
               shadow_fair_value=None, shadow_valuation_high=None,
               shadow_valuation_status="insufficient")
    # Neither the joint valuation nor the shadow fallback may reintroduce the
    # same contaminated market estimate. No persisted snapshot or job is changed.
    return row, None
