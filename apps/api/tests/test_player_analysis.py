from decimal import Decimal

import pytest

from card_platform.domain.player_analysis import calculate_player_potential


def test_young_player_with_role_and_trend_is_high_potential() -> None:
    result = calculate_player_potential(
        age=21,
        performance_score=Decimal("78"),
        performance_trend=Decimal("40"),
        availability_score=Decimal("85"),
        role_score=Decimal("90"),
        team_environment_score=Decimal("75"),
    )

    assert result.score == Decimal("84.50")
    assert result.label == "高潜力"
    assert result.components["age_curve"] == Decimal("100.00")


def test_veteran_can_score_well_but_has_lower_future_potential() -> None:
    result = calculate_player_potential(
        age=35,
        performance_score=Decimal("90"),
        performance_trend=Decimal("0"),
        availability_score=Decimal("90"),
        role_score=Decimal("90"),
        team_environment_score=Decimal("90"),
    )

    assert result.score < Decimal("70")
    assert result.label == "成长观察"


def test_invalid_normalized_component_is_rejected() -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        calculate_player_potential(
            age=24,
            performance_score=Decimal("101"),
            performance_trend=Decimal("0"),
            availability_score=Decimal("80"),
            role_score=Decimal("80"),
            team_environment_score=Decimal("80"),
        )
