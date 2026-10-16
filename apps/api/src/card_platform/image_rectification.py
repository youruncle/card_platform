from dataclasses import dataclass
from math import acos, degrees

import cv2
import numpy as np
from numpy.typing import NDArray


STANDARD_CARD_SHORT = 5
STANDARD_CARD_LONG = 7
MAX_DETECTION_EDGE = 1800
MAX_OUTPUT_EDGE = 2400
MIN_CARD_AREA_RATIO = 0.035
MAX_CARD_AREA_RATIO = 0.80
MIN_CONFIDENCE = 0.72
EDGE_MARGIN_RATIO = 0.018
MIN_CARD_ASPECT_RATIO = 1.06
MAX_CARD_ASPECT_RATIO = 2.65
MIN_CORNER_ANGLE = 28
MAX_CORNER_ANGLE = 152
MIN_OPPOSITE_EDGE_RATIO = 0.50
STANDARD_HOLDER_LONG_TO_SHORT = 1.65
CROP_SAFETY_PADDING_RATIO = 0.045


@dataclass(frozen=True)
class RectifiedImage:
    content: bytes
    content_type: str
    transformed: bool
    confidence: float
    normalized_quad: tuple[tuple[float, float], ...] | None = None


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


def _expand_quad(
    points: NDArray[np.float32], image_shape: tuple[int, int]
) -> NDArray[np.float32]:
    """Add a small safe border so holder/card edges are never shaved off."""
    image_height, image_width = image_shape
    center = points.mean(axis=0)
    expanded = center + (points - center) * (1 + CROP_SAFETY_PADDING_RATIO * 2)
    expanded[:, 0] = np.clip(expanded[:, 0], 0, image_width - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, image_height - 1)
    return expanded.astype(np.float32)


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
    image_shape: tuple[int, int],
    *,
    enforce_opposite_edge_stability: bool = True,
) -> tuple[float, float]:
    ordered = _order_points(points)
    image_height, image_width = image_shape
    image_area = float(image_height * image_width)
    area_ratio = abs(float(cv2.contourArea(ordered))) / image_area
    if not MIN_CARD_AREA_RATIO <= area_ratio <= MAX_CARD_AREA_RATIO:
        return 0, area_ratio
    widths = (_distance(ordered[0], ordered[1]), _distance(ordered[3], ordered[2]))
    heights = (_distance(ordered[0], ordered[3]), _distance(ordered[1], ordered[2]))
    sides = (*widths, *heights)
    if min(sides) / max(sides) < 0.12:
        return 0, area_ratio
    opposite_edge_ratios = (
        min(widths) / max(widths),
        min(heights) / max(heights),
    )
    # A strong projective view can shorten one opposite edge substantially, but
    # below one half the contour is too sensitive to a single misplaced corner.
    # This catches the scanner failure where an interior highlight supplied the
    # top-left corner (top/bottom ratio 0.468) while retaining the 0.54 ratio in
    # the severe-perspective regression fixture.
    if (
        enforce_opposite_edge_stability
        and min(opposite_edge_ratios) < MIN_OPPOSITE_EDGE_RATIO
    ):
        return 0, area_ratio
    short_side = min(sum(widths) / 2, sum(heights) / 2)
    long_side = max(sum(widths) / 2, sum(heights) / 2)
    if short_side <= 0:
        return 0, area_ratio
    aspect = long_side / short_side
    if not MIN_CARD_ASPECT_RATIO <= aspect <= MAX_CARD_ASPECT_RATIO:
        return 0, area_ratio

    edge_margin_x = image_width * EDGE_MARGIN_RATIO
    edge_margin_y = image_height * EDGE_MARGIN_RATIO
    touched_edges = sum(
        (
            float(ordered[:, 0].min()) <= edge_margin_x,
            float(ordered[:, 0].max()) >= image_width - edge_margin_x,
            float(ordered[:, 1].min()) <= edge_margin_y,
            float(ordered[:, 1].max()) >= image_height - edge_margin_y,
        )
    )
    # A real holder can be clipped on one side, but a huge quadrilateral
    # touching several frame edges is normally the scanner bed or camera frame.
    if touched_edges >= 3 or (area_ratio >= 0.58 and touched_edges >= 2):
        return 0, area_ratio

    angles = [
        _corner_angle(ordered[(index - 1) % 4], ordered[index], ordered[(index + 1) % 4])
        for index in range(4)
    ]
    # A rectangle viewed obliquely can legitimately have acute/obtuse image-space
    # corners and noticeably different opposite edge lengths. Keep rejecting
    # degenerate quadrilaterals, but do not score a real perspective trapezoid as
    # though it should already be a front-on rectangle.
    if min(angles) < MIN_CORNER_ANGLE or max(angles) > MAX_CORNER_ANGLE:
        return 0, area_ratio
    angle_quality = max(0.0, 1 - sum(abs(angle - 90) for angle in angles) / 300)
    aspect_quality = max(0.0, 1 - abs(aspect - 1.45) / 1.20)
    opposite_difference = (
        abs(widths[0] - widths[1]) / max(widths)
        + abs(heights[0] - heights[1]) / max(heights)
    )
    opposite_quality = 1 - min(
        1.0,
        opposite_difference / 2,
    )
    side_quality = min(1.0, min(sides) / max(sides) / 0.42)
    if area_ratio <= 0.08:
        area_quality = 0.72 + (area_ratio - MIN_CARD_AREA_RATIO) / 0.045 * 0.28
    elif area_ratio <= 0.22:
        area_quality = 1.0
    else:
        area_quality = max(0.20, 1 - (area_ratio - 0.22) / 0.58 * 0.80)
    score = (
        area_quality * 0.28
        + angle_quality * 0.22
        + aspect_quality * 0.18
        + opposite_quality * 0.12
        + side_quality * 0.20
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
    broad_foreground = np.where(background_distance > 24, 255, 0).astype(np.uint8)
    broad_foreground = cv2.morphologyEx(
        broad_foreground,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
    )
    broad_foreground = cv2.morphologyEx(
        broad_foreground,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (31, 31)),
        iterations=2,
    )
    masks = [
        edges,
        cv2.morphologyEx(light_foreground, cv2.MORPH_CLOSE, kernel, iterations=2),
        cv2.morphologyEx(
            different_from_background,
            cv2.MORPH_CLOSE,
            kernel,
            iterations=2,
        ),
        broad_foreground,
    ]
    contours = []
    for mask in masks:
        found, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(found)
    image_shape = working.shape[:2]
    best_polygon_points: NDArray[np.float32] | None = None
    best_polygon_score = 0.0
    best_rectangle_points: NDArray[np.float32] | None = None
    best_rectangle_score = 0.0
    best_unstable_polygon_score = 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:80]:
        # Broken/reflective holder edges often produce an irregular contour even
        # though its convex hull still contains the four true outer corners.
        # Sweep several simplification levels instead of accepting only the first
        # four-point approximation.
        for boundary in (contour, cv2.convexHull(contour)):
            perimeter = cv2.arcLength(boundary, True)
            for epsilon in (0.008, 0.012, 0.018, 0.025, 0.035, 0.05, 0.07):
                polygon = cv2.approxPolyDP(boundary, epsilon * perimeter, True)
                if len(polygon) != 4 or not cv2.isContourConvex(polygon):
                    continue
                points = polygon.reshape(4, 2).astype(np.float32)
                score, _ = _candidate_score(points, image_shape)
                if score == 0:
                    relaxed_score, _ = _candidate_score(
                        points,
                        image_shape,
                        enforce_opposite_edge_stability=False,
                    )
                    best_unstable_polygon_score = max(
                        best_unstable_polygon_score, relaxed_score
                    )
                # A genuine contour quadrilateral contains perspective information;
                # prefer it over a min-area rectangle, which can only rotate/crop.
                score = min(1.0, score + 0.05)
                if score > best_polygon_score:
                    best_polygon_points = points / scale
                    best_polygon_score = score
        rectangle = cv2.minAreaRect(contour)
        rectangle_area = rectangle[1][0] * rectangle[1][1]
        if rectangle_area <= 0 or cv2.contourArea(contour) / rectangle_area < 0.55:
            continue
        points = cv2.boxPoints(rectangle).astype(np.float32)
        score, _ = _candidate_score(points, image_shape)
        score *= 0.88
        if score > best_rectangle_score:
            best_rectangle_points = points / scale
            best_rectangle_score = score
    best_stable_score = max(best_polygon_score, best_rectangle_score)
    if best_unstable_polygon_score >= MIN_CONFIDENCE and (
        best_unstable_polygon_score >= best_stable_score
    ):
        return None, best_unstable_polygon_score
    if best_polygon_points is not None and best_polygon_score >= MIN_CONFIDENCE:
        return best_polygon_points, best_polygon_score
    if best_polygon_score >= best_rectangle_score:
        return best_polygon_points, best_polygon_score
    return best_rectangle_points, best_rectangle_score


