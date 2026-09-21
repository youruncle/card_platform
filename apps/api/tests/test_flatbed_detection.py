import cv2
import numpy as np

from card_platform.flatbed_detection import (
    Detection,
    _foreground_layout_is_complete,
    _complete_aligned_slab_envelopes,
    _full_bed_grid_detections,
    _grid_cell_occupancy,
    _irregular_slab_detections,
    _projection_gap_grid_detections,
    _refine_grid_cell_to_ink_extent,
    _stationary_layout_similarity,
    _transform_prior,
    detect_cards,
    detect_layout,
    explain_detection,
)


def encode(image: np.ndarray) -> bytes:
    ok, content = cv2.imencode(".png", image)
    assert ok
    return content.tobytes()


def test_complete_component_layout_prevents_incompatible_global_grid() -> None:
    detections = [
        Detection(
            x=column * 0.52,
            y=row * 0.245,
            width=0.46 if column else 0.51,
            height=0.225 if column else 0.24,
            angle=0,
            confidence=0.88,
        )
        for row in range(4)
        for column in range(2)
    ]

    assert _foreground_layout_is_complete(detections)
    assert not _foreground_layout_is_complete(
        [Detection(0, 0, 1, 1, 0, 0.5)]
    )


def test_irregular_back_layout_penalizes_moving_unequal_holders() -> None:
    prior = [
        Detection(0.0, 0.0, 0.62, 0.26, 0, 0.9),
        Detection(0.65, 0.0, 0.35, 0.44, 0, 0.9),
        Detection(0.0, 0.55, 0.62, 0.27, 0, 0.9),
    ]

    stationary = _stationary_layout_similarity(
        prior, _transform_prior(prior, "identity")
    )
    mirrored = _stationary_layout_similarity(
        prior, _transform_prior(prior, "mirror_x")
    )

    assert stationary == 1.0
    assert mirrored < 0.5


