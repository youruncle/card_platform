from card_platform.card_search_service import build_card_search_queries, normalize_resolved_field
from card_platform.schemas import CardRecognitionResult


def test_search_queries_use_identity_fields_and_stay_bounded() -> None:
    recognition = CardRecognitionResult(
        player_name="Kobe Bryant",
        season_start=1996,
        season_end=1997,
        brand_name="Topps",
        product_name="Topps Basketball",
        parallel_name="Refractor",
        card_number="138",
        serial_total=99,
        confidence="0.8",
    )

    queries = build_card_search_queries(recognition)

    assert len(queries) == 3
    assert all("Kobe Bryant" in query for query in queries)
    assert any("checklist" in query for query in queries)
    assert any("parallel print run" in query for query in queries)


def test_search_queries_can_use_listing_context_when_visual_identity_is_sparse() -> None:
    recognition = CardRecognitionResult(confidence="0")
    assert build_card_search_queries(recognition) == []
    queries = build_card_search_queries(
        recognition,
        "2021-22 Panini National Treasures LeBron James /99 Patch",
    )
    assert queries
    assert "LeBron James" in queries[0]


def test_search_field_values_are_normalized_to_card_schema_types() -> None:
    assert normalize_resolved_field("card_number", 2745) == "2745"
    assert normalize_resolved_field("serial_total", "99") == 99
    assert normalize_resolved_field("is_patch", "yes") is True
