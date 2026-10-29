"""Pair decisions must follow the two physical faces, not merged identity."""
import pytest
from fastapi import HTTPException

from card_platform.schemas import CardRecognitionResult

HASHES = {"front": "a" * 64, "back": "b" * 64}


def pair(**changes):
    value = {
        "status": "consistent", "evidence": ["Both faces independently read"],
        "front": {"role": "front", "player_names": ["LeBron James"],
                  "product_name": "Prizm", "insert_name": "Deep Space",
                  "evidence": ["Front Prizm / Deep Space title"]},
        "back": {"role": "back", "player_names": ["LeBron James"],
                 "product_name": "Prizm", "card_number": "5",
                 "evidence": ["Back 2024-25 Panini Prizm No.5"]},
    }
    value.update(changes)
    return value


def derive(value, **extra):
    from card_platform.pair_integrity import derive_pair_validation
    return derive_pair_validation({"pair_validation": value, **extra}, HASHES)


def gate(result, hashes=HASHES, review=None):
    from card_platform.pair_integrity import validate_pair_for_confirmation
    return validate_pair_for_confirmation(result, hashes, review)


def test_schema_preserves_independent_observations():
    result = CardRecognitionResult.model_validate({"confidence": ".9", "pair_validation": pair()})
    assert result.model_dump()["pair_validation"]["front"]["product_name"] == "Prizm"


def test_missing_model_field_remains_unknown_but_does_not_block_intake():
    decision = derive(None)
    assert decision["status"] == "unknown"
    gate({"pair_validation": decision})
    assert decision["status"] == "unknown"
    assert decision["front"]["evidence"] == []
    assert decision["back"]["evidence"] == []
    gate({"pair_validation": decision}, review={"confirmed": True, "image_content_hashes": HASHES})
    with pytest.raises(HTTPException):
        gate({"pair_validation": decision}, review={"confirmed": True, "image_content_hashes": {**HASHES, "back": "c" * 64}})


@pytest.mark.parametrize("field,value,reason", [
    ("product_name", "Instant", "product_conflict"),
    ("player_names", ["Stephen Curry"], "subject_conflict"),
    ("role", "front", "duplicate_front"),
])
def test_positive_conflicts_override_model_consistent(field, value, reason):
    raw = pair()
    raw["back"][field] = value
    decision = derive(raw)
    assert decision["status"] == "conflict"
    assert reason in decision["reason_codes"]
    with pytest.raises(HTTPException):
        gate({"pair_validation": decision}, review={"confirmed": True, "image_content_hashes": HASHES})


def test_bare_consistent_string_does_not_create_evidence():
    assert derive({"status": "consistent"})["status"] == "unknown"


def test_consistent_requires_current_images_and_preserves_model_conflict():
    decision = derive(pair())
    assert decision["status"] == "consistent"
    gate({"pair_validation": decision})
    with pytest.raises(HTTPException):
        gate({"pair_validation": decision}, {**HASHES, "front": "c" * 64})
    assert derive(pair(status="conflict", reason_codes=["different_cards"]))["status"] == "conflict"


@pytest.mark.parametrize("insert,number", [("Generations", "G11"), ("Impulse", "61")])
def test_legitimate_multi_subject_shared_design_is_not_a_name_conflict(insert, number):
    raw = pair()
    raw["front"].update(player_names=["Michael Jordan"], insert_name=insert, card_number=number)
    raw["back"].update(player_names=["LeBron James"], insert_name=insert, card_number=number)
    assert derive(raw)["status"] == "consistent"


def test_topps_and_bowman_manufacturer_logos_are_compatible():
    raw = pair()
    raw["front"].update(product_name="Bowman Chrome", brand_name="Bowman")
    raw["back"].update(product_name="Topps Chrome", brand_name="Topps")
    assert derive(raw)["status"] == "consistent"


def test_caveat_conflict_and_duplicate_bytes_cannot_be_reviewed_away():
    assert derive(pair(), caveats=["正面为LeBron James，背面为Stephen Curry，正反面不是同一张卡"])["status"] == "conflict"
    assert derive(pair(), caveats=["未发现正反面身份冲突"])["status"] == "consistent"
    with pytest.raises(HTTPException):
        gate({}, {"front": "a" * 64, "back": "a" * 64}, {"confirmed": True})


