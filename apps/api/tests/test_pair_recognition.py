from hashlib import sha256

import pytest

from card_platform.config import Settings
from card_platform.image_service import CardVisionService
from card_platform.schemas import CardRecognitionResult


@pytest.mark.asyncio
async def test_existing_combined_call_binds_pair_decision_to_current_bytes(monkeypatch):
    calls = []

    class Provider:
        async def recognize_images(self, images, prompt):
            calls.append(images)
            return CardRecognitionResult(confidence=".9", player_name="LeBron James")

    async def ocr(images):
        return []

    async def serial(images):
        return {"status": "failed", "observations": []}

    monkeypatch.setattr("card_platform.image_service.local_ocr.recognize_images", ocr)
    monkeypatch.setattr("card_platform.image_service.serial_ocr.recognize_serial_images", serial)
    service = CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    result = await service.recognize_pair_contents(b"front", "image/jpeg", b"back", "image/jpeg")
    assert len(calls) == 1
    assert len(calls[0]) == 2
    assert result.pair_validation.status == "unknown"
    assert result.pair_validation.image_content_hashes == {s: sha256(s.encode()).hexdigest() for s in ("front", "back")}
