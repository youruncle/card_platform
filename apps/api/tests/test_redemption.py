from decimal import Decimal

import pytest

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput, ComparableTier
from card_platform.domain.redemption import redemption_conflict, redemption_identity
from card_platform.ingestion.cardhobby import (
    CardHobbyProvider,
    CardHobbySale,
    assess_cardhobby_match,
)
from card_platform.market_data.models import MarketIdentity
from card_platform.recognition_benchmark_predictor import canonicalize_identity
from card_platform.schemas import CardRecognitionResult
from card_platform.valuation_worker import market_search_query_ladder


def identity(**updates):
    return {
        "player_name": "Khaman Maluach",
        "season_start": 2025,
        "season_end": 2026,
        "brand_name": "Topps",
        "product_name": "Topps Inception",
        "card_number": "RJ-KM",
        "insert_name": "Rookie Jumbo Relic Autograph",
        "is_autograph": True,
        "is_memorabilia": True,
        "is_redemption": True,
        **updates,
    }


TITLE = "2025-26 Topps Inception Khaman Maluach RJ-KM Rookie Jumbo Relic Autograph"


def test_old_recognition_recovers_form_and_promised_material_without_guessing_usage():
    result = canonicalize_identity(
        CardRecognitionResult(
            **identity(is_redemption=None, is_memorabilia=None),
            confidence=Decimal("0.86"),
            caveats=[
                "This is a Redemption Card; no physical image of the redeemed card is available"
            ],
            field_evidence={"is_memorabilia": ["Rookie Jumbo Relic Autograph"]},
        )
    )
    assert result.is_redemption is True
    assert result.redemption_status == "unknown"
    assert result.is_memorabilia is True
    assert result.is_autograph is True
    assert result.serial_total is None
    assert result.is_patch is not True


@pytest.mark.parametrize(
    "suffix,reason",
    [
        ("", "redemption_form_conflict"),
        (" Redeemed Redemption", "redemption_unusable_or_expired"),
        (" Expired Redemption", "redemption_unusable_or_expired"),
        (" 交换板 已兑换", "redemption_unusable_or_expired"),
    ],
)
def test_incompatible_sales_rejected_even_with_same_visual_template(suffix, reason):
    result = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity()),
        ComparableInput(
            title=TITLE + suffix,
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
            visual_reasons=("local_geometry_verified_same_printed_template",),
        ),
    )
    assert result.tier == ComparableTier.REJECTED
    assert reason in result.hard_conflicts


def test_matching_redemption_is_accepted():
    result = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity()),
        ComparableInput(title=TITLE + " Unredeemed Redemption"),
    )
    assert result.tier != ComparableTier.REJECTED, result.hard_conflicts
    assert redemption_conflict(identity(is_redemption=False), TITLE + " Redemption")


def test_fingerprints_separate_physical_form_and_code_status():
    fingerprints = {
        MarketIdentity.model_validate(identity(**updates)).fingerprint
        for updates in [{"is_redemption": False}, {}, {"redemption_status": "redeemed"}]
    }
    assert len(fingerprints) == 3


def test_query_budgets_reserve_redemption_searches():
    queries = CardHobbyProvider.build_query_plan(identity())
    assert len(queries) <= 12
    assert any("交换" in query for _, query in queries)
    assert any("redemption" in query for _, query in queries)
    assert "redemption" in market_search_query_ladder(identity())[0]


def test_cardhobby_rejects_delivered_card():
    sale = CardHobbySale(
        item_id=None,
        title=TITLE,
        source_url="https://www.cardhobby.com.cn/market/feedback/469520",
        occurred_at="2026-07-01T20:00:00+08:00",
        sale_price=Decimal("380"),
        sale_type="auction",
        evidence_level="confirmed_transaction",
        sold_status_evidence="来自售出的宝贝列表，竞价已结束",
        match_confidence=Decimal("0.95"),
    )
    confidence, reasons = assess_cardhobby_match(identity(), sale)
    assert confidence == 0
    assert "redemption_form_conflict" in reasons


def test_no_form_inference_from_congratulations_or_future_expiration():
    assert redemption_identity({"title": "Congratulations certified autograph"}) == (
        False,
        "unknown",
    )
    assert redemption_identity({"title": "Redemption Card Expires 5/31/2036"}) == (True, "unknown")


@pytest.mark.parametrize("title", ["Redemption not redeemed", "交换 未兑换", "交换 未刮"])
def test_unused_code_words_do_not_match_redeemed(title):
    assert redemption_identity({"title": title}) == (True, "unredeemed")


def test_reviewed_evidence_cannot_override_redemption_form():
    from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
    from card_platform.domain.comparables import Classification

    sale = CardHobbySale(
        item_id=None,
        title=TITLE,
        source_url="https://www.cardhobby.com.cn/market/feedback/469520",
        occurred_at="2026-07-01T20:00:00+08:00",
        sale_price=Decimal("380"),
        sale_type="auction",
        evidence_level="confirmed_transaction",
        sold_status_evidence="来自售出的宝贝列表，竞价已结束",
        match_confidence=Decimal("0.95"),
    )
    result = enforce_formal_cardhobby_identity(
        identity(),
        sale.model_dump(),
        Classification(ComparableTier.T0, Decimal("1"), Decimal("0.9")),
        identity_accepted=True,
        visual_verdict="same",
    )
    assert result.tier == ComparableTier.REJECTED
    assert "redemption_form_conflict" in result.hard_conflicts


def test_market_identity_keeps_promised_relic_attribute():
    market = MarketIdentity.model_validate(identity())
    assert market.is_memorabilia is True
    assert market.model_dump()["is_memorabilia"] is True
