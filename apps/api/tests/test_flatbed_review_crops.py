import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.flatbed_service import FlatbedService


@pytest.mark.asyncio
async def test_explicit_review_materializes_only_missing_crop_and_keeps_quality_warning(tmp_path):
    original = tmp_path / "original.png"
    cv2.imwrite(str(original), np.full((120, 160, 3), 100, np.uint8))
    owner, session, missing, existing, sheet = (uuid4() for _ in range(5))
    normal = tmp_path / "existing.jpg"
    normal.write_bytes(b"existing image bytes")
    quality = {"usable": False, "usability_score": 0.49}
    rows = [{"id": region, "sheet_id": sheet, "crop_path": path,
             "original_path": "original.png", "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
             "bounds": {"x": .25, "y": .25, "width": .5, "height": .5},
             "signals": {"crop_quality": quality, "orientation_rotation_degrees": 90}}
            for region, path in [(missing, None), (existing, "existing.jpg")]]
    result = AsyncMock()
    result.fetchall.return_value = rows
    db = AsyncMock()
    db.execute.return_value = result
    service = FlatbedService(str(tmp_path), "unused.swift")

    await service.materialize_review_crops(db, owner, session, [missing, existing])

    updates = [c.args[1] for c in db.execute.call_args_list if c.args[0].startswith("UPDATE flatbed_scan_region")]
    assert len(updates) == 1
    path, signals, region, recorded_owner = updates[0]
    assert region == missing and recorded_owner == owner
    image = cv2.imdecode(np.frombuffer(service.resolve_path(path).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[:2] == (80, 60)
    assert signals.obj["crop_quality"] == quality
    assert signals.obj["crop_fallback"]["method"] == "reviewed_reference_rectangle"
    assert signals.obj["crop_fallback"]["source_sha256"] == rows[0]["sha256"]
    assert normal.read_bytes() == b"existing image bytes"


@pytest.mark.asyncio
async def test_review_cannot_materialize_from_changed_original(tmp_path):
    cv2.imwrite(str(tmp_path / "original.png"), np.full((120, 160, 3), 100, np.uint8))
    result = AsyncMock()
    result.fetchall.return_value = [{"id": uuid4(), "sheet_id": uuid4(), "crop_path": None,
        "original_path": "original.png", "sha256": "0" * 64,
        "bounds": {"x": .25, "y": .25, "width": .5, "height": .5}, "signals": {}}]
    db = AsyncMock()
    db.execute.return_value = result
    with pytest.raises(ValueError, match="原图已改变"):
        await FlatbedService(str(tmp_path), "unused.swift").materialize_review_crops(db, uuid4(), uuid4(), [uuid4()])
    assert not list(tmp_path.glob("review-*.jpg"))


@pytest.mark.asyncio
async def test_single_layout_reuses_other_region_bytes_bounds_and_signals(tmp_path):
    from card_platform.flatbed_detection import Detection
    owner, session = uuid4(), uuid4()
    image = np.full((300, 400, 3), 240, np.uint8)
    image[30:240, 40:160] = (50, 80, 110)
    image[30:240, 240:360] = (110, 50, 80)
    _, content = cv2.imencode(".png", image)
    preserved = {"bounds": {"x": .1, "y": .1, "width": .3, "height": .7, "angle": 2},
                 "confidence": .87, "failure_reason": "source_edge_contact", "crop_path": "keep.jpg",
                 "signals": {"ocr_text": "KEEP", "orientation_rotation_degrees": 180}}
    (tmp_path / "keep.jpg").write_bytes(b"unchanged bytes")
    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session, "owner_id": owner, "dpi": 300}
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 2}
        return result
    db = AsyncMock()
    db.execute.side_effect = execute
    service = FlatbedService(str(tmp_path), "unused.swift", ml_enabled=False)
    service.observe_crop = AsyncMock(return_value=(None, {"ocr_method": "test"}))
    result = await service.store_sheet(db, owner, session, "front", content.tobytes(), "image/png",
        detections_override=[Detection(.1,.1,.3,.7,0,1), Detection(.6,.1,.3,.7,0,1)],
        preserved_regions={0: preserved})
    inserts = [c.args[1] for c in db.execute.call_args_list if c.args[0].startswith("INSERT INTO flatbed_scan_region")]
    assert len(result["regions"]) == 2
    assert inserts[0][4].obj == preserved["bounds"]
    assert inserts[0][5:8] == (preserved["confidence"], preserved["failure_reason"], "keep.jpg")
    assert inserts[0][8].obj == preserved["signals"]
    assert (tmp_path / "keep.jpg").read_bytes() == b"unchanged bytes"
    assert service.observe_crop.await_count == 1
    assert inserts[1][7] is not None
