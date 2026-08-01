from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from typing import Any

from card_platform.domain.adjustments import Adjustment
from card_platform.domain.comparables import Classification, ComparableTier
from card_platform.domain.hierarchical_estimator import (
    ClassifiedSale,
    HierarchicalEstimator,
)


def _rounded(value: float | Decimal) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def run_temporal_backtest(
    rows: list[dict[str, Any]],
    *,
    min_prior_sales: int = 2,
    max_prior_sales: int = 10,
) -> dict[str, Any]:
    """Predict each sale using only earlier T0 sales of the exact card."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["card_definition_id"])].append(row)

    predictions: list[dict[str, Any]] = []
    for card_definition_id, sales in grouped.items():
        sales.sort(key=lambda row: (row["occurred_at"], str(row["id"])))
        for index, target in enumerate(sales):
            prior = [
                row
                for row in sales[:index]
                if row["occurred_at"] < target["occurred_at"]
            ][-max_prior_sales:]
            if len(prior) < min_prior_sales:
                continue
            target_at: datetime = target["occurred_at"]
            estimate = HierarchicalEstimator().estimate(
                [
                    ClassifiedSale(
                        id=str(row["id"]),
                        sale_price_base=Decimal(str(row["value_base"])),
                        shipping_base=Decimal(str(row.get("shipping_base") or "0")),
                        age_days=max(0, (target_at - row["occurred_at"]).days),
                        classification=Classification(
                            ComparableTier.T0,
                            Decimal(str(row.get("similarity") or "1")),
                            Decimal(str(row.get("source_reliability") or "0.7")),
                        ),
                        adjustment=Adjustment(),
                    )
                    for row in prior
                ]
            )
            actual = Decimal(str(target["value_base"]))
            absolute_percentage_error = abs(estimate.fair - actual) / actual
            predictions.append(
                {
                    "card_definition_id": card_definition_id,
                    "target_sale_id": str(target["id"]),
                    "occurred_at": target_at.isoformat(),
                    "actual": str(actual),
                    "predicted_low": str(estimate.low),
                    "predicted_fair": str(estimate.fair),
                    "predicted_high": str(estimate.high),
                    "absolute_percentage_error": _rounded(
                        absolute_percentage_error
                    ),
                    "interval_covered": estimate.low <= actual <= estimate.high,
                    "prior_sale_count": len(prior),
                }
            )
    if not predictions:
        return {
            "sample_count": 0,
            "identity_count": 0,
            "median_absolute_percentage_error": None,
            "mean_absolute_percentage_error": None,
            "p90_absolute_percentage_error": None,
            "interval_coverage": None,
            "predictions": [],
        }
    errors = sorted(
        prediction["absolute_percentage_error"] for prediction in predictions
    )
    p90_index = min(len(errors) - 1, max(0, int((len(errors) - 1) * 0.9)))
    return {
        "sample_count": len(predictions),
        "identity_count": len(
            {prediction["card_definition_id"] for prediction in predictions}
        ),
        "median_absolute_percentage_error": _rounded(median(errors)),
        "mean_absolute_percentage_error": _rounded(sum(errors) / len(errors)),
        "p90_absolute_percentage_error": _rounded(errors[p90_index]),
        "interval_coverage": _rounded(
            sum(prediction["interval_covered"] for prediction in predictions)
            / len(predictions)
        ),
        "predictions": predictions,
    }
