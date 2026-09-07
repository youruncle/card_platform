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
