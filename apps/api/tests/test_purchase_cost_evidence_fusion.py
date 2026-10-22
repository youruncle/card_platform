"""Discovery may combine sources; only current paired evidence authorizes identity."""

import copy
import pytest
from card_platform.knowledge import KnowledgeResolver
from card_platform.purchase_cost_catalog import score_from_product
from card_platform.purchase_cost_images import (
    multi_review_eligible,
    evaluate_observation,
    apply_multi_observation,
)
from test_purchase_cost_images import H, evidence, field
from test_purchase_cost_semantics import bound


def product(*pairs, single=True, role="back", origin="printed"):
    return {
        "observations": {
            "single_card": single,
            "photos": [{"index": 0, "role": role}],
            "fields": [
                {"kind": k, "value": v, "text": v, "photo": 0, "origin": origin} for k, v in pairs
            ],
        }
    }


def test_recalled_title_still_uses_product_fields_for_review_not_authority():
    card = dict(
        player_name="Tracy McGrady",
        brand_name="Topps",
        product_name="Stadium Club",
        insert_name="Imminent Impact",
        card_number="217",
        season_start=1997,
    )
    line = dict(title="Tracy McGrady 1997 Topps NBA Draft Base")
    result = score_from_product(
        card,
        line,
        KnowledgeResolver(),
        product(("player", "Tracy McGrady"), ("product", "Stadium Club"), ("card_number", "217")),
    )
    assert multi_review_eligible(card, result)
    assert result["automatic"] is False and result["missing"] == ["产品", "Insert"]
    assert result["review_evidence"]["printed_card_number"] == "217"
    assert result["review_evidence"]["source"] == "independent_product_images"


def test_title_update_conflict_enters_review_without_clearing_conflict():
    card = dict(
        player_name="Cedric Coward",
        brand_name="Topps",
        product_name="Topps Chrome",
        insert_name="New Edition",
        card_number="GR-19",
        parallel_name="Silver",
        season_start=2025,
        season_end=2026,
    )
    result = score_from_product(
        card,
        dict(title="Cedric Coward 2025-26 Topps Chrome Update Edition New 银折"),
        KnowledgeResolver(),
        product(("player", "Cedric Coward"), ("card_number", "GR-19"), ("insert", "New Edition")),
    )
    assert multi_review_eligible(card, result)
    assert "产品或子系列冲突" in result["conflicts"]
    assert not result["automatic"]


@pytest.mark.parametrize(
    "change", ["advertisement", "sticker", "multi_card", "different_number", "fraction"]
)
def test_untrusted_or_numeric_conflicting_product_cannot_open_review(change):
    card = dict(
        player_name="Tracy McGrady",
        product_name="Stadium Club",
        insert_name="Imminent Impact",
        card_number="217",
    )
    p = product(("player", "Tracy McGrady"), ("card_number", "217"))
    if change == "advertisement":
        p["observations"]["photos"][0]["role"] = "advertisement"
    if change == "sticker":
        for f in p["observations"]["fields"]:
            f["origin"] = "sticker"
    if change == "multi_card":
        p["observations"]["single_card"] = False
    if change == "different_number":
        p["observations"]["fields"][1]["value"] = "218"
    if change == "fraction":
        p["observations"]["fields"][1]["value"] = "217/299"
    result = score_from_product(card, dict(title="Tracy McGrady"), KnowledgeResolver(), p)
    assert not multi_review_eligible(card, result)
    assert not result.get("review_evidence")


def test_scene_with_subject_text_missing_uses_multi_photo_review():
    card = dict(
        player_name="Homelander / Madelyn Stillwell",
        product_name="The Boys",
        brand_name="Upper Deck",
        insert_name="A View From Home",
        card_number="VHF-21",
    )
    result = score_from_product(
        card,
        dict(title="2025 Upper Deck Skybox The Boys A View From Home 祖国人 变态裁缝"),
        KnowledgeResolver(),
        product(("card_number", "VFH-21")),
    )
    assert multi_review_eligible(card, result)
    assert result["automatic"] is False


