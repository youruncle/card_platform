from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from card_platform.schemas import FactorScores


def clamp(value: Decimal, low: Decimal = Decimal("0"), high: Decimal = Decimal("100")) -> Decimal:
    return max(low, min(high, value)).quantize(Decimal("0.01"))


def metric(metrics: dict[str, Any], *names: str) -> Decimal | None:
    for name in names:
        value = metrics.get(name)
        if isinstance(value, (int, float, Decimal)):
            return Decimal(str(value))
    return None


@dataclass(frozen=True)
class AutomaticFactorEvidence:
    factors: FactorScores
    inputs: dict[str, Any]


def derive_automatic_factors(evidence: dict[str, Any]) -> AutomaticFactorEvidence:
    hall_of_fame = bool(evidence.get("hall_of_fame"))
    active = bool(evidence.get("active", True))
    historical = Decimal("95") if hall_of_fame else Decimal("48") if active else Decimal("72")

    metrics = evidence.get("metrics") or {}
    points = metric(metrics, "pts", "PTS", "points")
    rebounds = metric(metrics, "reb", "REB", "rebounds")
    assists = metric(metrics, "ast", "AST", "assists")
    if any(value is not None for value in (points, rebounds, assists)):
        current = (
            clamp((points or Decimal("0")) / Decimal("30") * 100) * Decimal("0.5")
            + clamp((rebounds or Decimal("0")) / Decimal("12") * 100) * Decimal("0.25")
            + clamp((assists or Decimal("0")) / Decimal("10") * 100) * Decimal("0.25")
        )
    else:
        current = Decimal("50") if active else Decimal("35")

    potential = Decimal(str(evidence.get("potential_score") or (55 if active else 25)))
    print_run = evidence.get("print_run") or evidence.get("serial_total")
    if print_run:
        run = int(print_run)
        scarcity = (
            Decimal("95") if run <= 10 else Decimal("88") if run <= 25 else
            Decimal("80") if run <= 99 else Decimal("68") if run <= 299 else Decimal("55")
        )
    else:
        scarcity = Decimal("45")
    scarcity += Decimal("5") if evidence.get("is_autograph") else Decimal("0")
    scarcity += Decimal("5") if evidence.get("is_patch") else Decimal("0")
    scarcity += Decimal("4") if evidence.get("is_rookie") else Decimal("0")

    tier = int(evidence.get("brand_tier") or 3)
    brand = Decimal(str({1: 30, 2: 45, 3: 60, 4: 78, 5: 92}.get(tier, 60)))

    comp_count = int(evidence.get("market_comp_count") or 0)
    event_impact = Decimal(str(evidence.get("event_impact") or 50))
    market_heat = Decimal("42") + Decimal(min(comp_count, 10)) * Decimal("4")
    market_heat += (event_impact - Decimal("50")) * Decimal("0.2")

    factors = FactorScores(
        historical_standing=clamp(historical),
        current_performance=clamp(current),
        age_potential=clamp(potential),
        card_scarcity=clamp(scarcity),
        brand_tier=clamp(brand),
        market_heat=clamp(market_heat),
    )
    return AutomaticFactorEvidence(
        factors=factors,
        inputs={
            "mode": "automatic",
            "rules_version": "1.0.0",
            "sources": {
                "historical_standing": {"hall_of_fame": hall_of_fame, "active": active},
                "current_performance": metrics,
                "age_potential": evidence.get("potential_score"),
                "card_scarcity": {
                    "print_run": print_run,
                    "is_rookie": bool(evidence.get("is_rookie")),
                    "is_autograph": bool(evidence.get("is_autograph")),
                    "is_patch": bool(evidence.get("is_patch")),
                },
                "brand_tier": tier,
                "market_heat": {"market_comp_count": comp_count, "event_impact": str(event_impact)},
            },
        },
    )
