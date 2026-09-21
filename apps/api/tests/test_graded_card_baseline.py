from datetime import UTC, datetime
from dataclasses import replace
from decimal import Decimal

import pytest

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput, ComparableTier
from card_platform.domain.grade_basis import UNGRADED_SAME_CARD_BASIS
from card_platform.domain.valuation_quality import ShadowSale, ShadowValuationEstimator
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
from card_platform.ingestion.cardhobby import (
    CardHobbyProvider, CardHobbySale, CardHobbySearchResult, assess_cardhobby_candidate,
)
from card_platform.valuation_worker import AutomatedValuationWorker


def target():
    return {
        "player_name": "Baron Davis", "brand_name": "Fleer",
        "product_name": "Flair Showcase", "season_start": 1999, "season_end": 2000,
        "card_number": "19", "insert_name": "NEXT", "parallel_name": None,
        "is_autograph": False, "is_patch": False,
        "grade_company": "AP", "grade_value": Decimal("8"),
    }


def sale(title="1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19", **updates):
    return CardHobbySale(
        item_id="12345", title=title, source_url="https://cardhobby.com.cn/Market/Item/12345",
        occurred_at=datetime(2026, 3, 1, tzinfo=UTC), sale_price=Decimal("3.20"),
        shipping_price=Decimal("15"), bid_count=4, evidence_level="ended_auction",
        sale_type="auction", sold_status_evidence="Status=-2, OutCount=4",
        match_confidence=Decimal("0.95"),
    ).model_copy(update=updates)


def classify(identity, candidate, **kwargs):
    preliminary = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity),
        ComparableInput(
            title=candidate.title, source_code="cardhobby",
            grade_company=candidate.grade_company, grade_value=candidate.grade_value,
        ),
    )
    return enforce_formal_cardhobby_identity(
        identity, candidate.model_dump(mode="json"), preliminary, **kwargs,
    )


def shadow_sale(classification, *, basis=UNGRADED_SAME_CARD_BASIS, sale_id="one"):
    return ShadowSale(
        id=sale_id, provider_code="cardhobby", price=Decimal("3.20"),
        shipping=Decimal("15"), age_days=190, tier=classification.tier,
        similarity=classification.similarity, source_reliability=classification.source_reliability,
        evidence_level="ended_auction", pricing_basis=basis,
    )


def test_same_card_ungraded_sale_reaches_low_confidence_pricing_without_shipping_or_grade_premium():
    identity, candidate = target(), sale()
    score, conflicts, tier = assess_cardhobby_candidate(identity, candidate)
    assert tier == UNGRADED_SAME_CARD_BASIS and score >= Decimal("0.5") and not conflicts
    formal = classify(identity, candidate)
    assert formal.tier == ComparableTier.T2 and not formal.hard_conflicts
    assert formal.field_matches["grade"] is False
    assert formal.field_matches[UNGRADED_SAME_CARD_BASIS] is True
    estimate = ShadowValuationEstimator().estimate([shadow_sale(formal)], identity)
    assert (estimate.low, estimate.fair, estimate.high) == (
        Decimal("1.44"), Decimal("3.20"), Decimal("5.28"),
    )
    assert estimate.confidence <= Decimal("0.25")
    assert "grade_premium_not_established" in estimate.review_reasons
    assert identity["grade_company"] == "AP" and identity["grade_value"] == Decimal("8")
    comp = AutomatedValuationWorker._cardhobby_comparables(CardHobbySearchResult(sales=[candidate]))[0]
    assert AutomatedValuationWorker._comparable_grade_matches(identity, comp)


@pytest.mark.parametrize("title", [
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #18",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 /100",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis Ball of Fame #10",
    "2001-2002 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 PSA 10",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 AP 7",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 BGS 8.6",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 PSA-10",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 老卡PSA10分",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 AP-8",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 PSA GEM MINT 10",
    "1999-2000 FLEER FLAIR SHOWCASE Baron Davis NEXT BASE #19 BGS NM-MT 8",
])
def test_baseline_marker_cannot_resurrect_a_different_card_or_explicit_grade(title):
    candidate = sale(title, matching_fields=[UNGRADED_SAME_CARD_BASIS])
    result = classify(target(), candidate)
    assert result.tier == ComparableTier.REJECTED


@pytest.mark.parametrize("kwargs", [{"visual_verdict": "different"}, {"identity_rejected": True}])
def test_baseline_preserves_explicit_visual_and_user_rejections(kwargs):
    assert classify(target(), sale(), **kwargs).tier == ComparableTier.REJECTED


def test_untyped_t2_keeps_existing_analog_policy_and_same_grade_sales_take_precedence():
    identity = target()
    baseline = classify(identity, sale())
    estimator = ShadowValuationEstimator()
    untyped = estimator.estimate([shadow_sale(baseline, basis=None)], identity)
    assert untyped.fair == Decimal("3.20")
    assert UNGRADED_SAME_CARD_BASIS not in untyped.review_reasons
    assert "cardhobby_same_type_proxy_range" in untyped.review_reasons
    exact = classify(identity, sale(grade_company="AP", grade_value=Decimal("8")))
    assert exact.tier in {ComparableTier.T0, ComparableTier.T1}
    result = estimator.estimate([
        shadow_sale(baseline), shadow_sale(exact, basis=None, sale_id="exact"),
    ], identity)
    assert result.used_ids == ("exact",)
    assert UNGRADED_SAME_CARD_BASIS not in result.review_reasons


