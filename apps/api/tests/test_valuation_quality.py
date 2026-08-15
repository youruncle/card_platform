from decimal import Decimal

from card_platform.domain.comparables import ComparableTier
from card_platform.domain.valuation_quality import (
    EvidenceGrade,
    ShadowSale,
    ShadowValuationEstimator,
    evidence_grade,
    identity_review_reasons,
)


def _identity(**updates) -> dict:
    identity = {
        "player_name": "Scottie Barnes",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Basketball",
        "card_number": "31",
        "parallel_name": "Gold Rainbow",
        "serial_number": 22,
        "serial_total": 50,
    }
    return {**identity, **updates}


def _sale(
    sale_id: str,
    price: str,
    *,
    tier: ComparableTier = ComparableTier.T0,
    level: str = "confirmed_transaction",
    age: int = 10,
    shipping: str = "0",
    provider: str = "ebay",
) -> ShadowSale:
    return ShadowSale(
        sale_id,
        provider,
        Decimal(price),
        Decimal(shipping),
        age,
        tier,
        Decimal("1"),
        Decimal("0.85"),
        level,
    )


def test_evidence_levels_separate_paid_ended_listing_and_proxy() -> None:
    assert evidence_grade("confirmed_transaction") == EvidenceGrade.E0
    assert evidence_grade("ended_auction") == EvidenceGrade.E1
    assert evidence_grade("aggregated_sale") == EvidenceGrade.E2
    assert evidence_grade("active_listing") == EvidenceGrade.E3
    assert evidence_grade("proxy") == EvidenceGrade.E4
    assert evidence_grade("unknown") == EvidenceGrade.E3


def test_numbered_card_with_generic_parallel_requires_review() -> None:
    reasons = identity_review_reasons(_identity(parallel_name="Prizm"))
    assert "generic_parallel_name" in reasons
    reasons = identity_review_reasons(_identity(parallel_name=None))
    assert "numbered_parallel_unknown" in reasons


def test_identity_blockers_prevent_even_shadow_price_output() -> None:
    estimate = ShadowValuationEstimator().estimate(
        [_sale("wrong-identity", "100")],
        _identity(parallel_name="Prizm"),
    )
    assert estimate.status == "manual_review"
    assert estimate.fair is None
    assert "generic_parallel_name" in estimate.review_reasons


def test_shadow_estimate_uses_only_exact_center_eligible_sales() -> None:
    estimate = ShadowValuationEstimator().estimate(
        [
            _sale("paid", "100"),
            _sale("ended", "110", level="ended_auction", provider="cardhobby"),
            _sale("wrong-card", "5", tier=ComparableTier.T2),
            _sale("listing", "300", level="active_listing"),
        ],
        _identity(),
    )
    assert estimate.fair in {Decimal("100.00"), Decimal("110.00")}
    assert set(estimate.used_ids) == {"paid", "ended"}
    assert {item["id"] for item in estimate.rejected} == {"wrong-card", "listing"}


def test_shadow_estimate_rejects_mad_outlier_with_five_sales() -> None:
    estimate = ShadowValuationEstimator().estimate(
        [_sale(str(index), price) for index, price in enumerate(("95", "98", "100", "102", "900"))],
        _identity(),
    )
    assert estimate.fair == Decimal("98.00") or estimate.fair == Decimal("100.00")
    assert "4" not in estimate.used_ids
    assert any(item["reasons"] == ["price_outlier_mad"] for item in estimate.rejected)


def test_single_sale_uses_price_plus_shipping_without_shipping_anomaly() -> None:
    estimate = ShadowValuationEstimator().estimate(
        [_sale("one", "1", shipping="18", provider="cardhobby")],
        _identity(),
    )
    assert estimate.confidence == Decimal("0.45")
    assert estimate.status == "manual_review"
    assert "fewer_than_two_exact_sales" in estimate.review_reasons
    assert "hammer_price_below_shipping" not in estimate.review_reasons
    assert estimate.fair == Decimal("19.00")


def test_shadow_center_uses_buyer_paid_price_but_keeps_shipping_separate() -> None:
    estimate = ShadowValuationEstimator().estimate(
        [
            _sale("one", "22", shipping="18", provider="cardhobby"),
            _sale("two", "23", shipping="12", provider="cardhobby"),
        ],
        _identity(),
    )
    assert estimate.fair in {Decimal("35.00"), Decimal("40.00")}
    assert Decimal("22") not in {estimate.low, estimate.fair, estimate.high}


def test_scottie_two_exact_sales_produce_sparse_shadow_range() -> None:
    estimate = ShadowValuationEstimator().estimate(
        [_sale("nov", "120.24", level="ended_auction", age=257),
         _sale("dec", "125.25", level="ended_auction", age=229)],
        _identity(parallel_name="Gold Diamante", serial_number=27),
    )
    assert Decimal("102") <= estimate.low <= Decimal("106")
    assert Decimal("120") <= estimate.fair <= Decimal("126")
    assert Decimal("143") <= estimate.high <= Decimal("145")
    assert estimate.confidence == Decimal("0.65")
