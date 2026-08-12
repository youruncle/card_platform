from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.config import Settings
from card_platform.scanner_queue import ScannerQueueService, _quad_distance, _quad_quality


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
