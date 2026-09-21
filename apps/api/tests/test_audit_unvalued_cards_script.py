import importlib.util
from decimal import Decimal
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "audit_unvalued_cards.py"
SPEC = importlib.util.spec_from_file_location("audit_unvalued_cards_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)


def test_root_causes_map_diagnostics_to_multiple_stable_categories() -> None:
    row = {
        "valuation_snapshot_id": None,
        "confirmed_identity": None,
        "latest_identity_conflicts": [{"field": "card_number", "reason": "role_misplaced"}],
        "market_evidence_count": 0,
        "job_status": "failed",
        "refresh_status": "insufficient",
        "analysis_status": None,
        "result_summary": {
            "missing_identity_fields": ["product_name"],
            "valuation_failure_diagnostics": {
                "stage": "cardhobby_recall_and_similarity_gate",
                "rejected_candidate_count": 7,
                "root_cause_codes": [
                    "no_candidate_reached_minimum_similarity",
                    "product_mismatch",
                ],
            },
        },
    }

    causes, details, raw_codes = SCRIPT.root_cause_assessment(row)

    assert causes == [
        "identity_fields_missing_or_role_misplaced",
        "product_brand_normalization_or_recall",
        "title_normalization_or_comparable_hard_conflict",
        "sparse_comparable_samples",
        "refresh_retry_or_job_state",
        "model_analysis_missing_or_not_persisted",
    ]
    assert raw_codes == ["no_candidate_reached_minimum_similarity", "product_mismatch"]
    assert details["title_normalization_or_comparable_hard_conflict"] == [
        "rejected_candidates=7"
    ]


def test_formal_snapshot_is_preferred_over_shadow_and_analysis() -> None:
    row = {
        "valuation_snapshot_id": "formal-id",
        "valuation_currency": "CNY",
        "valuation_low_value": Decimal("28.90"),
        "valuation_fair_value": Decimal("34.00"),
        "valuation_high_value": Decimal("39.10"),
        "valuation_confidence": Decimal("0.85"),
        "valuation_model_version": "relative-multimodal-comps-1.0",
        "valuation_evidence": {
            "used_comparables": [{"tier": "T0", "title": "CardHobby ended auction"}],
            "risk_flags": ["single_provider_concentration"],
        },
        "shadow_fair_value": Decimal("999"),
        "analysis_fair_value": Decimal("888"),
    }

    result = SCRIPT.valuation_result(row)

    assert result["source"] == "valuation_snapshot"
    assert result["is_formal"] is True
    assert result["fair_value"] == Decimal("34.00")
    assert result["used_comparables"] == [
        {"tier": "T0", "title": "CardHobby ended auction"}
    ]
    assert result["risk_flags"] == ["single_provider_concentration"]


def test_requested_affected_cards_include_cards_with_restored_formal_value() -> None:
    common = {
        "confirmed_identity": {},
        "latest_identity_conflicts": [],
        "market_evidence_count": 3,
        "job_status": "succeeded",
        "refresh_status": "succeeded",
        "analysis_status": None,
    }
    restored = {
        **common,
        "display_id": 361,
        "valuation_snapshot_id": "formal-id",
        "valuation_currency": "CNY",
        "valuation_low_value": Decimal("28.90"),
        "valuation_fair_value": Decimal("34.00"),
        "valuation_high_value": Decimal("39.10"),
        "valuation_confidence": Decimal("0.85"),
        "valuation_evidence": {"used_comparables": [{"tier": "T0"}]},
        "previous_failure_result_summary": {
            "valuation_failure_diagnostics": {
                "stage": "cardhobby_recall_and_similarity_gate",
                "root_cause_codes": ["product_mismatch"],
            }
        },
    }
    unresolved = {
        **common,
        "display_id": 183,
        "valuation_snapshot_id": None,
        "analysis_status": "failed",
        "market_evidence_count": 0,
    }

    report = SCRIPT.build_report(
        [restored, unresolved],
        database="card_platform",
        active=2,
        valued=1,
        requested_display_ids=[361, 999, 361],
    )

    assert [card["display_id"] for card in report["cards"]] == [183]
    assert [card["display_id"] for card in report["affected_cards"]] == [361]
    assert report["affected_cards"][0]["repair_outcome"] == "formal_valuation_published"
    assert report["affected_cards"][0]["repair_action_status"] == "applied_and_formalized"
    assert report["affected_cards"][0]["repair_actions"] == [
        "统一品牌/产品别名、子产品边界和卡淘查询扩展"
    ]
    assert report["affected_cards"][0]["valuation_result"]["fair_value"] == "34.00"
    assert report["selection"] == {
        "requested_display_ids": [361, 999],
        "found_display_ids": [361],
        "missing_display_ids": [999],
    }
    assert report["affected_root_cause_counts"] == {
        "product_brand_normalization_or_recall": 1
    }
