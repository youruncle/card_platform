from decimal import Decimal

from card_platform.valuation_worker import (
    AutomatedValuationWorker,
    DiscoveredComparable,
    PlayerAdjustment,
    PlayerPricePair,
    ValuationReference,
)


def test_raw_card_rejects_all_graded_references() -> None:
    identity = {"grade_company": None, "grade_value": None}
    raw = ValuationReference(
        title="Raw sale",
        source_url="https://example.com/raw",
        evidence_type="analog_sale",
        observed_price_base=Decimal("100"),
        match_confidence=Decimal("0.8"),
        adjustment="same raw condition",
        condition_type="raw",
        player_name="Tim Duncan",
    )
    graded = raw.model_copy(
        update={
            "title": "PSA 10 sale",
            "condition_type": "graded",
            "grade_company": "PSA",
            "grade_value": Decimal("10"),
        }
    )
    assert AutomatedValuationWorker._grade_matches(identity, raw)
    assert not AutomatedValuationWorker._grade_matches(identity, graded)


def test_graded_card_requires_exact_company_and_grade() -> None:
    identity = {"grade_company": "PSA", "grade_value": Decimal("10")}
    exact = DiscoveredComparable(
        title="PSA 10 sale",
        source_url="https://example.com/psa10",
        occurred_at="2026-01-01T00:00:00Z",
        sale_price=Decimal("500"),
        currency="USD",
        fx_rate_to_base=Decimal("7"),
        grade_company="PSA",
        grade_value=Decimal("10"),
        match_confidence=Decimal("0.9"),
    )
    assert AutomatedValuationWorker._comparable_grade_matches(identity, exact)
    assert not AutomatedValuationWorker._comparable_grade_matches(
        identity, exact.model_copy(update={"grade_value": Decimal("9")})
    )
    assert not AutomatedValuationWorker._comparable_grade_matches(
        identity, exact.model_copy(update={"grade_company": "BGS"})
    )


def test_cross_player_coefficient_uses_median_of_at_least_three_pairs() -> None:
    identity = {
        "player_name": "Tim Duncan",
        "grade_company": None,
        "grade_value": None,
    }
    ratios = [(84, 100), (170, 200), (82, 100)]
    pairs = [
        PlayerPricePair(
            target_title=f"Duncan comp {index}",
            target_url=f"https://example.com/duncan-{index}",
            target_price_base=Decimal(target),
            reference_title=f"Giannis comp {index}",
            reference_url=f"https://example.com/giannis-{index}",
            reference_price_base=Decimal(reference),
            condition_type="raw",
        )
        for index, (target, reference) in enumerate(ratios)
    ]
    coefficients = AutomatedValuationWorker.build_player_coefficients(
        identity,
        [
            PlayerAdjustment(
                target_player="Tim Duncan",
                reference_player="Giannis Antetokounmpo",
                segment="premium patch /26-/75 raw",
                pairs=pairs,
            )
        ],
    )
    result = coefficients["giannis antetokounmpo"]
    assert result["coefficient"] == Decimal("0.84")
    assert result["sample_count"] == 3
