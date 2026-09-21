from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection
from card_platform.flatbed_slot_refinement import (
    current_side_edge_fallback,
    refine_back_slots,
    slot_object_evidence,
    slot_search_windows,
)


def encoded_sheet(rectangles: list[tuple[int, int, int, int]]) -> bytes:
    image = np.full((600, 600, 3), 245, dtype=np.uint8)
    for x1, y1, x2, y2 in rectangles:
        cv2.rectangle(image, (x1, y1), (x2, y2), (40, 80, 120), -1)
        cv2.rectangle(image, (x1, y1), (x2, y2), (5, 5, 5), 4)
    success, encoded = cv2.imencode(".png", image)
    assert success
    return encoded.tobytes()


def refined(item: Detection, *, x: float | None = None) -> Detection:
    return Detection(
        item.x if x is None else x,
        item.y,
        item.width,
        item.height,
        0,
        0.92,
        None,
        evidence=(*item.evidence, "sam2_box_prompt", "sam2_oriented_mask"),
    )


@pytest.mark.asyncio
async def test_empty_slot_is_reported_and_never_manufactured(monkeypatch) -> None:
    content = encoded_sheet([(35, 40, 265, 185), (335, 40, 565, 185)])
    prior = [
        Detection(0.05, 0.05, 0.40, 0.28, 0, 1),
        Detection(0.55, 0.05, 0.40, 0.28, 0, 1),
        Detection(0.05, 0.58, 0.40, 0.28, 0, 1),
    ]
    worker = AsyncMock(
        side_effect=lambda content, detections, **kwargs: (
            [refined(item) for item in detections],
            {"status": "accepted", "refined_count": len(detections)},
        )
    )
    monkeypatch.setattr(
        "card_platform.flatbed_slot_refinement.refine_with_sam2", worker
    )

    detections, metadata = await refine_back_slots(
        content,
        prior,
        python_path="python",
        worker_script="worker.py",
        checkpoint="model.pt",
        timeout_seconds=1,
    )

    assert len(detections) == 2
    assert metadata["empty_slot_indices"] == [2]
    assert metadata["missing_slot_indices"] == [2]
    assert metadata["manufactured_count"] == 0
    assert len(worker.call_args.args[1]) == 2