def test_detects_irregular_mix_of_raw_card_and_graded_slabs() -> None:
    image = np.full((1800, 1300, 3), 245, dtype=np.uint8)

    def artwork(
        x: int,
        y: int,
        width: int,
        height: int,
        color: tuple[int, int, int],
    ) -> None:
        cv2.rectangle(image, (x, y), (x + width, y + height), color, -1)
        cv2.rectangle(
            image,
            (x + width // 4, y + height // 4),
            (x + width * 3 // 4, y + height * 3 // 4),
            (25, 80, 180),
            -1,
        )

    # One raw/top-loaded card without a grading label.
    artwork(25, 20, 555, 350, (35, 145, 70))
    # Portrait slab with its grading label below the artwork.
    artwork(800, 70, 385, 500, (45, 95, 190))
    cv2.rectangle(image, (790, 630), (1195, 735), (35, 70, 145), -1)
    # Two landscape slabs with labels to the left.
    artwork(190, 620, 520, 330, (55, 115, 170))
    cv2.rectangle(image, (30, 615), (145, 955), (35, 75, 150), -1)
    artwork(190, 1110, 520, 330, (45, 125, 160))
    cv2.rectangle(image, (30, 1105), (145, 1445), (45, 80, 145), -1)
    # A second portrait slab. Its nearest label is below; the upper slab's
    # label must not also be absorbed into this instance.
    artwork(805, 970, 380, 500, (45, 130, 80))
    cv2.rectangle(image, (795, 1570), (1195, 1675), (50, 85, 145), -1)

    direct = _irregular_slab_detections(image)
    detections = detect_cards(encode(image))

    assert len(direct) == 5
    assert len(detections) == 5
    assert sum(
        "grading_label_association" in item.evidence for item in detections
    ) == 4
    assert all(item.width * item.height < 0.2 for item in detections)
    assert detections[1].y + detections[1].height < detections[-1].y


def test_aligned_slabs_restore_a_low_saturation_missing_label() -> None:
    labelled_top = Detection(
        0.10,
        0.05,
        0.60,
        0.25,
        0,
        0.90,
        evidence=("grading_label_association",),
    )
    pale_label_middle = Detection(0.24, 0.36, 0.47, 0.25, 0, 0.84)
    labelled_bottom = Detection(
        0.11,
        0.68,
        0.60,
        0.25,
        0,
        0.90,
        evidence=("grading_label_association",),
    )

    completed = _complete_aligned_slab_envelopes(
        [labelled_top, pale_label_middle, labelled_bottom]
    )

    assert len(completed) == 3
    assert completed[1].x < 0.12
    assert completed[1].x + completed[1].width > 0.70
    assert "aligned_slab_envelope" in completed[1].evidence


def test_detects_multiple_rows_and_small_rotation() -> None:
    image = np.full((1200, 900, 3), 235, dtype=np.uint8)
    for center, angle in [((220, 280), -4), ((650, 300), 3), ((240, 870), 0)]:
        box = cv2.boxPoints((center, (270, 400), angle)).astype(np.int32)
        cv2.fillConvexPoly(image, box, (40, 80, 150))
    detections = detect_cards(encode(image))
    assert len(detections) == 3
    assert all(item.confidence >= 0.8 for item in detections)
    assert all(item.points and len(item.points) == 4 for item in detections)
    assert any(abs(item.angle) >= 2 for item in detections)


def test_empty_reflective_bed_has_explainable_failure() -> None:
    content = encode(np.full((500, 700, 3), 255, dtype=np.uint8))
    detections = detect_cards(content)
    assert detections == []
    assert explain_detection(content, detections) == [
        "no_card_outline_detected",
        "possible_glare_or_empty_bed",
    ]


def test_edge_clipped_card_is_flagged() -> None:
    image = np.full((600, 800, 3), 230, dtype=np.uint8)
    cv2.rectangle(image, (0, 80), (310, 540), (20, 20, 20), -1)
    detection = detect_cards(encode(image))[0]
    assert detection.failure == "severely_clipped"


def test_recovers_eight_sleeved_cards_when_two_foregrounds_touch() -> None:
    image = np.full((1600, 1100, 3), 248, dtype=np.uint8)
    card_width, card_height = 440, 330
    for column, x in enumerate((20, 640)):
        for row in range(4):
            # The top two cards in the right column deliberately touch, matching
            # a real clear-sleeve scan whose dark foreground became one component.
            y = row * card_height if column == 1 and row < 2 else row * 390
            cv2.rectangle(
                image,
                (x, y),
                (min(x + card_width, image.shape[1] - 1), min(y + card_height, image.shape[0] - 1)),
                (55 + row * 10, 70, 120 + column * 20),
                -1,
            )
    detections = detect_cards(encode(image))
    assert len(detections) == 8


def test_recovers_full_touching_column_when_other_column_has_single_card_references() -> None:
    image = np.full((1600, 1100, 3), 248, dtype=np.uint8)
    card_width, card_height = 440, 330
    for row in range(4):
        # Every holder in the left column touches the next one, producing one
        # full-height foreground component like the real LiDE 400 scan.
        cv2.rectangle(
            image,
            (0, row * card_height),
            (card_width, (row + 1) * card_height),
            (45 + row * 10, 65, 110),
            -1,
        )
        # The right column supplies normal single-card size references.
        cv2.rectangle(
            image,
            (640, row * 390),
            (1080, row * 390 + card_height),
            (65 + row * 10, 75, 135),
            -1,
        )

    detections = detect_cards(encode(image))

    assert len(detections) == 8
    assert sum(item.x < 0.5 for item in detections) == 4


def test_recovers_two_by_four_grid_when_all_clear_holders_touch_bed_edges() -> None:
    image = np.full((1600, 1100, 3), 180, dtype=np.uint8)
    cell_width, cell_height = 550, 400
    for row in range(4):
        for column in range(2):
            x = column * cell_width
            y = row * cell_height
            # Touching pale holders make the foreground mask cover the entire
            # bed, while the darker inset preserves enough edge references to
            # infer cell size and recover the complete grid.
            cv2.rectangle(
                image,
                (x, y),
                (x + cell_width - 1, y + cell_height - 1),
                (190, 190, 190),
                -1,
            )
            if (row, column) in {(0, 0), (2, 1), (3, 0), (3, 1)}:
                cv2.rectangle(
                    image,
                    (x + 35, y + 30),
                    (x + cell_width - 35, y + cell_height - 30),
                    (35 + row * 8, 60, 105 + column * 15),
                    -1,
                )

    detections = detect_cards(encode(image))

    assert len(detections) == 8
    assert sum(item.x < 0.5 for item in detections) == 4
    refined = [item for item in detections if "ink_extent_refinement" in item.evidence]
    assert refined
    assert all(item.width <= 0.5 and item.height <= 0.25 for item in refined)
    assert any(item.width < 0.5 or item.height < 0.25 for item in refined)


def test_threshold_content_references_recover_touching_magnetic_holder_grid() -> None:
    image = np.full((1600, 1100, 3), 180, dtype=np.uint8)
    cell_width, cell_height = 550, 400
    for row in range(4):
        for column in range(2):
            x, y = column * cell_width, row * cell_height
            # The pale magnetic holders touch, becoming one whole-bed
            # foreground component. Only the inset artwork has a closed shape.
            cv2.rectangle(
                image,
                (x, y),
                (x + cell_width - 1, y + cell_height - 1),
                (190, 190, 190),
                -1,
            )
            cv2.rectangle(
                image,
                (x + 45, y + 38),
                (x + cell_width - 45, y + cell_height - 38),
                (35 + row * 8, 80 + column * 10, 145),
                -1,
            )
            # Break the Canny outline on alternating cells. Multi-threshold
            # contours must still supply enough references for the 2x4 grid.
            if (row + column) % 2:
                cv2.line(
                    image,
                    (x + 40, y + cell_height // 2),
                    (x + 80, y + cell_height // 2),
                    (190, 190, 190),
                    80,
                )

    detections = detect_cards(encode(image))

    assert len(detections) == 8
    assert sum(item.x < 0.5 for item in detections) == 4


def test_internal_card_contours_do_not_inflate_landscape_grid_to_four_columns() -> None:
    image = np.full((3507, 2480, 3), 205, dtype=np.uint8)
    whole_bed = Detection(0, 0, 1, 1, 0, 0.5)
    # More contours than cards: logos and printed borders commonly yield
    # several card-shaped edge references inside the same physical holder.
    references = [
        Detection(
            0.03 + (index % 3) * 0.01,
            0.02 + (index % 4) * 0.02,
            0.44,
            0.22,
            0,
            0.7,
        )
        for index in range(12)
    ]

    detections = _full_bed_grid_detections(
        image, [whole_bed], references, dpi=300
    )

    assert detections is not None
    assert len(detections) == 8
    assert {round(item.width, 2) for item in detections} == {0.5}
    assert {round(item.height, 2) for item in detections} == {0.25}


def test_full_bed_grid_drops_unoccupied_trailing_slots() -> None:
    image = np.full((1600, 1100, 3), 224, dtype=np.uint8)
    for row in range(3):
        for column in range(2):
            x1, y1 = column * 550 + 25, row * 400 + 20
            cv2.rectangle(
                image,
                (x1, y1),
                (x1 + 500, y1 + 360),
                (45 + row * 10, 70, 120 + column * 15),
                -1,
            )
    # A sliver of the neighbouring holder enters the two empty allocation
    # cells, matching the real six-card scan that previously created cards 7/8.
    cv2.line(image, (535, 1210), (535, 1599), (125, 125, 125), 8)
    cv2.line(image, (1080, 1210), (1080, 1599), (125, 125, 125), 8)
    whole_bed = Detection(0, 0, 1, 1, 0, 0.5)
    references = [
        Detection(0.04, 0.02, 0.42, 0.20, 0, 0.7),
        Detection(0.54, 0.02, 0.42, 0.20, 0, 0.7),
    ]

    detections = _full_bed_grid_detections(
        image, [whole_bed], references, dpi=300
    )

    assert detections is not None
    assert len(detections) == 6
    assert max(item.y + item.height for item in detections) <= 0.75


def test_grid_occupancy_keeps_pale_card_back_with_distributed_print() -> None:
    image = np.full((800, 550, 3), 224, dtype=np.uint8)
    for y in range(160, 650, 55):
        cv2.line(image, (120, y), (430, y), (135, 135, 135), 3)
    cv2.rectangle(image, (210, 300), (340, 430), (170, 155, 145), 5)

    occupied, metrics = _grid_cell_occupancy(image, 0, 0, 1, 1)

    assert occupied
    assert metrics["edge_ratio"] >= 0.005


def test_grid_occupancy_rejects_white_bed_with_neighbour_edge_only() -> None:
    image = np.full((800, 550, 3), 224, dtype=np.uint8)
    cv2.line(image, (535, 0), (535, 799), (120, 120, 120), 8)

    occupied, metrics = _grid_cell_occupancy(image, 0, 0, 1, 1)

    assert not occupied
    assert metrics["ink_ratio"] < 0.035


def test_grid_occupancy_rejects_trailing_neighbour_sliver() -> None:
    image = np.full((800, 550, 3), 212, dtype=np.uint8)
    # The previous row's holder protrudes into the top of this empty grid cell.
    # It spans the full width but has no card-sized body below it.
    cv2.rectangle(image, (0, 0), (549, 140), (165, 165, 165), -1)

    occupied, metrics = _grid_cell_occupancy(image, 0, 0, 1, 1)

    assert not occupied
    assert metrics["ink_ratio"] < 0.12
    assert metrics["ink_row_coverage"] < 0.16


def test_full_grid_cell_is_not_promoted_as_ink_refinement() -> None:
    image = np.full((800, 550, 3), 170, dtype=np.uint8)
    cv2.rectangle(image, (0, 0), (549, 799), (40, 70, 120), -1)

    detection = _refine_grid_cell_to_ink_extent(image, 0, 0, 1, 1)

    assert detection is None


def test_deduplicates_offset_edge_and_foreground_boxes() -> None:
    image = np.full((1000, 700, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (100, 100), (390, 530), (35, 45, 80), -1)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    detections = detect_cards(encoded.tobytes())
    assert len(detections) == 1


def test_replaces_whole_sheet_and_internal_artwork_with_grid() -> None:
    image = np.full((1600, 1100, 3), 180, dtype=np.uint8)
    for row in range(4):
        for column in range(2):
            x1, y1 = column * 550, row * 400
            cv2.rectangle(image, (x1, y1), (x1 + 549, y1 + 399), (205, 205, 205), -1)
    for row, column in ((0, 1), (1, 0)):
        x1, y1 = column * 550 + 55, row * 400 + 40
        cv2.rectangle(image, (x1, y1), (x1 + 440, y1 + 320), (35, 60, 110), -1)

    detections = detect_cards(encode(image))

    assert len(detections) == 8
    assert all(item.width * item.height < 0.2 for item in detections)
    assert all(
        not (a.x <= b.x and a.y <= b.y and a.x + a.width >= b.x + b.width and a.y + a.height >= b.y + b.height)
        for index, a in enumerate(detections)
        for b in detections[index + 1 :]
    )


def test_stationary_back_uses_confirmed_front_slots_without_projection() -> None:
    prior = [
        Detection(column * 0.5, row * 0.25, 0.5, 0.25, 0, 0.9)
        for row in range(4)
        for column in range(2)
    ]
    back = np.full((1600, 1100, 3), 185, dtype=np.uint8)
    for row, column in ((0, 0), (2, 1), (3, 0)):
        cv2.rectangle(back, (column * 550 + 30, row * 400 + 25), ((column + 1) * 550 - 30, (row + 1) * 400 - 25), (35, 55, 100), -1)
    layout = detect_layout(
        encode(back),
        expected_count=8,
        prior=prior,
        stationary_prior=True,
    )
    assert len(layout.detections) == 8
    assert layout.signals["expected_count"] == 8
    assert layout.transform == "identity"
    assert layout.signals["prior_role"] == "identity_slot_topology_only"
    assert all(
        "front_to_back_prior" in item.evidence for item in layout.detections
    )


def test_stationary_slots_ignore_card_internal_white_projection_bands() -> None:
    image = np.full((1600, 1100, 3), 248, dtype=np.uint8)
    prior = []
    for row, (top, bottom) in enumerate(
        ((5, 350), (375, 720), (745, 1090), (1115, 1555))
    ):
        for left, right in ((35, 520), (580, 1070)):
            cv2.rectangle(
                image,
                (left, top),
                (right, bottom),
                (35, 70 + row * 8, 125),
                -1,
            )
            # A broad pale information panel inside every card is a stronger
            # global projection valley than the narrow physical holder seam.
            # The old back route chose it as a row boundary and sliced cards.
            band_top = top + round((bottom - top) * 0.70)
            cv2.rectangle(
                image,
                (left + 10, band_top),
                (right - 10, min(bottom - 8, band_top + 55)),
                (246, 246, 246),
                -1,
            )
            prior.append(
                Detection(
                    left / 1100,
                    top / 1600,
                    (right - left) / 1100,
                    (bottom - top) / 1600,
                    0,
                    0.9,
                )
            )

    layout = detect_layout(
        encode(image),
        expected_count=8,
        prior=prior,
        stationary_prior=True,
    )

    assert layout.transform == "identity"
    assert len(layout.detections) == 8
    assert all(
        abs(actual.y - expected.y) < 1e-6
        and abs(actual.height - expected.height) < 1e-6
        for actual, expected in zip(layout.detections, prior, strict=True)
    )
    assert all(
        "projection_gap_grid" not in item.evidence for item in layout.detections
    )


def test_stationary_prior_never_manufactures_an_empty_slot() -> None:
    image = np.full((800, 1000, 3), 246, dtype=np.uint8)
    cv2.rectangle(image, (40, 40), (460, 350), (35, 70, 125), -1)
    cv2.rectangle(image, (540, 40), (960, 350), (45, 80, 135), -1)
    prior = [
        Detection(0.04, 0.05, 0.42, 0.39, 0, 0.9),
        Detection(0.54, 0.05, 0.42, 0.39, 0, 0.9),
        Detection(0.04, 0.55, 0.42, 0.39, 0, 0.9),
    ]

    layout = detect_layout(
        encode(image),
        expected_count=3,
        prior=prior,
        stationary_prior=True,
    )

    assert len(layout.detections) == 2
    assert all(item.y < 0.5 for item in layout.detections)
    assert not any(
        "projection_grid" in item.evidence for item in layout.detections
    )


def test_projection_gap_grid_uses_current_scan_seams_instead_of_shifted_prior() -> None:
    image = np.full((1600, 1100, 3), 252, dtype=np.uint8)
    x_edges = (45, 525, 585, 1099)
    y_edges = ((0, 350), (365, 710), (725, 1070), (1085, 1535))
    for row, (top, bottom) in enumerate(y_edges):
        cv2.rectangle(image, (x_edges[0], top), (x_edges[1], bottom), (35, 70 + row * 8, 125), -1)
        cv2.rectangle(image, (x_edges[2], top), (x_edges[3], bottom), (45, 80 + row * 8, 135), -1)

    detections, quality = _projection_gap_grid_detections(image, 4, 2)

    assert len(detections) == 8
    assert quality > 0.5
    assert all("projection_gap_grid" in item.evidence for item in detections)
    for row in range(3):
        upper = detections[row * 2 : row * 2 + 2]
        lower = detections[(row + 1) * 2 : (row + 1) * 2 + 2]
        assert max(item.y + item.height for item in upper) <= min(item.y for item in lower)


def test_back_prefers_occupied_capacity_grid_over_full_height_projection() -> None:
    image = np.full((3507, 2480, 3), 224, dtype=np.uint8)
    cell_width, cell_height = 1240, 877
    for row in range(3):
        for column in range(2):
            x, y = column * cell_width, row * cell_height
            cv2.rectangle(
                image,
                (x, y),
                (x + cell_width - 1, y + cell_height - 1),
                (200, 200, 200),
                -1,
            )
            cv2.rectangle(
                image,
                (x + 70, y + 60),
                (x + cell_width - 70, y + cell_height - 60),
                (35 + row * 12, 70, 125 + column * 15),
                -1,
            )
    prior = [
        Detection(column * 0.5, row * 0.25, 0.5, 0.25, 0, 0.9)
        for row in range(3)
        for column in range(2)
    ]

    layout = detect_layout(
        encode(image), dpi=300, expected_count=6, prior=prior
    )

    assert len(layout.detections) == 6
    assert layout.transform == "independent"
    assert layout.signals["sources"] == "full_bed_grid+current_side_occupancy"
    assert layout.signals["front_prior_role"] == "count_only"
    assert max(item.y + item.height for item in layout.detections) <= 0.76
    assert all("full_bed_grid" in item.evidence for item in layout.detections)