def test_single_image_keeps_existing_confirmation_behavior():
    gate({}, {"front": "a" * 64})


def test_client_review_cannot_replace_unknown_with_consistent_claim():
    raw = {}
    gate(raw, review={"status": "consistent", "confirmed": True, "image_content_hashes": HASHES})
    assert raw == {}
    assert derive(None)["status"] == "unknown"


@pytest.mark.parametrize("validation", [None, pair()])
def test_supplied_review_for_different_current_images_still_blocks_intake(validation):
    decision = derive(validation)
    with pytest.raises(HTTPException) as caught:
        gate({"pair_validation": decision}, review={
            "confirmed": True, "image_content_hashes": {**HASHES, "back": "c" * 64},
        })
    assert caught.value.status_code == 409


def test_impulse_61_and_middle_name_variation_are_not_false_subject_conflicts():
    raw = pair()
    raw["front"].update(player_names=["Kobe Bryant"], product_name="Collector's Edge Impulse", insert_name=None, card_number="61")
    raw["back"].update(player_names=["Corey Benjamin"], product_name="Collector's Edge Impulse", insert_name=None, card_number="61", multi_subject=True)
    assert derive(raw)["status"] != "conflict"


def test_malformed_pair_data_remains_unknown_without_losing_card_identity():
    result = CardRecognitionResult.model_validate({"confidence": ".9", "player_name": "LeBron James",
                                                 "pair_validation": {"front": "unreadable"}})
    assert result.player_name == "LeBron James"
    assert result.pair_validation.status == "unknown"


def test_malformed_conflict_still_cannot_be_confirmed():
    raw = {"status": "conflict", "front": "bad model shape"}
    with pytest.raises(HTTPException):
        gate({"pair_validation": raw}, review={"confirmed": True, "image_content_hashes": HASHES})


def test_middle_name_variation_is_not_a_definite_subject_conflict():
    raw = pair()
    raw["back"]["player_names"] = ["LeBron Raymone James"]
    assert derive(raw)["status"] != "conflict"


def test_verified_impulse_61_front_style_reverse_is_unknown_not_hard_conflict():
    # Actual bounded vision replay of the verified two-subject design (#849).
    raw = {"status": "conflict", "reason_codes": ["both_front", "different_players", "different_card_numbers"],
           "front": {"role": "front", "player_names": ["Kobe Bryant"], "brand_name": "Collector's Edge",
                     "product_name": "Impulse'98", "card_number": "61", "evidence": ["Kobe Bryant /61"]},
           "back": {"role": "front", "player_names": ["Corey Benjamin"], "brand_name": "Collector's Edge",
                    "product_name": "Impulse'98", "card_number": None, "evidence": ["Corey Benjamin; Pick No.28 is draft position"]}}
    decision = derive(raw)
    assert decision["status"] == "unknown"
    gate({"pair_validation": decision}, review={"confirmed": True, "image_content_hashes": HASHES})
    raw["back"]["product_name"] = "Prizm"
    assert derive(raw)["status"] == "conflict"


def test_manufacturer_prefix_variation_is_not_a_product_conflict():
    raw = pair()
    raw["front"]["product_name"] = "Upper Deck Generations"
    raw["back"]["product_name"] = "Generations"
    assert derive(raw)["status"] != "conflict"


def test_ambiguous_initial_and_shared_insert_without_card_number_stays_unknown():
    raw = pair()
    raw["front"].update(player_names=["A. Thompson"], product_name="Hoops", insert_name="Rookie Special", card_number=None)
    raw["back"].update(player_names=["Ausar Thompson"], product_name="Hoops", insert_name="Rookie Special", card_number="6")
    assert derive(raw)["status"] == "unknown"


@pytest.mark.parametrize("brands", [("UpperDeck", "Upper Deck"), ("Collector's Edge", "CollectorsEdge")])
def test_known_manufacturer_spacing_is_not_a_conflict(brands):
    raw = pair()
    raw["front"]["brand_name"], raw["back"]["brand_name"] = brands
    assert derive(raw)["status"] != "conflict"


