import asyncio
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from card_platform.config import Settings
from card_platform.image_service import (
    CardVisionService,
    ImageStorageService,
    InvalidImageError,
    detect_image_content_type,
    read_validated_image,
    apply_local_ocr_anchors,
)
from card_platform.schemas import (
    CardRecognitionResult,
    SheetCardRecognitionItem,
    SheetCardRecognitionResult,
)


class _ImageResult:
    def __init__(self, row: dict | None) -> None:
        self.row = row

    async def fetchone(self):
        return self.row


class _ImageConnection:
    async def execute(self, query: str, _params: tuple) -> _ImageResult:
        return _ImageResult({"id": uuid4()}) if "SELECT id" in query else _ImageResult({"id": uuid4()})


def upload(content: bytes, content_type: str = "image/jpeg") -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename="card.jpg",
        headers=Headers({"content-type": content_type}),
    )


def test_image_validation_accepts_supported_content() -> None:
    content = asyncio.run(read_validated_image(upload(b"\xff\xd8\xffjpeg-bytes"), 100))
    assert content == b"\xff\xd8\xffjpeg-bytes"


def test_remote_image_signature_can_correct_wrong_declared_type() -> None:
    assert detect_image_content_type(b"\xff\xd8\xffjpeg-bytes") == "image/jpeg"
    assert detect_image_content_type(b"\x89PNG\r\n\x1a\nbytes") == "image/png"
    assert detect_image_content_type(b"not-an-image") is None


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


def test_item_image_cleanup_only_removes_owned_filename(tmp_path) -> None:
    item_id = uuid4()
    service = ImageStorageService(Settings(upload_dir=str(tmp_path)))
    owned = tmp_path / f"{item_id}-front-{'a' * 32}.jpg"
    unrelated = tmp_path / f"{uuid4()}-front-{'b' * 32}.jpg"
    suspicious = tmp_path / "outside.jpg"
    owned.write_bytes(b"owned")
    unrelated.write_bytes(b"unrelated")
    suspicious.write_bytes(b"suspicious")

    service.delete_item_images(
        item_id,
        (
            f"/uploads/{owned.name}",
            "/uploads/../outside.jpg",
        ),
    )

    assert not owned.exists()
    assert unrelated.exists()
    assert suspicious.exists()


