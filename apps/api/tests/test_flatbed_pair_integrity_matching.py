import json
from pathlib import Path

import pytest

from card_platform.flatbed_matching import CardRegion, propose_pairs, score_pair


def region(identifier, x=0.1, y=0.1, **signals):
    return CardRegion(identifier, x, y, 0.18, 0.28, signals=signals)


def test_frozen_real_mixed_product_layout_preserves_products_instead_of_flipping_years():
    case = json.loads(
        (Path(__file__).parent / "fixtures/flatbed/same-player-product-layout.json").read_text()
    )
    result = propose_pairs(
        [CardRegion(**row) for row in case["fronts"]],
        [CardRegion(**row) for row in case["backs"]],
    )
    assert result["transform"] == "identity"
    assert {(row["front_id"], row["back_id"]) for row in result["pairs"]} == {
        tuple(row) for row in case["expected_pairs"]
    }
    # These copies print the same '1 of 11941', so geometry cannot prove
    # which individual copy was flipped, even when the product matches.
    assert all(
        row["requires_review"]
        for row in result["pairs"]
        if row["front_id"] in {"f0", "f1", "f2", "f3", "f5"}
    )


def test_missing_ocr_keeps_geometry_candidate_explicitly_unverified():
    pair = propose_pairs([region("f")], [region("b")])["pairs"][0]
    assert pair["identity_status"] == "unknown"
    assert "ocr_identity_unverified" in pair["identity_reason_codes"]
    assert pair["signals"]["ocr"] == 0.5
    assert pair["requires_review"]
    assert "ocr_identity_conflict" not in pair["conflicts"]


def test_historical_year_alone_cannot_establish_or_conflict_identity():
    same = propose_pairs([region("f", year="2024")], [region("b", year="2024")])["pairs"][0]
    different = propose_pairs([region("f", year="2024")], [region("b", year="2023")])["pairs"][0]
    assert same["identity_status"] == different["identity_status"] == "unknown"
    assert same["score"] == different["score"]


def test_same_player_different_product_loses_to_nearby_correct_product():
    fronts = [
        region("fi", x=0.1, player_name="LeBron James", product_name="Instant"),
        region("fp", x=0.15, player_name="LeBron James", product_name="Prizm"),
    ]
    backs = [
        region("bp", x=0.1, player_name="LeBron James", product_name="Prizm"),
        region("bi", x=0.15, player_name="LeBron James", product_name="Instant"),
    ]
    result = propose_pairs(fronts, backs)
    assert {(row["front_id"], row["back_id"]) for row in result["pairs"]} == {
        ("fi", "bi"),
        ("fp", "bp"),
    }
    wrong = score_pair(fronts[0], backs[0], "identity")
    assert wrong.identity_status == "conflict"
    assert "ocr_product_conflict" in wrong.identity_reason_codes


def test_absent_independent_recognition_does_not_reuse_ocr_score():
    front = region("f", player_name="LeBron James", product_name="Instant")
    back = region("b", player_name="LeBron James", product_name="Instant")
    pair = score_pair(front, back, "identity")
    assert pair.signals["recognition_observed"] == 0
    # A missing independent recognition observation contributes no second
    # identity vote; an explicitly independent result can add information.
    observed = score_pair(
        front,
        region(
            "b", player_name="LeBron James", product_name="Instant", recognition_similarity={"f": 0}
        ),
        "identity",
    )
    assert observed.signals["recognition_observed"] == 1
    assert observed.score < pair.score


def test_normal_explicit_product_number_pair_can_be_consistent():
    pair = propose_pairs(
        [region("f", player_name="LeBron James", product_name="Status", card_number="5")],
        [region("b", player_name="LeBron James", product_name="Status", card_number="5")],
    )["pairs"][0]
    assert pair["identity_status"] == "consistent"
    assert not pair["requires_review"]


@pytest.mark.parametrize(
    "insert_name,product_name,card_number",
    [("Generations", "Century Legends", "G11"), ("Rookie+Veteran", "Impulse", "61")],
)
def test_shared_multi_subject_design_does_not_treat_different_player_as_wrong_card(
    insert_name, product_name, card_number
):
    pair = propose_pairs(
        [
            region(
                "f",
                player_name="Vince Carter",
                product_name=product_name,
                insert_name=insert_name,
                card_number=card_number,
            )
        ],
        [
            region(
                "b",
                player_name="Oscar Robertson",
                product_name=product_name,
                insert_name=insert_name,
                card_number=card_number,
            )
        ],
    )["pairs"][0]
    assert pair["identity_status"] != "conflict"


def test_duplicate_design_ambiguity_does_not_downgrade_known_product_conflict():
    result = propose_pairs(
        [
            region("f1", player_name="LeBron James", product_name="Select"),
            region("f2", x=0.6, player_name="LeBron James", product_name="Prizm"),
        ],
        [
            region("b1", player_name="LeBron James", product_name="Instant"),
            region("b2", x=0.6, player_name="LeBron James", product_name="Instant"),
        ],
    )
    assert all(pair["identity_status"] == "conflict" for pair in result["pairs"])


def test_prizm_foil_mark_alone_does_not_claim_product_conflict_with_select():
    result = propose_pairs(
        [region("f", ocr_text="PRIZM\nLEBRON JAMES")],
        [
            region(
                "b", ocr_text="No. 54\nCONCOURSE\nLEBRON JAMES\n2024-25 PANINI - SELECT BASKETBALL"
            )
        ],
    )
    assert result["pairs"][0]["identity_status"] != "conflict"


def test_explicit_different_release_seasons_remain_identity_evidence():
    pair = propose_pairs(
        [region("f", ocr_text="No. 5\nLEBRON JAMES\n2023-24 PANINI - PRIZM BASKETBALL")],
        [region("b", ocr_text="No. 5\nLEBRON JAMES\n2024-25 PANINI - PRIZM BASKETBALL")],
    )["pairs"][0]
    assert pair["identity_status"] == "conflict"
    assert "ocr_season_conflict" in pair["identity_reason_codes"]
