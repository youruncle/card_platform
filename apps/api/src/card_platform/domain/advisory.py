from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum


class Rating(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    REDUCE = "reduce"
    AVOID = "avoid"


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    rating: Rating
    price_assessment: str
    holding_period_min_months: int
    holding_period_max_months: int
    target_sell_low: Decimal
    target_sell_high: Decimal
    confidence: Decimal
    catalysts: tuple[str, ...]
    risks: tuple[str, ...]


def build_recommendation(
    *,
    total_score: Decimal,
    asking_price: Decimal,
    fair_value_low: Decimal,
    fair_value_high: Decimal,
    age_potential: Decimal,
    historical_standing: Decimal,
    evidence_count: int,
) -> RecommendationResult:
    if asking_price < 0 or fair_value_low < 0 or fair_value_high < fair_value_low:
        raise ValueError("invalid price range")

    midpoint = (fair_value_low + fair_value_high) / 2
    if asking_price <= fair_value_low and total_score >= 80 and evidence_count >= 2:
        rating = Rating.STRONG_BUY
    elif asking_price <= midpoint and total_score >= 68:
        rating = Rating.BUY
    elif asking_price <= fair_value_high:
        rating = Rating.HOLD
    elif asking_price <= fair_value_high * Decimal("1.15") and total_score >= 60:
        rating = Rating.REDUCE
    else:
        rating = Rating.AVOID

    if asking_price < fair_value_low:
        price_assessment = "低于合理区间"
    elif asking_price <= fair_value_high:
        price_assessment = "处于合理区间"
    else:
        price_assessment = "高于合理区间"

    if historical_standing >= 80:
        holding_period = (24, 60)
    elif age_potential >= 70:
        holding_period = (18, 36)
    else:
        holding_period = (12, 24)

    growth_multiplier = Decimal("1.05") + max(Decimal("0"), total_score - Decimal("50")) / Decimal(
        "200"
    )
    target_low = (fair_value_low * growth_multiplier).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    target_high = (fair_value_high * growth_multiplier).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    confidence = min(
        Decimal("0.92"),
        Decimal("0.38") + Decimal(min(evidence_count, 6)) * Decimal("0.09"),
    )

    catalysts: list[str] = []
    risks: list[str] = []
    if age_potential >= 70:
        catalysts.append("年龄曲线与成长空间")
        risks.append("年轻球员表现波动")
    if historical_standing >= 80:
        catalysts.append("历史地位与长期收藏共识")
    if total_score >= 75:
        catalysts.append("综合评分处于优质区间")
    if evidence_count < 2:
        risks.append("可比成交样本不足")
    if asking_price > fair_value_high:
        risks.append("买入价高于当前合理区间")

    return RecommendationResult(
        rating=rating,
        price_assessment=price_assessment,
        holding_period_min_months=holding_period[0],
        holding_period_max_months=holding_period[1],
        target_sell_low=target_low,
        target_sell_high=target_high,
        confidence=confidence,
        catalysts=tuple(catalysts),
        risks=tuple(risks),
    )
