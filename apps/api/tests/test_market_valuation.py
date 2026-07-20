from decimal import Decimal

import pytest

from card_platform.domain.valuation import ComparableSale, estimate_from_comps


def comp(value: str, age: int = 10, confidence: str = "0.9") -> ComparableSale:
    return ComparableSale(
        value_base=Decimal(value),
        age_days=age,
        match_confidence=Decimal(confidence),
    )


def test_single_comp_creates_conservative_range() -> None:
    result = estimate_from_comps([comp("200")])

    assert result.low == Decimal("170.00")
    assert result.fair == Decimal("200.00")
    assert result.high == Decimal("230.00")
    assert result.evidence_count == 1


def test_multiple_comps_resist_extreme_low_quality_outlier() -> None:
    result = estimate_from_comps(
        [
            comp("190"),
            comp("200"),
            comp("210"),
            comp("220"),
            comp("900", age=600, confidence="0.51"),
        ]
    )

    assert result.low == Decimal("200.00")
    assert result.fair == Decimal("210.00")
    assert result.high == Decimal("220.00")
    assert result.confidence > Decimal("0.7")


def test_low_confidence_comps_are_rejected() -> None:
    with pytest.raises(ValueError, match="confidence"):
        estimate_from_comps([comp("200", confidence="0.4")])