def rectify_card_image(
    content: bytes,
    content_type: str,
    *,
    standardize_ratio: bool | None = True,
    orient_portrait: bool = False,
    fallback_normalized_quad: tuple[tuple[float, float], ...] | None = None,
    fallback_confidence: float = 0,
    prefer_fallback_quad: bool = False,
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
    if fallback_normalized_quad and (
        prefer_fallback_quad or points is None or confidence < MIN_CONFIDENCE
    ):
        image_height, image_width = image.shape[:2]
        fallback_points = np.array(fallback_normalized_quad, dtype=np.float32)
        fallback_points[:, 0] *= image_width
        fallback_points[:, 1] *= image_height
        fallback_score, _ = _candidate_score(fallback_points, image.shape[:2])
        propagated_confidence = min(fallback_score, fallback_confidence * 0.94)
        if propagated_confidence >= MIN_CONFIDENCE:
            points = fallback_points
            confidence = propagated_confidence
    if points is None or confidence < MIN_CONFIDENCE:
        return RectifiedImage(content, content_type, False, confidence)

    ordered = _order_points(_expand_quad(points, image.shape[:2]))
    return apply_card_quad(
        content, content_type,
        tuple((float(p[0]) / image.shape[1], float(p[1]) / image.shape[0]) for p in ordered),
        standardize_ratio=standardize_ratio, orient_portrait=orient_portrait,
        confidence=confidence,
    )


def apply_card_quad(
    content: bytes,
    content_type: str,
    normalized_quad: tuple[tuple[float, float], ...],
    *,
    standardize_ratio: bool | None = False,
    orient_portrait: bool = False,
    confidence: float = 0,
) -> RectifiedImage:
    """Apply an already validated TL/TR/BR/BL quad using the shared perspective transform.

    Manual recipes intentionally bypass detection confidence and safety expansion.
    Callers validate geometry before invoking this primitive.
    """
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("image cannot be decoded")
    ordered = np.asarray(normalized_quad, dtype=np.float32).copy()
    ordered[:, 0] *= image.shape[1]
    ordered[:, 1] *= image.shape[0]
    horizontal_edges = (
        _distance(ordered[0], ordered[1]),
        _distance(ordered[3], ordered[2]),
    )
    vertical_edges = (
        _distance(ordered[0], ordered[3]),
        _distance(ordered[1], ordered[2]),
    )
    observed_width = sum(horizontal_edges) / 2
    observed_height = sum(vertical_edges) / 2
    portrait = observed_height >= observed_width
    observed_long_edge = max(*horizontal_edges, *vertical_edges)
    target_long = max(120, min(MAX_OUTPUT_EDGE, int(round(observed_long_edge))))
    observed_aspect = max(observed_width, observed_height) / max(
        1, min(observed_width, observed_height)
    )
    if standardize_ratio is True:
        target_aspect = STANDARD_CARD_LONG / STANDARD_CARD_SHORT
    elif standardize_ratio is None:
        # Scanner input can be a raw card/top-loader or a tall graded slab. Snap
        # each family to a stable physical shape instead of preserving the
        # trapezoid's misleading image-space edge ratio.
        target_aspect = (
            STANDARD_HOLDER_LONG_TO_SHORT if observed_aspect >= 1.55 else STANDARD_CARD_LONG / STANDARD_CARD_SHORT
        )
    else:
        target_aspect = observed_aspect
    target_short = max(86, int(round(target_long / target_aspect)))
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
    if orient_portrait and rectified.shape[1] > rectified.shape[0]:
        # NETUM's document mat is landscape while standard cards/slabs are
        # portrait. With the guided placement used by the capture UI, rotating
        # counter-clockwise makes both card faces upright.
        rectified = cv2.rotate(rectified, cv2.ROTATE_90_COUNTERCLOCKWISE)
    quality = 94 if content_type == "image/jpeg" else 96
    success, result = cv2.imencode(".jpg", rectified, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        return RectifiedImage(content, content_type, False, confidence)
    image_height, image_width = image.shape[:2]
    normalized_quad = tuple(
        (float(point[0]) / image_width, float(point[1]) / image_height)
        for point in ordered
    )
    return RectifiedImage(
        result.tobytes(),
        "image/jpeg",
        True,
        confidence,
        normalized_quad,
    )
