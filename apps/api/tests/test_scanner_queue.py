from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.config import Settings
from card_platform.image_rectification import RectifiedImage
from card_platform.scanner_queue import (
    ScannerQueueService,
    _auto_import_payload,
    _quad_distance,
    _quad_quality,
)
from card_platform.schemas import CardRecognitionResult


class _InsertResult:
    def __init__(self, row: dict) -> None:
        self.row = row

    async def fetchone(self) -> dict:
        return self.row


class _Connection:
    def __init__(self) -> None:
        self.params = None

    async def execute(self, _query: str, params: tuple) -> _InsertResult:
        self.params = params
        (
            job_id,
            owner_id,
            front_path,
            back_path,
            front_original_path,
            back_original_path,
            front_raw_path,
            back_raw_path,
            front_type,
            back_type,
            front_confidence,
            back_confidence,
            front_transformed,
            back_transformed,
        ) = params
        return _InsertResult(
            {
                "id": job_id,
                "owner_id": owner_id,
                "status": "queued",
                "front_image_path": front_path,
                "back_image_path": back_path,
                "front_original_image_path": front_original_path,
                "back_original_image_path": back_original_path,
                "front_raw_image_path": front_raw_path,
                "back_raw_image_path": back_raw_path,
                "front_content_type": front_type,
                "back_content_type": back_type,
                "front_crop_confidence": front_confidence,
                "back_crop_confidence": back_confidence,
                "front_transformed": front_transformed,
                "back_transformed": back_transformed,
                "front_rotation_degrees": 0,
                "back_rotation_degrees": 0,
                "front_orientation_confidence": 0,
                "back_orientation_confidence": 0,
                "front_orientation_method": "pending",
                "back_orientation_method": "pending",
                "result": None,
                "error_message": None,
                "attempt_count": 0,
                "started_at": None,
                "finished_at": None,
                "created_at": "2026-08-10T00:00:00Z",
                "updated_at": "2026-08-10T00:00:00Z",
            }
        )


