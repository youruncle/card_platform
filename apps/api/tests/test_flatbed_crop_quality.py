import cv2
import numpy as np

from card_platform.flatbed_crop_quality import assess_crop_quality
from card_platform.flatbed_detection import Detection


def pale_card_sheet() -> np.ndarray:
    image = np.full((800, 1000, 3), 246, dtype=np.uint8)
    cv2.rectangle(image, (100, 140), (480, 690), (232, 232, 232), -1)
    cv2.rectangle(image, (100, 140), (480, 690), (155, 155, 155), 5)
    cv2.rectangle(image, (140, 190), (440, 230), (205, 205, 205), -1)
    cv2.putText(
        image,
        "PLAYER 23",
        (145, 220),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (105, 105, 105),
        2,
        cv2.LINE_AA,
    )
    cv2.line(image, (150, 300), (430, 300), (180, 180, 180), 3)
    cv2.line(image, (150, 610), (430, 610), (180, 180, 180), 3)
    return image


def test_blank_cell_is_reported_from_pixels_even_with_high_model_confidence() -> None:
    image = pale_card_sheet()
    blank = Detection(0.58, 0.12, 0.35, 0.72, 0, 0.999)

    report = assess_crop_quality(image, blank)

    assert report.presence_score < 0.22
    assert not report.usable
    assert "blank_or_fragment" in {item.code for item in report.violations}


def test_low_contrast_card_back_is_not_mistaken_for_blank() -> None:
    image = pale_card_sheet()
    pale_card = Detection(0.10, 0.175, 0.38, 0.6875, 0, 0.12)

    report = assess_crop_quality(image, pale_card)

    assert report.presence_score >= 0.55
    assert report.usable
    assert "blank_or_fragment" not in {item.code for item in report.violations}


def test_sparse_white_card_uses_edge_density_even_when_ink_is_minimal() -> None:
    image = np.full((800, 1000, 3), 246, dtype=np.uint8)
    cv2.rectangle(image, (100, 140), (480, 690), (240, 240, 240), -1)
    cv2.rectangle(image, (100, 140), (480, 690), (190, 190, 190), 3)
    cv2.putText(
        image,
        "NO. 23",
        (245, 420),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (170, 170, 170),
        1,
        cv2.LINE_AA,
    )
    sparse_back = Detection(0.10, 0.175, 0.38, 0.6875, 0, 0.01)

    report = assess_crop_quality(image, sparse_back)

    assert report.metrics["content_axis_signal_density"] < 0.08
    assert report.metrics["content_ratio"] < 0.08
    assert report.metrics["edge_ratio"] > 0.008
    assert report.metrics["sparse_span_only"] == 0
    assert report.usable
    assert "blank_or_fragment" not in {item.code for item in report.violations}


def test_sparse_end_noise_cannot_stretch_a_blank_crop_into_a_card() -> None:
    image = np.full((1000, 1200, 3), 246, dtype=np.uint8)
    # Two narrow vertical traces are enough to span almost the full crop, but
    # they do not provide meaningful two-dimensional card evidence.
    cv2.rectangle(image, (120, 100), (153, 899), (180, 180, 180), -1)
    cv2.rectangle(image, (1032, 100), (1036, 899), (180, 180, 180), -1)
    mostly_empty = Detection(0.10, 0.10, 0.80, 0.80, 0, 0.999)

    report = assess_crop_quality(image, mostly_empty)

    assert report.metrics["content_axis_coverage"] > 0.90
    assert report.metrics["content_axis_signal_density"] < 0.08
    assert report.metrics["content_ratio"] < 0.08
    assert report.metrics["edge_ratio"] < 0.008
    assert report.metrics["sparse_span_only"] == 1
    assert "blank_or_fragment" in {item.code for item in report.violations}
    assert not report.usable


def test_neighbour_sliver_does_not_turn_an_empty_cell_into_a_card() -> None:
    image = np.full((800, 1000, 3), 246, dtype=np.uint8)
    cv2.rectangle(image, (600, 100), (900, 240), (30, 70, 150), -1)
    mostly_empty = Detection(0.58, 0.10, 0.34, 0.70, 0, 0.999)

    report = assess_crop_quality(image, mostly_empty)

    assert report.metrics["content_axis_coverage"] < 0.28
    assert "blank_or_fragment" in {item.code for item in report.violations}
    assert not report.usable


def test_peer_geometry_exposes_a_two_row_crop() -> None:
    image = np.full((1000, 1000, 3), 242, dtype=np.uint8)
    peers = [
        Detection(0.02, 0.02, 0.46, 0.22, 0, 0.8),
        Detection(0.52, 0.02, 0.46, 0.22, 0, 0.8),
        Detection(0.02, 0.26, 0.46, 0.46, 0, 0.99),
        Detection(0.52, 0.26, 0.46, 0.22, 0, 0.8),
        Detection(0.02, 0.74, 0.46, 0.22, 0, 0.8),
        Detection(0.52, 0.74, 0.46, 0.22, 0, 0.8),
    ]
    for row_top in (20, 260, 500, 740):
        cv2.rectangle(image, (20, row_top), (480, min(960, row_top + 220)), (40, 80, 160), -1)
    target = peers[2]

    report = assess_crop_quality(image, target, peers=peers)

    violation = next(item for item in report.violations if item.code == "possible_multiple_cards")
    assert violation.severity == "error"
    assert report.isolation_score <= 0.2
    assert not report.usable


