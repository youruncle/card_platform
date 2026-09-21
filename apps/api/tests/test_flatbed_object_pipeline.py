from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis
from card_platform.flatbed_service import FlatbedService


@pytest.mark.asyncio
async def test_service_audits_final_geometry_against_pre_sam_object_not_model_score(
    tmp_path, monkeypatch
):
    owner_id, session_id = uuid4(), uuid4()
    obj = Detection(0.1, 0.1, 0.5, 0.7, 0, 0.85, evidence=("physical_object_envelope",))
    fragment = replace(obj, height=0.35, confidence=0.999, evidence=("sam2_box_prompt",))
    image = np.full((1000, 600, 3), 245, np.uint8)
    cv2.rectangle(image, (60, 100), (360, 800), (60, 80, 120), -1)
    for y in range(110, 790, 20):
        cv2.line(image, (70, y), (340, y), (150, 170, 190), 2)
    _, encoded = cv2.imencode(".png", image)

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session_id, "owner_id": owner_id, "dpi": 300}
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 2}
        return result

    db = AsyncMock()
    db.execute.side_effect = execute
    monkeypatch.setattr(
        "card_platform.flatbed_service.detect_layout",
        lambda *a, **k: LayoutHypothesis((obj,), 0.9, 1, 1),
    )
    # Simulate a future/broken refiner: no self-reported error, high score, same
    # count, but only half the original physical object survives.
    monkeypatch.setattr(
        "card_platform.flatbed_service.refine_with_sam2",
        AsyncMock(return_value=([fragment], {"status": "accepted", "refined_count": 1})),
    )
    service = FlatbedService(str(tmp_path), "bridge.swift", ml_enabled=True)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    result = await service.store_sheet(
        db, owner_id, session_id, "front", encoded.tobytes(), "image/png"
    )

    assert len(result["regions"]) == 1  # Preserve the editable output.
    assert not result["regions"][0]["crop_quality"]["usable"]
    assert "crop_quality_incomplete_card_object" in result["failures"]
    audit = result["layout_hypothesis"]["object_integrity"]
    assert audit["status"] == "requires_review"
    assert audit["minimum_object_coverage"] == pytest.approx(0.5)
    stored_sheet = next(
        call.args[1]
        for call in db.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_scan_sheet")
    )
    assert stored_sheet[9].obj["object_integrity"] == audit
    assert stored_sheet[11] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("old_front_count", [1, 2])
async def test_complete_back_objects_bypass_old_front_slot_cropping(
    tmp_path, monkeypatch, old_front_count
):
    owner_id, session_id = uuid4(), uuid4()
    obj = Detection(0.15, 0.1, 0.45, 0.7, 0, 0.9, evidence=("physical_object_envelope",))
    prior = [
        {
            "bounds": {
                "x": obj.x,
                "y": obj.y + index * obj.height / old_front_count,
                "width": obj.width,
                "height": obj.height / old_front_count,
                "angle": 0,
            },
            "signals": {},
        }
        for index in range(old_front_count)
    ]
    image = np.full((600, 600, 3), 245, np.uint8)
    cv2.rectangle(image, (90, 60), (360, 480), (60, 80, 120), -1)
    for y in range(70, 470, 20):
        cv2.line(image, (100, y), (350, y), (150, 170, 190), 2)
    _, encoded = cv2.imencode(".png", image)

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session_id, "owner_id": owner_id, "dpi": 300}
        elif sql.startswith("SELECT r.bounds,r.signals"):
            result.fetchall.return_value = prior
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 2}
        return result

    db = AsyncMock()
    db.execute.side_effect = execute
    monkeypatch.setattr(
        "card_platform.flatbed_service.detect_layout",
        lambda *a, **k: LayoutHypothesis(
            (obj,),
            0.9,
            1,
            1,
            signals={
                "current_object_layout_authoritative": 1,
                "prior_topology_rejected": 1,
                "prior_topology_rejection_reason": (
                    "current_complete_object_layout_preferred"
                    if old_front_count == 1
                    else "current_complete_objects_replace_oversegmented_prior"
                ),
            },
        ),
    )
    stale_slots = AsyncMock(side_effect=AssertionError("Old slots must not override complete objects"))
    refiner = AsyncMock(return_value=([obj], {"status": "accepted", "refined_count": 1}))
    monkeypatch.setattr("card_platform.flatbed_slot_refinement.refine_back_slots", stale_slots)
    monkeypatch.setattr("card_platform.flatbed_service.refine_with_sam2", refiner)
    service = FlatbedService(str(tmp_path), "bridge.swift", ml_enabled=True)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    result = await service.store_sheet(db, owner_id, session_id, "back", encoded.tobytes(), "image/png")

    stale_slots.assert_not_called()
    assert refiner.call_args.args[1] == [obj]
    assert refiner.call_args.kwargs["prompt_detections"] is None
    assert len(result["regions"]) == 1
    assert result["regions"][0]["bounds"]["height"] == obj.height
    assert result["layout_hypothesis"]["object_integrity"]["status"] == "accepted"
    assert result["layout_hypothesis"]["slot_refinement"]["reason"] == "independent_back_geometry"
    assert ("front_back_count_mismatch" in result["failures"]) == (old_front_count == 2)
    assert "front_layout_splits_current_cards" not in result["failures"]
    assert "front_layout_merges_current_cards" not in result["failures"]
    assert "front_layout_conflicts_current_objects" not in result["failures"]
