import json

from card_platform.local_ocr import extract_anchors, parse_ocr_output


def test_parse_ocr_output_keeps_successful_non_empty_lines() -> None:
    payload = json.dumps(
        [
            {"path": "/tmp/front.jpg", "lines": [" NBA HOOPS ", "RC", ""], "error": None},
            {"path": "/tmp/back.jpg", "lines": ["ignored"], "error": "failed"},
        ]
    )
    assert parse_ocr_output(payload) == ["NBA HOOPS", "RC"]


def test_parse_ocr_output_accepts_missing_lines() -> None:
    assert parse_ocr_output(json.dumps([{"path": "/tmp/card.jpg"}])) == []


def test_extract_anchors_prefers_product_release_line() -> None:
    anchors = extract_anchors(
        [
            "DRAFTED: 2018",
            "24-25 THUNDER",
            "2023-24 PANINI - NATIONAL TREASURES BASKETBALL",
            "Nº VS-PGE",
            "14/25",
            "RC",
            "The autograph is guaranteed by Panini America, Inc.",
        ]
    )
    assert anchors["season_start"] == 2023
    assert anchors["season_end"] == 2024
    assert anchors["card_number"] == "VS-PGE"
    assert anchors["serial_number"] == 14
    assert anchors["serial_total"] == 25
    assert anchors["is_rookie"] is True
    assert anchors["is_autograph"] is True


def test_extract_anchors_reads_material_usage() -> None:
    anchors = extract_anchors(
        ["The enclosed game-worn material is guaranteed by Panini America, Inc."]
    )
    assert anchors["is_memorabilia"] is True
    assert anchors["memorabilia_usage"] == "game_used"