def _image() -> bytes:
    image = np.full((300, 500, 3), 127, dtype=np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    return encoded.tobytes()


def test_recognition_auto_import_preserves_editable_card_attributes() -> None:
    owner_id = uuid4()
    payload = _auto_import_payload(
        owner_id,
        CardRecognitionResult(
            confidence="0.91",
            player_name="Anfernee Simons",
            brand_name="Panini",
            season_start=2024,
            product_name="Mosaic Basketball",
            parallel_name="Blue Mosaic",
            card_number="18",
            serial_number=12,
            serial_total=99,
            is_rookie=False,
            is_autograph=True,
            is_memorabilia=True,
        ),
    )

    assert payload.owner_id == owner_id
    assert payload.card.player_name == "Anfernee Simons"
    assert payload.card.parallel_name == "Blue Mosaic"
    assert payload.is_numbered is True
    assert payload.serial_number == 12
    assert payload.serial_total == 99
    assert payload.card.is_autograph is True
    assert payload.card.is_memorabilia is True
    assert "识别置信度 91%" in (payload.notes or "")
    assert "可编辑并参与估值" in (payload.notes or "")


def test_missing_core_identity_requires_review_instead_of_placeholder_import() -> None:
    with pytest.raises(ValueError, match="发行赛季"):
        _auto_import_payload(uuid4(), CardRecognitionResult(confidence="0.58"))


def test_recognition_auto_import_drops_incompatible_season_end() -> None:
    payload = _auto_import_payload(
        uuid4(),
        CardRecognitionResult(
            confidence="0.80",
            player_name="Cade Cunningham",
            brand_name="Panini",
            season_start=2021,
            season_end=2024,
            product_name="Prizm Basketball",
        ),
    )

    assert payload.card.season_start == 2021
    assert payload.card.season_end is None


@pytest.mark.asyncio
async def test_enqueue_persists_images_before_returning(tmp_path: Path) -> None:
    settings = Settings(scanner_job_dir=str(tmp_path))
    service = ScannerQueueService(settings)
    connection = _Connection()
    owner_id = uuid4()

    job = await service.enqueue(
        connection,
        owner_id,
        _image(),
        "image/png",
        _image(),
        "image/png",
    )

    assert job.status == "queued"
    assert connection.params is not None
    front_path = service.image_path(connection.params[2])
    back_path = service.image_path(connection.params[3])
    front_original_path = service.image_path(connection.params[4])
    back_original_path = service.image_path(connection.params[5])
    front_raw_path = service.image_path(connection.params[6])
    back_raw_path = service.image_path(connection.params[7])
    assert front_path.read_bytes() == _image()
    assert back_path.read_bytes() == _image()
    assert front_original_path.read_bytes() == _image()
    assert back_original_path.read_bytes() == _image()
    assert front_raw_path.read_bytes() == _image()
    assert back_raw_path.read_bytes() == _image()
    assert "/images/front?v=" in job.front_image_url


@pytest.mark.asyncio
async def test_enqueue_recovers_suspicious_zoom_crop_from_full_resolution_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processed_front = b"zoomed-front"
    processed_back = b"zoomed-back"
    raw_front = b"full-resolution-front"
    raw_back = b"full-resolution-back"
    calls: list[bytes] = []

    def fake_rectify(content: bytes, content_type: str, **_kwargs) -> RectifiedImage:
        calls.append(content)
        quad = (
            ((0.30, 0.30), (0.50, 0.30), (0.50, 0.50), (0.30, 0.50))
            if content.startswith(b"zoomed")
            else ((0.20, 0.20), (0.60, 0.20), (0.60, 0.70), (0.20, 0.70))
        )
        return RectifiedImage(
            content=b"rectified-" + content,
            content_type="image/jpeg",
            transformed=True,
            confidence=0.95,
            normalized_quad=quad,
        )

    monkeypatch.setattr("card_platform.scanner_queue.rectify_card_image", fake_rectify)
    service = ScannerQueueService(Settings(scanner_job_dir=str(tmp_path)))
    connection = _Connection()

    await service.enqueue(
        connection,
        uuid4(),
        processed_front,
        "image/jpeg",
        processed_back,
        "image/jpeg",
        front_raw_content=raw_front,
        front_raw_content_type="image/jpeg",
        back_raw_content=raw_back,
        back_raw_content_type="image/jpeg",
    )

    assert calls == [processed_front, processed_back, raw_front, raw_back]
    assert connection.params is not None
    assert service.image_path(connection.params[2]).read_bytes() == b"rectified-" + raw_front
    assert service.image_path(connection.params[3]).read_bytes() == b"rectified-" + raw_back


@pytest.mark.asyncio
async def test_enqueue_preserves_preprocessed_flatbed_crops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_rectify(*_args, **_kwargs):
        raise AssertionError("preprocessed flatbed crop must not be rectified twice")

    monkeypatch.setattr("card_platform.scanner_queue.rectify_card_image", unexpected_rectify)
    service = ScannerQueueService(Settings(scanner_job_dir=str(tmp_path)))
    connection = _Connection()

    await service.enqueue(
        connection,
        uuid4(),
        b"flatbed-front",
        "image/jpeg",
        b"flatbed-back",
        "image/jpeg",
        preprocessed=True,
    )

    assert connection.params is not None
    assert service.image_path(connection.params[2]).read_bytes() == b"flatbed-front"
    assert service.image_path(connection.params[3]).read_bytes() == b"flatbed-back"
    assert connection.params[10:14] == (1.0, 1.0, False, False)


@pytest.mark.asyncio
async def test_enqueue_preserves_flatbed_crop_confidence(tmp_path: Path) -> None:
    service = ScannerQueueService(Settings(scanner_job_dir=str(tmp_path)))
    connection = _Connection()

    await service.enqueue(
        connection,
        uuid4(),
        b"flatbed-front",
        "image/jpeg",
        b"flatbed-back",
        "image/jpeg",
        preprocessed=True,
        front_crop_confidence=0.73,
        back_crop_confidence=0.61,
    )

    assert connection.params is not None
    assert connection.params[10:14] == (0.73, 0.61, False, False)


@pytest.mark.asyncio
async def test_enqueue_keeps_safe_zoom_crop_when_raw_frame_has_distractors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bytes] = []

    def fake_rectify(content: bytes, content_type: str, **_kwargs) -> RectifiedImage:
        calls.append(content)
        return RectifiedImage(
            content=b"rectified-" + content,
            content_type="image/jpeg",
            transformed=True,
            confidence=0.95,
            normalized_quad=((0.20, 0.20), (0.70, 0.20), (0.70, 0.70), (0.20, 0.70)),
        )

    monkeypatch.setattr("card_platform.scanner_queue.rectify_card_image", fake_rectify)
    service = ScannerQueueService(Settings(scanner_job_dir=str(tmp_path)))
    connection = _Connection()

    await service.enqueue(
        connection,
        uuid4(),
        b"zoomed-front",
        "image/jpeg",
        b"zoomed-back",
        "image/jpeg",
        front_raw_content=b"raw-front-with-hand",
        front_raw_content_type="image/jpeg",
        back_raw_content=b"raw-back-with-hand",
        back_raw_content_type="image/jpeg",
    )

    assert calls == [b"zoomed-front", b"zoomed-back"]
    assert connection.params is not None
    assert service.image_path(connection.params[2]).read_bytes() == b"rectified-zoomed-front"


def test_scanner_image_path_cannot_escape_upload_root(tmp_path: Path) -> None:
    service = ScannerQueueService(Settings(scanner_job_dir=str(tmp_path)))
    with pytest.raises(ValueError, match="invalid scanner image path"):
        service.image_path("../private.txt")


def test_pair_quality_identifies_brandon_miller_front_as_anomalous() -> None:
    front = ((0.5211, 0.2748), (0.7467, 0.2911), (0.7239, 0.7562), (0.2417, 0.7192))
    back = ((0.2672, 0.2148), (0.7200, 0.2770), (0.7078, 0.6984), (0.2661, 0.6932))

    mean_distance, max_distance = _quad_distance(front, back)

    assert mean_distance > 0.07
    assert max_distance > 0.12
    assert _quad_quality(back) - _quad_quality(front) > 0.10


def test_delete_job_files_removes_only_recorded_job_directory(tmp_path: Path) -> None:
    service = ScannerQueueService(Settings(scanner_job_dir=str(tmp_path)))
    job_dir = tmp_path / "owner" / "job"
    job_dir.mkdir(parents=True)
    row: dict[str, str] = {}
    columns = (
        "front_image_path",
        "back_image_path",
        "front_original_image_path",
        "back_original_image_path",
        "front_raw_image_path",
        "back_raw_image_path",
    )
    for index, column in enumerate(columns):
        path = job_dir / f"image-{index}.jpg"
        path.write_bytes(b"image")
        row[column] = str(path.relative_to(tmp_path))
    sibling = tmp_path / "owner" / "keep.jpg"
    sibling.write_bytes(b"keep")

    service.delete_job_files(row)

    assert not job_dir.exists()
    assert sibling.read_bytes() == b"keep"
