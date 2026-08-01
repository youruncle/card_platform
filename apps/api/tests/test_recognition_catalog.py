from decimal import Decimal

from card_platform.recognition_catalog import (
    knowledge_card_identity,
    machine_training_card_entity,
    parallel_option_name,
    rank_compositional_candidates,
    rank_knowledge_cards,
    rank_structured_uncertainty_candidates,
    scoped_parallel_options,
)
from card_platform.schemas import CardRecognitionResult


def test_rank_knowledge_cards_requires_player_and_two_identity_anchors() -> None:
    visual = CardRecognitionResult(
        player_name="Luka Dončić",
        season_start=2023,
        season_end=2024,
        product_name="Panini One and One",
        insert_name="Downtown",
        card_number="25",
        confidence=Decimal("0.9"),
    )
    entities = [
        {
            "id": "exact",
            "entity_type": "card",
            "canonical_name": "2023-24 One and One Downtown #25 Luka Doncic",
            "attributes": {
                "player": "Luka Doncic",
                "season_start": 2023,
                "season_end": 2024,
                "product": "One and One",
                "insert": "Downtown",
                "card_number": "25",
            },
        },
        {
            "id": "wrong-player",
            "entity_type": "card",
            "canonical_name": "Different player",
            "attributes": {
                "player": "LeBron James",
                "season_start": 2023,
                "product": "One and One",
                "card_number": "25",
            },
        },
        {
            "id": "too-vague",
            "entity_type": "card",
            "canonical_name": "Only player",
            "attributes": {"player": "Luka Doncic"},
        },
    ]
    ranked = rank_knowledge_cards(visual, entities)
    assert [candidate["knowledge_entity_id"] for candidate in ranked] == ["exact"]
    assert ranked[0]["match_confidence"] >= "0.8"


def test_machine_training_truth_is_usable_as_card_and_parallel_knowledge() -> None:
    entity = machine_training_card_entity(
        "sample-1",
        {
            "player_name": "Amen Thompson",
            "season_start": 2023,
            "season_end": 2024,
            "brand_name": "Panini",
            "product_name": "Prizm",
            "parallel_name": "Silver",
            "card_number": "150",
            "is_rookie": True,
        },
        confidence="0.96",
    )
    assert entity["entity_type"] == "card"
    assert entity["attributes"]["parallel"] == "Silver"
    assert entity["attributes"]["machine_training"] is True
    visual = CardRecognitionResult(
        player_name="Amen Thompson",
        season_start=2023,
        product_name="Prizm Basketball",
        card_number="150",
        confidence=Decimal("0.8"),
    )
    ranked = rank_knowledge_cards(visual, [entity])
    assert ranked[0]["identity"]["parallel_name"] == "Silver"


def test_rank_knowledge_cards_penalizes_conflicting_card_number() -> None:
    visual = CardRecognitionResult(
        player_name="Luka Doncic",
        season_start=2023,
        product_name="One and One",
        insert_name="Downtown",
        card_number="25",
        confidence=Decimal("0.9"),
    )
    entities = [
        {
            "id": "wrong-number",
            "entity_type": "card",
            "canonical_name": "Wrong",
            "attributes": {
                "player": "Luka Doncic",
                "season_start": 2023,
                "product": "One and One",
                "insert": "Downtown",
                "card_number": "3",
            },
        }
    ]
    candidate = rank_knowledge_cards(visual, entities)[0]
    assert "card_number" in candidate["conflicts"]


def test_knowledge_card_identity_preserves_rich_card_attributes_and_unknowns() -> None:
    identity = knowledge_card_identity(
        {
            "attributes": {
                "player": "Anthony Edwards",
                "product": "Impeccable",
                "is_rookie": True,
                "is_autograph": True,
                "autograph_type": "on_card",
                "is_memorabilia": True,
                "memorabilia_types": ["patch"],
                "memorabilia_usage": "not_from_specific_game_or_event",
                "is_patch": True,
            }
        }
    )
    assert identity["is_rookie"] is True
    assert identity["autograph_type"] == "on_card"
    assert identity["is_memorabilia"] is True
    assert identity["memorabilia_types"] == ["patch"]
    assert identity["is_patch"] is True

    unknown = knowledge_card_identity({"attributes": {"player": "Stephen Curry"}})
    assert unknown["is_rookie"] is None
    assert unknown["is_autograph"] is None
    assert unknown["is_patch"] is None


