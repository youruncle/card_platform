from __future__ import annotations

import cv2
import numpy as np

from card_platform.flatbed_detection import Detection, detect_layout


IMAGE_WIDTH = 1700
IMAGE_HEIGHT = 2338
NOMINAL_DPI = 300


def _encode(image: np.ndarray) -> bytes:
    ok, content = cv2.imencode(".png", image)
    assert ok
    return content.tobytes()


def _touching_grid(*, rows: int, columns: int) -> bytes:
    """Build a full-bed holder grid whose card interiors remain independent.

    The pale holders touch on every side, so their foreground is one sheet-sized
    component. The darker inset in each allocation cell is independent current
    pixel evidence for one physical card. At preview resolution, nominal 300 DPI
    must not make two adjacent landscape cards look like one portrait card.
    """
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 180, dtype=np.uint8)
    for row in range(rows):
        for column in range(columns):
            left = column * IMAGE_WIDTH // columns
            right = (column + 1) * IMAGE_WIDTH // columns
            top = row * IMAGE_HEIGHT // rows
            bottom = (row + 1) * IMAGE_HEIGHT // rows
            cv2.rectangle(
                image,
                (left, top),
                (right - 1, bottom - 1),
                (195, 195, 195),
                -1,
            )
            cv2.rectangle(
                image,
                (left + 60, top + 45),
                (right - 60, bottom - 45),
                (35 + row * 8, 75 + column * 12, 145),
                -1,
            )
    return _encode(image)


def _regular_prior(*, rows: int, columns: int) -> list[Detection]:
    return [
        Detection(
            column / columns,
            row / rows,
            1 / columns,
            1 / rows,
            0,
            0.9,
        )
        for row in range(rows)
        for column in range(columns)
    ]


def test_preview_resolution_uses_distributed_card_evidence_for_four_by_two_grid() -> None:
    layout = detect_layout(_touching_grid(rows=4, columns=2), dpi=NOMINAL_DPI)

    assert len(layout.detections) == 8
    assert (layout.rows, layout.columns) == (4, 2)
    assert max(item.height for item in layout.detections) <= 0.27


def test_current_four_by_two_topology_rejects_four_double_height_stationary_priors() -> None:
    content = _touching_grid(rows=4, columns=2)
    merged_priors = _regular_prior(rows=2, columns=2)

    layout = detect_layout(
        content,
        dpi=NOMINAL_DPI,
        expected_count=4,
        prior=merged_priors,
        stationary_prior=True,
    )

    assert len(layout.detections) == 8
    assert (layout.rows, layout.columns) == (4, 2)
    assert layout.transform == "independent"
    assert layout.signals["prior_topology_rejected"] == 1
    assert (
        layout.signals["prior_topology_rejection_reason"]
        == "current_layout_splits_multiple_prior_slots"
    )


def test_true_two_by_two_layout_keeps_matching_stationary_priors() -> None:
    content = _touching_grid(rows=2, columns=2)
    priors = _regular_prior(rows=2, columns=2)

    layout = detect_layout(
        content,
        dpi=NOMINAL_DPI,
        expected_count=4,
        prior=priors,
        stationary_prior=True,
    )

    assert len(layout.detections) == 4
    assert (layout.rows, layout.columns) == (2, 2)
    assert layout.transform == "identity"
    assert layout.signals.get("prior_topology_rejected", 0) == 0


def test_manual_multi_panel_priors_are_not_silently_replaced_by_auto_topology() -> None:
    content = _touching_grid(rows=4, columns=2)
    manual_priors = [
        Detection(
            item.x,
            item.y,
            item.width,
            item.height,
            item.angle,
            item.confidence,
            evidence=("manual_override",),
        )
        for item in _regular_prior(rows=2, columns=2)
    ]

    layout = detect_layout(
        content,
        dpi=NOMINAL_DPI,
        expected_count=4,
        prior=manual_priors,
        stationary_prior=True,
    )

    assert len(layout.detections) == 4
    assert layout.transform == "identity"
    assert layout.signals["prior_topology_rejected"] == 0
    assert all("manual_override" in item.evidence for item in layout.detections)


def test_one_graded_slab_with_two_content_blocks_does_not_reject_its_single_prior() -> None:
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 246, dtype=np.uint8)
    # One physical portrait slab with a label and card body that intentionally
    # appear as two separate content islands inside the clear holder.
    cv2.rectangle(image, (520, 250), (1180, 2080), (218, 218, 218), -1)
    cv2.rectangle(image, (565, 310), (1135, 650), (45, 80, 150), -1)
    cv2.rectangle(image, (565, 760), (1135, 1990), (45, 125, 80), -1)
    prior = [
        Detection(
            520 / IMAGE_WIDTH, 250 / IMAGE_HEIGHT, 660 / IMAGE_WIDTH, 1830 / IMAGE_HEIGHT, 0, 0.9
        )
    ]

    layout = detect_layout(
        _encode(image),
        dpi=NOMINAL_DPI,
        expected_count=1,
        prior=prior,
        stationary_prior=True,
    )

    assert len(layout.detections) == 1
    assert layout.transform == "identity"
    assert layout.signals.get("prior_topology_rejected", 0) == 0
