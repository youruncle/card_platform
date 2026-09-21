import pytest
from decimal import Decimal
from pydantic import ValidationError

from card_platform.domain.identity_review import ConfirmedIdentity, structured_evidence_decision
from card_platform.domain.identity_research import (
    ACE_BAILEY_FIXTURE_VISUAL,
    ANDREW_NEMBHARD_FIXTURE_VISUAL,
    BRANDON_MILLER_FIXTURE_VISUAL,
    JAREN_JACKSON_FIXTURE_VISUAL,
    JAREN_JACKSON_88_FIXTURE_VISUAL,
    LEBRON_JAMES_FIXTURE_VISUAL,
    LUKA_DONCIC_FIXTURE_VISUAL,
    SCOTTIE_BARNES_FIXTURE_VISUAL,
    STEPHEN_CURRY_FIXTURE_VISUAL,
    automatic_candidate_decision,
    generate_candidates,
)
from card_platform.identity_review_api import (
    _enrich_candidate_evidence,
    _identity,
    _market_comp_uuid_ids,
)


def identity(**updates):
    payload = {
        "player_name": "Stephen Curry", "season_start": 2023, "season_end": 2024,
        "brand_name": "Panini", "product_name": "Prizm", "set_name": "Base",
        "card_number": "1", "insert_name": None, "parallel_name": "Blue /250",
        "serial_number": 1, "serial_total": 250, "is_rookie": False,
        "is_autograph": False, "autograph_type": "none", "is_memorabilia": False,
        "memorabilia_types": [], "grade_company": None, "grade_value": None,
    }
    payload.update(updates)
    return payload


def test_identity_audit_payload_serializes_database_decimal_grade() -> None:
    payload = _identity(identity(grade_company="PSA", grade_value=Decimal("10.0")))

    assert payload["grade_value"] == 10.0


def test_candidate_evidence_is_enriched_with_canonical_source_and_image() -> None:
    candidates = [{
        "id": "candidate-1",
        "market_evidence": [{
            "id": "cardhobby:sale-1",
            "provider_code": "cardhobby",
            "external_sale_id": "sale-1",
            "sale_price": "4.00",
        }],
    }]
    evidence = [{
        "id": "evidence-1",
        "provider_code": "cardhobby",
        "external_sale_id": "sale-1",
        "raw_title": "Exact card sale",
        "source_url": "https://example.com/sale-1",
        "image_url": "https://example.com/sale-1.jpg",
        "sale_price": "4.00",
        "shipping_price": "18.00",
        "currency": "CNY",
        "occurred_at": "2026-08-15T12:00:00Z",
        "evidence_level": "ended_auction",
    }]

    enriched = _enrich_candidate_evidence(candidates, evidence)

    sale = enriched[0]["market_evidence"][0]
    assert sale["source_url"] == "https://example.com/sale-1"
    assert sale["image_url"] == "https://example.com/sale-1.jpg"
    assert sale["raw_title"] == "Exact card sale"
    assert sale["shipping_price"] == "18.00"
    assert "source_url" not in candidates[0]["market_evidence"][0]


def test_shadow_source_lookup_ignores_provider_native_evidence_ids() -> None:
    valid_id = "a928d653-5b9e-4688-a6bd-8b7f17404ae7"

    assert _market_comp_uuid_ids([
        "ebay:scottie-gold-diamante-2025-11-28",
        valid_id,
        "",
    ]) == [valid_id]


def test_confirmed_candidate_gets_only_sources_used_by_latest_shadow() -> None:
    confirmed = {"player_name": "LeBron James", "card_number": "BH-2"}
    candidates = [{"id": "candidate-1", "identity": confirmed, "market_evidence": []}]
    evidence = [
        {
            "id": "used-id",
            "market_comp_id": "used-comp-id",
            "provider_code": "cardhobby",
            "external_sale_id": "used-sale",
            "source_url": "https://example.com/used",
            "image_url": "https://example.com/used.jpg",
        },
        {
            "id": "rejected-id",
            "provider_code": "cardhobby",
            "external_sale_id": "rejected-sale",
            "source_url": "https://example.com/rejected",
            "image_url": "https://example.com/rejected.jpg",
        },
    ]

    enriched = _enrich_candidate_evidence(
        candidates,
        evidence,
        confirmed_identity=confirmed,
        shadow_used_ids=["used-comp-id"],
    )

    assert [sale["external_sale_id"] for sale in enriched[0]["market_evidence"]] == [
        "used-sale"
    ]


@pytest.mark.parametrize("card_number", ["1/250", "001 / 250"])
def test_card_number_cannot_be_serial_fraction(card_number: str) -> None:
    with pytest.raises(ValidationError, match="serial fraction"):
        ConfirmedIdentity.model_validate(identity(card_number=card_number))