def test_compositional_candidate_uses_scoped_serial_parallel_without_changing_visual() -> None:
    visual = CardRecognitionResult(
        player_name="Stephen Curry",
        season_start=2023,
        season_end=2024,
        product_name="Prizm Basketball",
        parallel_name="Gold",
        serial_total=10,
        confidence=Decimal("0.8"),
    )
    entities = [
        {
            "id": "gold-laser",
            "entity_type": "parallel",
            "canonical_name": "2023-24 Panini Prizm Gold Laser /10",
            "attributes": {
                "product": "2023-24 Panini Prizm Basketball",
                "season_start": 2023,
                "color": "Gold",
                "pattern": "Laser",
                "serial_total": 10,
            },
        },
        {
            "id": "wrong-product",
            "entity_type": "parallel",
            "canonical_name": "2023-24 Select Gold Laser /10",
            "attributes": {
                "product": "2023-24 Select Basketball",
                "season_start": 2023,
                "color": "Gold",
                "pattern": "Laser",
                "serial_total": 10,
            },
        },
    ]
    candidates = rank_compositional_candidates(visual, entities)
    assert visual.parallel_name == "Gold"
    assert [item["knowledge_entity_id"] for item in candidates] == ["gold-laser"]
    assert candidates[0]["identity"]["parallel_name"] == "Gold Laser"


def test_compositional_candidate_rejects_cross_season_component() -> None:
    visual = CardRecognitionResult(
        player_name="Stephen Curry",
        season_start=2023,
        product_name="Prizm Basketball",
        parallel_name="Silver",
        confidence=Decimal("0.8"),
    )
    entities = [
        {
            "id": "wrong-season",
            "entity_type": "parallel",
            "canonical_name": "2022-23 Panini Prizm Silver",
            "attributes": {
                "product": "2022-23 Panini Prizm Basketball",
                "season_start": 2022,
                "finish": "Silver",
            },
        }
    ]
    assert rank_compositional_candidates(visual, entities) == []


def test_structured_uncertainty_prioritizes_adjacent_season_candidates() -> None:
    visual = CardRecognitionResult(
        player_name="Cooper Flagg",
        season_start=2024,
        season_end=2025,
        product_name="Topps Chrome",
        card_number="251",
        confidence=Decimal("0.8"),
    )
    candidates = rank_structured_uncertainty_candidates(visual, limit=2)
    assert candidates[0]["identity"]["season_start"] == 2025
    assert candidates[0]["identity"]["season_end"] == 2026
    assert set(candidates[0]["conflicts"]) == {"season_start", "season_end"}


def test_structured_uncertainty_uses_copyright_for_statistical_year_pollution() -> None:
    visual = CardRecognitionResult(
        player_name="Dylan Harper",
        season_start=2024,
        season_end=2025,
        copyright_year=2025,
        product_name="Topps Chrome",
        card_number="251",
        confidence=Decimal("0.8"),
        field_evidence={
            "season_start": ["YEAR SCHOOL 24-25 RUTGERS college record"],
            "copyright_year": ["© 2025 THE TOPPS COMPANY, INC."],
        },
    )

    candidates = rank_structured_uncertainty_candidates(visual, limit=2)
    assert candidates[0]["identity"]["season_start"] == 2025
    assert candidates[0]["identity"]["season_end"] == 2026


