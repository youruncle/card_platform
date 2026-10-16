"""SAM can refine complete objects, but cannot replace them with confident fragments."""

import json
import math
import sys

import numpy as np
import pytest

from card_platform.flatbed_detection import Detection
from card_platform.flatbed_ml import refine_with_sam2


async def run_worker(tmp_path, items, originals, **kwargs):
    script = tmp_path / "worker.py"
    script.write_text(
        "import sys\nsys.stdin.buffer.read()\nprint("
        + repr(json.dumps({"detections": items}))
        + ")\n"
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
        **kwargs,
    )


def original_object(evidence):
    return Detection(0.2, 0.2, 0.4, 0.5, 0, 0.86, evidence=evidence)


def raw_box(x=0.2, y=0.2, width=0.4, height=0.5, **extra):
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "confidence": 0.995,
        "refined": True,
        **extra,
    }


def rotated_same_area_box():
    points = np.array([[0.2, 0.2], [0.6, 0.2], [0.6, 0.7], [0.2, 0.7]])
    angle = math.radians(22)
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    points = (points - points.mean(axis=0)) @ rotation.T + points.mean(axis=0)
    left, top = points.min(axis=0)
    right, bottom = points.max(axis=0)
    return raw_box(
        float(left), float(top), float(right - left), float(bottom - top), points=points.tolist()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence", [("physical_object_envelope",), ("grading_label_association",)]
)
@pytest.mark.parametrize(
    "proposal",
    [
        raw_box(y=0.45, height=0.25),  # Dense colour/ink in only half of a real card.
        raw_box(y=0.28),  # Same area, but loses the pale label/autograph strip.
        rotated_same_area_box(),  # Same polygon area, but exposes original corners.
    ],
    ids=["half_card", "same_area_shift", "same_area_rotation"],
)
async def test_proven_object_rejects_high_confidence_incomplete_masks(tmp_path, evidence, proposal):
    original = original_object(evidence)
    result, metadata = await run_worker(tmp_path, [proposal], [original])

    candidate = result[0]
    assert (candidate.x, candidate.y, candidate.width, candidate.height, candidate.points) == (
        original.x,
        original.y,
        original.width,
        original.height,
        original.points,
    )
    assert candidate.confidence <= original.confidence
    assert candidate.confidence <= 0.49
    assert "sam2_box_prompt" not in candidate.evidence
    assert "sam2_object_preservation_fallback" in candidate.evidence
    assert metadata["refined_count"] == 0
    assert metadata["fallback_count"] == 1
    assert metadata["reason"] == "protected_object_not_preserved"
    assert metadata["object_preservation_fallback_count"] == 1
    assert metadata["object_preservation_fallback_indices"] == [0]
    assert metadata["preservation_fallback_details"][0]["original_polygon_coverage"] < 0.95
    label = "grading_label_association" in evidence
    assert metadata["label_preservation_fallback_count"] == int(label)
    assert metadata["label_preservation_fallback_indices"] == ([0] if label else [])
    assert ("sam2_label_preservation_fallback" in candidate.evidence) == label


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence", [("physical_object_envelope",), ("grading_label_association",)]
)
async def test_proven_object_allows_narrow_edge_refinement(tmp_path, evidence):
    original = original_object(evidence)
    # A small shift/inset retains over 95% of the original polygon after the
    # standard output safety margin, so we do not freeze every strong box.
    proposal = raw_box(x=0.208, y=0.205, width=0.39, height=0.49)
    result, metadata = await run_worker(tmp_path, [proposal], [original])

    assert "sam2_box_prompt" in result[0].evidence
    assert metadata["refined_count"] == 1
    assert metadata["object_preservation_fallback_count"] == 0
    assert metadata["preservation_fallback_details"] == []


@pytest.mark.asyncio
async def test_weak_grid_allocation_can_still_refine_to_smaller_shifted_card(tmp_path):
    original = original_object(("full_bed_grid", "cell_extent_fallback", "grid_capacity:4x2"))
    proposal = raw_box(x=0.26, y=0.3, width=0.31, height=0.39)
    result, metadata = await run_worker(tmp_path, [proposal], [original])

    assert "sam2_box_prompt" in result[0].evidence
    assert result[0].x > original.x
    assert result[0].height < original.height
    assert metadata["refined_count"] == 1
    assert metadata["object_preservation_fallback_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_marker", ["slot_prompt_only", "front_to_back_prior"])
async def test_old_front_geometry_is_not_mistaken_for_a_current_complete_object(
    tmp_path, prior_marker
):
    original = original_object(
        ("physical_object_envelope", "grading_label_association", prior_marker)
    )
    proposal = raw_box(y=0.28)
    result, metadata = await run_worker(tmp_path, [proposal], [original])

    assert "sam2_box_prompt" in result[0].evidence
    assert result[0].y > original.y
    assert metadata["refined_count"] == 1
    assert metadata["object_preservation_fallback_count"] == 0


@pytest.mark.asyncio
async def test_object_polygon_not_axis_aligned_bounds_is_the_coverage_reference(tmp_path):
    proposal = rotated_same_area_box()
    points = tuple(tuple(point) for point in proposal["points"])
    original = Detection(
        proposal["x"],
        proposal["y"],
        proposal["width"],
        proposal["height"],
        22,
        0.86,
        points=points,
        evidence=("physical_object_envelope", "grading_label_association"),
    )
    # The same rotated quad has large empty corners in its AABB. Using an
    # axis-aligned denominator would falsely reject this exact preservation.
    result, metadata = await run_worker(tmp_path, [proposal], [original])

    assert "sam2_box_prompt" in result[0].evidence
    assert metadata["refined_count"] == 1
    assert metadata["object_preservation_fallback_count"] == 0


@pytest.mark.asyncio
async def test_mixed_batch_records_fallback_indices_without_reverting_unrelated_masks(tmp_path):
    originals = [
        original_object(("physical_object_envelope", "grading_label_association")),
        Detection(0.7, 0.2, 0.25, 0.5, 0, 0.8),
    ]
    result, metadata = await run_worker(
        tmp_path,
        [raw_box(y=0.28), raw_box(x=0.71, y=0.21, width=0.23, height=0.48)],
        originals,
    )

    assert "sam2_object_preservation_fallback" in result[0].evidence
    assert "sam2_box_prompt" in result[1].evidence
    assert metadata["status"] == "accepted"
    assert metadata["refined_count"] == 1
    assert metadata["object_preservation_fallback_indices"] == [0]
    assert metadata["label_preservation_fallback_indices"] == [0]


@pytest.mark.asyncio
async def test_content_guarded_refinement_defers_axis_background_area_check(tmp_path):
    raw = rotated_same_area_box()
    original = Detection(raw["x"], raw["y"], raw["width"], raw["height"], 0, .9,
        evidence=("physical_object_envelope", "grading_label_association", "original_content_verified"))
    final, metadata = await run_worker(tmp_path, [raw], [original], content_guarded=True)
    assert "sam2_oriented_mask" in final[0].evidence
    assert metadata["object_preservation_fallback_count"] == 0