def test_uncertain_product_title_variation_stays_unknown_without_blocking_intake():
    raw = pair()
    raw["front"]["product_name"] = "Unclear foil title"
    raw["back"]["product_name"] = "Unclear title"
    decision = derive(raw)
    assert decision["status"] == "unknown"
    gate({"pair_validation": decision})


def test_verified_century_legends_generations_missing_number_remains_pending():
    # Actual vision observations for the source-verified Vince/Oscar Generations card (#340).
    raw = pair(status="conflict", reason_codes=["different_players"])
    raw["front"].update(player_names=["Vince Carter"], brand_name="Upper Deck",
                        product_name="Century Legends", insert_name="Generations", card_number=None)
    raw["back"].update(player_names=["Oscar Robertson"], brand_name="UD",
                       product_name="Century Legends", insert_name="Generations", card_number=None)
    decision = derive(raw)
    assert decision["status"] == "unknown"
    gate({"pair_validation": decision})
    gate({"pair_validation": decision}, review={"confirmed": True, "image_content_hashes": HASHES})
    raw["back"]["card_number"] = "27"
    assert derive(raw)["status"] == "conflict"


def generations_without_printed_number():
    raw = pair(status='conflict', reason_codes=['different_players', 'no_shared_card_number'])
    raw['front'].update(player_names=['Vince Carter'], product_name='Century Legends', insert_name='Generations', card_number=None, brand_name='Upper Deck')
    raw['back'].update(player_names=['Oscar Robertson'], product_name='Century Legends', insert_name='Generations', card_number=None, brand_name='Upper Deck')
    return raw


def test_real_generations_observations_without_visible_number_remain_unknown_at_intake():
    raw = generations_without_printed_number()
    decision = derive(raw, caveats=['两面分别为不同球员，不能配对'])
    assert decision['status'] == 'unknown'
    gate({'pair_validation': decision})
    gate({'pair_validation': decision}, review={'confirmed': True, 'image_content_hashes': HASHES})


@pytest.mark.parametrize('field,value', [('card_number','G12'), ('product_name','Prizm'), ('brand_name','Panini')])
def test_generations_design_does_not_override_independent_contradictions(field, value):
    raw = generations_without_printed_number()
    raw['back'][field] = value
    assert derive(raw)['status'] == 'conflict'


@pytest.mark.parametrize("front_number,back_number", [
    ("No.4", "4"), ("No. 004", "004"), ("Nº4", "#4"),
    ("#G11", "G11"), ("No. A04", "A04"),
])
def test_printed_number_labels_do_not_create_a_pair_conflict(front_number, back_number):
    raw = pair()
    raw["front"]["card_number"] = front_number
    raw["back"]["card_number"] = back_number
    decision = derive(raw)
    assert decision["status"] == "consistent"
    gate({"pair_validation": decision})


@pytest.mark.parametrize("front_number,back_number", [
    ("No.04", "4"), ("#A4", "4"), ("NºG11", "G12"), ("NO4", "4"),
])
def test_printed_number_normalization_preserves_catalog_meaning(front_number, back_number):
    raw = pair()
    raw["front"]["card_number"] = front_number
    raw["back"]["card_number"] = back_number
    decision = derive(raw)
    assert decision["status"] == "conflict"
    assert "card_number_conflict" in decision["reason_codes"]


def test_subject_diacritics_do_not_create_a_pair_conflict():
    raw = pair()
    raw["front"]["player_names"] = ["Nikola Jokic"]
    raw["back"]["player_names"] = ["Nikola Jokić"]
    assert derive(raw)["status"] == "consistent"


@pytest.mark.parametrize("front_name,back_name", [
    ("Gary Payton Jr.", "Gary Payton Sr."),
    ("Gary Payton II", "Gary Payton III"),
    ("John Robert Smith", "John Edward Smith"),
    ("James Brown", "John Brown"),
])
def test_distinct_full_names_or_explicit_suffixes_cannot_certify_a_pair(front_name, back_name):
    raw = pair()
    raw["front"]["player_names"] = [front_name]
    raw["back"]["player_names"] = [back_name]
    decision = derive(raw)
    assert decision["status"] == "conflict"
    assert "subject_conflict" in decision["reason_codes"]
    with pytest.raises(HTTPException):
        gate({"pair_validation": decision}, review={"confirmed": True, "image_content_hashes": HASHES})


