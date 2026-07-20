from decimal import Decimal

from card_platform.domain.auto_analysis import derive_automatic_factors


def test_automatic_factors_use_player_card_and_market_evidence() -> None:
    result = derive_automatic_factors(
        {
            "hall_of_fame": False,
            "active": True,
            "brand_tier": 5,
            "is_rookie": True,
            "is_autograph": True,
            "is_patch": True,
            "print_run": 99,
            "potential_score": Decimal("82"),
            "metrics": {"pts": 20, "reb": 10, "ast": 5},
            "market_comp_count": 4,
            "event_impact": Decimal("70"),
        }
    )
    assert result.factors.age_potential == Decimal("82.00")
    assert result.factors.card_scarcity == Decimal("94.00")
    assert result.factors.brand_tier == Decimal("92.00")
    assert result.factors.market_heat == Decimal("62.00")
    assert result.inputs["mode"] == "automatic"


def test_automatic_factors_have_conservative_defaults_without_evidence() -> None:
    result = derive_automatic_factors({"active": True})
    assert result.factors.current_performance == Decimal("50.00")
    assert result.factors.age_potential == Decimal("55.00")
    assert result.factors.card_scarcity == Decimal("45.00")
    assert result.factors.market_heat == Decimal("42.00")
