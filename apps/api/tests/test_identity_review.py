import pytest
from pydantic import ValidationError

from card_platform.domain.identity_review import ConfirmedIdentity, structured_evidence_decision
from card_platform.domain.identity_research import SCOTTIE_BARNES_FIXTURE_VISUAL, generate_candidates


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


@pytest.mark.parametrize("card_number", ["1/250", "001 / 250"])
def test_card_number_cannot_be_serial_fraction(card_number: str) -> None:
    with pytest.raises(ValidationError, match="serial fraction"):
        ConfirmedIdentity.model_validate(identity(card_number=card_number))


def test_serial_number_cannot_be_misread_as_one_of_one() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        ConfirmedIdentity.model_validate(identity(serial_number=250, serial_total=1))


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
    candidates = generate_candidates(identity(serial_total=50), SCOTTIE_BARNES_FIXTURE_VISUAL)
    assert {item["identity"]["parallel_name"] for item in candidates} == {
        "Gold Diamante", "Gold Rainbow", "Gold Holo Foil",
        "Season Tip-Off Gold Foilboard", "Crackleboard Gold",
    }
