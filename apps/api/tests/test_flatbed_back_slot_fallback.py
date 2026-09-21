from __future__ import annotations

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection
from card_platform.flatbed_slot_refinement import refine_back_slots


IMAGE_WIDTH = 1000
IMAGE_HEIGHT = 1400
FULL_BED_WIDTH = 2480
FULL_BED_HEIGHT = 3507

PixelBox = tuple[int, int, int, int]


def _encode_sheet(
    boxes: list[PixelBox],
    *,
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
) -> bytes:
    image = np.full((height, width, 3), 245, dtype=np.uint8)
    for index, (left, top, right, bottom) in enumerate(boxes):
        cv2.rectangle(
            image,
            (left, top),
            (right - 1, bottom - 1),
            (35 + index * 4, 75, 125),
            -1,
        )
        cv2.rectangle(
            image,
            (left, top),
            (right - 1, bottom - 1),
            (5, 5, 5),
            5,
        )
        cv2.rectangle(
            image,
            (left + 55, top + 45),
            (right - 55, bottom - 45),
            (150, 105 + index * 3, 60),
            -1,
        )
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _full_bed_with_strong_capacity_seam() -> tuple[bytes, PixelBox, int]:
    image = np.full((FULL_BED_HEIGHT, FULL_BED_WIDTH, 3), 224, dtype=np.uint8)
    x_edges = (0, FULL_BED_WIDTH // 2, FULL_BED_WIDTH)
    y_edges = [round(index * FULL_BED_HEIGHT / 4) for index in range(5)]
    for row in range(3):
        for column in range(2):
            left, right = x_edges[column], x_edges[column + 1]
            top, bottom = y_edges[row], y_edges[row + 1]
            # Touching clear holders force the independent detector through
            # its fixed-capacity full-bed grid route.
            cv2.rectangle(
                image,
                (left, top),
                (right - 1, bottom - 1),
                (200, 200, 200),
                -1,
            )
            cv2.rectangle(
                image,
                (left + 50, top + 45),
                (right - 51, bottom - 46),
                (35 + row * 8 + column * 3, 75, 125),
                -1,
            )
            cv2.rectangle(
                image,
                (left + 50, top + 45),
                (right - 51, bottom - 46),
                (5, 5, 5),
                12,
            )

    last_body = (0, 2665, 1190, FULL_BED_HEIGHT)
    left, top, right, bottom = last_body
    cv2.rectangle(
        image,
        (left, top),
        (right - 1, bottom - 1),
        (59, 75, 125),
        -1,
    )
    cv2.rectangle(
        image,
        (left, top),
        (right - 1, bottom - 1),
        (5, 5, 5),
        12,
    )
    capacity_seam = y_edges[3]
    # This exact 0.75 allocation seam is stronger than the physical holder
    # top at about 0.76. It is an ownership boundary, not a valid crop edge.
    cv2.line(
        image,
        (0, capacity_seam),
        (FULL_BED_WIDTH // 2 - 1, capacity_seam),
        (0, 0, 0),
        8,
    )
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes(), last_body, capacity_seam


def _seven_front_slots() -> list[Detection]:
    slots = [
        Detection(left / IMAGE_WIDTH, top / IMAGE_HEIGHT, 0.42, 0.20, 0, 1)
        for top in (20, 360, 700)
        for left in (40, 540)
    ]
    # The final front card was farther right and slightly taller than the
    # current back body. It is a search hint, not valid fallback crop geometry.
    slots.append(Detection(0.10, 0.77, 0.44, 0.23, 0, 1))
    return slots


def _seven_back_bodies() -> list[PixelBox]:
    bodies = [(left, top, left + 420, top + 280) for top in (20, 360, 700) for left in (40, 540)]
    # This body moved left and reaches two trustworthy source-image edges.
    bodies.append((0, 1092, 440, IMAGE_HEIGHT))
    return bodies


def _slot_index(item: Detection) -> int:
    marker = next(value for value in item.evidence if value.startswith("front_slot_index:"))
    return int(marker.split(":", maxsplit=1)[1])


def _sam_success(item: Detection) -> Detection:
    return Detection(
        item.x,
        item.y,
        item.width,
        item.height,
        item.angle,
        0.92,
        None,
        item.points,
        (*item.evidence, "sam2_box_prompt", "sam2_oriented_mask"),
    )


def _sam_no_mask(item: Detection) -> Detection:
    return Detection(
        item.x,
        item.y,
        item.width,
        item.height,
        item.angle,
        0.49,
        "card_outline_unresolved",
        item.points,
        (*item.evidence, "sam2_fallback"),
    )


def _install_sam_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    no_mask_slots: set[int],
) -> list[tuple[int, ...]]:
    calls: list[tuple[int, ...]] = []

    async def fake_refinement(content: bytes, detections: list[Detection], **kwargs):
        del content, kwargs
        indices = tuple(_slot_index(item) for item in detections)
        calls.append(indices)
        results = [
            _sam_no_mask(item) if index in no_mask_slots else _sam_success(item)
            for index, item in zip(indices, detections, strict=True)
        ]
        refined_count = sum(item.failure is None for item in results)
        return results, {
            "status": "accepted" if refined_count == len(results) else "fallback",
            "refined_count": refined_count,
            "fallback_count": len(results) - refined_count,
        }

    monkeypatch.setattr(
        "card_platform.flatbed_slot_refinement.refine_with_sam2",
        fake_refinement,
    )
    return calls


async def _refine(content: bytes, prior: list[Detection]):
    return await refine_back_slots(
        content,
        prior,
        python_path="unused-python",
        worker_script="unused-worker.py",
        checkpoint="unused-model.pt",
        timeout_seconds=1,
    )


def _coverage(
    detection: Detection,
    expected: PixelBox,
    *,
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
) -> float:
    left = detection.x * width
    top = detection.y * height
    right = (detection.x + detection.width) * width
    bottom = (detection.y + detection.height) * height
    expected_left, expected_top, expected_right, expected_bottom = expected
    intersection_width = max(0.0, min(right, expected_right) - max(left, expected_left))
    intersection_height = max(0.0, min(bottom, expected_bottom) - max(top, expected_top))
    expected_area = (expected_right - expected_left) * (expected_bottom - expected_top)
    return intersection_width * intersection_height / expected_area


def _intersection_over_smaller(left: Detection, right: Detection) -> float:
    intersection_width = max(
        0.0,
        min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
    )
    intersection_height = max(
        0.0,
        min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
    )
    intersection = intersection_width * intersection_height
    smaller = min(left.width * left.height, right.width * right.height)
    return intersection / smaller if smaller else 0.0


@pytest.mark.asyncio
async def test_current_detection_keeps_shifted_seventh_back_after_two_sam_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _seven_front_slots()
    current_bodies = _seven_back_bodies()
    calls = _install_sam_stub(monkeypatch, no_mask_slots={6})

    detections, metadata = await _refine(_encode_sheet(current_bodies), prior)

    assert calls == [(0, 1, 2, 3, 4, 5, 6), (6,)]
    assert len(detections) == 7
    assert metadata["localized_slot_indices"] == list(range(7))
    assert metadata["missing_slot_indices"] == []
    assert metadata["current_detection_count"] == 7
    assert metadata["current_candidate_fallback_indices"] == [6]
    assert metadata["manufactured_count"] == 0

    last = next(item for item in detections if _slot_index(item) == 6)
    assert _coverage(last, current_bodies[6]) >= 0.95
    assert last.x < prior[6].x - 0.05
    assert last.y + last.height == pytest.approx(1.0)
    assert "current_side_independent_detection" in last.evidence
    assert "current_candidate_edge_fallback" in last.evidence


@pytest.mark.asyncio
async def test_blank_seventh_back_slot_is_not_restored_from_front_prior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior = _seven_front_slots()
    current_bodies = _seven_back_bodies()[:6]
    calls = _install_sam_stub(monkeypatch, no_mask_slots=set())

    detections, metadata = await _refine(_encode_sheet(current_bodies), prior)

    assert calls == [(0, 1, 2, 3, 4, 5)]
    assert len(detections) == 6
    assert metadata["empty_slot_indices"] == [6]
    assert metadata["missing_slot_indices"] == [6]
    assert metadata["manufactured_count"] == 0
    assert not any(_slot_index(item) == 6 for item in detections)


@pytest.mark.asyncio
async def test_current_detection_recovers_one_card_crossing_a_capacity_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_body = (310, 950, 750, 1286)
    # Both current x-edges moved beyond the old prior-centred gradient radius,
    # while the complete body remains inside the expanded ownership window.
    prior = [Detection(0.24, 0.65, 0.44, 0.24, 0, 1)]
    calls = _install_sam_stub(monkeypatch, no_mask_slots={0})

    detections, metadata = await _refine(_encode_sheet([current_body]), prior)

    assert calls == [(0,), (0,)]
    assert len(detections) == 1
    assert metadata["current_candidate_fallback_indices"] == [0]
    assert metadata["missing_slot_indices"] == []
    assert _coverage(detections[0], current_body) >= 0.95
    assert detections[0].x < 0.5 < detections[0].x + detections[0].width


@pytest.mark.asyncio
async def test_current_candidate_does_not_take_stronger_edge_from_card_above(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content, last_body, capacity_seam = _full_bed_with_strong_capacity_seam()
    calls = _install_sam_stub(monkeypatch, no_mask_slots={6})

    detections, metadata = await _refine(content, _seven_front_slots())

    assert calls == [(0, 1, 2, 3, 4, 5, 6), (6,)]
    assert len(detections) == 7
    assert metadata["current_candidate_fallback_indices"] == [6]
    last = next(item for item in detections if _slot_index(item) == 6)
    assert (
        _coverage(
            last,
            last_body,
            width=FULL_BED_WIDTH,
            height=FULL_BED_HEIGHT,
        )
        >= 0.95
    )
    assert last.y * FULL_BED_HEIGHT >= last_body[1] - 12
    # Normal 3% short-axis crop padding must still stop below the preceding
    # card, otherwise the exported seventh crop contains a strip of card six.
    padded_top = (last.y - last.height * 0.03) * FULL_BED_HEIGHT
    assert padded_top >= capacity_seam
    assert "current_candidate_inward_edges" in last.evidence
    assert "current_grid_seam_excluded" in last.evidence


@pytest.mark.asyncio
async def test_independent_fallback_keeps_adjacent_cards_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_height = 700
    current_bodies = [(20, 180, 470, 500), (530, 180, 980, 500)]
    prior = [
        Detection(0.08, 0.27, 0.40, 0.43, 0, 1),
        Detection(0.52, 0.27, 0.40, 0.43, 0, 1),
    ]
    calls = _install_sam_stub(monkeypatch, no_mask_slots={0, 1})

    detections, metadata = await _refine(
        _encode_sheet(current_bodies, height=image_height),
        prior,
    )

    assert calls == [(0, 1), (0, 1)]
    assert len(detections) == 2
    assert metadata["current_candidate_fallback_indices"] == [0, 1]
    assert metadata["missing_slot_indices"] == []
    by_slot = {_slot_index(item): item for item in detections}
    assert set(by_slot) == {0, 1}
    assert all(
        _coverage(by_slot[index], body, height=image_height) >= 0.95
        for index, body in enumerate(current_bodies)
    )
    assert _intersection_over_smaller(by_slot[0], by_slot[1]) <= 0.18