def test_raw_target_does_not_use_graded_sale():
    identity = {**target(), "grade_company": None, "grade_value": None}
    assert classify(identity, sale(grade_company="AP", grade_value=Decimal("8"))).tier == ComparableTier.REJECTED


def test_same_card_basis_outweighs_high_price_ordinary_t2_analogs():
    identity = target()
    baseline = shadow_sale(classify(identity, sale()))
    analogs = [
        replace(
            baseline,
            id=f"analog-{index}",
            price=Decimal("3200"),
            age_days=0,
            similarity=Decimal("0.95"),
            pricing_basis=None,
        )
        for index in range(4)
    ]
    result = ShadowValuationEstimator().estimate([*analogs, baseline], identity)

    assert result.used_ids == ("one",)
    assert (result.low, result.fair, result.high) == (
        Decimal("1.44"), Decimal("3.20"), Decimal("5.28"),
    )
    assert result.confidence <= Decimal("0.25")
    assert UNGRADED_SAME_CARD_BASIS in result.review_reasons
    assert "grade_premium_not_established" in result.review_reasons
    assert result.rejected == tuple(
        {"id": comp.id, "reasons": ["weaker_relative_tier"]} for comp in analogs
    )


@pytest.mark.parametrize("parallel", ["Gold", "Blue"])
def test_named_numbered_parallel_uses_its_same_card_ungraded_basis(parallel):
    identity = {
        "player_name": "Example Player", "brand_name": "Panini",
        "product_name": "Prizm", "season_start": 2023, "season_end": 2024,
        "card_number": "19", "parallel_name": parallel, "serial_total": 10,
        "grade_company": "PSA", "grade_value": Decimal("9"),
    }
    candidate = sale(f"2023-24 Panini Prizm Example Player #19 {parallel} /10")
    result = classify(identity, candidate)
    assert result.tier == ComparableTier.T2 and not result.hard_conflicts
    assert result.field_matches["parallel"] is True
    assert result.field_matches["serial_total"] is True
    assert result.field_matches[UNGRADED_SAME_CARD_BASIS] is True
    assert ShadowValuationEstimator().estimate([shadow_sale(result)], identity).fair == Decimal("3.20")


@pytest.mark.parametrize(("target_parallel", "sale_parallel", "sale_total"), [
    ("Gold", "Blue", 10), ("Blue", "Gold", 10),
    ("Gold", "Gold", 25), ("Blue", "Blue", 25),
])
def test_named_numbered_parallel_baseline_rejects_other_parallel_or_total(
    target_parallel, sale_parallel, sale_total,
):
    identity = {
        "player_name": "Example Player", "brand_name": "Panini",
        "product_name": "Prizm", "season_start": 2023, "season_end": 2024,
        "card_number": "19", "parallel_name": target_parallel, "serial_total": 10,
        "grade_company": "PSA", "grade_value": Decimal("9"),
    }
    candidate = sale(
        f"2023-24 Panini Prizm Example Player #19 {sale_parallel} /{sale_total}",
        matching_fields=[UNGRADED_SAME_CARD_BASIS],
    )
    assert classify(identity, candidate).tier == ComparableTier.REJECTED


@pytest.mark.parametrize("company,grade", [("AP", "8"), ("GBTC", "9.5"), ("BGS", "8.6"), ("PSA", "6")])
def test_market_parser_and_grade_detection_share_company_and_score_coverage(company, grade):
    candidate = CardHobbyProvider.parse_market_item({
        "ID": 12345, "Title": f"1999-00 FLEER FLAIR SHOWCASE Baron Davis NEXT #19 {company} {grade}",
        "Status": -2, "OutCount": 4, "ByWay": 2,
        "EffectiveDate": "2026-03-01 21:37:58", "LowestPrice": "3.2",
    })
    assert candidate.grade_company == company
    assert candidate.grade_value == Decimal(grade)


@pytest.mark.parametrize(("grade_label", "company", "grade"), [
    ("PSA-10", "PSA", "10"), ("老卡PSA10分", "PSA", "10"),
    ("AP-8", "AP", "8"), ("PSA GEM MINT 10", "PSA", "10"),
    ("BGS NM-MT 8", "BGS", "8"),
])
def test_market_parser_recognizes_grade_separators_and_description_words(grade_label, company, grade):
    candidate = CardHobbyProvider.parse_market_item({
        "ID": 12345, "Title": f"1999-00 FLEER FLAIR SHOWCASE Baron Davis NEXT #19 {grade_label}",
        "Status": -2, "OutCount": 4, "ByWay": 2,
        "EffectiveDate": "2026-03-01 21:37:58", "LowestPrice": "3.2",
    })
    assert candidate.grade_company == company
    assert candidate.grade_value == Decimal(grade)


@pytest.mark.asyncio
async def test_unverified_model_discovery_prose_does_not_become_market_facts(monkeypatch):
    # This worktree deliberately skips model fallback without a client. A
    # sentinel reaches the monkeypatched fallback without any network request.
    provider = CardHobbyProvider(object(), "unused")
    async def direct(_identity):
        return CardHobbySearchResult(notes=["公开查询返回候选，身份待确认"])
    async def model(_identity):
        return CardHobbySearchResult(notes=["虚构限20 RPA且没有任何成交"])
    monkeypatch.setattr(provider, "_search_direct", direct)
    monkeypatch.setattr(provider, "_search_model", model)
    result = await provider.search(target())
    assert "公开查询返回候选，身份待确认" in result.notes
    assert all("虚构限20" not in note for note in result.notes)
