from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from card_platform.comparable_image_verifier import ComparableImageVerifier, VisualCandidate
from card_platform.config import Settings
from card_platform.domain.comparables import Classification, ComparableTier
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
from card_platform.formal_cardhobby_gate import title_discloses_damage
from card_platform.formal_cardhobby_gate import title_requires_named_parallel
from card_platform.ingestion.cardhobby import CardHobbyProvider
from card_platform.domain.valuation_quality import ShadowSale, ShadowValuationEstimator


def source_row(**updates):
    return {
        "ID": 123, "Title": "2023 Synthetic Player Product #7", "Status": -2,
        "OutCount": 2, "PriceCount": 2, "ByWay": 2, "LowestPrice": "28.50",
        "Price": "100", "EffectiveDate": "2025-09-01 20:00:00", **updates,
    }


@pytest.mark.parametrize("updates", [
    {"Status": 1}, {"Status": "invalid"}, {"OutCount": "unknown"},
    {"OutCount": 0, "PriceCount": 9}, {"ByWay": 1}, {"ByWay": None},
    {"LowestPrice": 0}, {"LowestPrice": None}, {"LowestPrice": "NaN"},
    {"LowestPrice": "Infinity"}, {"LowestPrice": "invalid"},
    {"EffectiveDate": "unknown"}, {"EffectiveDate": "2099-01-01 00:00:00"},
])
def test_malformed_active_future_or_unpriced_rows_cannot_create_sales(updates):
    assert CardHobbyProvider.parse_market_item(source_row(**updates)) is None


def test_real_ended_auction_is_cny_hammer_price_with_shanghai_time_and_no_payment_claim():
    sale = CardHobbyProvider.parse_market_item(source_row())
    assert sale.sale_price == Decimal("28.50")
    assert sale.occurred_at.utcoffset() == timedelta(hours=8)
    assert sale.evidence_level == "ended_auction"
    assert sale.bid_count == 2


@pytest.mark.parametrize("updates,reason", [
    ({"bid_count": 0}, "public_auction_has_no_verified_bid"),
    ({"bid_count": None}, "public_auction_has_no_verified_bid"),
    ({"sale_type": "fixed_price"}, "public_auction_sale_type_not_verified"),
    ({"evidence_level": "confirmed_transaction"}, "public_auction_does_not_prove_payment"),
    ({"occurred_at": "2099-01-01T00:00:00+08:00"}, "sale_timestamp_in_future"),
    ({"occurred_at": "2025-01-01T00:00:00"}, "sale_timestamp_timezone_missing"),
    ({"fx_rate_to_base": "7.2"}, "cardhobby_cny_source_has_unverified_fx"),
])
def test_persisted_acceptance_and_positive_visual_cannot_override_source_facts(
    monkeypatch, updates, reason,
):
    monkeypatch.setattr(
        "card_platform.formal_cardhobby_gate.assess_cardhobby_candidate",
        lambda *_: (Decimal("1"), [], "exact_or_near_exact"),
    )
    payload = CardHobbyProvider.parse_market_item(source_row()).model_dump(mode="json")
    result = enforce_formal_cardhobby_identity(
        {}, {**payload, **updates},
        Classification(ComparableTier.T0, Decimal("1"), Decimal("0.7")),
        identity_accepted=True, visual_verdict="same",
    )
    assert result.tier == ComparableTier.REJECTED
    assert reason in result.hard_conflicts


@pytest.mark.asyncio
async def test_visual_prompt_contains_target_back_and_parallel_requirement():
    verifier = ComparableImageVerifier(Settings(vision_api_key=None))
    create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content='{"decisions": []}')
    )]))
    verifier.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=create,
    )))
    await verifier._compare_batch(
        b"front", "image/jpeg", [(VisualCandidate("candidate", "title", "url"),
                                   b"candidate", "image/jpeg")],
        target_back=(b"back", "image/jpeg"), low_resolution_target=False,
        requires_parallel_match=True,
    )
    content = create.call_args.kwargs["messages"][-1]["content"]
    assert sum(item["type"] == "image_url" for item in content) == 3
    texts = " ".join(item["text"] for item in content if item["type"] == "text")
    assert "目标卡背面（非候选）" in texts
    assert "本次必须确认平行版本" in texts
    assert "统计年份不是发行年份" in texts


