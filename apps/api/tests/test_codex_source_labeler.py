from card_platform.codex_source_labeler import (
    build_source_assisted_truth,
    label_against_truth,
)


def _prediction(identity: dict) -> dict:
    return {"candidates": [{"rank": 1, "identity": identity}]}


def test_title_overrides_explicit_structured_facts_without_inventing_unknowns() -> None:
    prediction = _prediction(
        {
            "player_name": "Antonio Reeves",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Immaculate Collection",
            "card_number": "132",
            "serial_number": 9,
            "serial_total": 49,
            "parallel_name": "Prizm",
            "is_rookie": True,
            "is_autograph": True,
            "autograph_type": "sticker",
            "is_memorabilia": True,
        }
    )
    truth, evidence, confidence = build_source_assisted_truth(
        prediction,
        "2024-25 Panini Immaculate Antonio Reeves RC RPA 卡签 4/99编 球衣物料",
    )
    assert truth is not None
    assert truth["serial_number"] == 4
    assert truth["serial_total"] == 99
    assert truth["autograph_type"] == "on_card"
    assert truth["memorabilia_types"] == ["jersey"]
    assert truth["parallel_name"] is None
    assert truth["rookie_evidence"] == []
    assert confidence >= 0.9
    assert any("4/99" in item for item in evidence)


def test_rc_marker_is_not_treated_as_insert_name() -> None:
    truth, _, _ = build_source_assisted_truth(
        _prediction(
            {
                "player_name": "Amen Thompson",
                "season_start": 2023,
                "season_end": 2024,
                "product_name": "Prizm",
                "card_number": "150",
                "insert_name": "Rookie RC Card",
                "parallel_name": "Prizm",
            }
        ),
        "2023-24 Panini Prizm Amen Thompson RC 银折",
    )
    assert truth is not None
    assert truth["insert_name"] is None
    assert truth["parallel_name"] == "Silver"


def test_label_is_none_when_explicit_source_truth_corrects_top1() -> None:
    prediction = _prediction(
        {
            "player_name": "Metta World Peace",
            "season_start": 2009,
            "season_end": 2010,
            "product_name": "Immaculate Collection",
            "card_number": "II-MWP",
            "serial_total": 99,
        }
    )
    truth, _, _ = build_source_assisted_truth(
        prediction,
        "2022-23 Panini Immaculate Metta World Peace 卡签 89/99编",
    )
    assert label_against_truth(prediction, truth) == "none_correct"


def test_player_surname_is_not_mistaken_for_color_when_other_color_is_explicit() -> None:
    truth, _, _ = build_source_assisted_truth(
        _prediction(
            {
                "player_name": "Anthony Black",
                "season_start": 2025,
                "season_end": 2026,
                "product_name": "Topps Finest",
                "card_number": "12",
                "parallel_name": None,
            }
        ),
        "2025-26 Topps Finest Anthony Black 安东尼布莱克 银几何折",
    )
    assert truth is not None
    assert truth["parallel_name"] == "Silver"


def test_compound_chinese_parallel_and_prediction_share_color_taxonomy() -> None:
    title_truth, _, _ = build_source_assisted_truth(
        _prediction(
            {
                "player_name": "Ace Bailey",
                "product_name": "Topps Chrome",
                "parallel_name": "Gold Holo",
            }
        ),
        "2025-26 Topps Chrome Ace Bailey 橙碎冰折 /25编",
    )
    prediction_truth, _, _ = build_source_assisted_truth(
        _prediction(
            {
                "player_name": "Another Player",
                "product_name": "Prizm",
                "parallel_name": "Blue Prizm",
            }
        ),
        "2025-26 Panini Prizm Another Player /99编",
    )

    assert title_truth is not None
    assert title_truth["parallel_name"] == "Orange"
    assert prediction_truth is not None
    assert prediction_truth["parallel_name"] == "Blue"


def test_jersey_cutout_is_memorabilia_but_not_automatically_patch() -> None:
    truth, _, _ = build_source_assisted_truth(
        _prediction(
            {
                "player_name": "Will Barton",
                "season_start": 2020,
                "season_end": 2021,
                "product_name": "Noir",
                "card_number": "12",
                "is_patch": False,
            }
        ),
        "2020-21 Panini Noir Will Barton 18/99编 球衣切割 亲穿",
    )
    assert truth is not None
    assert truth["is_memorabilia"] is True
    assert truth["memorabilia_types"] == ["jersey"]
    assert truth["is_patch"] is False