def test_serial_number_cannot_be_misread_as_one_of_one() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        ConfirmedIdentity.model_validate(identity(serial_number=250, serial_total=1))


def test_numbered_autograph_insert_can_be_confirmed_without_invented_parallel() -> None:
    confirmed = ConfirmedIdentity.model_validate(
        identity(
            insert_name="Hoops Retro Autographs",
            parallel_name=None,
            serial_number=24,
            serial_total=199,
            is_autograph=True,
            autograph_type="sticker",
        )
    )

    assert confirmed.parallel_name is None
    assert confirmed.serial_total == 199


def test_numbered_base_with_card_number_can_be_confirmed_without_parallel() -> None:
    confirmed = ConfirmedIdentity.model_validate(
        identity(
            card_number="97",
            insert_name=None,
            parallel_name=None,
            serial_number=1,
            serial_total=99,
        )
    )

    assert confirmed.card_number == "97"
    assert confirmed.parallel_name is None


@pytest.mark.parametrize("parallel", ["Prizm", "Refractor", "parallel", "折射版"])
def test_generic_parallel_cannot_be_confirmed(parallel: str) -> None:
    with pytest.raises(ValidationError, match="generic"):
        ConfirmedIdentity.model_validate(identity(parallel_name=parallel))


def test_evidence_decision_requires_t0_and_eligible_grade() -> None:
    assert structured_evidence_decision({"comparable_tier": "T0"}, "ended_auction") == (
        "accepted", ["exact_identity_t0_e0_e2"]
    )
    decision, reasons = structured_evidence_decision(
        {"comparable_tier": "T1", "hard_conflicts": ["serial_total_mismatch"]},
        "asking_price",
    )
    assert decision == "rejected"
    assert reasons == ["serial_total_mismatch", "not_exact_identity", "evidence_grade_not_eligible"]


def test_scottie_fixture_research_ranks_gold_diamante_first() -> None:
    current = identity(player_name="SCOTTIE BARNES", season_start=2024, season_end=2025,
                       brand_name="Topps", product_name="Flagship", set_name=None,
                       card_number="31", insert_name="Flagship", parallel_name="Prizm",
                       serial_number=27, serial_total=50)
    candidates = generate_candidates(current, SCOTTIE_BARNES_FIXTURE_VISUAL)
    first = candidates[0]
    assert first["identity"]["season_start"] == 2025
    assert first["identity"]["parallel_name"] == "Gold Diamante"
    assert first["identity"]["serial_number"] == 27
    assert first["identity"]["serial_total"] == 50
    assert first["is_valid"] is True
    assert len(first["market_evidence"]) == 2


def test_same_serial_total_keeps_distinct_parallel_candidates() -> None:
    candidates = generate_candidates(
        identity(brand_name="Topps", product_name="Flagship", serial_total=50),
        SCOTTIE_BARNES_FIXTURE_VISUAL,
    )
    assert {item["identity"]["parallel_name"] for item in candidates} == {
        "Gold Diamante", "Gold Rainbow", "Gold Holo Foil",
        "Season Tip-Off Gold Foilboard", "Crackleboard Gold",
    }


def test_automatic_identity_accepts_one_valid_resolved_candidate() -> None:
    candidate = {
        "identity": identity(parallel_name="Blue", serial_number=1, serial_total=250),
        "confidence": "0.96",
        "is_valid": True,
        "validation_reasons": [],
        "visual_reasons": ["tiered_recognition_status:resolved"],
        "field_provenance": {
            "player_name": ["front"],
            "season_start": ["checklist"],
            "product_name": ["logo"],
            "card_number": ["back"],
        },
    }

    selected, reasons = automatic_candidate_decision([candidate])

    assert selected is candidate
    assert reasons == ["single_valid_high_confidence_candidate"]


def test_automatic_identity_rejects_model_research_request_with_diagnostics() -> None:
    candidate = {
        "identity": identity(parallel_name="Blue", serial_number=1, serial_total=250),
        "confidence": "0.99",
        "is_valid": True,
        "validation_reasons": [],
        "visual_reasons": [
            "tiered_recognition_status:research_required",
            "quality_issue:model_reported_conflict",
        ],
        "field_provenance": {
            "player_name": ["front"],
            "season_start": ["checklist"],
            "product_name": ["logo"],
            "card_number": ["back"],
        },
    }

    selected, reasons = automatic_candidate_decision([candidate])

    assert selected is None
    assert reasons[0] == "vision_quality_gate_failed"
    assert "quality_issue:model_reported_conflict" in reasons


