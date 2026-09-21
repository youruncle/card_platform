import cv2
import numpy as np

from card_platform.flatbed_crop_quality import assess_crop_quality
from card_platform.flatbed_detection import Detection


IMAGE_SIZE = 2400
CARD_SIZE = (700, 500)


def _quad_detection(
    center: tuple[float, float],
    size: tuple[float, float] = CARD_SIZE,
    angle: float = 0,
) -> tuple[Detection, np.ndarray]:
    points = cv2.boxPoints((center, size, angle)).astype(np.float32)
    left, top = points.min(axis=0)
    right, bottom = points.max(axis=0)
    detection = Detection(
        x=float(left / IMAGE_SIZE),
        y=float(top / IMAGE_SIZE),
        width=float((right - left) / IMAGE_SIZE),
        height=float((bottom - top) / IMAGE_SIZE),
        angle=angle,
        confidence=0.9,
        points=tuple(
            (float(point[0] / IMAGE_SIZE), float(point[1] / IMAGE_SIZE)) for point in points
        ),
    )
    return detection, points


def _paint_card(image: np.ndarray, points: np.ndarray, colour: tuple[int, int, int]) -> None:
    polygon = np.rint(points).astype(np.int32)
    cv2.fillConvexPoly(image, polygon, colour)
    cv2.polylines(image, [polygon], True, (55, 55, 55), 5, cv2.LINE_AA)


def _same_size_peers(image: np.ndarray) -> list[Detection]:
    peers: list[Detection] = []
    for center, colour in (
        ((1700, 400), (70, 125, 175)),
        ((1700, 1150), (155, 85, 70)),
        ((700, 1850), (80, 155, 95)),
    ):
        detection, points = _quad_detection(center)
        _paint_card(image, points, colour)
        peers.append(detection)
    return peers


def test_same_size_card_rotated_45_degrees_is_not_a_multiple_card_crop() -> None:
    image = np.full((IMAGE_SIZE, IMAGE_SIZE, 3), 246, dtype=np.uint8)
    target, points = _quad_detection((600, 600), angle=45)
    _paint_card(image, points, (65, 105, 180))
    peers = _same_size_peers(image)

    report = assess_crop_quality(image, target, peers=peers)

    assert report.usable
    assert "possible_multiple_cards" not in {item.code for item in report.violations}
    assert 0.99 <= report.metrics["area_to_peer_median"] <= 1.01
    assert 0.99 <= report.metrics["axis_span_to_peer_median"] <= 1.01
    # The physical card occupies only about half of its inflated 45-degree
    # bounding box; presence must be measured inside the polygon.
    assert report.metrics["polygon_mask_ratio"] < 0.6


def test_content_outside_rotated_polygon_cannot_create_false_presence() -> None:
    image = np.full((IMAGE_SIZE, IMAGE_SIZE, 3), 246, dtype=np.uint8)
    target, points = _quad_detection((900, 900), angle=45)
    left, top = np.floor(points.min(axis=0)).astype(int)
    right, bottom = np.ceil(points.max(axis=0)).astype(int)
    polygon_mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(polygon_mask, np.rint(points).astype(np.int32), 255)
    bbox_mask = np.zeros_like(polygon_mask)
    bbox_mask[top : bottom + 1, left : right + 1] = 255
    outside_polygon = (bbox_mask > 0) & (polygon_mask == 0)
    image[outside_polygon] = (35, 80, 170)

    report = assess_crop_quality(image, target)

    assert report.metrics["content_ratio"] < 0.01
    assert "blank_or_fragment" in {item.code for item in report.violations}
    assert not report.usable


def test_true_two_card_span_still_reports_multiple_cards() -> None:
    image = np.full((IMAGE_SIZE, IMAGE_SIZE, 3), 246, dtype=np.uint8)
    target, points = _quad_detection((600, 800), size=(700, 1050), angle=12)
    _paint_card(image, points, (60, 110, 175))
    peers = _same_size_peers(image)

    report = assess_crop_quality(image, target, peers=peers)

    violation = next(item for item in report.violations if item.code == "possible_multiple_cards")
    assert violation.severity == "error"
    assert report.metrics["area_to_peer_median"] > 2.0
    assert not report.usable
