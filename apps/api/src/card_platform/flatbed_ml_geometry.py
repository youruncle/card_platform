from __future__ import annotations

import math

import cv2
import numpy as np


def canonical_long_edge_angle(points: np.ndarray) -> float:
    edges = np.roll(points, -1, axis=0) - points
    edge = edges[int(np.argmax(np.linalg.norm(edges, axis=1)))]
    angle = math.degrees(math.atan2(float(edge[1]), float(edge[0])))
    while angle < -90:
        angle += 180
    while angle >= 90:
        angle -= 180
    return angle


def consensus_edge_quad(image: np.ndarray, coarse: dict[str, float]) -> dict[str, object] | None:
    """Find a rotated card rectangle only when several edge passes agree."""
    image_height, image_width = image.shape[:2]
    x = float(coarse["x"])
    y = float(coarse["y"])
    width = float(coarse["width"])
    height = float(coarse["height"])
    margin_x = width * 0.04
    margin_y = height * 0.18
    left = max(0, round((x - margin_x) * image_width))
    top = max(0, round((y - margin_y) * image_height))
    right = min(image_width, round((x + width + margin_x) * image_width))
    bottom = min(image_height, round((y + height + margin_y) * image_height))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    coarse_area = width * image_width * height * image_height
    candidates: list[dict[str, object]] = []
    for threshold_index, (low, high) in enumerate(((20, 60), (35, 100), (50, 150), (80, 200))):
        edges = cv2.Canny(gray, low, high)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8))
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            rectangle = cv2.minAreaRect(contour)
            (center_x, center_y), (rect_width, rect_height), _ = rectangle
            rect_area = rect_width * rect_height
            if rect_area <= 0:
                continue
            area_ratio = rect_area / coarse_area
            aspect = max(rect_width, rect_height) / max(1.0, min(rect_width, rect_height))
            if not 0.55 <= area_ratio <= 1.30 or not 1.18 <= aspect <= 1.75:
                continue
            normalized_center_x = (center_x + left) / image_width
            normalized_center_y = (center_y + top) / image_height
            if (
                abs(normalized_center_x - (x + width / 2)) > width * 0.28
                or abs(normalized_center_y - (y + height / 2)) > height * 0.32
            ):
                continue
            local_points = cv2.boxPoints(rectangle)
            angle = canonical_long_edge_angle(local_points)
            if not 1.5 <= abs(angle) <= 12:
                continue
            points = np.column_stack(
                (
                    (local_points[:, 0] + left) / image_width,
                    (local_points[:, 1] + top) / image_height,
                )
            )
            candidates.append(
                {
                    "points": points,
                    "angle": angle,
                    "area_ratio": area_ratio,
                    "aspect": aspect,
                    "center": np.array(
                        [normalized_center_x, normalized_center_y], dtype=np.float64
                    ),
                    "contour_fill": float(cv2.contourArea(contour)) / rect_area,
                    "threshold_index": threshold_index,
                }
            )
    best: tuple[float, dict[str, object], int] | None = None
    for candidate in candidates:
        peers = [
            other
            for other in candidates
            if abs(float(other["angle"]) - float(candidate["angle"])) <= 1.2
            and abs(float(other["area_ratio"]) - float(candidate["area_ratio"])) <= 0.16
            and np.linalg.norm(other["center"] - candidate["center"]) <= max(width, height) * 0.07
        ]
        thresholds_agree = len({int(other["threshold_index"]) for other in peers})
        if thresholds_agree < 2:
            continue
        score = (
            thresholds_agree * 2
            - abs(float(candidate["area_ratio"]) - 0.95)
            - abs(float(candidate["aspect"]) - 1.40) * 0.3
            + min(0.5, float(candidate["contour_fill"]))
        )
        if best is None or score > best[0]:
            best = score, candidate, thresholds_agree
    if best is None:
        return None
    _, candidate, agreement = best
    points = np.asarray(candidate["points"], dtype=np.float64)
    return {
        "points": [[float(value) for value in point] for point in points],
        "angle": float(candidate["angle"]),
        "agreement": agreement,
        "area_ratio": float(candidate["area_ratio"]),
    }


