from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from card_platform.flatbed_detection import (
    Detection,
    LayoutHypothesis,
    _complete_multi_panel_object_envelopes,
    _irregular_slab_detections,
    detect_layout,
)


IMAGE_WIDTH = 2480
IMAGE_HEIGHT = 3507
TEST_DPI = 300


@dataclass(frozen=True)
class ObjectSpec:
    left: int
    top: int
    right: int
    bottom: int


MIXED_ORIENTATION_OBJECTS = (
    ObjectSpec(60, 100, 1190, 760),
    ObjectSpec(100, 1280, 1230, 1950),
    ObjectSpec(40, 2460, 1180, 3180),
    ObjectSpec(1550, 100, 2260, 1160),
    ObjectSpec(1480, 2060, 2260, 3180),
)

VARIABLE_LANDSCAPE_OBJECTS = (
    ObjectSpec(60, 90, 1160, 720),
    ObjectSpec(1320, 150, 2380, 790),
    ObjectSpec(160, 1080, 1310, 1740),
    ObjectSpec(1330, 1190, 2350, 1890),
    ObjectSpec(530, 2400, 1730, 3090),
)

OBJECT_COLOURS = (
    (35, 80, 155),
    (45, 130, 75),
    (150, 65, 45),
    (55, 75, 165),
    (40, 120, 140),
    (125, 55, 145),
    (60, 140, 105),
    (145, 95, 35),
)


def _encode(image: np.ndarray) -> bytes:
    ok, content = cv2.imencode(".png", image)
    assert ok
    return content.tobytes()


def _exposure_gradient_bed() -> np.ndarray:
    horizontal = np.linspace(178, 184, IMAGE_WIDTH, dtype=np.float32)[None, :, None]
    vertical = np.linspace(-1, 1, IMAGE_HEIGHT, dtype=np.float32)[:, None, None]
    gray = np.clip(horizontal + vertical, 0, 255).astype(np.uint8)
    return np.repeat(gray, 3, axis=2)


def _draw_complete_object(
    image: np.ndarray,
    spec: ObjectSpec,
    colour: tuple[int, int, int],
) -> np.ndarray:
    # The light holder, its dark rim, and the printed body create independent
    # two-dimensional routes for the same complete physical object.
    cv2.rectangle(
        image,
        (spec.left, spec.top),
        (spec.right, spec.bottom),
        (220, 220, 220),
        -1,
    )
    cv2.rectangle(
        image,
        (spec.left, spec.top),
        (spec.right, spec.bottom),
        (105, 105, 105),
        10,
    )
    body = ObjectSpec(spec.left + 35, spec.top + 35, spec.right - 35, spec.bottom - 35)
    cv2.rectangle(
        image,
        (body.left, body.top),
        (body.right, body.bottom),
        colour,
        -1,
    )
    cv2.rectangle(
        image,
        (body.left + 80, body.top + 70),
        (body.right - 70, body.bottom - 80),
        (colour[2], colour[0], colour[1]),
        -1,
    )
    mask = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint8)
    cv2.rectangle(mask, (body.left, body.top), (body.right, body.bottom), 1, -1)
    return mask


def _object_sheet(specs: tuple[ObjectSpec, ...]) -> tuple[bytes, list[np.ndarray]]:
    image = _exposure_gradient_bed()
    masks = [
        _draw_complete_object(image, spec, OBJECT_COLOURS[index])
        for index, spec in enumerate(specs)
    ]
    return _encode(image), masks


def _four_by_two_prior(*, evidence: tuple[str, ...] = ()) -> list[Detection]:
    return [
        Detection(column * 0.5, row * 0.25, 0.5, 0.25, 0.0, 0.9, evidence=evidence)
        for row in range(4)
        for column in range(2)
    ]


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