@pytest.mark.asyncio
async def test_failed_primary_prompt_gets_one_expanded_slot_retry(monkeypatch) -> None:
    content = encoded_sheet([(35, 40, 265, 185), (335, 40, 565, 185)])
    prior = [
        Detection(0.05, 0.05, 0.40, 0.28, 0, 1),
        Detection(0.55, 0.05, 0.40, 0.28, 0, 1),
    ]
    calls = 0

    async def fake_refinement(content, detections, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [
                refined(detections[0]),
                Detection(
                    detections[1].x,
                    detections[1].y,
                    detections[1].width,
                    detections[1].height,
                    0,
                    0.49,
                    "card_outline_unresolved",
                    evidence=(*detections[1].evidence, "sam2_fallback"),
                ),
            ], {"status": "accepted", "refined_count": 1}
        assert len(detections) == 1
        assert kwargs["prompt_detections"][0].width > detections[0].width
        return [refined(detections[0])], {"status": "accepted", "refined_count": 1}

    monkeypatch.setattr(
        "card_platform.flatbed_slot_refinement.refine_with_sam2", fake_refinement
    )

    detections, metadata = await refine_back_slots(
        content,
        prior,
        python_path="python",
        worker_script="worker.py",
        checkpoint="model.pt",
        timeout_seconds=1,
    )

    assert len(detections) == 2
    assert calls == 2
    assert "expanded_slot_retry" in detections[1].evidence
    assert metadata["second_pass"]["refined_count"] == 1


@pytest.mark.asyncio
async def test_misplaced_sam_geometry_uses_current_side_edge_fallback(monkeypatch) -> None:
    content = encoded_sheet([(35, 40, 265, 185), (335, 40, 565, 185)])
    prior = [
        Detection(0.05, 0.05, 0.40, 0.28, 0, 1),
        Detection(0.55, 0.05, 0.40, 0.28, 0, 1),
    ]
    async def misplaced_refinement(content, detections, **kwargs):
        if len(detections) == 2:
            result = [refined(detections[0], x=0.52), refined(detections[1])]
        else:
            result = [refined(detections[0], x=0.52)]
        return result, {"status": "accepted", "refined_count": len(result)}

    worker = AsyncMock(side_effect=misplaced_refinement)
    monkeypatch.setattr(
        "card_platform.flatbed_slot_refinement.refine_with_sam2", worker
    )

    detections, metadata = await refine_back_slots(
        content,
        prior,
        python_path="python",
        worker_script="worker.py",
        checkpoint="model.pt",
        timeout_seconds=1,
    )

    assert len(detections) == 2
    assert metadata["missing_slot_indices"] == []
    assert metadata["geometry_fallback_indices"] == [0]
    assert "current_side_edge_fallback" in detections[0].evidence


def test_neighbour_sliver_is_not_current_object_evidence() -> None:
    image = np.full((600, 600, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (0, 0), (600, 95), (20, 40, 80), -1)
    slot = Detection(0, 0, 1, 0.5, 0, 0)

    evidence = slot_object_evidence(image, slot)

    assert evidence.row_coverage < 0.30
    assert not evidence.object_present


def test_slot_windows_expand_but_stop_at_neighbour_midpoints() -> None:
    prior = [
        Detection(0.05, 0.10, 0.40, 0.25, 0, 1),
        Detection(0.55, 0.10, 0.40, 0.25, 0, 1),
    ]

    left, right = slot_search_windows(prior)

    assert left.x < prior[0].x
    assert right.x < prior[1].x
    overlap = left.x + left.width - right.x
    assert 0 < overlap < prior[0].width * 0.30
    assert left.failure == "search_window_only"


@pytest.mark.asyncio
async def test_source_edge_card_is_recovered_after_sam_fallback(monkeypatch) -> None:
    content = encoded_sheet([(330, 75, 599, 255)])
    prior = [Detection(0.53, 0.10, 0.47, 0.32, 0, 1)]

    async def unresolved(content, detections, **kwargs):
        return [
            Detection(
                item.x,
                item.y,
                item.width,
                item.height,
                item.angle,
                0.49,
                "card_outline_unresolved",
                evidence=(*item.evidence, "sam2_fallback"),
            )
            for item in detections
        ], {"status": "fallback", "refined_count": 0}

    monkeypatch.setattr(
        "card_platform.flatbed_slot_refinement.refine_with_sam2", unresolved
    )

    detections, metadata = await refine_back_slots(
        content,
        prior,
        python_path="python",
        worker_script="worker.py",
        checkpoint="model.pt",
        timeout_seconds=1,
    )

    assert len(detections) == 1
    assert detections[0].x > prior[0].x
    assert detections[0].x + detections[0].width == pytest.approx(1.0)
    assert detections[0].failure == "source_edge_contact"
    assert detections[0].points != prior[0].points
    assert "current_side_edge_fallback" in detections[0].evidence
    assert metadata["geometry_fallback_indices"] == [0]
    assert metadata["manufactured_count"] == 0


@pytest.mark.asyncio
async def test_fully_empty_slot_does_not_call_ml_or_edge_fallback(monkeypatch) -> None:
    success, encoded = cv2.imencode(
        ".png", np.full((600, 600, 3), 245, dtype=np.uint8)
    )
    assert success
    worker = AsyncMock()
    monkeypatch.setattr(
        "card_platform.flatbed_slot_refinement.refine_with_sam2", worker
    )

    detections, metadata = await refine_back_slots(
        encoded.tobytes(),
        [Detection(0.5, 0.5, 0.45, 0.30, 0, 1)],
        python_path="python",
        worker_script="worker.py",
        checkpoint="model.pt",
        timeout_seconds=1,
    )

    assert detections == []
    assert metadata["status"] == "empty"
    assert metadata["empty_slot_indices"] == [0]
    assert metadata["manufactured_count"] == 0
    worker.assert_not_called()


def test_current_edge_fallback_rejects_true_empty_source_edge_slot() -> None:
    image = np.full((600, 600, 3), 245, dtype=np.uint8)
    prior = Detection(0.53, 0.10, 0.47, 0.32, 0, 1)
    window = slot_search_windows([prior])[0]

    assert current_side_edge_fallback(image, prior, window, slot_index=0) is None
