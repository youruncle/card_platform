from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True, slots=True)
class ComparableSale:
    value_base: Decimal
    age_days: int
    match_confidence: Decimal


@dataclass(frozen=True, slots=True)
class EstimatedRange:
    low: Decimal
    fair: Decimal
    high: Decimal
    confidence: Decimal
    evidence_count: int


def estimate_from_comps(comps: list[ComparableSale]) -> EstimatedRange:
    usable = [
        comp
        for comp in comps
        if comp.value_base > 0
        and comp.age_days >= 0
        and Decimal("0.5") <= comp.match_confidence <= 1
    ]
    if not usable:
        raise ValueError("at least one comparable sale with confidence >= 0.5 is required")

    weighted = sorted(
        (
            comp.value_base,
            comp.match_confidence
            * max(Decimal("0.25"), Decimal("1") - Decimal(comp.age_days) / Decimal("730")),
        )
        for comp in usable
    )
    if len(weighted) == 1:
        fair = weighted[0][0]
        low, high = fair * Decimal("0.85"), fair * Decimal("1.15")
    elif len(weighted) == 2:
        low, high = weighted[0][0], weighted[1][0]
        fair = _weighted_quantile(weighted, Decimal("0.5"))
    else:
        low = _weighted_quantile(weighted, Decimal("0.25"))
        fair = _weighted_quantile(weighted, Decimal("0.5"))
        high = _weighted_quantile(weighted, Decimal("0.75"))
        if low == high:
            low, high = fair * Decimal("0.95"), fair * Decimal("1.05")

    average_match = sum((comp.match_confidence for comp in usable), Decimal("0")) / len(usable)
    stale_penalty = min(
        Decimal("0.15"),
        Decimal(max(comp.age_days for comp in usable)) / Decimal("3650"),
    )
    confidence = min(
        Decimal("0.95"),
        Decimal("0.28")
        + Decimal(min(len(usable), 6)) * Decimal("0.09")
        + average_match * Decimal("0.20")
        - stale_penalty,
    )
    def quantize(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return EstimatedRange(
        low=quantize(low),
        fair=quantize(fair),
        high=quantize(high),
        confidence=confidence.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
        evidence_count=len(usable),
    )


def _weighted_quantile(values: list[tuple[Decimal, Decimal]], quantile: Decimal) -> Decimal:
    total = sum((weight for _, weight in values), Decimal("0"))
    threshold = total * quantile
    cumulative = Decimal("0")
    for value, weight in values:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return values[-1][0]
