from dataclasses import dataclass
from math import acos, degrees

import cv2
import numpy as np
from numpy.typing import NDArray


STANDARD_CARD_SHORT = 5
STANDARD_CARD_LONG = 7
MAX_DETECTION_EDGE = 1800
MAX_OUTPUT_EDGE = 2400
MIN_CARD_AREA_RATIO = 0.12
MAX_CARD_AREA_RATIO = 0.965
MIN_CONFIDENCE = 0.72


@dataclass(frozen=True)
class RectifiedImage:
    content: bytes
    content_type: str
    transformed: bool
    confidence: float


def _order_points(points: NDArray[np.float32]) -> NDArray[np.float32]:
    ordered = np.zeros((4, 2), dtype=np.float32)
    coordinate_sum = points.sum(axis=1)
    coordinate_delta = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(coordinate_sum)]
    ordered[2] = points[np.argmax(coordinate_sum)]
    ordered[1] = points[np.argmin(coordinate_delta)]
    ordered[3] = points[np.argmax(coordinate_delta)]
    return ordered


def _distance(first: NDArray[np.float32], second: NDArray[np.float32]) -> float:
    return float(np.linalg.norm(first - second))


def _corner_angle(
    previous: NDArray[np.float32],
    corner: NDArray[np.float32],
    following: NDArray[np.float32],
) -> float:
    first = previous - corner
    second = following - corner
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator <= 0:
        return 0
    cosine = float(np.clip(np.dot(first, second) / denominator, -1, 1))
    return degrees(acos(cosine))


def _candidate_score(
    points: NDArray[np.float32],
    image_area: float,
) -> tuple[float, float]:
    ordered = _order_points(points)
    area_ratio = abs(float(cv2.contourArea(ordered))) / image_area
    if not MIN_CARD_AREA_RATIO <= area_ratio <= MAX_CARD_AREA_RATIO:
        return 0, area_ratio
    widths = (_distance(ordered[0], ordered[1]), _distance(ordered[3], ordered[2]))
    heights = (_distance(ordered[0], ordered[3]), _distance(ordered[1], ordered[2]))
    short_side = min(sum(widths) / 2, sum(heights) / 2)
    long_side = max(sum(widths) / 2, sum(heights) / 2)
    if short_side <= 0:
        return 0, area_ratio
    aspect = long_side / short_side
    if not 1.20 <= aspect <= 1.78:
        return 0, area_ratio

    angles = [
        _corner_angle(ordered[(index - 1) % 4], ordered[index], ordered[(index + 1) % 4])
        for index in range(4)
    ]
    angle_quality = max(0.0, 1 - sum(abs(angle - 90) for angle in angles) / 180)
    aspect_quality = max(0.0, 1 - abs(aspect - 1.4) / 0.38)
    opposite_quality = 1 - min(
        1.0,
        abs(widths[0] - widths[1]) / max(widths)
        + abs(heights[0] - heights[1]) / max(heights),
    )
    area_quality = min(1.0, area_ratio / 0.45)
    score = (
        area_quality * 0.30
        + angle_quality * 0.28
        + aspect_quality * 0.27
        + opposite_quality * 0.15
    )
    return score, area_ratio


