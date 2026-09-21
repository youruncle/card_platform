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


def test_extract_anchors_does_not_treat_november_or_stats_as_identity() -> None:
    anchors = extract_anchors([
        "2024/25. He led Toronto in assists per game.",
        "On November 29, Barnes had a triple-double.",
        "31",
        "FLAGSHIP",
        "© 2025 THE TOPPS COMPANY",
    ])
    assert "season_start" not in anchors
    assert anchors["card_number"] == "31"


def test_extract_anchors_prefers_unique_stamp_over_birthdate_prefix() -> None:
    anchors = extract_anchors(
        [
            "BORN: 3/6/73",
            "20/20",
        ]
    )

    assert anchors["serial_number"] == 20
    assert anchors["serial_total"] == 20


def test_extract_anchors_rejects_multiple_physical_serial_candidates() -> None:
    anchors = extract_anchors(["20/20", "03/10"])

    assert "serial_number" not in anchors
    assert "serial_total" not in anchors


def test_extract_anchors_reads_fragmented_vertical_unicode_fraction() -> None:
    anchors = extract_anchors(["20", "／", "20"])

    assert anchors["serial_number"] == 20
    assert anchors["serial_total"] == 20


def test_explicit_card_number_wins_over_earlier_contextual_digit() -> None:
    anchors = extract_anchors(
        [
            "PANINI",
            "3",
            "PLAYER BIOGRAPHY",
            "No. 5",
        ]
    )

    assert anchors["card_number"] == "5"
    assert anchors["card_number_evidence"] == "No. 5"
    assert anchors["card_number_evidence_strength"] == "explicit_label"


def test_conflicting_explicit_card_numbers_remain_unknown() -> None:
    anchors = extract_anchors(["No. 5", "#12", "PANINI"])

    assert "card_number" not in anchors
    assert "card_number_evidence" not in anchors


def test_fraction_after_number_label_is_not_a_card_number() -> None:
    anchors = extract_anchors(["No. 20/20", "PRIZM"])

    assert "card_number" not in anchors
    assert anchors["serial_number"] == 20
    assert anchors["serial_total"] == 20
