from datetime import datetime, timedelta, timezone
from decimal import Decimal

from card_platform.valuation_backtest import run_temporal_backtest


def row(index: int, value: str, *, identity: str = "card-1") -> dict:
    return {
        "id": f"sale-{identity}-{index}",
        "card_definition_id": identity,
        "occurred_at": datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(days=index),
        "value_base": Decimal(value),
        "match_confidence": Decimal("1"),
        "similarity": Decimal("1"),
    }


def test_temporal_backtest_never_uses_future_sales() -> None:
    result = run_temporal_backtest(
        [row(0, "100"), row(1, "110"), row(2, "120"), row(3, "130")]
    )
    assert result["sample_count"] == 2
    assert result["predictions"][0]["actual"] == "120"
    assert result["predictions"][0]["prior_sale_count"] == 2
    assert result["median_absolute_percentage_error"] is not None


def test_temporal_backtest_requires_prior_history_per_identity() -> None:
    result = run_temporal_backtest(
        [row(0, "100", identity="a"), row(1, "110", identity="a"), row(2, "80", identity="b")]
    )
    assert result["sample_count"] == 0
    assert result["interval_coverage"] is None
