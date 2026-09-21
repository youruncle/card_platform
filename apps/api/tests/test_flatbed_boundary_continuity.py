import cv2
import numpy as np
import pytest

from card_platform.flatbed_crop_quality import assess_crop_quality
from card_platform.flatbed_detection import Detection


@pytest.mark.parametrize("axis", ["x", "y"])
def test_half_card_has_presence_but_cannot_silently_pass_boundary_check(axis):
    image = np.full((1000, 1000, 3), 246, np.uint8)
    cv2.rectangle(image, (150, 150), (850, 800), (60, 110, 170), -1)
    regions = (
        [Detection(0.10, 0.10, 0.40, 0.75, 0, 0.99), Detection(0.50, 0.10, 0.40, 0.75, 0, 0.99)]
        if axis == "x"
        else [
            Detection(0.10, 0.10, 0.80, 0.40, 0, 0.99),
            Detection(0.10, 0.50, 0.80, 0.40, 0, 0.99),
        ]
    )
    for region in regions:
        report = assess_crop_quality(image, region, peers=regions)
        assert report.presence_score > 0.8
        assert report.metrics["max_boundary_content_continuity"] > 0.5
        assert "content_crosses_crop_boundary" in {v.code for v in report.violations}
        assert report.needs_review
        # Advisory, so the user can still edit/recover a region.
        assert report.usable


def test_separate_cards_with_thin_holder_edges_do_not_trigger_continuity():
    image = np.full((1000, 1000, 3), 246, np.uint8)
    regions = [Detection(0.10, 0.10, 0.80, 0.36, 0, 0.9), Detection(0.10, 0.47, 0.80, 0.36, 0, 0.9)]
    for y in (100, 470):
        cv2.rectangle(image, (100, y), (900, y + 360), (135, 135, 135), 4)
        cv2.rectangle(image, (125, y + 25), (875, y + 335), (60, 110, 170), -1)
    for region in regions:
        report = assess_crop_quality(image, region, peers=regions)
        assert report.metrics["max_boundary_content_continuity"] == 0
        assert "content_crosses_crop_boundary" not in {v.code for v in report.violations}


def test_rotated_polygon_uses_its_edges_not_axis_aligned_bounding_box():
    image = np.full((1000, 1000, 3), 246, np.uint8)
    outer = cv2.boxPoints(((500, 500), (700, 480), 17))
    inner = cv2.boxPoints(((500, 500), (670, 450), 17)).astype(np.int32)
    cv2.fillConvexPoly(image, inner, (60, 110, 170))
    left, top = outer.min(axis=0) / 1000
    right, bottom = outer.max(axis=0) / 1000
    region = Detection(
        left,
        top,
        right - left,
        bottom - top,
        17,
        0.99,
        points=tuple(tuple(point / 1000) for point in outer),
    )
    report = assess_crop_quality(image, region)
    assert report.metrics["max_boundary_content_continuity"] == 0
    assert not report.needs_review


def test_broad_grey_holder_rim_is_not_printed_content_crossing():
    image = np.full((1000, 1000, 3), 246, np.uint8)
    cv2.rectangle(image, (60, 60), (940, 940), (210, 202, 192), -1)
    cv2.rectangle(image, (150, 150), (850, 850), (50, 100, 165), -1)
    region = Detection(0.10, 0.10, 0.80, 0.80, 0, 0.99)
    report = assess_crop_quality(image, region)
    assert report.metrics["max_boundary_content_continuity"] == 0
    assert not report.needs_review


def test_sheet_context_computes_background_once_without_changing_any_report(monkeypatch):
    from card_platform import flatbed_crop_quality as quality

    image = np.full((800, 1000, 3), 246, np.uint8)
    cv2.rectangle(image, (100, 100), (900, 700), (50, 100, 165), -1)
    boxes = [Detection(.08, .1, .84, .8, 0, .9), Detection(.1, .15, .8, .4, 0, .9)]
    expected = [quality.assess_crop_quality(image, box, peers=boxes) for box in boxes]
    original = quality._background_luma
    calls = []

    def counted_background(source):
        calls.append(source.shape)
        return original(source)

    monkeypatch.setattr(quality, "_background_luma", counted_background)
    assert quality.assess_sheet_crop_quality(image, boxes) == expected
    assert len(calls) == 1
