from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis, detect_layout


IMAGE_WIDTH = 1240
IMAGE_HEIGHT = 1754
TEST_DPI = 150
CAPACITY_X_EDGES = (0, IMAGE_WIDTH // 2, IMAGE_WIDTH)
CAPACITY_Y_EDGES = tuple(round(index * IMAGE_HEIGHT / 4) for index in range(5))

CardSpec = tuple[float, float, float, float, str, float]


def _draw_card(
    image: np.ndarray,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
    style: str,
    angle: float = 0.0,
    variation: int = 0,
) -> np.ndarray:
    center = (left + width / 2, top + height / 2)
    points = np.rint(cv2.boxPoints((center, (width, height), angle))).astype(np.int32)
    fill = (238, 238, 238) if style == "low_saturation_back" else (40 + variation * 5, 75, 130)
    border = (145, 145, 145) if style == "low_saturation_back" else (5, 5, 5)
    cv2.fillConvexPoly(image, points, fill)
    cv2.polylines(image, [points], True, border, 8)

    inner = np.rint(
        cv2.boxPoints(
            (
                center,
                (width * 0.56, height * 0.54),
                angle,
            )
        )
    ).astype(np.int32)
    if style == "low_saturation_back":
        cv2.polylines(image, [inner], True, (165, 165, 165), 5)
        radians = math.radians(angle)
        long_axis = np.asarray((math.cos(radians), math.sin(radians)))
        short_axis = np.asarray((-math.sin(radians), math.cos(radians)))
        for offset in (-0.25, -0.08, 0.10, 0.27):
            line_center = np.asarray(center) + short_axis * height * offset
            start = np.rint(line_center - long_axis * width * 0.32).astype(int)
            end = np.rint(line_center + long_axis * width * 0.32).astype(int)
            cv2.line(image, tuple(start), tuple(end), (165, 165, 165), 4)
    else:
        cv2.fillConvexPoly(image, inner, (155, 105 + variation * 3, 60))

    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, points, 1)
    return mask


def _object_sheet(
    bottom_cards: list[CardSpec],
    *,
    second_row_body_bottom: float = 0.505,
    second_row_holder_bottom: float = 0.50,
) -> tuple[bytes, list[np.ndarray]]:
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 224, dtype=np.uint8)
    masks: list[np.ndarray] = []
    for row in range(2):
        for column in range(2):
            left, right = CAPACITY_X_EDGES[column : column + 2]
            top, bottom = CAPACITY_Y_EDGES[row : row + 2]
            holder_bottom = round(IMAGE_HEIGHT * second_row_holder_bottom) if row == 1 else bottom
            # Four touching clear holders make the foreground look like one
            # large sheet and exercise the detector's capacity-grid route.
            cv2.rectangle(
                image,
                (left, top),
                (right - 1, holder_bottom - 1),
                (200, 200, 200),
                -1,
            )
            body_bottom = round(IMAGE_HEIGHT * second_row_body_bottom) if row == 1 else bottom - 22
            masks.append(
                _draw_card(
                    image,
                    left=left + 25,
                    top=top + 22,
                    width=right - left - 50,
                    height=body_bottom - top - 22,
                    style="colour_front",
                    variation=row * 2 + column,
                )
            )

    for variation, (left, top, width, height, style, angle) in enumerate(
        bottom_cards,
        start=4,
    ):
        masks.append(
            _draw_card(
                image,
                left=round(left * IMAGE_WIDTH),
                top=round(top * IMAGE_HEIGHT),
                width=round(width * IMAGE_WIDTH),
                height=round(height * IMAGE_HEIGHT),
                style=style,
                angle=angle,
                variation=variation,
            )
        )

    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes(), masks