def _find_card_quad(image: NDArray[np.uint8]) -> tuple[NDArray[np.float32] | None, float]:
    height, width = image.shape[:2]
    scale = min(1.0, MAX_DETECTION_EDGE / max(height, width))
    working = (
        cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scale < 1
        else image.copy()
    )
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    median = float(np.median(blurred))
    lower = int(max(20, median * 0.55))
    upper = int(min(255, max(lower + 40, median * 1.45)))
    edges = cv2.Canny(blurred, lower, upper)
    edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)),
        iterations=2,
    )
    _, light_foreground = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    corner_pixels = np.concatenate(
        [
            working[: max(2, working.shape[0] // 14), : max(2, working.shape[1] // 14)].reshape(
                -1, 3
            ),
            working[: max(2, working.shape[0] // 14), -max(2, working.shape[1] // 14) :].reshape(
                -1, 3
            ),
            working[-max(2, working.shape[0] // 14) :, : max(2, working.shape[1] // 14)].reshape(
                -1, 3
            ),
            working[-max(2, working.shape[0] // 14) :, -max(2, working.shape[1] // 14) :].reshape(
                -1, 3
            ),
        ]
    )
    background_color = np.median(corner_pixels.astype(np.float32), axis=0)
    background_distance = np.linalg.norm(
        working.astype(np.float32) - background_color,
        axis=2,
    )
    different_from_background = np.where(background_distance > 32, 255, 0).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    masks = [
        edges,
        cv2.morphologyEx(light_foreground, cv2.MORPH_CLOSE, kernel, iterations=2),
        cv2.morphologyEx(
            different_from_background,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=2,
        ),
    ]
    contours = []
    for mask in masks:
        found, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(found)
    image_area = float(working.shape[0] * working.shape[1])
    best_points: NDArray[np.float32] | None = None
    best_score = 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:40]:
        perimeter = cv2.arcLength(contour, True)
        for epsilon in (0.015, 0.025, 0.04):
            polygon = cv2.approxPolyDP(contour, epsilon * perimeter, True)
            if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                continue
            points = polygon.reshape(4, 2).astype(np.float32)
            score, _ = _candidate_score(points, image_area)
            if score > best_score:
                best_points = points / scale
                best_score = score
            break
        rectangle = cv2.minAreaRect(contour)
        rectangle_area = rectangle[1][0] * rectangle[1][1]
        if rectangle_area <= 0 or cv2.contourArea(contour) / rectangle_area < 0.55:
            continue
        points = cv2.boxPoints(rectangle).astype(np.float32)
        score, _ = _candidate_score(points, image_area)
        score *= 0.96
        if score > best_score:
            best_points = points / scale
            best_score = score
    return best_points, best_score


def rectify_card_image(
    content: bytes,
    content_type: str,
    *,
    standardize_ratio: bool = True,
) -> RectifiedImage:
    """Automatically crop and perspective-correct a standard trading card.

    Ambiguous images are returned untouched. This deliberately prefers a missed crop
    over permanently storing a confidently wrong crop.
    """

    encoded = np.frombuffer(content, dtype=np.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None or min(image.shape[:2]) < 80:
        return RectifiedImage(content, content_type, False, 0)
    points, confidence = _find_card_quad(image)
    if points is None or confidence < MIN_CONFIDENCE:
        return RectifiedImage(content, content_type, False, confidence)

    ordered = _order_points(points)
    observed_width = max(
        _distance(ordered[0], ordered[1]),
        _distance(ordered[3], ordered[2]),
    )
    observed_height = max(
        _distance(ordered[0], ordered[3]),
        _distance(ordered[1], ordered[2]),
    )
    portrait = observed_height >= observed_width
    observed_long_edge = max(observed_width, observed_height)
    target_long = max(120, min(MAX_OUTPUT_EDGE, int(round(observed_long_edge))))
    observed_short_edge = min(observed_width, observed_height)
    target_short = (
        max(86, int(round(target_long * STANDARD_CARD_SHORT / STANDARD_CARD_LONG)))
        if standardize_ratio
        else max(86, int(round(target_long * observed_short_edge / observed_long_edge)))
    )
    target_width, target_height = (
        (target_short, target_long) if portrait else (target_long, target_short)
    )
    destination = np.array(
        [
            [0, 0],
            [target_width - 1, 0],
            [target_width - 1, target_height - 1],
            [0, target_height - 1],
        ],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(ordered, destination)
    rectified = cv2.warpPerspective(
        image,
        transform,
        (target_width, target_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    quality = 94 if content_type == "image/jpeg" else 96
    success, result = cv2.imencode(".jpg", rectified, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        return RectifiedImage(content, content_type, False, confidence)
    return RectifiedImage(result.tobytes(), "image/jpeg", True, confidence)