@pytest.mark.parametrize("front_name,back_name", [
    ("LeBron James", "LeBron Raymone James"),
    ("Gary Payton", "Gary Payton Jr."),
    ("A. Thompson", "Ausar Thompson"),
])
def test_incomplete_full_name_stays_unknown_without_a_second_identity_anchor(front_name, back_name):
    raw = pair()
    raw["front"]["player_names"] = [front_name]
    raw["back"]["player_names"] = [back_name]
    assert derive(raw)["status"] == "unknown"


def test_back_only_product_can_pair_with_independent_subject_and_printed_number():
    raw = pair()
    raw["front"].update(product_name=None, player_names=["Nikola Jokic"], card_number="No.4")
    raw["back"].update(player_names=["Nikola Jokić"], card_number="4")
    decision = derive(raw)
    assert decision["status"] == "consistent"
    assert decision["front"]["product_name"] is None
    assert decision["front"]["card_number"] == "No.4"
    gate({"pair_validation": decision})


@pytest.mark.parametrize("missing", ["number", "subject", "evidence"])
def test_back_only_product_without_independent_anchors_remains_unknown_at_intake(missing):
    raw = pair()
    raw["front"].update(product_name=None, card_number="4")
    raw["back"]["card_number"] = "4"
    if missing == "number":
        raw["front"]["card_number"] = None
    elif missing == "subject":
        raw["front"]["player_names"] = []
    else:
        raw["front"]["evidence"] = []
    decision = derive(raw, confidence=.999)
    assert decision["status"] == "unknown"
    gate({"pair_validation": decision})
    assert decision["front"]["product_name"] is None
    gate({"pair_validation": decision}, review={"confirmed": True, "image_content_hashes": HASHES})


def skybox_manufacturer_layers():
    # Preserved observations for the photographed 2008-09 SkyBox Close Ups #179.
    return {
        "player_name": "Dirk Nowitzki", "brand_name": "Upper Deck",
        "product_name": "SkyBox", "card_number": "179", "insert_name": "Close Ups",
        "field_evidence": {"brand_name": ['正面右下：“SkyBox™”；背面左下：“UPPER DECK™”']},
        "pair_validation": {
            "status": "conflict", "reason_codes": ["brand_conflict", "shared_player_and_insert",
                                                      "matching_design", "draft_identity_conflict"],
            "front": {"role": "front", "brand_name": "SkyBox", "product_name": "2008-09 NBA SkyBox",
                      "player_names": ["Dirk Nowitzki"], "insert_name": "Close Ups", "card_number": None,
                      "evidence": ["Dirk NOWITZKI", "CLOSE UPS", "SkyBox"]},
            "back": {"role": "back", "brand_name": "Upper Deck", "product_name": "2008-09 NBA SkyBox",
                     "player_names": ["Dirk Nowitzki"], "insert_name": "Close Ups", "card_number": "179",
                     "evidence": ["179", "CLOSE UPS", "Dirk NOWITZKI", "2008-09 NBA SKYBOX"]},
        },
    }


def test_printed_manufacturer_and_product_brand_layers_do_not_block_the_real_skybox_pair():
    from card_platform.pair_integrity import derive_pair_validation
    raw = skybox_manufacturer_layers()
    decision = derive_pair_validation(raw, HASHES)
    assert decision["status"] != "conflict"
    assert not {"brand_conflict", "draft_identity_conflict"} & set(decision["reason_codes"])
    assert decision["front"]["brand_name"] == "SkyBox"
    assert decision["back"]["brand_name"] == "Upper Deck"
    gate({**raw, "pair_validation": decision})
    assert raw["pair_validation"]["status"] == "conflict"


