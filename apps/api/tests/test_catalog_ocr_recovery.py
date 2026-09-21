from decimal import Decimal

import pytest

from card_platform.image_service import apply_local_ocr_anchors
from card_platform.local_ocr import extract_anchors
from card_platform.schemas import CardRecognitionResult
from card_platform.vision_routing import finalize_recognition_status


@pytest.mark.parametrize("number", ["157", "231"])
def test_footer_manufacturing_code_does_not_hide_bare_checklist_number(number):
    anchors = extract_anchors([
        "FLAGSHIP", "X D @TOPPS", number, "EXAMPLE PLAYER",
        "WWW.TOPPS.COM. CODE#CMP126264",
    ])
    assert anchors["card_number"] == number
    assert anchors["social_mark_evidence"] == "X D @TOPPS"
    assert "serial_number" not in anchors


def recognition(**updates):
    return CardRecognitionResult.model_validate({
        "player_name": "Example Player", "brand_name": "Topps",
        "product_name": "Flagship", "season_start": 2025, "season_end": 2026,
        "card_number": "CMP126264", "parallel_name": "X", "confidence": Decimal(".95"),
        "serial_number": 157, "serial_total": 157, "is_numbered": True,
        "field_evidence": {"serial_number": ["背面 157/157（x/y 格式）"]},
        **updates,
    })


def test_independent_ocr_overrules_model_invented_fraction_and_social_parallel():
    anchors = extract_anchors(["FLAGSHIP", "X D @TOPPS", "157", "CODE#CMP126264"])
    result = finalize_recognition_status(apply_local_ocr_anchors(recognition(), anchors, 1))
    assert result.card_number == "157"
    assert result.parallel_name is None
    assert result.serial_number is result.serial_total is result.is_numbered is None
    assert "catalog_number_serial_collision" in result.recognition_quality_issues
    assert result.recognition_status == "research_required"
    assert "157/157" not in " ".join(result.field_evidence["serial_number"])


@pytest.mark.parametrize("source", ["local_ocr", "local_serial_ocr"])
def test_real_end_of_run_fraction_is_preserved(source):
    anchors = extract_anchors(["FLAGSHIP", "157"])
    result = apply_local_ocr_anchors(recognition(parallel_name="X-Fractor"), anchors, 1)
    if source == "local_ocr":
        result = apply_local_ocr_anchors(result, {**anchors, "serial_number": 157, "serial_total": 157}, 1)
    else:
        result = result.model_copy(update={"serial_number": 157, "serial_total": 157, "recognition_route": [
            {"tier": "local_serial_ocr", "outcome": "confirmed"}, *result.recognition_route,
        ]})
    result = finalize_recognition_status(result)
    assert result.serial_number == result.serial_total == 157
    assert result.parallel_name == "X-Fractor"


def test_missing_ocr_does_not_clear_existing_serial():
    result = finalize_recognition_status(recognition())
    assert result.serial_total == 157


def test_unrelated_serial_not_cleared_by_checklist_ocr():
    result = finalize_recognition_status(apply_local_ocr_anchors(
        recognition(serial_number=12, serial_total=25), {"card_number": "157"}, 1,
    ))
    assert (result.serial_number, result.serial_total) == (12, 25)