@pytest.mark.parametrize("basis", [None, "ungraded_same_card_no_grade_premium"])
def test_formal_price_cannot_publish_product_or_ungraded_analogy(basis):
    candidate = ShadowSale(
        "analogy", "cardhobby", Decimal("100"), Decimal("0"), 5,
        ComparableTier.T2, Decimal("0.9"), Decimal("0.7"), "ended_auction",
        pricing_basis=basis,
    )
    identity = {"player_name": "Synthetic Player", "product_name": "Synthetic Product",
                "season_start": 2023, "card_number": "7",
                "grade_company": "PSA", "grade_value": Decimal("10")}
    result = ShadowValuationEstimator().estimate([candidate], identity, require_same_card=True)
    assert result.status == "insufficient" and result.fair is None
    assert "formal_price_requires_same_card_and_grade" in result.rejected[0]["reasons"]


def test_legacy_identity_review_preserves_schema_defaults_for_new_fields():
    from card_platform.identity_review_api import _identity
    from card_platform.domain.identity_review import ConfirmedIdentity

    legacy = {
        "player_name": "Synthetic Player", "season_start": 2023,
        "brand_name": "Panini", "product_name": "Prizm", "card_number": "7",
        "is_rookie": False, "is_autograph": False, "is_memorabilia": False,
    }
    restored = _identity(legacy)
    assert restored["is_redemption"] is False
    assert restored["redemption_status"] == "unknown"
    assert restored["memorabilia_types"] == []
    assert ConfirmedIdentity.model_validate(restored).redemption_status == "unknown"


@pytest.mark.parametrize("title,damaged", [
    ("Player Chrome 印刷线如图", True),
    ("Player Chrome 无明显印刷线", False),
    ("Player Chrome 卡品如图", False),
    ("Player Chrome 表面磨损", True),
])
def test_condition_disclosures_are_not_confused_with_generic_photo_disclaimers(title, damaged):
    assert title_discloses_damage(title) is damaged


def test_refreshed_www_item_retains_the_same_cached_auction_without_cross_host_aliasing():
    from card_platform.valuation_worker import source_matches_current_search

    allowed = {"https://www.cardhobby.com.cn/market/item/123"}
    assert source_matches_current_search("https://cardhobby.com.cn/Market/Item/123", allowed)
    assert not source_matches_current_search("https://cardhobby.com.cn/Market/Item/124", allowed)
    assert not source_matches_current_search("https://example.com/market/item/123", allowed)
    assert not source_matches_current_search("https://cardhobby.com.cn/market/feedback/123", allowed)


@pytest.mark.parametrize("variant", ["银折", "紫折 57/75编", "紫波纹折 14/75编",
                                     "Silver Refractor", "Purple Wave Refractor"])
def test_unknown_parallel_cannot_inherit_named_variant_from_similar_image(monkeypatch, variant):
    monkeypatch.setattr("card_platform.formal_cardhobby_gate.assess_cardhobby_candidate",
                        lambda *_: (Decimal("1"), [], "exact_or_near_exact"))
    sale = CardHobbyProvider.parse_market_item(source_row(Title=f"2023 Player Product #7 {variant}"))
    result = enforce_formal_cardhobby_identity(
        {"serial_total": 75}, sale.model_dump(mode="json"),
        Classification(ComparableTier.T0, Decimal("1"), Decimal("0.7"),
                       field_matches={"visual_same_template_anchor": True}),
        visual_verdict="same", identity_accepted=True,
    )
    assert "target_parallel_unknown_for_explicit_listing_variant" in result.hard_conflicts


def test_generic_native_foil_and_explicit_target_parallel_are_not_unknown_versions():
    assert not title_requires_named_parallel({}, "2023 Product insert 普通特卡 折射")
    assert not title_requires_named_parallel({"parallel_name": "Silver"}, "Product 银折")
