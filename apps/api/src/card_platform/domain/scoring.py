from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

FACTOR_NAMES = (
    "historical_standing",
    "current_performance",
    "age_potential",
    "card_scarcity",
    "brand_tier",
    "market_heat",
)


@dataclass(frozen=True, slots=True)
class ScoreResult:
    total: Decimal
    contributions: dict[str, Decimal]


def calculate_score(factor_scores: dict[str, Decimal], weights: dict[str, Decimal]) -> ScoreResult:
    missing = set(FACTOR_NAMES) - factor_scores.keys()
    if missing:
        raise ValueError(f"missing factor scores: {', '.join(sorted(missing))}")
    if set(FACTOR_NAMES) - weights.keys():
        raise ValueError("weights must include all scoring factors")
    if any(score < 0 or score > 100 for score in factor_scores.values()):
        raise ValueError("factor scores must be between 0 and 100")
    weight_total = sum((weights[name] for name in FACTOR_NAMES), Decimal("0"))
    if abs(weight_total - Decimal("1")) > Decimal("0.0001"):
        raise ValueError("scoring weights must sum to 1")

    contributions = {
        name: (factor_scores[name] * weights[name]).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        for name in FACTOR_NAMES
    }
    total = sum(contributions.values(), Decimal("0")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return ScoreResult(total=total, contributions=contributions)