def _assert_one_detection_per_body(
    layout: LayoutHypothesis,
    expected_bodies: list[np.ndarray],
) -> list[Detection]:
    assert len(layout.detections) == len(expected_bodies)
    detection_masks = [_detection_mask(item) for item in layout.detections]
    matched_indices: list[int] = []
    for body in expected_bodies:
        body_area = int(np.count_nonzero(body))
        coverage = [
            float(np.count_nonzero(body & detected) / body_area) for detected in detection_masks
        ]
        assert max(coverage, default=0.0) >= 0.95, coverage
        assert sum(value >= 0.95 for value in coverage) == 1, coverage
        matched_indices.append(int(np.argmax(coverage)))
    assert len(set(matched_indices)) == len(expected_bodies)
    return [layout.detections[index] for index in matched_indices]


def test_five_mixed_orientation_objects_override_false_full_bed_occupancy() -> None:
    content, expected_bodies = _object_sheet(MIXED_ORIENTATION_OBJECTS)

    layout = detect_layout(content, dpi=TEST_DPI)

    matched = _assert_one_detection_per_body(layout, expected_bodies)
    assert all("physical_object_envelope" in item.evidence for item in matched)

    # This deliberately empty, exposure-shaded bed area sits between the two
    # portrait objects and beside the middle landscape object. A capacity-grid
    # fallback must not manufacture a card there merely because gray is < 190.
    forbidden = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint8)
    forbidden[1280:1900, 1320:2400] = 1
    assert all(not np.any(_detection_mask(item) & forbidden) for item in layout.detections)


def test_five_same_orientation_objects_with_variable_holders_are_object_first() -> None:
    content, expected_bodies = _object_sheet(VARIABLE_LANDSCAPE_OBJECTS)

    layout = detect_layout(content, dpi=TEST_DPI)

    matched = _assert_one_detection_per_body(layout, expected_bodies)
    assert all("physical_object_envelope" in item.evidence for item in matched)


def test_complete_current_objects_override_automatic_oversegmented_prior() -> None:
    content, expected_bodies = _object_sheet(MIXED_ORIENTATION_OBJECTS)

    layout = detect_layout(
        content,
        dpi=TEST_DPI,
        expected_count=8,
        prior=_four_by_two_prior(),
        stationary_prior=True,
    )

    _assert_one_detection_per_body(layout, expected_bodies)
    assert layout.signals["current_object_layout_authoritative"] == 1
    assert layout.signals["prior_topology_rejected"] == 1
    assert (
        layout.signals["prior_topology_rejection_reason"]
        == "current_complete_objects_replace_oversegmented_prior"
    )


def test_complete_current_objects_do_not_silently_override_manual_prior() -> None:
    content, _ = _object_sheet(MIXED_ORIENTATION_OBJECTS)

    layout = detect_layout(
        content,
        dpi=TEST_DPI,
        expected_count=8,
        prior=_four_by_two_prior(evidence=("manual_override",)),
        stationary_prior=True,
    )

    assert layout.signals["current_object_layout_authoritative"] == 0
    assert layout.signals["prior_topology_rejected"] == 0


def _full_capacity_sheet(*, weak_cells: set[tuple[int, int]]) -> tuple[bytes, list[np.ndarray]]:
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 224, dtype=np.uint8)
    bodies: list[np.ndarray] = []
    for row in range(4):
        for column in range(2):
            left = column * IMAGE_WIDTH // 2
            right = (column + 1) * IMAGE_WIDTH // 2
            top = row * IMAGE_HEIGHT // 4
            bottom = (row + 1) * IMAGE_HEIGHT // 4
            cv2.rectangle(
                image,
                (left, top),
                (right - 1, bottom - 1),
                (204, 204, 204),
                -1,
            )
            body = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH), dtype=np.uint8)
            cv2.rectangle(
                body,
                (left + 65, top + 55),
                (right - 65, bottom - 55),
                1,
                -1,
            )
            bodies.append(body)
            if (row, column) not in weak_cells:
                colour = OBJECT_COLOURS[row * 2 + column]
                cv2.rectangle(
                    image,
                    (left + 65, top + 55),
                    (right - 65, bottom - 55),
                    colour,
                    -1,
                )
                cv2.rectangle(
                    image,
                    (left + 180, top + 150),
                    (right - 180, bottom - 150),
                    (colour[2], colour[0], colour[1]),
                    -1,
                )
                continue

            # A pale back with distributed structure but no closed, strongly
            # saturated card-sized component. It is valid occupancy/residual
            # evidence, not a strong physical-object envelope by itself.
            for line_y in np.linspace(top + 120, bottom - 120, 9).astype(int):
                cv2.line(
                    image,
                    (left + 155, line_y),
                    (right - 155, line_y),
                    (170, 170, 170),
                    6,
                )
            for mark_x in (left + 270, left + 530, left + 790, left + 1010):
                cv2.rectangle(
                    image,
                    (mark_x, top + 180),
                    (mark_x + 35, top + 230),
                    (170, 170, 170),
                    3,
                )
    return _encode(image), bodies