def _full_grid_with_cross_row_artifact(
    artifact: str,
) -> tuple[bytes, list[np.ndarray]]:
    assert artifact in {"holder_tail", "cross_row_instance"}
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 224, dtype=np.uint8)
    masks: list[np.ndarray] = []
    for row in range(4):
        for column in range(2):
            left, right = CAPACITY_X_EDGES[column : column + 2]
            top, bottom = CAPACITY_Y_EDGES[row : row + 2]
            cv2.rectangle(
                image,
                (left, top),
                (right - 1, bottom - 1),
                (200, 200, 200),
                -1,
            )
            if artifact == "holder_tail" and (row, column) == (2, 0):
                # Distributed monochrome print proves that this slot contains
                # a card without providing a closed, card-sized contour.
                mask = np.zeros(image.shape[:2], dtype=np.uint8)
                cv2.rectangle(
                    mask,
                    (left + 25, top + 45),
                    (right - 25, bottom - 22),
                    1,
                    -1,
                )
                masks.append(mask)
                for line_y in np.linspace(top + 65, bottom - 55, 7).astype(int):
                    cv2.line(
                        image,
                        (left + 75, line_y),
                        (right - 75, line_y),
                        (165, 165, 165),
                        5,
                    )
                for mark_x in (left + 130, left + 300, left + 455):
                    cv2.rectangle(
                        image,
                        (mark_x, top + 90),
                        (mark_x + 34, top + 135),
                        (170, 170, 170),
                        3,
                    )
                continue

            masks.append(
                _draw_card(
                    image,
                    left=left + 25,
                    top=top + 22,
                    width=right - left - 50,
                    height=bottom - top - 44,
                    style="colour_front",
                    variation=row * 2 + column,
                )
            )
            if artifact == "cross_row_instance" and column == 0 and row in (2, 3):
                fill = (115, 70, 25) if row == 2 else (45, 35, 155)
                accent = (30, 145, 175) if row == 2 else (135, 45, 85)
                cv2.rectangle(
                    image,
                    (left + 36, top + 34),
                    (right - 36, bottom - 34),
                    fill,
                    -1,
                )
                cv2.rectangle(
                    image,
                    (left + 120, top + 85),
                    (right - 120, bottom - 85),
                    accent,
                    -1,
                )

    left, right = CAPACITY_X_EDGES[:2]
    if artifact == "holder_tail":
        top, seam = CAPACITY_Y_EDGES[1:3]
        tail = round((IMAGE_HEIGHT / 4) * 0.08)
        envelope_top = top + 10
        envelope_bottom = seam + tail
    else:
        envelope_top = round(IMAGE_HEIGHT * 0.67)
        envelope_bottom = round(IMAGE_HEIGHT * 0.95)
    # A neutral clear-holder rim creates a plausible broad observation without
    # erasing the independently visible card content on either side of it.
    cv2.rectangle(
        image,
        (left + 12, envelope_top),
        (right - 12, envelope_bottom),
        (210, 210, 210),
        30,
    )
    cv2.rectangle(
        image,
        (left + 12, envelope_top),
        (right - 12, envelope_bottom),
        (125, 125, 125),
        5,
    )

    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes(), masks


def _detection_mask(item: Detection) -> np.ndarray:
    points = item.points or (
        (item.x, item.y),
        (item.x + item.width, item.y),
        (item.x + item.width, item.y + item.height),
        (item.x, item.y + item.height),
    )
    pixels = np.asarray(
        [(round(x * IMAGE_WIDTH), round(y * IMAGE_HEIGHT)) for x, y in points],
        dtype=np.int32,
    )
    pixels[:, 0] = np.clip(pixels[:, 0], 0, IMAGE_WIDTH - 1)
    pixels[:, 1] = np.clip(pixels[:, 1], 0, IMAGE_HEIGHT - 1)
    mask = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint8)
    cv2.fillConvexPoly(mask, pixels, 1)
    return mask


def _assert_one_detection_per_object(
    layout: LayoutHypothesis,
    expected_masks: list[np.ndarray],
) -> list[Detection]:
    assert len(layout.detections) == len(expected_masks)
    detection_masks = [_detection_mask(item) for item in layout.detections]
    matched_indices: list[int] = []
    for object_index, expected in enumerate(expected_masks):
        expected_area = int(np.count_nonzero(expected))
        coverage = [
            float(np.count_nonzero(expected & detected) / expected_area)
            for detected in detection_masks
        ]
        assert max(coverage, default=0.0) >= 0.95, (object_index, coverage)
        # Two grid fragments or duplicate boxes must not both masquerade as a
        # complete match for one physical object.
        assert sum(value >= 0.95 for value in coverage) == 1
        matched_indices.append(int(np.argmax(coverage)))

    assert len(set(matched_indices)) == len(expected_masks)
    for object_index, detection_index in enumerate(matched_indices):
        neighbours = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint8)
        for neighbour_index, expected in enumerate(expected_masks):
            if neighbour_index != object_index:
                neighbours |= expected
        object_area = int(np.count_nonzero(expected_masks[object_index]))
        neighbour_contamination = float(
            np.count_nonzero(detection_masks[detection_index] & neighbours) / object_area
        )
        assert neighbour_contamination <= 0.03, (
            object_index,
            neighbour_contamination,
        )
    return [layout.detections[index] for index in matched_indices]


