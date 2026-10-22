"""The main player signal uses one disclosed comparable card, not the index."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from card_platform.player_prices.domain import calculate_market
from test_player_prices import sale

DAY = date(2026, 10, 21)
TZ = ZoneInfo("Asia/Shanghai")


def rows_for(key, *, current=3, previous=3, older=0, now=120, before=100):
    rows = []
    for window, count, price, day in [
        ("current", current, now, datetime(2026, 10, 10, tzinfo=TZ)),
        ("previous", previous, before, datetime(2026, 9, 10, tzinfo=TZ)),
        ("older", older, before, datetime(2026, 8, 1, tzinfo=TZ)),
    ]:
        rows.extend(
            dict(sale(key, str(price), source_id=f"{key}-{window}-{i}"), occurred_at=day)
            for i in range(count)
        )
    return rows


def test_reference_is_a_single_ready_same_card_with_its_own_medians():
    result = calculate_market(
        rows_for("reference") + rows_for("sparse", current=50, previous=2), DAY
    )
    reference = result.get("reference_card")
    assert reference is not None, (
        "A usable 30-day card signal must be returned even when a 3-card index is absent"
    )
    assert reference["card_key"] == "reference"
    assert reference["change_pct"] == "20.0000"
    assert (reference["current_price"], reference["previous_price"]) == ("120", "100")
    assert (reference["current_count"], reference["previous_count"]) == (3, 3)


def test_reference_uses_all_90_day_activity_before_display_truncation():
    rows = []
    for i in range(21):
        rows += rows_for(f"busy-{i:02}", current=4, previous=3)
    rows += rows_for("reference", current=3, previous=3, older=20, now=80)
    result = calculate_market(rows, DAY)
    assert len(result["cards"]) == 20 and result["cards_truncated"] is True
    assert all(card["card_key"] != "reference" for card in result["cards"])
    assert result.get("reference_card", {}).get("card_key") == "reference"
    assert result["reference_card"]["change_pct"] == "-20.0000"
    assert result["reference_card"]["selection_count"] == 26


def test_reference_ties_are_stable_and_do_not_mix_card_prices():
    rows = rows_for("b", now=200) + rows_for("a", now=80)
    for ordered in [rows, list(reversed(rows))]:
        result = calculate_market(ordered, DAY)
        assert result.get("reference_card", {}).get("card_key") == "a"
        assert result["reference_card"]["change_pct"] == "-20.0000"


def test_reference_remains_missing_when_either_window_lacks_three_sales():
    result = calculate_market(rows_for("one", current=3, previous=2), DAY)
    assert "reference_card" in result
    assert result["reference_card"] is None
    assert result["sale_count"] == 5 and result["card_count"] == 1