@pytest.mark.parametrize(
    ("identity_updates", "rationale", "expected_reason"),
    [
        (
            {"card_number": "30"},
            "正面 30 为球员球衣号，非卡号；背面实际编号为 SC-15。",
            "candidate_card_number_denied_by_own_rationale",
        ),
        (
            {"serial_number": 3, "serial_total": 19},
            "正面 3/19/98 是比赛日期，并非限编编号。",
            "candidate_serial_denied_by_own_rationale",
        ),
    ],
)
def test_automatic_identity_rejects_fields_denied_by_its_own_rationale(
    identity_updates: dict, rationale: str, expected_reason: str
) -> None:
    candidate = {
        "identity": identity(parallel_name="Blue", **identity_updates),
        "confidence": "0.99",
        "is_valid": True,
        "validation_reasons": [],
        "visual_reasons": ["tiered_recognition_status:resolved", rationale],
        "field_provenance": {
            "player_name": ["front"],
            "season_start": ["checklist"],
            "product_name": ["logo"],
            "card_number": ["back"],
        },
    }

    selected, reasons = automatic_candidate_decision([candidate])

    assert selected is None
    assert expected_reason in reasons


def test_brandon_prizm_base_removes_generic_parallel_fields() -> None:
    current = identity(
        player_name="BRANDON MILLER",
        product_name="Prizm Basketball",
        card_number="152",
        insert_name="Prizm",
        parallel_name="Prizm",
        serial_number=None,
        serial_total=None,
        is_rookie=True,
    )
    candidate = generate_candidates(current, BRANDON_MILLER_FIXTURE_VISUAL)[0]
    assert candidate["identity"]["insert_name"] is None
    assert candidate["identity"]["parallel_name"] is None
    assert candidate["identity"]["set_name"] == "Base"
    assert candidate["is_valid"] is True


def test_jaren_image_and_checklist_correct_season_and_parallel() -> None:
    current = identity(
        player_name="JAREN JACKSON JR.",
        season_start=2019,
        season_end=2020,
        product_name="Mosaic Basketball",
        card_number="29",
        insert_name="Prizm",
        parallel_name="Prizm",
        serial_number=22,
        serial_total=50,
    )
    candidate = generate_candidates(current, JAREN_JACKSON_FIXTURE_VISUAL)[0]
    assert candidate["identity"]["season_start"] == 2020
    assert candidate["identity"]["season_end"] == 2021
    assert candidate["identity"]["parallel_name"] == "Fast Break Purple Mosaic"
    assert candidate["identity"]["serial_number"] == 22
    assert candidate["identity"]["serial_total"] == 50
    assert candidate["is_valid"] is True
    assert float(candidate["confidence"]) >= 0.85


def test_luka_image_and_checklist_replace_wrong_parallel_with_blue_candidate() -> None:
    current = identity(
        player_name="LUKA DONČIĆ",
        season_start=2023,
        season_end=2024,
        product_name="Crown Royale Basketball",
        set_name=None,
        card_number="No. 4",
        insert_name="Test of Time",
        parallel_name="Fusion Red and Yellow",
        serial_number=10,
        serial_total=75,
    )
    candidate = generate_candidates(current, LUKA_DONCIC_FIXTURE_VISUAL)[0]
    assert candidate["identity"]["card_number"] == "4"
    assert candidate["identity"]["insert_name"] == "Test of Time"
    assert candidate["identity"]["parallel_name"] == "Blue"
    assert candidate["identity"]["serial_number"] == 10
    assert candidate["identity"]["serial_total"] == 75
    assert candidate["is_valid"] is True
    assert float(candidate["confidence"]) >= 0.90
    assert "Fusion Red and Yellow" not in candidate["search_queries"][0]


@pytest.mark.parametrize(
    ("visual", "expected"),
    [
        (JAREN_JACKSON_88_FIXTURE_VISUAL, (2020, "29", "Fusion Red and Yellow Choice Mosaic", 88)),
        (ANDREW_NEMBHARD_FIXTURE_VISUAL, (2023, "83", "Aspirations", 8)),
        (STEPHEN_CURRY_FIXTURE_VISUAL, (2025, "SA-5", None, None)),
        (LEBRON_JAMES_FIXTURE_VISUAL, (2025, "BH-2", None, None)),
        (ACE_BAILEY_FIXTURE_VISUAL, (2025, "255", "Basketball Refractor", None)),
    ],
)
def test_failed_portfolio_cards_get_valid_image_backed_candidates(visual, expected) -> None:
    candidate = generate_candidates(identity(), visual)[0]
    resolved = candidate["identity"]
    assert (resolved["season_start"], resolved["card_number"],
            resolved["parallel_name"], resolved["serial_total"]) == expected
    assert candidate["is_valid"] is True
    assert candidate["checklist_evidence"]
