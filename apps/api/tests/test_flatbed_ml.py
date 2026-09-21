import json
import sys
from pathlib import Path

import pytest

from card_platform.flatbed_detection import Detection
from card_platform.flatbed_ml import (
    _expand_safety_margin,
    _expanded_oriented_geometry,
    refine_with_sam2,
)


def worker(tmp_path: Path, payload: dict) -> tuple[str, str]:
    script = tmp_path / "worker.py"
    script.write_text(
        "import sys\nsys.stdin.buffer.read()\nprint(" + repr(json.dumps(payload)) + ")\n"
    )
    checkpoint = tmp_path / "model.pt"
    checkpoint.write_bytes(b"model")
    return str(script), str(checkpoint)


@pytest.mark.asyncio
async def test_sam2_refinement_accepts_same_count_safe_geometry(tmp_path: Path) -> None:
    original = [Detection(0.0, 0.0, 0.48, 0.48, 0, 0.8)]
    script, checkpoint = worker(
        tmp_path,
        {
            "detections": [
                {
                    "x": 0.02,
                    "y": 0.03,
                    "width": 0.44,
                    "height": 0.42,
                    "confidence": 0.97,
                    "refined": True,
                }
            ],
            "inference_seconds": 0.5,
        },
    )

    detections, metadata = await refine_with_sam2(
        b"image",
        original,
        python_path=sys.executable,
        worker_script=script,
        checkpoint=checkpoint,
        timeout_seconds=2,
    )

    assert metadata["status"] == "accepted"
    assert detections[0].x < 0.02
    assert detections[0].y < 0.03
    assert detections[0].width > 0.44
    assert detections[0].height > 0.42
    assert "sam2_box_prompt" in detections[0].evidence
    assert "sam2_safety_margin" in detections[0].evidence


@pytest.mark.asyncio
async def test_sam2_soft_prompt_falls_back_to_current_side_geometry(
    tmp_path: Path,
) -> None:
    current = [
        Detection(0.5, 0.5, 0.5, 0.25, 0, 0.46, "card_outline_unresolved")
    ]
    prompt = [Detection(0.54, 0.48, 0.46, 0.27, 0, 0.46)]
    script, checkpoint = worker(
        tmp_path,
        {
            "detections": [
                {
                    "x": 0.54,
                    "y": 0.48,
                    "width": 0.46,
                    "height": 0.27,
                    "refined": False,
                }
            ]
        },
    )

    detections, metadata = await refine_with_sam2(
        b"image",
        current,
        prompt_detections=prompt,
        python_path=sys.executable,
        worker_script=script,
        checkpoint=checkpoint,
        timeout_seconds=2,
    )

    assert metadata["fallback_count"] == 1
    assert detections[0].x == current[0].x
    assert detections[0].y == current[0].y
    assert "front_prior_soft_prompt" in detections[0].evidence


@pytest.mark.asyncio
async def test_sam2_refinement_cannot_drop_associated_grading_label(
    tmp_path: Path,
) -> None:
    original = [
        Detection(
            0.0,
            0.25,
            0.60,
            0.26,
            0,
            0.9,
            evidence=(
                "mixed_format_instance",
                "grading_label_association",
                "sam2_box_prompt",
            ),
        )
    ]
    script, checkpoint = worker(
        tmp_path,
        {
            "detections": [
                {
                    "x": 0.11,
                    "y": 0.26,
                    "width": 0.49,
                    "height": 0.25,
                    "confidence": 0.97,
                    "refined": True,
                }
            ]
        },
    )

    detections, metadata = await refine_with_sam2(
        b"image",
        original,
        python_path=sys.executable,
        worker_script=script,
        checkpoint=checkpoint,
        timeout_seconds=2,
    )

    assert metadata["fallback_count"] == 1
    assert detections[0].x == original[0].x
    assert detections[0].width == original[0].width
    assert "sam2_label_preservation_fallback" in detections[0].evidence


