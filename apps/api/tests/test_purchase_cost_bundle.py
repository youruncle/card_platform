from decimal import Decimal

import pytest

from card_platform.purchase_cost_search import build_purchase_cost_search_plan


def test_dc_character_search_reaches_chinese_order_titles():
    plan = build_purchase_cost_search_plan({"player_name": "Harley Quinn"})
    assert "哈莉奎茵" in plan["keywords"]
    assert "小丑女" in plan["keywords"]


def test_bundle_split_preserves_price_and_shipping_separately():
    from card_platform.purchase_cost_bundle import split_bundle

    assert split_bundle(Decimal("55"), Decimal("1.23"), 2) == [
        {"bundle_index": 1, "unit_price": Decimal("27.50"), "shipping": Decimal(".62")},
        {"bundle_index": 2, "unit_price": Decimal("27.50"), "shipping": Decimal(".61")},
    ]
    assert [r["unit_price"] for r in split_bundle(Decimal("10"), Decimal("0"), 3)] == [
        Decimal("3.34"),
        Decimal("3.33"),
        Decimal("3.33"),
    ]


@pytest.mark.parametrize("count", [0, 1, 21, True])
def test_unknown_or_unbounded_count_cannot_split(count):
    from card_platform.purchase_cost_bundle import split_bundle

    with pytest.raises(ValueError):
        split_bundle(Decimal("55"), Decimal("0"), count)


def test_only_observed_unique_matching_component_can_be_recorded():
    from card_platform.purchase_cost_bundle import bundle_match

    decision = dict(
        contract="purchase-bundle-v1",
        source="human_paired_image_review",
        card_count=2,
        matched_index=1,
        complete=True,
        unique_match=True,
        verdict="same",
        confidence=".95",
        design_match="same",
        finish_match="same",
        reasons=["左侧卡设计及绿色字样一致"],
    )
    assert bundle_match(decision, 1)["automatic"]
    assert not bundle_match(decision, 2)["recalled"]
    for changes in [
        dict(complete=False),
        dict(unique_match=False),
        dict(finish_match="unknown"),
        dict(matched_index=3),
        dict(confidence=".7"),
        dict(card_count=20, complete=False),
    ]:
        assert not bundle_match({**decision, **changes}, 1)["automatic"]


def test_model_claim_alone_cannot_assign_nonexistent_bundle_card():
    from card_platform.purchase_cost_bundle import bundle_match

    claim = dict(
        contract="purchase-bundle-v1",
        source="purchase_bundle_vision",
        card_count=6,
        matched_index=2,
        complete=True,
        unique_match=True,
        verdict="same",
        confidence="1",
        design_match="same",
        finish_match="same",
        reasons=["model says same"],
    )
    assert not bundle_match(claim, 2)["automatic"]
    assert not bundle_match({**claim, "geometry": {"verified_slot": False}}, 2)["automatic"]
    assert not bundle_match({**claim, "geometry": {"verified_slot": True}}, 2)["automatic"]
    assert bundle_match(
        {
            **claim,
            "card_boxes": [[i / 6, 0, (i + .8) / 6, 1] for i in range(6)],
            "slot_comparison": {"verdict": "same", "design_match": "same", "finish_match": "same", "confidence": ".95"},
            "geometry": {"verified_slot": True},
        },
        2,
    )["automatic"]


def test_bundle_geometry_rejects_wrong_slot_and_unrelated_picture():
    import cv2
    import numpy as np
    from card_platform.purchase_bundle_verifier import verify_bundle_slot

    rng = np.random.default_rng(73)
    target = rng.integers(0, 256, (400, 260, 3), dtype=np.uint8)
    board = np.full((500, 700, 3), 240, dtype=np.uint8)
    board[50:450, 30:290] = target
    board[50:450, 400:660] = rng.integers(0, 256, target.shape, dtype=np.uint8)
    encoded = cv2.imencode(".png", board)[1].tobytes()
    boxes = [[30 / 700, 0.1, 290 / 700, 0.9], [400 / 700, 0.1, 660 / 700, 0.9]]
    assert verify_bundle_slot(target, encoded, boxes, 1)["verified_slot"]
    assert not verify_bundle_slot(target, encoded, boxes, 2)["verified_slot"]
    unrelated = rng.integers(0, 256, target.shape, dtype=np.uint8)
    assert not verify_bundle_slot(unrelated, encoded, boxes, 1)["verified_slot"]


def test_purchase_search_reuses_shared_bilingual_and_diacritic_names():
    assert '东契奇' in build_purchase_cost_search_plan({'player_name': 'LUKA DONČIĆ'})['keywords']
    assert '拉文' in build_purchase_cost_search_plan({'player_name': 'Zach LaVine'})['keywords']
    assert 'Egor Demin' in build_purchase_cost_search_plan({'player_name': 'Egor Dëmin'})['keywords']


def test_dual_player_card_recalls_one_name_but_requires_both_for_identity():
    from card_platform.purchase_cost import score_match
    card = dict(player_name='Magic Johnson, Kareem Abdul-Jabbar', brand_name='Panini', product_name='Prizm', season_start=2024)
    line = dict(title='2024 Panini Prizm Magic Johnson', item_url='', image_urls=[])
    result = score_match(card, line)
    assert result['recalled']
    assert '球员' in result['missing']
    assert not result['automatic']


def test_bundle_layout_rejects_duplicate_overlap_and_reversed_slots():
    from card_platform.purchase_cost_bundle import BundleObservation
    base = dict(card_count=2, matched_index=1, complete=True, unique_match=True,
                verdict='same', confidence='.95', design_match='same', finish_match='same', reasons=['test'])
    for boxes in ([[0, 0, .4, 1]] * 2,
                  [[0, 0, .6, 1], [.4, 0, 1, 1]],
                  [[.6, 0, 1, 1], [0, 0, .4, 1]]):
        with pytest.raises(ValueError):
            BundleObservation.model_validate({**base, 'card_boxes': boxes})


def test_separate_observations_cannot_remap_existing_slots():
    from card_platform.purchase_cost_bundle import same_bundle_layout
    boxes = [[0, 0, .4, 1], [.6, 0, 1, 1]]
    stored = dict(card_boxes=boxes, candidate_image_hash='original')
    decision = dict(card_boxes=boxes, image_hashes={'candidate': 'original'})
    assert same_bundle_layout(stored, decision)
    assert not same_bundle_layout(stored, {**decision, 'card_boxes': list(reversed(boxes))})
    assert not same_bundle_layout(stored, {**decision, 'image_hashes': {'candidate': 'other'}})
    assert not same_bundle_layout(stored, {})


def test_purchase_names_tolerate_seller_apostrophe_omission():
    from card_platform.purchase_cost import score_match
    card = dict(player_name="De'Andre Hunter", brand_name='Panini', product_name='Impeccable', season_start=2019)
    result = score_match(card, dict(title='2019 Panini Impeccable DeAndre Hunter', image_urls=[], item_url=''))
    assert result['recalled']
    assert '球员' not in result['missing']