def strong_axis_aligned_edge_quad(
    image: np.ndarray, coarse: dict[str, float]
) -> dict[str, object] | None:
    """Recover a strong axis-aligned card edge after segmentation misses.

    This is intentionally narrower than ``consensus_edge_quad``. It is used
    only for an unresolved grid cell and requires a dense rectangular contour
    that is materially smaller than the allocation cell. That prevents an
    entire grid slot or a small decorative frame from becoming the crop.
    """
    image_height, image_width = image.shape[:2]
    x = float(coarse["x"])
    y = float(coarse["y"])
    width = float(coarse["width"])
    height = float(coarse["height"])
    margin_x = width * 0.04
    margin_y = height * 0.12
    left = max(0, round((x - margin_x) * image_width))
    top = max(0, round((y - margin_y) * image_height))
    right = min(image_width, round((x + width + margin_x) * image_width))
    bottom = min(image_height, round((y + height + margin_y) * image_height))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return None

    gray = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    coarse_area = width * image_width * height * image_height
    candidates: list[tuple[float, dict[str, object]]] = []
    for threshold_index, (low, high) in enumerate(
        ((20, 60), (35, 100), (50, 150), (80, 200))
    ):
        edges = cv2.Canny(gray, low, high)
        edges = cv2.morphologyEx(
            edges, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8)
        )
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            rectangle = cv2.minAreaRect(contour)
            (center_x, center_y), (rect_width, rect_height), _ = rectangle
            rect_area = rect_width * rect_height
            if rect_area <= 0:
                continue
            area_ratio = rect_area / coarse_area
            aspect = max(rect_width, rect_height) / max(
                1.0, min(rect_width, rect_height)
            )
            contour_fill = float(cv2.contourArea(contour)) / rect_area
            local_points = cv2.boxPoints(rectangle)
            angle = canonical_long_edge_angle(local_points)
            normalized_center_x = (center_x + left) / image_width
            normalized_center_y = (center_y + top) / image_height
            if not (
                0.52 <= area_ratio <= 0.92
                and 1.20 <= aspect <= 1.72
                and contour_fill >= 0.72
                and abs(angle) <= 1.25
                and abs(normalized_center_x - (x + width / 2)) <= width * 0.32
                and abs(normalized_center_y - (y + height / 2)) <= height * 0.36
            ):
                continue
            points = np.column_stack(
                (
                    (local_points[:, 0] + left) / image_width,
                    (local_points[:, 1] + top) / image_height,
                )
            )
            # If the allocation cell itself touches the scanner boundary, the
            # recovered object must agree on that physical edge. Otherwise an
            # unrelated inner artwork rectangle could win by fill ratio.
            point_left, point_top = points.min(axis=0)
            point_right, point_bottom = points.max(axis=0)
            boundary_checks = (
                x > 0.002 or point_left <= 0.012,
                y > 0.002 or point_top <= 0.012,
                x + width < 0.998 or point_right >= 0.988,
                y + height < 0.998 or point_bottom >= 0.988,
            )
            if not all(boundary_checks):
                continue
            score = (
                contour_fill
                - abs(area_ratio - 0.72) * 0.35
                - abs(aspect - 1.40) * 0.12
            )
            candidates.append(
                (
                    score,
                    {
                        "points": points,
                        "angle": angle,
                        "agreement": 1,
                        "area_ratio": area_ratio,
                        "threshold_index": threshold_index,
                    },
                )
            )
    if not candidates:
        return None
    _, candidate = max(candidates, key=lambda item: item[0])
    points = np.asarray(candidate["points"], dtype=np.float64)
    return {
        "points": [[float(value) for value in point] for point in points],
        "angle": float(candidate["angle"]),
        "agreement": int(candidate["agreement"]),
        "area_ratio": float(candidate["area_ratio"]),
    }
