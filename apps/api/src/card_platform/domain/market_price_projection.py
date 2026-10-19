"""Read-only market publication checks against current ownership provenance."""
from decimal import Decimal, InvalidOperation

from card_platform.purchase_source_roles import is_owned_purchase_source

CONTRACT = "market-price-ownership-read-v1"
HISTORICAL_CODEX_MODELS = {"codex-independent-fallback-v1", "codex-researched-fallback-v2"}


def apply_market_snapshot_read_contract(row: dict, snapshot: dict | None):
    """Withhold a contaminated market projection, retaining its immutable record.

    Only the owner/item-scoped assignment join is authority. A persisted role
    boolean, notes link or another holding's purchase does not establish ownership.
    Manual prices and explicitly labelled historical Codex references retain their
    existing presentation contract.
    """
    purchase_sources = row.pop("market_purchase_sources", None) or []
    if (not snapshot or snapshot.get("method") in {"manual", "manual_override"}
            or snapshot.get("model_version") in HISTORICAL_CODEX_MODELS):
        return row, snapshot
    evidence = snapshot.get("evidence") or {}
    if not isinstance(evidence, dict):
        return row, snapshot
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
    if not owned:
        return row, snapshot
    prior_status = row.get("valuation_status")
    summary = dict(row.get("valuation_result_summary") or {})
    if prior_status in {None, "succeeded"}:
        summary["reason"] = "持仓本件成交已保留为历史参考；缺少可独立支持的市场估值"
    summary.update(publication_validation={"contract": CONTRACT, "status": "withheld",
                       "reason_codes": ["owned_copy_history_not_independent_market_price"],
                       "snapshot_id": str(snapshot.get("id")), "source_urls": owned,
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