def test_bound_paired_letters_transposition_keeps_inventory_and_suggestion():
    card, binding, proof = bound(
        dict(card_number="VHF-21", image_content_hashes={"front": H, "back": H})
    )
    f = [field("card_number", "VFH-21"), field("product", "The Boys")]
    obs = evidence(f, copy.deepcopy(f))
    result = evaluate_observation(
        obs, card, [H, H], binding=binding, independent_confirmation=proof
    )
    assert result["verdict"] == "same", result
    assert result["identity_suggestions"] == {
        "card_number": {"stored": "VHF-21", "observed": "VFH-21"}
    }
    assert card["card_number"] == "VHF-21" and result["cost_authorized"] is False


def test_actual_printed_conflict_is_not_hidden_by_bad_stored_number():
    obs = evidence([field("card_number", "VFH-21")], [field("card_number", "VFH-22")])
    result = evaluate_observation(obs, {"card_number": "VHF-21"}, [H, H])
    assert result["verdict"] == "different"
    assert result["reasons"] == ["printed_card_number_conflict"]


@pytest.mark.parametrize("anchor,left_value,right_value,reason", [
    ("card_number", "VFH-21", "TC-LM", "printed_card_number_conflict"),
    ("serial", "26/75", "27/75", "serial_conflict"),
])
def test_actual_card_number_conflict_precedes_other_printed_field_differences(
    anchor, left_value, right_value, reason
):
    obs = evidence(
        [field(anchor, left_value), field("player", "Homelander"), field("brand", "Skybox")],
        [field(anchor, right_value), field("player", "Liam McNeeley"), field("brand", "Topps")],
    )
    result = evaluate_observation(obs, {"card_number": "VHF-21"}, [H, H])
    assert result["verdict"] == "different", result
    assert result["reasons"] == [reason]


def test_cached_uncertain_pair_can_only_gain_a_bound_negative_classification():
    card, binding, proof = bound(dict(card_number="VHF-21", image_content_hashes={"front": H, "back": H}))
    obs = evidence(
        [field("card_number", "VFH-21"), field("player", "Homelander")],
        [field("card_number", "TC-LM"), field("player", "Liam McNeeley")],
    )
    decision = dict(source="purchase_multi_image", verdict="uncertain",
                    reasons=["printed_identity_fields_conflict:player"],
                    observations=obs.model_dump(mode="json"), evidence_binding=binding)
    match = dict(automatic=False, recalled=True, conflicts=[], missing=[], reasons=[],
                 item_id="123", evidence_hash="order")
    result = apply_multi_observation(card, match, decision)
    assert not result["automatic"]
    assert result["visual_inference"]["verdict"] == "different"
    assert result["visual_inference"]["rule_reclassified_from"] == "uncertain"
    assert result["visual_inference"]["observations"] == decision["observations"]
    assert decision["verdict"] == "uncertain"
    stale = copy.deepcopy(decision)
    stale["evidence_binding"]["order_hash"] = "stale"
    assert "visual_inference" not in apply_multi_observation(card, match, stale)


@pytest.mark.parametrize("reasons", [
    ["repair_dropped_identity_observation"],
    ["printed_identity_fields_conflict:player", "repair_dropped_identity_observation"],
    ["card_number_reading_inconsistent"],
    ["field_semantics_unconfirmed"],
    ["independent_finish_unconfirmed"],
    [],
])
@pytest.mark.parametrize("review_only", [False, True])
def test_uncertain_integrity_veto_cannot_become_exclusion(reasons, review_only):
    card, binding, _ = bound(dict(card_number="77", image_content_hashes={"front": H, "back": H}))
    obs = evidence([field("card_number", "77")], [field("card_number", "78")])
    decision = dict(source="purchase_multi_image", verdict="uncertain", reasons=reasons,
                    observations=obs.model_dump(mode="json"), evidence_binding=binding)
    match = dict(automatic=False, recalled=True, conflicts=[], missing=[], reasons=[],
                 item_id="123", evidence_hash="order", field_review_only=review_only)
    result = apply_multi_observation(card, match, decision)
    assert result["conflicts"] == []
    assert not result["automatic"]
    assert result["visual_inference"] == decision


