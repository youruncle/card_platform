from card_platform.card_search_service import (
    build_card_search_queries,
    candidate_is_exact_image_match,
    extract_page_image,
    is_trusted_catalog_url,
    normalize_resolved_field,
    resolved_update_is_safe,
    candidate_supports_resolved_field,
    source_page_supports_candidate,
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


def test_catalog_queries_keep_independent_insert_when_product_and_number_are_uncertain() -> None:
    recognition = CardRecognitionResult(
        player_name="Fixture Player",
        product_name="Uncertain Parent Product",
        insert_name="Distinctive Insert",
        card_number="OCR-77",
        season_start=2025,
        serial_total=150,
        confidence="0.5",
    )
    queries = build_card_search_queries(recognition)
    assert len(queries) == 4
    independent = '"Fixture Player" "Distinctive Insert" card checklist'
    assert independent in queries
    assert "Uncertain" not in independent
    assert "OCR-77" not in independent
    assert any("parallel print run" in query for query in queries)


def test_search_field_values_are_normalized_to_card_schema_types() -> None:
    assert normalize_resolved_field("card_number", 2745) == "2745"
    assert normalize_resolved_field("serial_total", "99") == 99
    assert normalize_resolved_field("is_patch", "yes") is True
    assert normalize_resolved_field("product_name", "   ") is None


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


def test_catalog_cannot_invent_or_replace_owned_copy_serial() -> None:
    no_stamp = CardRecognitionResult(confidence="0.8")
    assert not resolved_update_is_safe(no_stamp, "serial_total", 99)

    structured_stamp = CardRecognitionResult(
        serial_number=194,
        serial_total=249,
        is_numbered=True,
        confidence="0.8",
    )
    assert resolved_update_is_safe(structured_stamp, "serial_total", 249)
    assert not resolved_update_is_safe(structured_stamp, "serial_total", 99)
    disagreeing_evidence = structured_stamp.model_copy(
        update={"field_evidence": {"serial_total": ["Apple Vision OCR：20/20"]}}
    )
    assert resolved_update_is_safe(disagreeing_evidence, "serial_total", 249)
    assert not resolved_update_is_safe(disagreeing_evidence, "serial_total", 20)


def test_conflicting_physical_serial_evidence_fails_closed() -> None:
    visual = CardRecognitionResult(
        confidence="0.8",
        field_evidence={
            "serial_total": [
                "Apple Vision OCR：20/20",
                "Apple Vision OCR：20/28",
            ]
        },
    )
    assert not resolved_update_is_safe(visual, "serial_total", 20)
    assert not resolved_update_is_safe(visual, "serial_total", 28)
    assert not resolved_update_is_safe(visual, "serial_total", 99)


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


def test_resolved_field_requires_same_strong_candidate_to_support_value() -> None:
    candidate = CardWebCandidate(
        title="Scottie Barnes checklist candidate",
        identity={
            "player_name": "Scottie Barnes",
            "card_number": "31",
            "season_start": 2025,
            "parallel_name": "Gold Diamante",
        },
        source_urls=["https://www.beckett.com/card/123"],
        matching_fields=[
            "player_name",
            "card_number",
            "season_start",
            "parallel_name",
        ],
        match_confidence="0.92",
    )
    assert candidate_supports_resolved_field(candidate, "season_start", 2025)
    assert not candidate_supports_resolved_field(candidate, "season_start", 2024)
    assert not candidate_supports_resolved_field(
        candidate.model_copy(update={"source_urls": ["https://ebay.com/item/1"]}),
        "parallel_name",
        "Gold Diamante",
    )


def test_resolved_field_requires_joint_player_and_card_or_insert_anchor() -> None:
    candidate = CardWebCandidate(
        title="Kobe Bryant product candidate",
        identity={
            "player_name": "Kobe Bryant",
            "product_name": "Wrong Child",
            "season_start": 2020,
        },
        source_urls=["https://www.tcdb.com/card/123"],
        matching_fields=["player_name", "product_name", "season_start"],
        match_confidence="0.95",
    )
    assert not candidate_supports_resolved_field(candidate, "product_name", "Wrong Child")


def test_strong_identity_sources_accept_only_same_domain_or_safe_subdomain() -> None:
    candidate = CardWebCandidate(
        title="Kobe Bryant All Heart",
        identity={
            "player_name": "Kobe Bryant",
            "insert_name": "All Heart",
            "product_name": "Donruss",
        },
        source_urls=["https://checklists.paniniamerica.net/card/123"],
        matching_fields=["player_name", "insert_name", "product_name"],
        match_confidence="0.95",
    )
    assert candidate_supports_resolved_field(candidate, "product_name", "Donruss")
    assert not candidate_supports_resolved_field(
        candidate.model_copy(
            update={"source_urls": ["https://example.paniniamerica.net.evil.test/card"]}
        ),
        "product_name",
        "Donruss",
    )


def test_source_page_must_contain_exact_candidate_anchors() -> None:
    candidate = CardWebCandidate(
        title="Scottie Barnes Gold Diamante",
        identity={
            "player_name": "Scottie Barnes",
            "card_number": "31",
            "brand_name": "Panini",
            "product_name": "Donruss Elite",
            "season_start": 2025,
            "season_end": 2026,
            "parallel_name": "Gold Diamante",
        },
        source_urls=["https://www.beckett.com/checklist"],
        matching_fields=[
            "player_name",
            "card_number",
            "brand_name",
            "product_name",
            "season_start",
            "season_end",
            "parallel_name",
        ],
        match_confidence="0.95",
    )
    assert source_page_supports_candidate(
        "2025-26 Panini Donruss Elite checklist: #31 Scottie Barnes — Gold Diamante /50",
        candidate,
    )
    assert not source_page_supports_candidate(
        "Generic Topps Basketball product page with Gold parallels", candidate
    )


def test_source_page_cannot_validate_model_invented_child_product_or_season() -> None:
    candidate = CardWebCandidate(
        title="Kobe Bryant All Heart Wrong Child",
        identity={
            "player_name": "Kobe Bryant",
            "brand_name": "Panini",
            "product_name": "Wrong Child",
            "insert_name": "All Heart",
            "season_start": 2020,
        },
        source_urls=["https://www.tcdb.com/card/123"],
        matching_fields=[
            "player_name",
            "brand_name",
            "product_name",
            "insert_name",
            "season_start",
        ],
        match_confidence="0.95",
    )
    assert not source_page_supports_candidate(
        "Panini checklist: Kobe Bryant — All Heart", candidate
    )
    assert source_page_supports_candidate(
        "2020 Panini Wrong Child checklist: Kobe Bryant — All Heart", candidate
    )


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
