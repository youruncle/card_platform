from __future__ import annotations

from typing import Literal

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis, detect_layout


IMAGE_WIDTH = 1100
IMAGE_HEIGHT = 1600
CELL_WIDTH = IMAGE_WIDTH // 2
CELL_HEIGHT = IMAGE_HEIGHT // 4

CardStyle = Literal["colour_front", "white_back"]
PixelBox = tuple[int, int, int, int]


def _encode(image: np.ndarray) -> bytes:
    ok, content = cv2.imencode(".png", image)
    assert ok
    return content.tobytes()


def _draw_toploader(
    image: np.ndarray,
    left: int,
    top: int,
    *,
    style: CardStyle,
    variation: int,
) -> PixelBox:
    """Draw one generic landscape card and return its visible card-body box."""
    right = left + CELL_WIDTH
    bottom = top + CELL_HEIGHT
    cv2.rectangle(
        image,
        (left, top),
        (right - 1, bottom - 1),
        (200, 200, 200),
        -1,
    )

    body = (left + 34, top + 28, right - 34, bottom - 28)
    body_left, body_top, body_right, body_bottom = body
    if style == "colour_front":
        cv2.rectangle(
            image,
            (body_left, body_top),
            (body_right - 1, body_bottom - 1),
            (35 + variation * 4, 70, 125 + variation * 6),
            -1,
        )
        cv2.rectangle(
            image,
            (left + 85, top + 75),
            (right - 85, bottom - 75),
            (155, 105 + variation * 3, 55),
            -1,
        )
    else:
        # A white card back remains objectively visible through its closed
        # outline and distributed print, despite carrying little saturation.
        cv2.rectangle(
            image,
            (body_left, body_top),
            (body_right - 1, body_bottom - 1),
            (238, 238, 238),
            -1,
        )
        cv2.rectangle(
            image,
            (body_left + 14, body_top + 14),
            (body_right - 15, body_bottom - 15),
            (155, 155, 155),
            4,
        )
        for line_y in range(top + 75, bottom - 60, 42):
            cv2.line(
                image,
                (left + 90, line_y),
                (right - 90, line_y),
                (170, 170, 170),
                3,
            )
        cv2.rectangle(
            image,
            (left + 210, top + 140),
            (left + 340, top + 260),
            (180, 165, 155),
            5,
        )
    return body


def _partial_sheet(
    *,
    last_left: int,
    style: CardStyle,
    neighbour_sliver: bool = False,
) -> tuple[bytes, PixelBox]:
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 224, dtype=np.uint8)
    variation = 0
    for row in range(3):
        for column in range(2):
            _draw_toploader(
                image,
                column * CELL_WIDTH,
                row * CELL_HEIGHT,
                style=style,
                variation=variation,
            )
            variation += 1

    last_body = _draw_toploader(
        image,
        last_left,
        3 * CELL_HEIGHT,
        style=style,
        variation=variation,
    )
    if neighbour_sliver:
        # An otherwise empty allocation slot can contain a narrow holder edge
        # or scanner shadow. It has no card-sized two-dimensional body.
        sliver_x = CELL_WIDTH + 45
        cv2.line(
            image,
            (sliver_x, 3 * CELL_HEIGHT + 12),
            (sliver_x, IMAGE_HEIGHT - 1),
            (125, 125, 125),
            8,
        )
    return _encode(image), last_body


def _complete_sheet(*, style: CardStyle) -> tuple[bytes, tuple[PixelBox, PixelBox]]:
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 224, dtype=np.uint8)
    bottom_bodies: list[PixelBox] = []
    variation = 0
    for row in range(4):
        for column in range(2):
            body = _draw_toploader(
                image,
                column * CELL_WIDTH,
                row * CELL_HEIGHT,
                style=style,
                variation=variation,
            )
            if row == 3:
                bottom_bodies.append(body)
            variation += 1
    assert len(bottom_bodies) == 2
    return _encode(image), (bottom_bodies[0], bottom_bodies[1])


def _bottom_row(layout: LayoutHypothesis) -> list[Detection]:
    return [
        item
        for item in layout.detections
        if (item.y + item.height / 2) * IMAGE_HEIGHT > 3 * CELL_HEIGHT
    ]


def _body_coverage(detection: Detection, expected: PixelBox) -> float:
    left = detection.x * IMAGE_WIDTH
    top = detection.y * IMAGE_HEIGHT
    right = (detection.x + detection.width) * IMAGE_WIDTH
    bottom = (detection.y + detection.height) * IMAGE_HEIGHT
    expected_left, expected_top, expected_right, expected_bottom = expected
    intersection_width = max(0.0, min(right, expected_right) - max(left, expected_left))
    intersection_height = max(0.0, min(bottom, expected_bottom) - max(top, expected_top))
    expected_area = (expected_right - expected_left) * (expected_bottom - expected_top)
    return intersection_width * intersection_height / expected_area


@pytest.mark.parametrize(
    "last_left",
    (
        pytest.param(170, id="left_of_seam"),
        pytest.param(275, id="centred_on_seam"),
        pytest.param(380, id="right_of_seam"),
    ),
)
@pytest.mark.parametrize("style", ("colour_front", "white_back"))
def test_offset_partial_last_row_is_one_complete_card(
    last_left: int,
    style: CardStyle,
) -> None:
    content, last_body = _partial_sheet(last_left=last_left, style=style)

    layout = detect_layout(content, dpi=300)
    bottom = _bottom_row(layout)

    assert len(layout.detections) == 7
    assert len(bottom) == 1
    assert _body_coverage(bottom[0], last_body) >= 0.96
    assert (layout.rows, layout.columns) == (4, 2)
    assert layout.signals["row_local_body_recovery"] == 1 or (
        layout.signals["grid_oversegmentation_rejected"] == 1
        and "physical_object_envelope" in bottom[0].evidence
    )


@pytest.mark.parametrize("style", ("colour_front", "white_back"))
def test_complete_two_by_four_sheet_does_not_merge_bottom_neighbours(style: CardStyle) -> None:
    content, bottom_bodies = _complete_sheet(style=style)

    layout = detect_layout(content, dpi=300)
    bottom = _bottom_row(layout)

    assert len(layout.detections) == 8
    assert len(bottom) == 2
    assert all(max(_body_coverage(item, body) for item in bottom) >= 0.96 for body in bottom_bodies)
    assert (layout.rows, layout.columns) == (4, 2)


def test_thin_neighbour_edge_in_empty_slot_does_not_become_a_card() -> None:
    content, last_body = _partial_sheet(
        last_left=0,
        style="colour_front",
        neighbour_sliver=True,
    )

    layout = detect_layout(content, dpi=300)
    bottom = _bottom_row(layout)

    assert len(layout.detections) == 7
    assert len(bottom) == 1
    assert _body_coverage(bottom[0], last_body) >= 0.96
    assert (layout.rows, layout.columns) == (4, 2)