def test_rotated_graded_slab_uses_orientation_invariant_peer_size() -> None:
    image = np.full((1600, 1100, 3), 245, dtype=np.uint8)
    peers = [
        Detection(0.02, 0.02, 0.49, 0.23, 0, 0.84),
        Detection(0.51, 0.02, 0.47, 0.23, 0, 0.84),
        Detection(0.02, 0.26, 0.47, 0.23, 0, 0.84),
        Detection(0.02, 0.50, 0.49, 0.23, 0, 0.84),
        Detection(0.02, 0.75, 0.49, 0.23, 0, 0.84),
    ]
    slab = Detection(
        0.62,
        0.26,
        0.35,
        0.45,
        0,
        0.90,
        evidence=(
            "saturation_content_island",
            "mixed_format_instance",
            "grading_label_association",
        ),
    )
    peers.append(slab)
    for peer in peers[:-1]:
        left = round(peer.x * image.shape[1])
        top = round(peer.y * image.shape[0])
        right = round((peer.x + peer.width) * image.shape[1])
        bottom = round((peer.y + peer.height) * image.shape[0])
        cv2.rectangle(image, (left, top), (right, bottom), (45, 90, 165), -1)
    cv2.rectangle(image, (700, 440), (1040, 550), (45, 70, 145), -1)
    cv2.rectangle(image, (720, 610), (1020, 1080), (40, 95, 175), -1)

    report = assess_crop_quality(image, slab, peers=peers)

    multiple_card_errors = [
        item
        for item in report.violations
        if item.code == "possible_multiple_cards" and item.severity == "error"
    ]
    assert not multiple_card_errors
    assert report.metrics["short_edge_to_peer_median"] < 1.2
    assert report.metrics["long_edge_to_peer_median"] < 1.5
    assert report.metrics["orientation_invariant_peer_size"] == 1
    assert report.usable


def test_unlabelled_tall_crop_keeps_the_scanner_axis_merge_guard() -> None:
    image = np.full((1000, 1000, 3), 242, dtype=np.uint8)
    peers = [
        Detection(0.02, 0.02, 0.46, 0.22, 0, 0.8),
        Detection(0.52, 0.02, 0.46, 0.22, 0, 0.8),
        Detection(0.02, 0.26, 0.47, 0.368, 0, 0.99),
        Detection(0.52, 0.26, 0.46, 0.22, 0, 0.8),
        Detection(0.02, 0.74, 0.46, 0.22, 0, 0.8),
        Detection(0.52, 0.74, 0.46, 0.22, 0, 0.8),
    ]
    target = peers[2]
    cv2.rectangle(image, (20, 260), (490, 628), (40, 80, 160), -1)

    report = assess_crop_quality(image, target, peers=peers)

    violation = next(item for item in report.violations if item.code == "possible_multiple_cards")
    assert report.metrics["orientation_invariant_peer_size"] == 0
    assert violation.severity == "error"
    assert not report.usable


def test_neighbour_overlap_is_reported() -> None:
    image = pale_card_sheet()
    target = Detection(0.10, 0.175, 0.38, 0.6875, 0, 0.8)
    overlapping = Detection(0.30, 0.20, 0.38, 0.65, 0, 0.8)

    report = assess_crop_quality(image, target, peers=[target, overlapping])

    assert "overlaps_neighbour" in {item.code for item in report.violations}
    assert report.isolation_score < 1.0


def test_source_edge_contact_is_visible_but_does_not_hide_a_usable_card() -> None:
    image = pale_card_sheet()
    cv2.rectangle(image, (0, 140), (380, 690), (232, 232, 232), -1)
    cv2.rectangle(image, (0, 140), (380, 690), (155, 155, 155), 5)
    cv2.putText(
        image,
        "CARD",
        (60, 350),
        cv2.FONT_HERSHEY_SIMPLEX,
        2,
        (80, 80, 80),
        5,
        cv2.LINE_AA,
    )
    edge_card = Detection(0.0, 0.175, 0.38, 0.6875, 0, 0.8)

    report = assess_crop_quality(image, edge_card)

    assert "source_edge_contact" in {item.code for item in report.violations}
    assert report.boundary_score < 1.0
    assert report.usable
    assert report.needs_review


def test_scores_are_independent_of_detector_or_sam_confidence() -> None:
    image = pale_card_sheet()
    low_confidence = Detection(0.10, 0.175, 0.38, 0.6875, 0, 0.01)
    high_confidence = Detection(0.10, 0.175, 0.38, 0.6875, 0, 0.999)

    low_report = assess_crop_quality(image, low_confidence)
    high_report = assess_crop_quality(image, high_confidence)

    assert low_report == high_report


def test_invalid_geometry_cannot_be_usable() -> None:
    image = pale_card_sheet()
    outside = Detection(1.2, 0.2, 0.3, 0.4, 0, 1.0)

    report = assess_crop_quality(image, outside)

    codes = {item.code for item in report.violations}
    assert {"invalid_crop_geometry", "geometry_outside_source"} <= codes
    assert not report.usable
