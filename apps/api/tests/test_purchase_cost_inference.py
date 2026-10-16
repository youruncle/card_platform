import pytest

from card_platform.purchase_cost import score_match
from card_platform.purchase_cost_inference import apply_inference, inference_key


def pair():
    return (
        {
            "player_name": "Example Player",
            "brand_name": "Topps",
            "product_name": "NBA Hoops",
            "season_start": 2025,
            "season_end": 2026,
            "insert_name": "Oasis",
            "is_autograph": False,
            "is_memorabilia": False,
        },
        {
            "title": "2026 Topps NBA Hoops Example Player Oasis 折射 切割瑕疵图三",
            "image_urls": ["https://images.cardhobby.com/a.jpg"],
            "item_url": "https://cardhobby.com.cn/market/item/1",
            "item_id": "123",
        },
    )


def test_season_end_and_cutting_defect_are_not_false_hard_conflicts():
    card, line = pair()
    base = score_match(card, line)
    assert not base["conflicts"] and not base["automatic"]
    result = apply_inference(
        card, base, {"verdict": "same", "confidence": ".90", "reasons": ["same visual layout"]}
    )
    assert result["automatic"] and result["match_method"] == "inferred"
    assert result["inference_warnings"] and result["soft_differences"]


@pytest.mark.parametrize(
    "text",
    [
        "2024 Topps NBA Hoops Example Player Oasis",
        "2026 Panini NBA Hoops Example Player Oasis",
        "2026 Topps NBA Hoops Example Player Oasis 球衣 patch",
        "2026 Topps NBA Hoops Example Player Oasis 球员签字",
    ],
)
def test_real_conflicts_still_block_even_with_high_visual_similarity(text):
    card, line = pair()
    line["title"] = text
    result = apply_inference(
        card, score_match(card, line), {"verdict": "same", "confidence": ".99"}
    )
    assert not result["automatic"] and result["conflicts"]


@pytest.mark.parametrize(
    "verdict,confidence,geometry,allowed",
    [
        ("same", ".80", False, True),
        ("same", ".79", True, False),
        ("compatible", ".86", True, True),
        ("compatible", ".86", False, False),
        ("uncertain", ".99", True, False),
        ("different", ".99", True, False),
    ],
)
def test_partial_visual_match_is_explicit_and_bounded(verdict, confidence, geometry, allowed):
    card, line = pair()
    result = apply_inference(
        card,
        score_match(card, line),
        {
            "verdict": verdict,
            "confidence": confidence,
            "reasons": ["geometry_same_template:matches=100"] if geometry else [],
        },
    )
    assert result["automatic"] == allowed


def test_order_price_or_target_change_invalidates_cached_inference():
    card, line = pair()
    key = inference_key(card, line, "order-price-v1")
    assert key != inference_key(card, line, "order-price-v2")
    assert key != inference_key({**card, "image_front_url": "new-image"}, line, "order-price-v1")
    assert key != inference_key(card, {**line, "title": "different-title"}, "order-price-v1")


def test_bound_image_rejection_blocks_even_exact_title_match():
    card, line = pair()
    match = {**score_match(card, line), 'automatic': True, 'missing': [], 'conflicts': []}
    result = apply_inference(card, match, {'verdict': 'different', 'confidence': '.99',
                                         'reasons': ['银色全幅与白边小图版式不同']})
    assert not result['automatic']
    assert '图片核心信息不一致' in result['conflicts']
