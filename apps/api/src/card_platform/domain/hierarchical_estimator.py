from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from card_platform.domain.adjustments import Adjustment, Weighting
from card_platform.domain.comparables import Classification, ComparableTier


@dataclass(frozen=True, slots=True)
class ClassifiedSale:
    id: str
    sale_price_base: Decimal
    shipping_base: Decimal
    age_days: int
    classification: Classification
    adjustment: Adjustment = Adjustment()


@dataclass(frozen=True, slots=True)
class WeightedSale:
    sale: ClassifiedSale
    adjusted_price: Decimal
    raw_weight: Decimal
    final_weight: Decimal
    used_for_center: bool


@dataclass(frozen=True, slots=True)
class HierarchicalEstimate:
    low: Decimal
    fair: Decimal
    high: Decimal
    landed_low: Decimal
    landed_fair: Decimal
    landed_high: Decimal
    confidence: Decimal
    evidence: tuple[WeightedSale, ...]

    @property
    def evidence_count(self) -> int:
        return len(self.evidence)


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class HierarchicalEstimator:
    def estimate(self, sales: list[ClassifiedSale]) -> HierarchicalEstimate:
        accepted = [
            sale for sale in sales
            if sale.classification.tier != ComparableTier.REJECTED
            and sale.adjustment.reproducible
            and sale.sale_price_base > 0
        ]
        if not accepted:
            raise ValueError("at least one accepted comparable is required")
        t0 = [sale for sale in accepted if sale.classification.tier == ComparableTier.T0]
        # Once an exact-card comparable exists, lower tiers are context only.
        # Even a large pile of base cards or other parallels must not pull the
        # center away from a verified T0 sale. A single T0 deliberately keeps
        # lower coverage/confidence and a wider interval below.
        center_sales = t0 if t0 else accepted
        if len(t0) > 3:
            # Exact-card prices can trend quickly; temporal backtesting showed that
            # the latest three sales generalize better than a long historical median.
            center_sales = sorted(t0, key=lambda sale: sale.age_days)[:3]
        if len(t0) >= 3:
            newest_age = min(sale.age_days for sale in t0)
            recent_t0 = [sale for sale in t0 if sale.age_days <= newest_age + 30]
            if len(recent_t0) >= 3:
                recent_prices = [
                    sale.sale_price_base * sale.adjustment.factor
                    for sale in recent_t0
                ]
                recent_median = Decimal(str(median(recent_prices)))
                recent_spread = (
                    (max(recent_prices) - min(recent_prices)) / recent_median
                    if recent_median
                    else Decimal("1")
                )
                if recent_spread <= Decimal("0.15"):
                    center_sales = sorted(
                        recent_t0, key=lambda sale: sale.age_days
                    )[:3]
        raw = {
            sale.id: Weighting.raw_weight(
                sale.classification.tier.value,
                sale.classification.similarity,
                sale.classification.source_reliability,
                sale.age_days,
            )
            for sale in accepted
        }
        total = sum((raw[sale.id] for sale in center_sales), Decimal("0"))
        weighted: list[WeightedSale] = []
        for sale in accepted:
            used = sale in center_sales
            weighted.append(
                WeightedSale(
                    sale= sale,
                    adjusted_price=sale.sale_price_base * sale.adjustment.factor,
                    raw_weight=raw[sale.id],
                    final_weight=(raw[sale.id] / total if used and total else Decimal("0")),
                    used_for_center=used,
                )
            )
        fair = sum(
            (entry.adjusted_price * entry.final_weight for entry in weighted),
            Decimal("0"),
        )
        center_prices = [entry.adjusted_price for entry in weighted if entry.used_for_center]
        spread = (
            (max(center_prices) - min(center_prices)) / fair
            if len(center_prices) > 1 and fair
            else Decimal("0.30")
        )
        minimum_margin = (
            Decimal("0.30") if len(center_sales) <= 3 else Decimal("0.12")
        )
        base_margin = max(
            minimum_margin,
            min(Decimal("0.35"), spread / 2),
        )
        secondary_prices = [entry.adjusted_price for entry in weighted if not entry.used_for_center]
        anomaly_margin = Decimal("0")
        if secondary_prices and fair:
            outside = max(abs(price - fair) / fair for price in secondary_prices)
            anomaly_margin = min(Decimal("0.07"), outside * Decimal("0.08"))
        margin = base_margin + anomaly_margin
        low, high = fair * (1 - margin), fair * (1 + margin)

        shipping = Decimal(str(median([float(sale.shipping_base) for sale in center_sales])))
        coverage = Decimal("1") if len(t0) >= 2 else Decimal("0.65") if t0 else Decimal("0.35")
        dispersion_quality = max(Decimal("0"), Decimal("1") - min(spread, Decimal("1")))
        recency_quality = sum(
            (max(Decimal("0.35"), Decimal("1") - Decimal(sale.age_days) / Decimal("730")) for sale in center_sales),
            Decimal("0"),
        ) / len(center_sales)
        source_quality = sum(
            (sale.classification.source_reliability for sale in center_sales), Decimal("0")
        ) / len(center_sales)
        confidence = min(
            Decimal("0.90"),
            Decimal("0.10") + coverage * Decimal("0.38")
            + dispersion_quality * Decimal("0.18")
            + recency_quality * Decimal("0.14")
            + source_quality * Decimal("0.12"),
        )
        return HierarchicalEstimate(
            low=_q(low), fair=_q(fair), high=_q(high),
            landed_low=_q(low + shipping), landed_fair=_q(fair + shipping),
            landed_high=_q(high + shipping),
            confidence=confidence.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            evidence=tuple(weighted),
        )
