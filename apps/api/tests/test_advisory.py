from decimal import Decimal

from card_platform.domain.advisory import Rating, build_recommendation


def test_discounted_high_score_card_is_strong_buy_with_evidence() -> None:
    result = build_recommendation(
        total_score=Decimal("84"),
        asking_price=Decimal("210"),
        fair_value_low=Decimal("230"),
        fair_value_high=Decimal("280"),
        age_potential=Decimal("88"),
        historical_standing=Decimal("55"),
        evidence_count=4,
    )

    assert result.rating == Rating.STRONG_BUY
    assert result.price_assessment == "低于合理区间"
    assert (result.holding_period_min_months, result.holding_period_max_months) == (18, 36)
    assert result.target_sell_low > Decimal("230")


def test_sparse_evidence_prevents_strong_buy_and_reduces_confidence() -> None:
    result = build_recommendation(
        total_score=Decimal("90"),
        asking_price=Decimal("180"),
        fair_value_low=Decimal("200"),
        fair_value_high=Decimal("240"),
        age_potential=Decimal("90"),
        historical_standing=Decimal("50"),
        evidence_count=0,
    )

    assert result.rating == Rating.BUY
    assert result.confidence == Decimal("0.38")
    assert "可比成交样本不足" in result.risks


def test_overpriced_card_is_avoid() -> None:
    result = build_recommendation(
        total_score=Decimal("58"),
        asking_price=Decimal("350"),
        fair_value_low=Decimal("200"),
        fair_value_high=Decimal("250"),
        age_potential=Decimal("45"),
        historical_standing=Decimal("65"),
        evidence_count=5,
    )

    assert result.rating == Rating.AVOID
    assert result.price_assessment == "高于合理区间"
    assert "买入价高于当前合理区间" in result.risks
