import json
import sys

import pytest

from card_platform.flatbed_detection import Detection
from card_platform.flatbed_ml import refine_with_sam2


async def run_worker(tmp_path, payload, originals):
    script = tmp_path / "worker.py"
    script.write_text(
        "import sys\nsys.stdin.buffer.read()\nprint(" + repr(json.dumps(payload)) + ")\n"
    )
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"stub")
    return await refine_with_sam2(
        b"image",
        originals,
        python_path=sys.executable,
        worker_script=str(script),
        checkpoint=str(checkpoint),
        timeout_seconds=2,
    )


@pytest.mark.asyncio
async def test_jointly_valid_refinements_are_not_vetoed_by_old_coarse_peer_boxes(tmp_path):
    originals = [Detection(0.02, 0.1, 0.4, 0.3, 0, 0.8), Detection(0.44, 0.1, 0.4, 0.3, 0, 0.8)]
    payload = {
        "detections": [
            {"x": 0.05, "y": 0.1, "width": 0.5, "height": 0.3, "confidence": 0.96, "refined": True},
            {
                "x": 0.60,
                "y": 0.1,
                "width": 0.25,
                "height": 0.3,
                "confidence": 0.96,
                "refined": True,
            },
        ]
    }
    result, metadata = await run_worker(tmp_path, payload, originals)
    assert metadata["refined_count"] == 2
    assert metadata["fallback_count"] == 0
    assert result[0].x + result[0].width < result[1].x


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["x", "y", "width", "height", "angle", "confidence"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
async def test_nonfinite_worker_values_cannot_escape_as_accepted_geometry(tmp_path, field, value):
    originals = [Detection(0.1, 0.1, 0.3, 0.4, 0, 0.8)]
    item = {
        "x": 0.1,
        "y": 0.1,
        "width": 0.3,
        "height": 0.4,
        "angle": 0,
        "confidence": 0.9,
        "refined": True,
        field: value,
    }
    result, metadata = await run_worker(tmp_path, {"detections": [item]}, originals)
    assert result == originals
    assert metadata["status"] == "fallback"


@pytest.mark.asyncio
async def test_non_object_worker_json_has_safe_fallback(tmp_path):
    originals = [Detection(0.1, 0.1, 0.3, 0.4, 0, 0.8)]
    result, metadata = await run_worker(tmp_path, [], originals)
    assert result == originals
    assert metadata["reason"] == "invalid_worker_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["false", "true", 1, None])
async def test_worker_refinement_flag_must_be_an_actual_boolean(tmp_path, flag):
    originals = [Detection(0.1, 0.1, 0.3, 0.4, 0, 0.8)]
    item = {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.4, "confidence": 0.95, "refined": flag}
    result, metadata = await run_worker(tmp_path, {"detections": [item]}, originals)
    assert result == originals
    assert metadata["reason"] == "invalid_geometry"


@pytest.mark.asyncio
async def test_conflict_fallback_is_rechecked_without_discarding_unrelated_refinements(tmp_path):
    originals = [
        Detection(0, 0.1, 0.3, 0.3, 0, 0.8),
        Detection(0.33, 0.1, 0.3, 0.3, 0, 0.8),
        Detection(0.70, 0.60, 0.25, 0.3, 0, 0.8),
    ]
    payload = {
        "detections": [
            {"x": 0.12, "y": 0.1, "width": 0.4, "height": 0.3, "confidence": 0.9, "refined": True},
            {"x": 0.19, "y": 0.1, "width": 0.4, "height": 0.3, "confidence": 0.9, "refined": True},
            {
                "x": 0.72,
                "y": 0.61,
                "width": 0.23,
                "height": 0.28,
                "confidence": 0.95,
                "refined": True,
            },
        ]
    }
    result, metadata = await run_worker(tmp_path, payload, originals)
    assert metadata["refined_count"] == 1
    assert set(metadata["overlap_fallback_indices"]) == {0, 1}
    assert result[0].x == originals[0].x
    assert result[1].x == originals[1].x
    assert result[0].x + result[0].width < result[1].x
    assert "sam2_box_prompt" in result[2].evidence
