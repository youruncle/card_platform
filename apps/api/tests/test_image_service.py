import asyncio
from io import BytesIO

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from card_platform.config import Settings
from card_platform.image_service import CardVisionService, InvalidImageError, read_validated_image
from card_platform.schemas import CardRecognitionResult


def upload(content: bytes, content_type: str = "image/jpeg") -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename="card.jpg",
        headers=Headers({"content-type": content_type}),
    )


def test_image_validation_accepts_supported_content() -> None:
    content = asyncio.run(read_validated_image(upload(b"\xff\xd8\xffjpeg-bytes"), 100))
    assert content == b"\xff\xd8\xffjpeg-bytes"


@pytest.mark.parametrize("content_type", ["text/plain", "image/gif", "application/pdf"])
def test_image_validation_rejects_unsupported_content_type(content_type: str) -> None:
    with pytest.raises(InvalidImageError, match="JPEG, PNG, or WebP"):
        asyncio.run(read_validated_image(upload(b"content", content_type), 100))


def test_image_validation_rejects_empty_and_oversized_content() -> None:
    with pytest.raises(InvalidImageError, match="empty"):
        asyncio.run(read_validated_image(upload(b""), 100))
    with pytest.raises(InvalidImageError, match="exceeds"):
        asyncio.run(read_validated_image(upload(b"\xff\xd8\xff123456"), 5))


def test_image_validation_rejects_spoofed_mime_type() -> None:
    with pytest.raises(InvalidImageError, match="does not match"):
        asyncio.run(read_validated_image(upload(b"not-a-real-jpeg"), 100))


class FakeVisionProvider:
    async def recognize(self, content: bytes, content_type: str) -> CardRecognitionResult:
        assert content == b"\xff\xd8\xfffixture"
        assert content_type == "image/jpeg"
        return CardRecognitionResult(
            player_name="Kobe Bryant",
            season_start=1996,
            season_end=1997,
            brand_name="Upper Deck",
            product_name="UD3",
            insert_name="Hardwood Prospects",
            is_rookie=True,
            confidence="0.91",
            caveats=["编号需查看背面"],
        )


def test_card_vision_service_returns_structured_provider_result() -> None:
    service = CardVisionService(Settings(max_image_bytes=100), provider=FakeVisionProvider())
    result = asyncio.run(service.recognize(upload(b"\xff\xd8\xfffixture")))
    assert result.player_name == "Kobe Bryant"
    assert result.is_rookie is True
    assert str(result.confidence) == "0.91"


def test_card_vision_requires_api_key_without_provider() -> None:
    service = CardVisionService(Settings(openai_api_key=None))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        asyncio.run(service.recognize(upload(b"\xff\xd8\xfffixture")))
