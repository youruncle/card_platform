import pytest

from card_platform.recognition_benchmark_api import (
    validate_label_correction,
    validate_rank_label,
)


def test_rank_label_requires_candidate_to_exist() -> None:
    prediction = {"candidates": [{"rank": 1}]}
    validate_rank_label(prediction, "top1_correct")
    validate_rank_label(prediction, "none_correct")
    with pytest.raises(ValueError, match="Top-2"):
        validate_rank_label(prediction, "top2_correct")
    with pytest.raises(ValueError, match="Top-3"):
        validate_rank_label(prediction, "top3_correct")


def test_rank_label_rejects_missing_prediction() -> None:
    with pytest.raises(ValueError, match="Top-1"):
        validate_rank_label(None, "top1_correct")


def test_none_correct_requires_structured_ground_truth() -> None:
    with pytest.raises(ValueError, match="必须补充完整"):
        validate_label_correction("none_correct", None)
    with pytest.raises(ValueError, match="不足以评分"):
        validate_label_correction("none_correct", {})
    with pytest.raises(ValueError, match="不足以评分"):
        validate_label_correction(
            "none_correct",
            {"player_name": "Jerry West", "is_autograph": True},
        )
    validate_label_correction("none_correct", {
        "player_name": "Jerry West",
        "product_name": "National Treasures",
        "season_start": 2015,
        "card_number": "S-JWS",
    })
    validate_label_correction("top1_correct", None)


def test_any_manual_correction_must_be_scorable() -> None:
    with pytest.raises(ValueError, match="不足以评分"):
        validate_label_correction("top1_correct", {"parallel_name": "Gold"})
    validate_label_correction("top1_correct", {
        "player_name": "Stephen Curry",
        "product_name": "Obsidian",
        "season_start": 2024,
        "card_number": "10",
    })