@pytest.mark.parametrize(
    ("spec", "requires_instance_recovery"),
    (
        pytest.param(
            (0.27, 0.525, 0.46, 0.20, "colour_front", 0.0),
            False,
            id="vertical_capacity_seam_only",
        ),
        pytest.param(
            (0.02, 0.64, 0.46, 0.22, "colour_front", 0.0),
            True,
            id="horizontal_capacity_seam_only",
        ),
        pytest.param(
            (0.17, 0.59, 0.46, 0.22, "colour_front", 0.0),
            True,
            id="dual_seams_upper_left",
        ),
        pytest.param(
            (0.27, 0.64, 0.46, 0.22, "colour_front", 0.0),
            True,
            id="dual_seams_centred",
        ),
        pytest.param(
            (0.37, 0.69, 0.46, 0.22, "colour_front", 0.0),
            True,
            id="dual_seams_lower_right",
        ),
    ),
)
def test_single_physical_object_survives_capacity_seams_at_different_offsets(
    spec: CardSpec,
    requires_instance_recovery: bool,
) -> None:
    content, expected_masks = _object_sheet([spec])

    layout = detect_layout(content, dpi=TEST_DPI)
    matched = _assert_one_detection_per_object(layout, expected_masks)

    if requires_instance_recovery:
        assert layout.signals["current_instance_recovery"] >= 1
        assert "grid_fragment_replacement" in matched[-1].evidence


def test_low_saturation_back_crossing_two_capacity_seams_is_one_object() -> None:
    content, expected_masks = _object_sheet([(0.27, 0.64, 0.46, 0.22, "low_saturation_back", 0.0)])

    layout = detect_layout(content, dpi=TEST_DPI)
    matched = _assert_one_detection_per_object(layout, expected_masks)

    assert layout.signals["current_instance_recovery"] >= 1
    assert "current_instance_grid_reconciliation" in matched[-1].evidence


def test_rotated_object_crossing_two_capacity_seams_is_not_split() -> None:
    content, expected_masks = _object_sheet([(0.27, 0.64, 0.46, 0.22, "colour_front", 7.0)])

    layout = detect_layout(content, dpi=TEST_DPI)
    matched = _assert_one_detection_per_object(layout, expected_masks)

    assert layout.signals["current_instance_recovery"] >= 1
    assert "grid_fragment_replacement" in matched[-1].evidence


def test_recovered_object_excludes_previous_row_after_large_vertical_gap() -> None:
    content, expected_masks = _object_sheet(
        [(0.20, 0.575, 0.52, 0.30, "colour_front", 0.0)],
        second_row_body_bottom=0.535,
        second_row_holder_bottom=0.555,
    )

    layout = detect_layout(content, dpi=TEST_DPI)
    matched = _assert_one_detection_per_object(layout, expected_masks)

    # The capacity boundary sits at y=.50, inside the preceding row. A crop
    # starting there would include that card despite the true empty gap.
    assert matched[-1].y >= 0.56
    assert "grid_fragment_replacement" in matched[-1].evidence


def test_tightly_touching_full_row_remains_two_independent_cards() -> None:
    content, expected_masks = _object_sheet(
        [
            (0.0, 0.525, 0.50, 0.20, "colour_front", 0.0),
            (0.50, 0.525, 0.50, 0.20, "colour_front", 0.0),
        ]
    )

    layout = detect_layout(content, dpi=TEST_DPI)

    _assert_one_detection_per_object(layout, expected_masks)


def test_eight_percent_holder_tail_does_not_consume_occupied_slot() -> None:
    content, expected_masks = _full_grid_with_cross_row_artifact("holder_tail")

    layout = detect_layout(content, dpi=TEST_DPI)

    _assert_one_detection_per_object(layout, expected_masks)


def test_cross_row_instance_does_not_merge_colour_distinct_cards() -> None:
    content, expected_masks = _full_grid_with_cross_row_artifact("cross_row_instance")

    layout = detect_layout(content, dpi=TEST_DPI)

    _assert_one_detection_per_object(layout, expected_masks)
