import pytest

from card_platform import local_ocr
from card_platform.local_vision_process import LocalVisionCircuitOpen


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,status,reason", [
    (LocalVisionCircuitOpen("cooldown"), "skipped", "local_vision_cooldown"),
    (TimeoutError(), "failed", "local_vision_timeout"),
    (OSError(), "failed", "local_vision_unavailable"),
    ((b'[{"lines": []}]', b'', 0), "not_observed", None),
    ((b'[{"lines": ["Panini"]}]', b'', 0), "succeeded", None),
])
async def test_ocr_distinguishes_engine_failure_from_empty_text(monkeypatch, outcome, status, reason):
    monkeypatch.setattr(local_ocr, "available", lambda: True)

    async def run(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(local_ocr, "run_local_vision", run)
    lines = await local_ocr.recognize_images([(b"image", "image/jpeg")])
    assert isinstance(lines, list)
    assert lines.status == status
    assert lines.failure_reason == reason


@pytest.mark.asyncio
async def test_orientation_records_image_failure_despite_successful_process_exit(monkeypatch, tmp_path):
    from card_platform import card_orientation

    async def run(*args, **kwargs):
        return b'[{"error": "fixture failure", "confidence": 0}]', b'', 0

    monkeypatch.setattr(card_orientation, "run_local_vision", run)
    monkeypatch.setattr(card_orientation.AppleVisionOrientationClassifier, "available", property(lambda self: True))
    classifier = card_orientation.AppleVisionOrientationClassifier(str(tmp_path / "fixture.swift"))
    assert await classifier.classify(tmp_path / "image.jpg") is None
    assert classifier.last_outcome == {"status": "failed", "reason": "process_image_failed"}
