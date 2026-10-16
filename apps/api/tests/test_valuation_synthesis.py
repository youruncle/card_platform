from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from card_platform.domain.valuation_synthesis import synthesize_valuation

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def inputs():
    original = dict(
        id="v1",
        currency="CNY",
        low_value=80,
        fair_value=100,
        high_value=120,
        confidence=0.8,
        valued_at=NOW,
        method="ai_estimate",
        evidence={
            "identity_version_id": "identity-1",
            "comparables": [{"source_url": "https://market.test/1"}],
        },
    )
    codex = dict(
        id="c1",
        analysis_version=2,
        currency="CNY",
        fair_value_low=100,
        fair_value=120,
        fair_value_high=140,
        valuation_confidence=0.8,
        identity_confidence=0.95,
        identity_version_id="identity-1",
        created_at=NOW,
        raw_result={},
        evidence_summary=[
            dict(
                type="completed_sale",
                verified=True,
                url="https://market.test/2",
                price=120,
                currency="CNY",
                sold_at="2026-09-26",
                match_type="exact",
            )
        ],
    )
    return original, codex


def run(original, codex, identity="identity-1"):
    return synthesize_valuation(original, codex, identity, "CNY", now=NOW)


def test_independent_evidence_combines_prices_without_changing_sources():
    original, codex = inputs()
    before = deepcopy((original, codex))
    result = run(original, codex)
    assert result["status"] == "combined"
    assert result["fair_value"] == Decimal("110.00")
    assert result["low_value"] == Decimal("90.00")
    assert result["high_value"] == Decimal("130.00")
    assert (original, codex) == before


def test_supported_codex_rescues_missing_valuation():
    _, codex = inputs()
    result = run(None, codex)
    assert result["status"] == "codex_supported"
    assert result["fair_value"] == Decimal("120")


@pytest.mark.parametrize(
    "change",
    [
        {"evidence_summary": []},
        {"raw_result": {"reliable_price": False}},
        {"valuation_confidence": 0.3},
        {"valuation_confidence": None},
    ],
)
def test_confidence_or_experience_cannot_replace_sale_evidence(change):
    _, codex = inputs()
    codex.update(change)
    result = run(None, codex)
    assert result["fair_value"] is None
    assert result["status"] == "insufficient_evidence"


@pytest.mark.parametrize(
    "change",
    [
        {"verified": False},
        {"type": "public_research"},
        {"type": "asking_price"},
        {"price": None},
        {"price": "NaN"},
        {"url": ""},
        {"currency": "USD"},
        {"sold_at": "2024-01-01"},
        {"sold_at": "2027-01-01"},
        {"match_type": "unknown"},
    ],
)
def test_unverified_or_invalid_sale_cannot_publish(change):
    _, codex = inputs()
    codex["evidence_summary"][0].update(change)
    assert run(None, codex)["fair_value"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"identity_version_id": None},
        {"identity_version_id": "old"},
        {"identity_confidence": 0.3},
        {"currency": "USD"},
        {"fair_value_low": 130},
        {"fair_value": "NaN"},
    ],
)
def test_unsafe_codex_does_not_overwrite_original(change):
    original, codex = inputs()
    codex.update(change)
    result = run(original, codex)
    assert result["fair_value"] == 100
    assert result["status"] in {"review_required", "insufficient_evidence"}


def test_large_price_conflict_requires_review():
    original, codex = inputs()
    codex.update(fair_value_low=250, fair_value=300, fair_value_high=350)
    result = run(original, codex)
    assert result["status"] == "review_required"
    assert result["fair_value"] == 100


def test_shared_sale_does_not_double_count_aggregate_prices():
    original, codex = inputs()
    codex["evidence_summary"][0]["url"] = "https://market.test/1?utm_source=codex"
    result = run(original, codex)
    assert result["status"] == "original_only"
    assert result["fair_value"] == 100


def test_duplicate_codex_evidence_does_not_increase_weight():
    original, codex = inputs()
    result = run(original, codex)
    codex["evidence_summary"] *= 3
    assert run(original, codex)["fair_value"] == result["fair_value"]
    assert len(run(original, codex)["evidence_urls"]) == 1


def test_older_and_comparable_evidence_has_less_weight():
    original, codex = inputs()
    codex["created_at"] = datetime(2026, 3, 26, tzinfo=timezone.utc)
    codex["evidence_summary"][0].update(sold_at="2026-03-26", match_type="comparable")
    assert 100 < run(original, codex)["fair_value"] < 110


def test_manual_price_is_preserved():
    original, codex = inputs()
    original["method"] = "manual"
    assert run(original, codex)["fair_value"] == 100


def test_explicit_identity_conflict_cannot_publish_despite_bound_version():
    _, codex = inputs()
    codex["raw_result"]["identity_conflict"] = True
    result = run(None, codex)
    assert result["fair_value"] is None
    assert result["status"] == "review_required"


@pytest.mark.parametrize("binding", [None, "old-version"])
def test_original_without_current_identity_binding_cannot_be_combined(binding):
    original, codex = inputs()
    original["evidence"]["identity_version_id"] = binding
    result = run(original, codex)
    assert result["status"] == "review_required"
    assert result["fair_value"] == 100


def test_a_token_sale_cannot_authorize_an_unsupported_codex_price():
    _, codex = inputs()
    codex["evidence_summary"][0]["price"] = 1
    codex.update(fair_value_low=90000, fair_value=100000, fair_value_high=110000)
    result = run(None, codex)
    assert result["status"] == "review_required"
    assert result["fair_value"] is None
