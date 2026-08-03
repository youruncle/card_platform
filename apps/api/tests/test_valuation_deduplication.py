from datetime import datetime, timezone
from decimal import Decimal

from card_platform.valuation_worker import (
    deduplicate_market_rows,
    valuation_identity_specificity,
)


def sale(
    row_id: str,
    source: str,
    external_id: str | None,
    *,
    origin: str | None = None,
    reliability: str = "0.7",
) -> dict:
    return {
        "id": row_id,
        "source_code": source,
        "external_id": external_id,
        "source_reliability": Decimal(reliability),
        "classification": {"origin_source_code": origin} if origin else {},
        "occurred_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
    }


def test_direct_marketplace_sale_wins_over_aggregator_copy() -> None:
    rows, duplicates = deduplicate_market_rows(
        [
            sale("aggregated", "130point", "ebay:123", origin="ebay", reliability="0.9"),
            sale("direct", "ebay", "123", reliability="0.8"),
        ]
    )
    assert [row["id"] for row in rows] == ["direct"]
    assert duplicates[0]["dropped_id"] == "aggregated"


def test_sales_without_external_ids_are_not_collapsed() -> None:
    rows, duplicates = deduplicate_market_rows(
        [
            sale("one", "130point", None),
            sale("two", "ebay", None),
        ]
    )
    assert len(rows) == 2
    assert duplicates == []


def test_valuation_requires_exact_card_anchor() -> None:
    specific, missing = valuation_identity_specificity(
        {
            "player_name": "Cooper Flagg",
            "season_start": 2025,
            "product_name": "Finest",
        }
    )
    assert specific is False
    assert "card_number_or_insert_name" in missing

    specific, missing = valuation_identity_specificity(
        {
            "player_name": "Cooper Flagg",
            "season_start": 2025,
            "product_name": "Finest",
            "insert_name": "Headliners",
        }
    )
    assert specific is True
    assert missing == []

    specific, missing = valuation_identity_specificity(
        {
            "player_name": "Jason Kidd",
            "season_start": 2024,
            "brand_name": "Panini Flawless",
            "product_name": "Panini Flawless",
            "card_number": "05/25",
        }
    )
    assert specific is False
    assert "card_number_is_serial_fraction" in missing
    assert "brand_product_duplicate" in missing


def test_valuation_allows_legitimate_self_named_vintage_products() -> None:
    for product in ("Hoops", "Upper Deck"):
        specific, missing = valuation_identity_specificity(
            {
                "player_name": "Michael Jordan",
                "season_start": 1991,
                "brand_name": product,
                "product_name": product,
                "card_number": "358",
            }
        )

        assert specific is True
        assert "brand_product_duplicate" not in missing