@pytest.mark.asyncio
async def test_preprocessed_scanner_image_is_saved_without_second_rectification(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"\xff\xd8\xffalready-cropped-scanner-image"
    service = ImageStorageService(Settings(upload_dir=str(tmp_path), max_image_bytes=100))

    def fail_if_rectified(*_args, **_kwargs):
        raise AssertionError("preprocessed scanner image must not be rectified again")

    monkeypatch.setattr("card_platform.image_service.rectify_card_image", fail_if_rectified)
    result = await service.save_preprocessed_item_image_content(
        _ImageConnection(), uuid4(), uuid4(), "front", content, "image/jpeg"
    )

    saved = tmp_path / result.image_url.rsplit("/", 1)[-1]
    assert saved.read_bytes() == content


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

    async def recognize_images(
        self, images: list[tuple[bytes, str]], prompt: str
    ) -> CardRecognitionResult:
        assert len(images) == 2
        assert "正面和背面" in prompt
        return CardRecognitionResult(
            player_name="Kobe Bryant",
            season_start=1996,
            season_end=1997,
            brand_name="Topps",
            product_name="Topps Basketball",
            card_number="138",
            is_rookie=True,
            confidence="0.97",
            caveats=[],
        )

    async def recognize_sheet(
        self, content: bytes, content_type: str
    ) -> SheetCardRecognitionResult:
        assert content == b"\xff\xd8\xfffixture"
        return SheetCardRecognitionResult(
            detected_count=2,
            cards=[
                SheetCardRecognitionItem(
                    result=CardRecognitionResult(
                        player_name="Tim Duncan",
                        season_start=2021,
                        season_end=2022,
                        brand_name="Panini",
                        product_name="Immaculate",
                        serial_total=75,
                        is_patch=True,
                        confidence="0.93",
                    ),
                    order_number="260719215304998007",
                    product_number="260716000053100",
                    purchased_at="2026-07-19T00:00:00Z",
                    purchase_price="380.00",
                    shipping_price="18.00",
                    currency="CNY",
                    listing_title="2021-22 Panini Immaculate Tim Duncan /75 Patch",
                    visible_description="双窗球衣物料，75周年，75编",
                    text_derived_fields={
                        "serial_total": "75编",
                        "is_patch": "双窗球衣物料",
                    },
                ),
                SheetCardRecognitionItem(
                    result=CardRecognitionResult(
                        player_name="LeBron James",
                        season_start=2021,
                        season_end=2022,
                        brand_name="Panini",
                        product_name="National Treasures",
                        serial_total=99,
                        is_patch=True,
                        confidence="0.92",
                    ),
                    purchase_price="888.00",
                    currency="CNY",
                ),
            ],
        )


def test_card_vision_service_returns_structured_provider_result() -> None:
    service = CardVisionService(
        Settings(max_image_bytes=100, card_search_enabled=False),
        provider=FakeVisionProvider(),
    )
    result = asyncio.run(service.recognize(upload(b"\xff\xd8\xfffixture")))
    assert result.player_name == "Kobe Bryant"
    assert result.is_rookie is True
    assert str(result.confidence) == "0.91"


def test_card_vision_service_combines_front_and_back() -> None:
    service = CardVisionService(
        Settings(max_image_bytes=100, card_search_enabled=False),
        provider=FakeVisionProvider(),
    )
    result = asyncio.run(
        service.recognize_pair(
            upload(b"\xff\xd8\xfffixture"),
            upload(b"\xff\xd8\xffback-side"),
        )
    )
    assert result.card_number == "138"
    assert str(result.confidence) == "0.97"


def test_card_vision_service_discovers_multiple_cards_in_one_image() -> None:
    service = CardVisionService(
        Settings(max_image_bytes=100, card_search_enabled=False),
        provider=FakeVisionProvider(),
    )
    result = asyncio.run(service.recognize_sheet(upload(b"\xff\xd8\xfffixture")))
    assert result.detected_count == 2
    assert [card.result.player_name for card in result.cards] == [
        "Tim Duncan",
        "LeBron James",
    ]
    assert str(result.cards[0].purchase_price) == "380.00"
    assert result.cards[0].result.field_evidence["serial_total"] == ["截图文字：75编"]


def test_sheet_result_normalizes_provider_card_count_mismatch() -> None:
    result = SheetCardRecognitionResult.model_validate(
        {
            "detected_count": 3,
            "cards": [
                {
                    "result": {
                        "player_name": "Stephen Curry",
                        "confidence": "0.80",
                    }
                }
            ],
            "caveats": [],
        }
    )

    assert result.detected_count == 1
    assert len(result.cards) == 1
    assert "已按实际结果修正" in result.caveats[0]


def test_card_vision_requires_vision_provider() -> None:
    service = CardVisionService(Settings(vision_api_key=None))
    with pytest.raises(RuntimeError, match="VISION_API_KEY"):
        asyncio.run(service.recognize(upload(b"\xff\xd8\xfffixture")))


def test_local_ocr_anchors_override_serial_and_record_provenance() -> None:
    visual = CardRecognitionResult(
        player_name="Scottie Barnes", season_start=2025, season_end=2026,
        product_name="Topps Basketball", card_number="27/50",
        serial_number=None, serial_total=None, confidence="0.90",
    )
    resolved = apply_local_ocr_anchors(visual, {
        "card_number": "31", "card_number_evidence": "No. 31",
        "serial_number": 27, "serial_total": 50, "is_numbered": True,
        "serial_evidence": "27/50",
    }, 12)
    assert resolved.card_number == "31"
    assert resolved.serial_number == 27
    assert resolved.serial_total == 50
    assert resolved.field_evidence["card_number"] == ["Apple Vision OCR：No. 31"]
    assert resolved.recognition_route[0]["model"] == "apple_vision"
