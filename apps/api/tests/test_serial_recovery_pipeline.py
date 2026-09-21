import pytest

from card_platform import local_ocr, serial_ocr
from card_platform.card_search_service import merge_search_evidence, resolved_update_is_safe
from card_platform.config import Settings
from card_platform.image_service import CardVisionService, apply_local_ocr_anchors
from card_platform.recognition_benchmark_predictor import canonicalize_identity
from card_platform.schemas import CardRecognitionResult, CardSearchEnrichment


def observation(text, *, context=None, orientation=180, read_pass="full"):
    return {
        "image_index": 1,
        "text": text,
        "context": context or text,
        "orientation_degrees": orientation,
        "read_pass": read_pass,
        "confidence": 0.3,
        "bbox": [0.74, 0.36, 0.05, 0.08],
    }


def test_invalid_partial_read_recovers_only_from_actual_roi_digits():
    result = serial_ocr.apply_serial_ocr(
        CardRecognitionResult(confidence="0.9"),
        {
            "status": "succeeded",
            "observations": [
                observation("0 /20"),
                observation(
                    "20/20", context="0 /20\n局部 OCR：20/20", read_pass="padded_roi_rotation_0"
                ),
                observation("20 /20", read_pass="padded_roi_rotation_90"),
            ],
        },
        100,
    )
    assert (result.serial_number, result.serial_total, result.is_numbered) == (20, 20, True)
    assert result.recognition_route[-1]["outcome"] == "confirmed"
    stored = canonicalize_identity(result)
    assert (stored.serial_number, stored.serial_total) == (20, 20)


@pytest.mark.parametrize("text", ["0/20", "BORN 3/6/73", "FG 12/24", "联网候选：印编20/20"])
def test_no_stamp_guessed_from_invalid_partial_or_non_physical_evidence(text):
    result = serial_ocr.apply_serial_ocr(
        CardRecognitionResult(confidence="0.9"),
        {
            "status": "succeeded",
            "observations": [observation(text)],
        },
        100,
    )
    assert result.serial_number is None
    assert result.serial_total is None


def test_conflicting_ocr_remains_reviewable_instead_of_minimum_denominator():
    result = serial_ocr.apply_serial_ocr(
        CardRecognitionResult(confidence="0.9"),
        {
            "status": "succeeded",
            "observations": [observation("20/20"), observation("20/28")],
        },
        100,
    )
    assert result.serial_number is None
    assert result.serial_total is None
    assert result.recognition_route[-1]["outcome"] == "conflicting"
    assert len(result.field_evidence["serial_total"]) == 2


def test_local_serial_survives_search_and_scanner_canonicalization():
    initial = CardRecognitionResult(confidence="0.9")
    visual = apply_local_ocr_anchors(
        initial,
        {
            "is_numbered": True,
            "serial_number": 194,
            "serial_total": 249,
            "serial_evidence": "194/249",
        },
        20,
    )
    enrichment = CardSearchEnrichment(
        field_evidence={
            "is_numbered": ["目录里有194/249"],
            "serial_number": ["194/249"],
            "serial_total": ["此系列249编"],
        }
    )
    merged = visual.model_copy(
        update={"field_evidence": merge_search_evidence(visual, enrichment, {})}
    )
    stored = canonicalize_identity(merged)
    assert (stored.serial_number, stored.serial_total, stored.is_numbered) == (194, 249, True)
    assert "Apple Vision" in stored.field_evidence["is_numbered"][0]
    assert "联网候选" in stored.field_evidence["is_numbered"][1]
    assert not resolved_update_is_safe(visual, "serial_total", 99)
    assert resolved_update_is_safe(visual, "serial_total", 249)


def test_search_cannot_replace_explicit_physical_card_number_with_jersey_number():
    visual = apply_local_ocr_anchors(
        CardRecognitionResult(confidence="0.9"),
        {
            "card_number": "5",
            "card_number_evidence": "No. 5",
            "card_number_evidence_strength": "explicit_label",
        },
        20,
    )
    assert not resolved_update_is_safe(visual, "card_number", "12")
    assert resolved_update_is_safe(visual, "card_number", "5")
    assert (
        visual.recognition_route[0]["anchor_evidence"]["card_number_evidence_strength"]
        == "explicit_label"
    )


@pytest.mark.asyncio
async def test_paired_recognition_uses_multiorientation_recovery_on_missing_local_stamp(
    monkeypatch,
):
    class Provider:
        async def recognize_images(self, images, prompt):
            return CardRecognitionResult(confidence="0.9")

    async def ordinary_ocr(images):
        return ["PRIZM", "No. 105"]

    async def serial_pass(images):
        assert images == [(b"front", "image/jpeg"), (b"back", "image/jpeg")]
        return {"status": "succeeded", "observations": [observation("20/20")]}

    monkeypatch.setattr(local_ocr, "recognize_images", ordinary_ocr)
    monkeypatch.setattr(serial_ocr, "recognize_serial_images", serial_pass)
    service = CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    result = await service.recognize_pair_contents(b"front", "image/jpeg", b"back", "image/jpeg")
    assert result.is_numbered is True
    assert (result.serial_number, result.serial_total) == (20, 20)


@pytest.mark.asyncio
async def test_local_serial_reader_failure_does_not_fail_card_recognition(monkeypatch):
    class Provider:
        async def recognize_images(self, images, prompt):
            return CardRecognitionResult(player_name="Fixture Player", confidence="0.9")

    async def ordinary_ocr(images):
        return []

    async def serial_pass(images):
        return {"status": "failed", "reason": "local_serial_ocr_timeout", "observations": []}

    monkeypatch.setattr(local_ocr, "recognize_images", ordinary_ocr)
    monkeypatch.setattr(serial_ocr, "recognize_serial_images", serial_pass)
    service = CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    result = await service.recognize_pair_contents(b"front", "image/jpeg", b"back", "image/jpeg")
    assert result.player_name == "Fixture Player"
    assert result.recognition_route[-1]["status"] == "failed"
