from decimal import Decimal

from card_platform.identity_audit import build_identity_suggestions, extract_identity_fields


def test_fractional_card_number_generates_structural_correction_without_comps() -> None:
    suggestions = build_identity_suggestions(
        {
            "card_number": "05/25",
            "serial_total": 25,
        },
        [],
    )
    assert len(suggestions) == 1
    assert suggestions[0].field_name == "card_number"
    assert suggestions[0].suggested_value is None
    assert suggestions[0].confidence == Decimal("0.99")


def lebron_identity(**updates):
    values = {
        "player_name": "LeBron James",
        "season_start": 2021,
        "product_name": "Origins",
        "card_number": "WRONG",
        "insert_name": "GU Jersey",
        "parallel_name": "Cur",
        "serial_total": None,
        "is_autograph": False,
        "is_patch": True,
    }
    values.update(updates)
    return values


def test_extracts_sleeve_technical_fields_from_exact_title() -> None:
    fields = extract_identity_fields(
        "2021-22 Panini Origins LeBron James GU亲穿 球衣 【Cur】 01",
        lebron_identity(),
    )
    assert fields["insert_name"] == "Gu Jersey"
    assert fields["parallel_name"] == "Cur"
    assert fields["card_number"] == "01"
    assert fields["is_patch"]
    assert not fields["is_autograph"]


def test_exact_structured_sale_can_suggest_card_number_correction() -> None:
    suggestions = build_identity_suggestions(
        lebron_identity(),
        [
            {
                "id": "sale-1",
                "listing_title": (
                    "2021-22 Panini Origins LeBron James GU亲穿 球衣 【Cur】 01"
                ),
                "source_url": "https://www.cardhobby.com.cn/market/item/122450195",
                "source_reliability": "0.75",
                "match_confidence": "0.70",
            }
        ],
    )
    card_number = next(item for item in suggestions if item.field_name == "card_number")
    assert card_number.current_value == "WRONG"
    assert card_number.suggested_value == "01"
    assert card_number.evidence[0]["source_url"].endswith("122450195")


def test_ambiguous_consensus_does_not_create_suggestion() -> None:
    suggestions = build_identity_suggestions(
        lebron_identity(card_number=None),
        [
            {
                "id": "one",
                "listing_title": "2021-22 Origins LeBron James GU亲穿 Cur 卡号 01",
                "source_url": "https://example.com/one",
            },
            {
                "id": "two",
                "listing_title": "2021-22 Origins LeBron James GU亲穿 Cur 卡号 02",
                "source_url": "https://example.com/two",
            },
        ],
    )
    assert all(item.field_name != "card_number" for item in suggestions)


def test_single_slash_number_does_not_suggest_print_run() -> None:
    suggestions = build_identity_suggestions(
        lebron_identity(),
        [
            {
                "id": "one",
                "listing_title": (
                    "2021-22 Origins LeBron James GU亲穿 Cur "
                    "卖家批次信息/72"
                ),
                "source_url": "https://example.com/one",
            }
        ],
    )
    assert all(item.field_name != "serial_total" for item in suggestions)