def test_regular_full_two_by_four_sheet_remains_eight_cards() -> None:
    content, expected_bodies = _full_capacity_sheet(weak_cells=set())

    layout = detect_layout(content, dpi=TEST_DPI)

    _assert_one_detection_per_body(layout, expected_bodies)
    assert (layout.rows, layout.columns) == (4, 2)


def test_unexplained_low_saturation_bodies_survive_five_strong_object_envelopes() -> None:
    weak_cells = {(2, 1), (3, 0), (3, 1)}
    content, _ = _full_capacity_sheet(weak_cells=weak_cells)

    layout = detect_layout(content, dpi=TEST_DPI)

    assert len(layout.detections) >= 8
    for row in range(4):
        for column in range(2):
            center_x = (column + 0.5) / 2
            center_y = (row + 0.5) / 4
            assert any(
                item.x <= center_x <= item.x + item.width
                and item.y <= center_y <= item.y + item.height
                for item in layout.detections
            ), (row, column)


def test_single_slab_uses_two_dimensional_label_instead_of_thin_edge_strip() -> None:
    image = np.full((IMAGE_HEIGHT, IMAGE_WIDTH, 3), 246, dtype=np.uint8)
    cv2.rectangle(image, (800, 340), (1660, 1810), (215, 215, 215), -1)
    cv2.rectangle(image, (800, 340), (1660, 1810), (145, 145, 145), 10)
    # The real label is a two-dimensional block above the card body.
    cv2.rectangle(image, (865, 410), (1595, 610), (45, 75, 155), -1)
    cv2.rectangle(image, (865, 760), (1595, 1700), (45, 130, 75), -1)
    # A long, narrow coloured rim is deliberately closer to the body edge, but
    # is not a grading label and must not become a second card or association.
    cv2.rectangle(image, (790, 760), (850, 1700), (155, 75, 45), -1)

    irregular = _irregular_slab_detections(image)
    layout = detect_layout(_encode(image), dpi=TEST_DPI)

    assert len(layout.detections) == 1
    assert len(irregular) == 1
    slab = irregular[0]
    assert "grading_label_association" in slab.evidence
    assert slab.y * IMAGE_HEIGHT < 410
    assert (slab.y + slab.height) * IMAGE_HEIGHT > 1700
    assert slab.x * IMAGE_WIDTH > 805


def test_multi_panel_completion_never_drops_part_of_supported_body() -> None:
    image = np.full((1000, 1000, 3), 224, dtype=np.uint8)
    body = Detection(
        0.20,
        0.20,
        0.20,
        0.30,
        0.0,
        0.90,
        evidence=("current_image_instance", "multi_route_instance_corroboration"),
    )
    shifted_outer = Detection(0.16, 0.24, 0.28, 0.35, 0.0, 0.88)
    completed = _complete_multi_panel_object_envelopes(
        image,
        [body],
        [
            (shifted_outer, "adaptive_threshold_component"),
            (shifted_outer, "structural_edge_component"),
        ],
        cell_area=0.10,
    )

    assert len(completed) == 1
    result = completed[0]
    intersection_width = max(
        0.0,
        min(result.x + result.width, body.x + body.width) - max(result.x, body.x),
    )
    intersection_height = max(
        0.0,
        min(result.y + result.height, body.y + body.height) - max(result.y, body.y),
    )
    body_coverage = intersection_width * intersection_height / (body.width * body.height)
    assert body_coverage >= 0.95
    assert "multi_panel_object_envelope" in result.evidence
