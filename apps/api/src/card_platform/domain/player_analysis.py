from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True, slots=True)
class PotentialResult:
    score: Decimal
    label: str
    components: dict[str, Decimal]


def calculate_player_potential(
    *,
    age: int,
    performance_score: Decimal,
    performance_trend: Decimal,
    availability_score: Decimal,
    role_score: Decimal,
    team_environment_score: Decimal,
) -> PotentialResult:
    """Estimate future potential from normalized, auditable inputs.

    performance_trend uses -100..100; all other scores use 0..100.
    """
    if age < 18 or age > 60:
        raise ValueError("age must be between 18 and 60")
    values = (performance_score, availability_score, role_score, team_environment_score)
    if any(value < 0 or value > 100 for value in values):
        raise ValueError("component scores must be between 0 and 100")
    if performance_trend < -100 or performance_trend > 100:
        raise ValueError("performance trend must be between -100 and 100")

    if age <= 21:
        age_curve = Decimal("100")
    elif age <= 27:
        age_curve = Decimal(100 - (age - 21) * 6)
    elif age <= 32:
        age_curve = Decimal(64 - (age - 27) * 7)
    else:
        age_curve = max(Decimal("5"), Decimal(29 - (age - 32) * 4))
    normalized_trend = (performance_trend + Decimal("100")) / 2

    components = {
        "age_curve": age_curve.quantize(Decimal("0.01")),
        "performance": performance_score.quantize(Decimal("0.01")),
        "trend": normalized_trend.quantize(Decimal("0.01")),
        "availability": availability_score.quantize(Decimal("0.01")),
        "role": role_score.quantize(Decimal("0.01")),
        "team_environment": team_environment_score.quantize(Decimal("0.01")),
    }
    score = (
        components["age_curve"] * Decimal("0.25")
        + components["performance"] * Decimal("0.25")
        + components["trend"] * Decimal("0.15")
        + components["availability"] * Decimal("0.10")
        + components["role"] * Decimal("0.15")
        + components["team_environment"] * Decimal("0.10")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    label = "高潜力" if score >= 75 else "成长观察" if score >= 58 else "成熟/低成长"
    return PotentialResult(score=score, label=label, components=components)
