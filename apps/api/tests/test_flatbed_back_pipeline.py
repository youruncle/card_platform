from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis
from card_platform.flatbed_service import FlatbedService
from card_platform.flatbed_object_quads import resolve_object_layout


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_back_pipeline_persists_current_geometry_and_honest_source_counts(
    tmp_path, monkeypatch, missing
):
    owner_id, session_id = uuid4(), uuid4()
    detections = [
        Detection(0.03 + (i % 2) * 0.5, 0.015 + (i // 2) * 0.25, 0.44, 0.22, 0, 0.8)
        for i in range(7)
    ]
    last = detections[-1]
    detections[-1] = replace(
        last,
        points=(
            (last.x, last.y),
            (last.x + last.width, last.y),
            (last.x + last.width, last.y + last.height),
            (last.x, last.y + last.height),
        ),
        evidence=("current_candidate_inward_edges",),
    )
    image = np.full((1400, 1000, 3), 245, np.uint8)
    for item in detections:
        cv2.rectangle(
            image,
            (round(item.x * 1000), round(item.y * 1400)),
            (round((item.x + item.width) * 1000), round((item.y + item.height) * 1400)),
            (30, 80, 120),
            -1,
        )
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    prior = [
        {
            "bounds": {"x": d.x, "y": d.y, "width": d.width, "height": d.height, "angle": 0},
            "signals": {},
        }
        for d in detections
    ]
    # The last prior is shifted; the pipeline must persist localized geometry,
    # never replace it with that front-side hint to reconcile cardinality.
    prior[-1]["bounds"]["x"] += 0.12

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session_id, "owner_id": owner_id, "dpi": 600}
        elif sql.startswith("SELECT r.bounds,r.signals"):
            result.fetchall.return_value = prior
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 2}
        return result

    connection = AsyncMock()
    connection.execute.side_effect = execute
    localized = detections[:-1] if missing else detections
    localizer = AsyncMock(side_effect=AssertionError("Front slots cannot crop backs"))
    resolved = list(
        resolve_object_layout(
            encoded.tobytes(), LayoutHypothesis(tuple(localized), 0.9, 4, 2)
        ).detections
    )
    general_ml = AsyncMock(
        return_value=(resolved, {"status": "accepted", "refined_count": len(localized)})
    )
    monkeypatch.setattr("card_platform.flatbed_slot_refinement.refine_back_slots", localizer)
    monkeypatch.setattr("card_platform.flatbed_service.refine_with_sam2", general_ml)
    monkeypatch.setattr(
        "card_platform.flatbed_service.detect_layout",
        lambda *args, **kwargs: LayoutHypothesis(tuple(localized), 0.9, 4, 2),
    )
    service = FlatbedService(str(tmp_path), "bridge.swift", ml_enabled=True)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    output = await service.store_sheet(
        connection, owner_id, session_id, "back", encoded.tobytes(), "image/png"
    )

    localizer.assert_not_called()
    assert general_ml.call_args.args[1] == resolved
    assert general_ml.call_args.kwargs["prompt_detections"] is None
    assert len(output["regions"]) == len(localized)
    assert ("front_back_count_mismatch" in output["failures"]) == missing
    ml = output["layout_hypothesis"]["ml_refinement"]
    assert ml["refined_count"] == len(localized)
    inserts = [
        call.args
        for call in connection.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_scan_region")
    ]
    assert len(inserts) == len(localized)
    assert inserts[-1][1][4].obj["x"] == resolved[-1].x
    if not missing:
        assert inserts[-1][1][4].obj["crop_padding_ratio"] == 0
        assert inserts[-1][1][4].obj["output_border_ratio"] > 0
    assert "slot-local" not in inserts[-1][1][8].obj["detector_version"]
    stored_sheet = next(
        call.args[1]
        for call in connection.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_scan_sheet")
    )
    assert stored_sheet[9].obj["ml_refinement"] == ml
    assert stored_sheet[10].obj["prior_role"] == "count_and_pairing_advisory_only"
    assert stored_sheet[10].obj["geometry_source"] == "current_image"
