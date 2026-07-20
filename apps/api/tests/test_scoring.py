from decimal import Decimal

import pytest

from card_platform.domain.scoring import calculate_score

WEIGHTS = {
    "historical_standing": Decimal("0.20"),
    "current_performance": Decimal("0.20"),
    "age_potential": Decimal("0.15"),
    "card_scarcity": Decimal("0.20"),
    "brand_tier": Decimal("0.10"),
    "market_heat": Decimal("0.15"),
}


def test_weighted_score_is_explainable() -> None:
    factors = {
        "historical_standing": Decimal("60"),
        "current_performance": Decimal("80"),
        "age_potential": Decimal("90"),
        "card_scarcity": Decimal("85"),
        "brand_tier": Decimal("95"),
        "market_heat": Decimal("70"),
    }

    result = calculate_score(factors, WEIGHTS)

    assert result.total == Decimal("78.50")
    assert result.contributions["card_scarcity"] == Decimal("17.00")
    assert sum(result.contributions.values()) == result.total


def test_score_rejects_missing_factor() -> None:
    with pytest.raises(ValueError, match="missing factor"):
        calculate_score({"historical_standing": Decimal("80")}, WEIGHTS)


def test_score_rejects_invalid_weights() -> None:
    invalid = {**WEIGHTS, "market_heat": Decimal("0.20")}
    factors = {name: Decimal("70") for name in WEIGHTS}
    with pytest.raises(ValueError, match="sum to 1"):
        calculate_score(factors, invalid)