@pytest.mark.parametrize(
    "stored,observed", [("217", "218"), ("VFH-21", "VFH-22"), ("VFH-21", "ABC-21")]
)
def test_stored_numeric_or_substantial_difference_remains_unknown(stored, observed):
    f = [field("card_number", observed), field("product", "The Boys")]
    assert (
        evaluate_observation(evidence(f, copy.deepcopy(f)), {"card_number": stored}, [H, H])[
            "verdict"
        ]
        != "same"
    )


def test_discovery_does_not_override_actual_pair_rejection():
    card, binding, proof = bound(
        dict(card_number="77", image_content_hashes={"front": H, "back": H})
    )
    obs = evidence([field("card_number", "77")], [field("card_number", "78")])
    checked = evaluate_observation(
        obs, card, [H, H], binding=binding, independent_confirmation=proof
    )
    decision = {
        **checked,
        "source": "purchase_multi_image",
        "evidence_binding": binding,
        "independent_confirmation": proof,
    }
    match = dict(
        automatic=False,
        recalled=True,
        conflicts=[],
        missing=["产品"],
        reasons=[],
        item_id="123",
        evidence_hash="order",
        review_evidence={"source": "independent_product_images", "printed_card_number": "77"},
    )
    result = apply_multi_observation(card, match, decision)
    assert not result["automatic"] and "printed_card_number_conflict" in result["conflicts"]


def test_complete_text_identity_is_not_downgraded_by_redundant_ocr():
    card = dict(
        player_name="Example Player",
        brand_name="Topps",
        product_name="Chrome",
        season_start=2025,
        card_number="77",
    )
    line = dict(title="2025 Topps Chrome Example Player #77")
    base = score_from_product(card, line, KnowledgeResolver())
    assert base["automatic"]
    assert (
        score_from_product(
            card,
            line,
            KnowledgeResolver(),
            product(("player", "Example Player"), ("card_number", "77")),
        )
        == base
    )


@pytest.mark.parametrize(
    "failure", [None, "removed", "missing_image", "quantity", "no_history", "inactive", "no_unit"]
)
def test_legacy_rebuild_requires_all_historical_products_and_current_units(failure):
    from card_platform.purchase_cost_service import legacy_rebuild_refs

    lines = [
        dict(item_id="1", quantity=1, image_urls=["a"]),
        dict(item_id="2", quantity=1, image_urls=["b"]),
    ]
    captures = [dict(external_order_id="order", lines=copy.deepcopy(lines))]
    orders = copy.deepcopy(captures)
    units = [
        dict(external_order_id="order", item_id=str(n), active=True, bundle_index=0) for n in (1, 2)
    ]
    if failure == "removed":
        orders[0]["lines"].pop()
    if failure == "missing_image":
        orders[0]["lines"][1]["image_urls"] = []
    if failure == "quantity":
        orders[0]["lines"][1]["quantity"] = 2
    if failure == "no_history":
        captures = []
    if failure == "inactive":
        units[1]["active"] = False
    if failure == "no_unit":
        units.pop()
    result = legacy_rebuild_refs({"order"}, orders, captures, units)
    assert result == ({("order", "1"), ("order", "2")} if failure is None else set())


def test_printed_change_back_caption_is_not_a_back_view():
    from card_platform.domain.independent_finish import IndependentFinishObservation

    _, _, proof = bound()
    obs = proof["observation"]
    for side in ("target", "candidate"):
        obs[side]["layout"] = (
            'Horizontal front artwork with two people, printed caption "CHANGE BACK!" at lower right.'
        )
    assert IndependentFinishObservation.model_validate(obs).establishes_same()


@pytest.mark.parametrize(
    "description",
    [
        "back view of card",
        "card back with matching design",
        "on the back of the card",
        "backside",
        "卡背图案",
    ],
)
def test_real_back_descriptions_remain_invalid_independent_front_proof(description):
    from card_platform.domain.independent_finish import IndependentFinishObservation

    _, _, proof = bound()
    obs = proof["observation"]
    obs["target"]["layout"] = description
    assert not IndependentFinishObservation.model_validate(obs).establishes_same()
