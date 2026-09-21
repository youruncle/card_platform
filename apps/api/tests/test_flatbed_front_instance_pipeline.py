from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis
from card_platform.flatbed_service import FlatbedService


@pytest.mark.asyncio
@pytest.mark.parametrize("ml_enabled", [True, False])
async def test_complete_instance_fallback_is_saved_without_reintroducing_neighbour_padding(
    tmp_path, monkeypatch, ml_enabled
):
    owner, session = uuid4(), uuid4()
    image = np.full((900, 1000, 3), 246, np.uint8)
    # A complete lower object is only 4px from the previous physical object.
    # Adding the old 3% source padding would pull that neighbour into the crop.
    cv2.rectangle(image, (200, 50), (800, 296), (30, 80, 230), -1)
    cv2.rectangle(image, (200, 300), (800, 740), (50, 110, 160), -1)
    points = ((0.2, 300 / 900), (0.8, 300 / 900), (0.8, 740 / 900), (0.2, 740 / 900))
    instance = Detection(
        0.2,
        300 / 900,
        0.6,
        440 / 900,
        0,
        0.88,
        points=points,
        evidence=("current_image_instance", "current_instance_grid_reconciliation"),
    )
    monkeypatch.setattr(
        "card_platform.flatbed_service.detect_layout",
        lambda *args, **kwargs: LayoutHypothesis((instance,), 0.90, 4, 2),
    )
    refinement = AsyncMock(
        return_value=(
            [instance],
            {
                "status": "fallback",
                "reason": "no_confident_masks",
                "refined_count": 0,
                "fallback_count": 1,
            },
        )
    )
    monkeypatch.setattr("card_platform.flatbed_service.refine_with_sam2", refinement)

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session, "owner_id": owner, "dpi": 300}
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 2}
        return result

    connection = AsyncMock()
    connection.execute.side_effect = execute
    service = FlatbedService(str(tmp_path), "unused.swift", ml_enabled=ml_enabled)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    ok, content = cv2.imencode(".png", image)
    assert ok
    output = await service.store_sheet(
        connection, owner, session, "front", content.tobytes(), "image/png"
    )
    assert len(output["regions"]) == 1
    assert refinement.await_count == int(ml_enabled)
    region = output["regions"][0]
    assert region["bounds"]["crop_padding_ratio"] == 0
    assert region["bounds"]["output_border_ratio"] > 0
    cropped = cv2.imread(str(next(tmp_path.glob("*/*/*/region-0.jpg"))))
    # No red neighbour pixels survive the actual service warp + border + JPEG.
    assert np.mean((cropped[:, :, 2].astype(int) - cropped[:, :, 1]) > 100) < 0.001
    sheet_insert = next(
        call.args[1]
        for call in connection.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_scan_sheet")
    )
    assert sheet_insert[9].obj["crop_quality"]["region_count"] == 1
    if ml_enabled:
        assert sheet_insert[9].obj["ml_refinement"]["fallback_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [False, True])
async def test_manual_layout_uses_current_pixels_not_failed_automatic_hypothesis(
    tmp_path, monkeypatch, blank
):
    owner, session = uuid4(), uuid4()
    image = np.full((900, 1000, 3), 246, np.uint8)
    if not blank:
        cv2.rectangle(image, (100, 100), (800, 600), (40, 90, 190), -1)
    detection = Detection(
        0.1,
        100 / 900,
        0.7,
        500 / 900,
        0,
        1,
        points=((0.1, 100 / 900), (0.8, 100 / 900), (0.8, 600 / 900), (0.1, 600 / 900)),
        evidence=("manual_override",),
    )

    def automatic_not_needed(*args, **kwargs):
        raise AssertionError("manual quads must not depend on automatic detection")

    monkeypatch.setattr("card_platform.flatbed_service.detect_layout", automatic_not_needed)

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session, "owner_id": owner, "dpi": 300}
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 2}
        return result

    connection = AsyncMock()
    connection.execute.side_effect = execute
    service = FlatbedService(str(tmp_path), "unused.swift", ml_enabled=False)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    ok, content = cv2.imencode(".png", image)
    assert ok
    output = await service.store_sheet(
        connection,
        owner,
        session,
        "front",
        content.tobytes(),
        "image/png",
        detections_override=[detection],
    )
    assert len(output["regions"]) == 1
    insert = next(
        call.args[1]
        for call in connection.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_scan_sheet")
    )
    assert insert[9].obj["transform"] == "manual"
    assert insert[11] is blank
    assert output["regions"][0]["crop_quality"]["usable"] is not blank
