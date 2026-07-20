import pytest

from scripts.import_collection import row_to_payload


def valid_row() -> dict[str, str]:
    return {
        "player_name": "Kobe Bryant",
        "brand_name": "Upper Deck",
        "season_start": "1996",
        "season_end": "1997",
        "product_name": "UD3",
        "insert_name": "Hardwood Prospects",
        "card_number": "",
        "is_rookie": "false",
        "is_autograph": "false",
        "is_patch": "false",
        "serial_number": "",
        "serial_total": "",
        "price": "550",
        "currency": "CNY",
        "fx_rate_to_base": "1",
        "acquired_at": "2026-07-01T00:00:00Z",
        "strategy_tags": "历史球星资产|长期持有",
        "notes": "",
    }


def test_collection_row_maps_to_api_payload() -> None:
    payload = row_to_payload(valid_row(), "00000000-0000-0000-0000-000000000001")

    assert payload["card"]["season_end"] == 1997
    assert payload["purchase"]["price"] == "550"
    assert payload["strategy_tags"] == ["历史球星资产", "长期持有"]


def test_collection_import_rejects_unconfirmed_currency() -> None:
    row = valid_row()
    row["currency"] = ""
    with pytest.raises(ValueError, match="currency"):
        row_to_payload(row, "00000000-0000-0000-0000-000000000001")
