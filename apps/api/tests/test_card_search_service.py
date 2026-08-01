from card_platform.card_search_service import (
    build_card_search_queries,
    candidate_is_exact_image_match,
    extract_page_image,
    is_trusted_catalog_url,
    normalize_resolved_field,
    resolved_update_is_safe,
)
from card_platform.schemas import CardRecognitionResult, CardWebCandidate


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


def test_web_resolution_cannot_negate_visual_positive_autograph() -> None:
    visual = CardRecognitionResult(
        is_autograph=True,
        autograph_type="on_card",
        confidence="0.9",
        field_evidence={"is_autograph": ["visible blue ink signature"]},
    )
    assert not resolved_update_is_safe(visual, "is_autograph", False)
    assert resolved_update_is_safe(visual, "is_autograph", True)
    assert resolved_update_is_safe(visual, "is_rookie", False)


def test_catalog_page_image_prefers_public_social_metadata() -> None:
    html = '<meta property="og:image" content="/images/kyrie-b52.jpg">'
    assert (
        extract_page_image(html, "https://www.beckett.com/card/123")
        == "https://www.beckett.com/images/kyrie-b52.jpg"
    )


def test_catalog_fetch_only_accepts_trusted_sources() -> None:
    assert is_trusted_catalog_url("https://www.tcdb.com/ViewCard.cfm?id=123")
    assert is_trusted_catalog_url("https://checklists.paniniamerica.net/card")
    assert not is_trusted_catalog_url("https://example.com/fake-card")


def test_catalog_image_requires_exact_identity_and_no_conflicts() -> None:
    target = CardRecognitionResult(
        player_name="Kyrie Irving",
        season_start=2012,
        brand_name="Panini",
        product_name="Elite",
        insert_name="Rookie Autograph",
        card_number="B52",
        is_rookie=True,
        is_autograph=True,
        confidence="1",
    )
    candidate = CardWebCandidate(
        title="2012-13 Panini Elite Kyrie Irving B52 Rookie Autograph",
        source_urls=["https://www.tcdb.com/card/123"],
        matching_fields=[
            "player_name",
            "season_start",
            "brand_name",
            "product_name",
            "insert_name",
            "card_number",
            "is_rookie",
            "is_autograph",
        ],
        conflicts=[],
        match_confidence="0.98",
    )
    assert candidate_is_exact_image_match(candidate, target)
    assert not candidate_is_exact_image_match(
        candidate.model_copy(update={"conflicts": ["parallel_name differs"]}),
        target,
    )
    assert not candidate_is_exact_image_match(
        candidate.model_copy(
            update={
                "matching_fields": [
                    "player_name",
                    "season_start",
                    "brand_name",
                    "product_name",
                ]
            }
        ),
        target,
    )