@pytest.mark.parametrize("field,value", [
    ("player_name", "Stephen Curry"), ("card_number", "180"),
    ("product_name", "Prizm"), ("insert_name", "Premium"),
])
@pytest.mark.parametrize("model_status", ["conflict", "consistent"])
def test_brand_layers_never_hide_an_explicit_joint_identity_conflict(field, value, model_status):
    from card_platform.pair_integrity import derive_pair_validation
    raw = skybox_manufacturer_layers()
    raw[field] = value
    raw["pair_validation"]["status"] = model_status
    decision = derive_pair_validation(raw, HASHES)
    assert decision["status"] == "conflict"
    assert "draft_identity_conflict" in decision["reason_codes"]
    with pytest.raises(HTTPException):
        gate({**raw, "pair_validation": decision})


def test_skybox_upper_deck_are_not_global_brand_aliases_without_printed_context():
    from card_platform.pair_integrity import derive_pair_validation
    raw = skybox_manufacturer_layers()
    raw["field_evidence"] = {}
    assert derive_pair_validation(raw, HASHES)["status"] == "conflict"


def test_genuine_panini_topps_conflict_is_not_a_manufacturer_layer_exception():
    from card_platform.pair_integrity import derive_pair_validation
    raw = skybox_manufacturer_layers()
    raw["pair_validation"]["front"]["brand_name"] = "Panini"
    raw["pair_validation"]["back"]["brand_name"] = "Topps"
    raw["field_evidence"]["brand_name"] = ["正面 PANINI；背面 TOPPS"]
    decision = derive_pair_validation(raw, HASHES)
    assert decision["status"] == "conflict" and "brand_conflict" in decision["reason_codes"]


@pytest.mark.parametrize("defect", ["both_front", "identical_images"])
def test_manufacturer_layers_do_not_override_physical_pair_conflicts(defect):
    from card_platform.pair_integrity import derive_pair_validation
    raw = skybox_manufacturer_layers()
    hashes = dict(HASHES)
    if defect == "both_front":
        raw["pair_validation"]["back"]["role"] = "front"
    else:
        hashes["back"] = hashes["front"]
    decision = derive_pair_validation(raw, hashes)
    assert decision["status"] == "conflict"
    assert ("duplicate_front" if defect == "both_front" else "identical_images") in decision["reason_codes"]


def deron_missing_front_product():
    raw = skybox_manufacturer_layers()
    raw.update(player_name="Deron Williams", card_number="199")
    for side in ("front", "back"):
        raw["pair_validation"][side]["player_names"] = ["Deron Williams"]
    raw["pair_validation"]["front"].update(
        product_name=None, evidence=["正面下方印有 Deron WILLIAMS", "正面下方印有 CLOSE UPS",
                                     "正面右下角有“SkyBox”标志"],
    )
    raw["pair_validation"]["back"].update(
        product_name="SkyBox", card_number="199",
        evidence=["背面左上角印有199", "背面左侧印有CLOSE UPS和Deron WILLIAMS",
                  "背面底部产品行原文2008-09 NBA SKYBOX"],
    )
    raw["pair_validation"]["reason_codes"] = ["brand_conflict"]
    return raw


def test_real_deron_skybox_logo_allows_brand_layers_without_inventing_front_product():
    from card_platform.pair_integrity import derive_pair_validation
    raw = deron_missing_front_product()
    decision = derive_pair_validation(raw, HASHES)
    assert decision["status"] == "unknown"
    assert "brand_conflict" not in decision["reason_codes"]
    assert decision["front"]["product_name"] is None
    assert raw["pair_validation"]["front"]["product_name"] is None
    gate({**raw, "pair_validation": decision})


def test_missing_front_product_needs_its_own_printed_logo_for_brand_layer_compatibility():
    from card_platform.pair_integrity import derive_pair_validation
    raw = deron_missing_front_product()
    raw["pair_validation"]["front"]["evidence"] = [
        "正面下方印有 Deron WILLIAMS", "正面下方印有 CLOSE UPS",
    ]
    decision = derive_pair_validation(raw, HASHES)
    assert decision["status"] == "conflict"
    assert "brand_conflict" in decision["reason_codes"]