@pytest.mark.asyncio
async def test_edge_consensus_refinement_is_reported_separately(tmp_path: Path) -> None:
    original = [Detection(0.5, 0.7, 0.5, 0.3, 0, 0.8)]
    script, checkpoint = worker(
        tmp_path,
        {
            "detections": [
                {
                    "x": 0.56,
                    "y": 0.72,
                    "width": 0.44,
                    "height": 0.27,
                    "angle": -5.0,
                    "points": [
                        [0.59, 0.99],
                        [0.56, 0.76],
                        [1.0, 0.72],
                        [1.0, 0.96],
                    ],
                    "confidence": 0.92,
                    "refined": True,
                    "method": "edge_consensus",
                }
            ]
        },
    )

    detections, metadata = await refine_with_sam2(
        b"image",
        original,
        python_path=sys.executable,
        worker_script=script,
        checkpoint=checkpoint,
        timeout_seconds=2,
    )

    assert metadata["status"] == "accepted"
    assert metadata["edge_refined_count"] == 1
    assert "edge_consensus_oriented" in detections[0].evidence
    assert "sam2_oriented_mask" not in detections[0].evidence


def test_sam2_safety_margin_is_narrow_and_clamped_to_scan_bed() -> None:
    assert _expand_safety_margin(0.2, 0.3, 0.4, 0.2) == pytest.approx(
        (0.1952, 0.2976, 0.4096, 0.2048)
    )
    left, top, width, height = _expand_safety_margin(0.0, 0.0, 0.4, 0.2)
    assert (left, top) == (0.0, 0.0)
    assert width > 0.4
    assert height > 0.2


def test_sam2_oriented_geometry_preserves_rotation_and_adds_margin() -> None:
    raw_points = [[0.2, 0.31], [0.6, 0.29], [0.61, 0.49], [0.21, 0.51]]

    x, y, width, height, points = _expanded_oriented_geometry(raw_points, (0.2, 0.29, 0.41, 0.22))

    assert x < 0.2
    assert y < 0.29
    assert width > 0.41
    assert height > 0.22
    assert points[0][1] > points[1][1]


@pytest.mark.asyncio
async def test_sam2_refinement_rejects_overlapping_result(tmp_path: Path) -> None:
    original = [
        Detection(0.0, 0.0, 0.45, 0.45, 0, 0.8),
        Detection(0.55, 0.0, 0.45, 0.45, 0, 0.8),
    ]
    script, checkpoint = worker(
        tmp_path,
        {
            "detections": [
                {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.4, "refined": True},
                {"x": 0.4, "y": 0.1, "width": 0.5, "height": 0.4, "refined": True},
            ]
        },
    )

    detections, metadata = await refine_with_sam2(
        b"image",
        original,
        python_path=sys.executable,
        worker_script=script,
        checkpoint=checkpoint,
        timeout_seconds=2,
    )

    assert metadata["status"] == "accepted"
    assert metadata["refined_count"] == 1
    assert metadata["fallback_count"] == 1
    assert sum("sam2_overlap_fallback" in item.evidence for item in detections) == 1


@pytest.mark.asyncio
async def test_sam2_refinement_skips_missing_runtime(tmp_path: Path) -> None:
    original = [Detection(0.0, 0.0, 0.5, 0.5, 0, 0.8)]

    detections, metadata = await refine_with_sam2(
        b"image",
        original,
        python_path=str(tmp_path / "missing-python"),
        worker_script=str(tmp_path / "missing-worker"),
        checkpoint=str(tmp_path / "missing-model"),
        timeout_seconds=2,
    )

    assert metadata == {"status": "skipped", "reason": "runtime_unavailable"}
    assert detections == original


@pytest.mark.asyncio
async def test_unresolved_grid_fallback_is_low_confidence_and_explainable(
    tmp_path: Path,
) -> None:
    original = [
        Detection(
            0.5,
            0.75,
            0.5,
            0.25,
            0,
            0.86,
            "card_outline_unresolved",
            evidence=("full_bed_grid", "cell_extent_fallback"),
        )
    ]
    script, checkpoint = worker(
        tmp_path,
        {
            "detections": [
                {
                    "x": 0.5,
                    "y": 0.75,
                    "width": 0.5,
                    "height": 0.25,
                    "confidence": 0.0,
                    "refined": False,
                }
            ]
        },
    )

    detections, metadata = await refine_with_sam2(
        b"image",
        original,
        python_path=sys.executable,
        worker_script=script,
        checkpoint=checkpoint,
        timeout_seconds=2,
    )

    assert metadata["fallback_count"] == 1
    assert detections[0].confidence == 0.49
    assert detections[0].failure == "card_outline_unresolved"