def test_structured_uncertainty_candidates_change_only_one_field() -> None:
    visual = CardRecognitionResult(
        player_name="Victor Wembanyama",
        product_name="Prizm",
        is_rookie=True,
        is_numbered=True,
        serial_total=99,
        is_autograph=True,
        autograph_type="on_card",
        confidence=Decimal("0.8"),
    )
    candidates = rank_structured_uncertainty_candidates(visual)
    assert len(candidates) == 2
    for candidate in candidates:
        changed = [
            field
            for field, value in candidate["identity"].items()
            if value != visual.model_dump(mode="json").get(field)
        ]
        assert changed == candidate["conflicts"]


def test_parallel_option_name_removes_release_and_parent_set() -> None:
    entity = {
        "canonical_name": "2025-26 Topps Chrome Autograph Refractor Gold /50",
        "attributes": {
            "product": "2025-26 Topps Chrome Basketball",
            "parent_set": "Chrome Autograph Refractors",
            "serial_total": 50,
        },
    }
    assert parallel_option_name(entity) == "Gold"


def test_scoped_parallel_options_enforces_product_season_insert_and_print_run() -> None:
    visual = CardRecognitionResult(
        player_name="Nique Clifford",
        season_start=2025,
        product_name="Topps Chrome",
        insert_name="Next Stop Signatures",
        serial_total=75,
        confidence=Decimal("0.8"),
    )
    entities = [
        {
            "id": "purple",
            "entity_type": "parallel",
            "canonical_name": "2025-26 Topps Chrome Next Stop Signatures Purple /75",
            "attributes": {
                "product": "2025-26 Topps Chrome Basketball",
                "insert": "Next Stop Signatures",
                "season_start": 2025,
                "color": "Purple",
                "serial_total": 75,
            },
        },
        {
            "id": "wrong-run",
            "entity_type": "parallel",
            "canonical_name": "2025-26 Topps Chrome Next Stop Signatures Gold /50",
            "attributes": {
                "product": "2025-26 Topps Chrome Basketball",
                "insert": "Next Stop Signatures",
                "season_start": 2025,
                "color": "Gold",
                "serial_total": 50,
            },
        },
        {
            "id": "wrong-product",
            "entity_type": "parallel",
            "canonical_name": "2025-26 Topps Finest Purple /75",
            "attributes": {
                "product": "2025-26 Topps Finest Basketball",
                "season_start": 2025,
                "color": "Purple",
                "serial_total": 75,
            },
        },
    ]
    options = scoped_parallel_options(visual, entities)
    assert [option["knowledge_entity_id"] for option in options] == ["purple"]
    assert options[0]["name"] == "Purple"


def test_scoped_parallel_options_reuses_observed_card_parallel() -> None:
    visual = CardRecognitionResult(
        player_name="Another Player",
        season_start=2021,
        product_name="Prizm",
        serial_total=75,
        confidence=Decimal("0.8"),
    )
    entities = [
        {
            "id": "observed-card",
            "entity_type": "card",
            "canonical_name": "Observed Mobley card",
            "attributes": {
                "player": "Evan Mobley",
                "product": "Prizm",
                "season_start": 2021,
                "parallel_name": "75th Anniversary Diamond",
                "serial_total": 75,
            },
        }
    ]
    options = scoped_parallel_options(visual, entities)
    assert options[0]["name"] == "75th Anniversary Diamond"


def test_scoped_parallel_options_allows_insert_conflict_with_exact_print_run() -> None:
    visual = CardRecognitionResult(
        player_name="Kasparas Jakucionis",
        season_start=2025,
        product_name="Topps Chrome",
        insert_name="Tales",
        serial_total=5,
        confidence=Decimal("0.8"),
    )
    entities = [
        {
            "id": "court-giants-red",
            "entity_type": "parallel",
            "canonical_name": "Court Giants Red /5",
            "attributes": {
                "product": "Topps Chrome",
                "insert": "Court Giants",
                "season_start": 2025,
                "parallel_name": "Red",
                "serial_total": 5,
            },
        }
    ]
    options = scoped_parallel_options(visual, entities)
    assert options[0]["name"] == "Red"
    assert options[0]["conflicts"] == ["insert_name"]
