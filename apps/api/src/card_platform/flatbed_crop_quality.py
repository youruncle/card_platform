"""Independent pixel-quality checks for flatbed scan crops.

The detector and an optional segmentation model answer *where* a card might
be.  This module answers the separate question: "does that rectangle produce
a usable, isolated card crop?"  In particular, model confidence is deliberately
not an input to any score below.  A confident geometry still has to pass the
same image-evidence checks as every other geometry.

All coordinates are normalized to the source scan.  ``peers`` is optional but
recommended: it lets the validator find a box whose physical span is roughly
two grid rows/columns even when the pixels in both cards look perfectly real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from card_platform.flatbed_detection import Detection


def _native_metric(value):
    """Keep NumPy scalar measurements JSON-safe without stringifying numbers."""
    return value.item() if isinstance(value, np.generic) else value


@dataclass(frozen=True)
class CropQualityViolation:
    code: str
    severity: str
    message: str
    evidence: dict[str, float | int | str]

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": {key: _native_metric(value) for key, value in self.evidence.items()},
        }


@dataclass(frozen=True)
class CropQualityReport:
    presence_score: float
    isolation_score: float
    boundary_score: float
    usability_score: float
    usable: bool
    needs_review: bool
    violations: tuple[CropQualityViolation, ...]
    metrics: dict[str, float | int]

    def as_dict(self) -> dict:
        return {
            "presence_score": float(self.presence_score),
            "isolation_score": float(self.isolation_score),
            "boundary_score": float(self.boundary_score),
            "usability_score": float(self.usability_score),
            "usable": bool(self.usable),
            "needs_review": bool(self.needs_review),
            "violations": [item.as_dict() for item in self.violations],
            "metrics": {key: _native_metric(value) for key, value in self.metrics.items()},
        }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def _score_between(value: float, low: float, high: float) -> float:
    return _clamp((value - low) / max(1e-9, high - low))


def _rectangle(
    detection: Detection, image_width: int, image_height: int
) -> tuple[int, int, int, int]:
    left = max(0, min(image_width, round(detection.x * image_width)))
    top = max(0, min(image_height, round(detection.y * image_height)))
    right = max(left, min(image_width, round((detection.x + detection.width) * image_width)))
    bottom = max(top, min(image_height, round((detection.y + detection.height) * image_height)))
    return left, top, right, bottom


def _ordered_polygon(
    detection: Detection, image_width: int, image_height: int
) -> np.ndarray | None:
    """Return a stable clockwise/counter-clockwise pixel-space quadrilateral."""
    normalized = detection.points or (
        (detection.x, detection.y),
        (detection.x + detection.width, detection.y),
        (detection.x + detection.width, detection.y + detection.height),
        (detection.x, detection.y + detection.height),
    )
    points = np.asarray(normalized, dtype=np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        return None
    points *= np.asarray((image_width, image_height), dtype=np.float32)
    center = points.mean(axis=0)
    order = np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))
    return points[order]


def _polygon_geometry(
    detection: Detection, image_width: int, image_height: int
) -> tuple[np.ndarray | None, float, float, float]:
    """Return points, short edge, long edge, and area in source pixels."""
    points = _ordered_polygon(detection, image_width, image_height)
    if points is None:
        return None, 0.0, 0.0, 0.0
    edge_lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    # Perspective can make opposite edges differ. Their means retain physical
    # card dimensions without inflating them to an axis-aligned bounding box.
    dimensions = (
        float((edge_lengths[0] + edge_lengths[2]) / 2),
        float((edge_lengths[1] + edge_lengths[3]) / 2),
    )
    return (
        points,
        min(dimensions),
        max(dimensions),
        abs(float(cv2.contourArea(points))),
    )


def _background_luma(image: np.ndarray) -> float:
    """Estimate a pale scanner bed without assuming it is pure white."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1]
    bright_cutoff = float(np.quantile(gray, 0.65))
    candidates = gray[(gray >= bright_cutoff) & (saturation < 45)]
    if candidates.size < 100:
        candidates = gray[gray >= bright_cutoff]
    return float(np.median(candidates)) if candidates.size else float(np.median(gray))


def _pixel_metrics(
    image: np.ndarray, detection: Detection, bed_luma: float
) -> dict[str, float | int]:
    image_height, image_width = image.shape[:2]
    points, _, _, polygon_area = _polygon_geometry(detection, image_width, image_height)
    if points is None:
        left, top, right, bottom = _rectangle(detection, image_width, image_height)
    else:
        left = max(0, min(image_width, int(np.floor(points[:, 0].min()))))
        top = max(0, min(image_height, int(np.floor(points[:, 1].min()))))
        right = max(left, min(image_width, int(np.ceil(points[:, 0].max()))))
        bottom = max(top, min(image_height, int(np.ceil(points[:, 1].max()))))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return {
            "crop_width_px": 0,
            "crop_height_px": 0,
            "content_ratio": 0.0,
            "edge_ratio": 0.0,
            "luma_stddev": 0.0,
            "row_content_coverage": 0.0,
            "column_content_coverage": 0.0,
            "row_content_span": 0.0,
            "column_content_span": 0.0,
            "content_axis_coverage": 0.0,
            "content_axis_signal_density": 0.0,
            "polygon_area_px": polygon_area,
            "polygon_mask_ratio": 0.0,
        }

    polygon_mask = np.zeros(crop.shape[:2], dtype=np.uint8)
    if points is not None and polygon_area > 0:
        local_points = np.rint(points - np.asarray((left, top), dtype=np.float32)).astype(np.int32)
        cv2.fillConvexPoly(polygon_mask, local_points, 255)
    # Neutralise pixels outside the quad before any downsampling, otherwise
    # interpolation can bleed a neighbouring card into the polygon boundary.
    crop = crop.copy()
    crop[polygon_mask == 0] = np.uint8(round(_clamp(bed_luma, 0, 255)))

    # Bound CPU and memory for 600-DPI originals while retaining far more than
    # enough detail to distinguish text/artwork from an empty scanner bed.
    if crop.shape[0] * crop.shape[1] > 1_200_000:
        scale = (1_200_000 / (crop.shape[0] * crop.shape[1])) ** 0.5
        crop = cv2.resize(
            crop,
            (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        polygon_mask = cv2.resize(
            polygon_mask,
            (crop.shape[1], crop.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        )

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1]
    inside_polygon = polygon_mask > 0
    if not inside_polygon.any():
        return {
            "crop_width_px": right - left,
            "crop_height_px": bottom - top,
            "content_ratio": 0.0,
            "edge_ratio": 0.0,
            "luma_stddev": 0.0,
            "row_content_coverage": 0.0,
            "column_content_coverage": 0.0,
            "row_content_span": 0.0,
            "column_content_span": 0.0,
            "content_axis_coverage": 0.0,
            "content_axis_signal_density": 0.0,
            "polygon_area_px": polygon_area,
            "polygon_mask_ratio": 0.0,
        }
    content = ((saturation > 26) | (gray < bed_luma - 24)) & inside_polygon
    # Content immediately outside a rotated quad must not manufacture an edge
    # *inside* an otherwise blank candidate. Neutralising it before Canny still
    # preserves a real card boundary whenever the interior differs from the bed.
    masked_gray = gray.copy()
    masked_gray[~inside_polygon] = np.uint8(round(_clamp(bed_luma, 0, 255)))
    edges = (cv2.Canny(masked_gray, 45, 120) > 0) & inside_polygon

    # Presence must cover both axes.  This is what separates a real pale card
    # (whose border/text is spatially distributed) from a blank cell containing
    # only a narrow sliver of its neighbour.
    row_pixels = inside_polygon.sum(axis=1)
    column_pixels = inside_polygon.sum(axis=0)
    valid_rows = row_pixels > 0
    valid_columns = column_pixels > 0
    row_signal = valid_rows & (
        (content.sum(axis=1) / np.maximum(1, row_pixels) > 0.025)
        | (edges.sum(axis=1) / np.maximum(1, row_pixels) > 0.008)
    )
    column_signal = valid_columns & (
        (content.sum(axis=0) / np.maximum(1, column_pixels) > 0.025)
        | (edges.sum(axis=0) / np.maximum(1, column_pixels) > 0.008)
    )
    row_coverage = float(row_signal.sum() / max(1, valid_rows.sum()))
    column_coverage = float(column_signal.sum() / max(1, valid_columns.sum()))

    def signal_span(signal: np.ndarray, valid: np.ndarray) -> float:
        active = np.flatnonzero(signal)
        support = np.flatnonzero(valid)
        if not active.size or not support.size:
            return 0.0
        return float((active[-1] - active[0] + 1) / (support[-1] - support[0] + 1))

    row_span = signal_span(row_signal, valid_rows)
    column_span = signal_span(column_signal, valid_columns)
    polygon_pixels = int(inside_polygon.sum())

    return {
        "crop_width_px": right - left,
        "crop_height_px": bottom - top,
        "content_ratio": float(content.sum() / polygon_pixels),
        "edge_ratio": float(edges.sum() / polygon_pixels),
        "luma_stddev": float(gray[inside_polygon].std()),
        "row_content_coverage": row_coverage,
        "column_content_coverage": column_coverage,
        "row_content_span": row_span,
        "column_content_span": column_span,
        # Keep ``content_axis_coverage`` as the outer span for compatibility
        # with existing audit payloads.  A pair of thin, distant borders can
        # make that span approach 1.0, so also record how much of the weaker
        # axis is actually supported by content/edges.
        "content_axis_coverage": min(row_span, column_span),
        "content_axis_signal_density": min(row_coverage, column_coverage),
        "polygon_area_px": polygon_area,
        "polygon_mask_ratio": float(inside_polygon.mean()),
    }


def _intersection_over_smaller(
    left: Detection, right: Detection, image_width: int, image_height: int
) -> float:
    left_points, _, _, left_area = _polygon_geometry(left, image_width, image_height)
    right_points, _, _, right_area = _polygon_geometry(right, image_width, image_height)
    smaller = min(left_area, right_area)
    if left_points is None or right_points is None or smaller <= 0:
        return 0.0
    intersection, _ = cv2.intersectConvexConvex(left_points, right_points)
    return float(intersection) / smaller


def _boundary_content_continuity(image: np.ndarray, detection: Detection, bed_luma: float) -> float:
    """Measure dense image content continuing through a proposed crop edge.

    Presence inside a box cannot distinguish a whole card from half a card.
    Sample on both sides of each *actual polygon* edge, independent of the
    detector's confidence or grid. Several normal offsets must all contain
    ink; this avoids treating a thin holder outline as a severed card. This
    is an advisory signal, not permission to merge neighbouring cards.
    """
    height, width = image.shape[:2]
    points = _ordered_polygon(detection, width, height)
    if points is None:
        return 0.0
    lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    distance = max(3.0, float(lengths.min()) * 0.018)
    maximum = 0.0
    for start, end, length in zip(points, np.roll(points, -1, axis=0), lengths, strict=True):
        if length < 12:
            continue
        direction = (end - start) / length
        normal = np.array([-direction[1], direction[0]])
        # Exclude corners, where another card or a rounded holder is common.
        line = start + np.linspace(0.10, 0.90, 160)[:, None] * (end - start)
        offsets = np.array([-2.0, -1.0, 0.0, 1.0, 2.0]) * distance
        samples = line[None, :, :] + offsets[:, None, None] * normal
        valid = (
            (samples[:, :, 0] >= 0)
            & (samples[:, :, 0] < width)
            & (samples[:, :, 1] >= 0)
            & (samples[:, :, 1] < height)
        ).all(axis=0)
        if valid.mean() < 0.90:
            # There is no evidence outside the source; report that separately.
            continue
        strip = cv2.remap(
            image,
            samples[:, :, 0].astype(np.float32),
            samples[:, :, 1].astype(np.float32),
            cv2.INTER_LINEAR,
        )
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        saturation = cv2.cvtColor(strip, cv2.COLOR_BGR2HSV)[:, :, 1]
        # Broad grey/blue transparent rims can continue into a neighbouring
        # holder without cutting either card. Require printed colour or dark
        # ink here, stronger than the low-contrast *presence* threshold above.
        content = ((saturation > 45) & (gray < bed_luma - 20)) | (gray < bed_luma - 60)
        continuous = content.all(axis=0) & valid
        maximum = max(maximum, float(continuous.sum() / valid.sum()))
    return maximum


def assess_crop_quality(
    image: np.ndarray,
    detection: Detection,
    *,
    peers: Sequence[Detection] = (),
    background_luma: float | None = None,
) -> CropQualityReport:
    """Return an explainable quality report for one proposed crop.

    Thresholds intentionally favour recall for pale/low-contrast card backs.
    The report is advisory: ``usable=False`` records strong pixel evidence but
    does not delete, hide, or block a region from reaching the editing UI.
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a BGR image with three channels")

    image_height, image_width = image.shape[:2]
    bed_luma = _background_luma(image) if background_luma is None else background_luma
    metrics = _pixel_metrics(image, detection, bed_luma)
    violations: list[CropQualityViolation] = []
    if "quad_unresolved" in detection.evidence:
        violations.append(CropQualityViolation(
            "card_outline_unresolved", "error", "未确认真实卡片四角，请人工校正",
            {"geometry_source": "search_region_only"},
        ))

    content_score = _score_between(float(metrics["content_ratio"]), 0.015, 0.16)
    edge_score = _score_between(float(metrics["edge_ratio"]), 0.001, 0.022)
    texture_score = _score_between(float(metrics["luma_stddev"]), 4.0, 30.0)
    coverage_score = _score_between(float(metrics["content_axis_coverage"]), 0.08, 0.48)
    presence_score = _clamp(
        0.25 * content_score + 0.20 * edge_score + 0.15 * texture_score + 0.40 * coverage_score
    )
    sparse_span_only = (
        float(metrics["content_axis_coverage"]) >= 0.28
        and float(metrics["content_axis_signal_density"]) < 0.08
        and float(metrics["content_ratio"]) < 0.08
        and float(metrics["edge_ratio"]) < 0.008
    )
    metrics["sparse_span_only"] = int(sparse_span_only)

    if int(metrics["crop_width_px"]) < 8 or int(metrics["crop_height_px"]) < 8:
        presence_score = 0.0
        violations.append(
            CropQualityViolation(
                "invalid_crop_geometry",
                "error",
                "裁图范围为空或过小",
                {
                    "crop_width_px": int(metrics["crop_width_px"]),
                    "crop_height_px": int(metrics["crop_height_px"]),
                },
            )
        )
    elif (
        float(metrics["content_axis_coverage"]) < 0.28 or presence_score < 0.22 or sparse_span_only
    ):
        violations.append(
            CropQualityViolation(
                "blank_or_fragment",
                "error",
                "区域基本为空，或只包含邻卡的一条窄边",
                {
                    "presence_score": round(presence_score, 4),
                    "content_axis_coverage": round(float(metrics["content_axis_coverage"]), 4),
                    "content_axis_signal_density": round(
                        float(metrics["content_axis_signal_density"]), 4
                    ),
                    "content_ratio": round(float(metrics["content_ratio"]), 4),
                    "edge_ratio": round(float(metrics["edge_ratio"]), 4),
                },
            )
        )
    elif float(metrics["content_axis_coverage"]) < 0.45 or presence_score < 0.55:
        violations.append(
            CropQualityViolation(
                "weak_card_presence",
                "warning",
                "卡牌主体覆盖不足，建议人工确认",
                {
                    "presence_score": round(presence_score, 4),
                    "content_axis_coverage": round(float(metrics["content_axis_coverage"]), 4),
                },
            )
        )

    _, crop_short_edge, crop_long_edge, crop_area = _polygon_geometry(
        detection, image_width, image_height
    )
    pixel_aspect_ratio = crop_long_edge / max(1.0, crop_short_edge)
    metrics["pixel_aspect_ratio"] = pixel_aspect_ratio

    peer_overlap = max(
        (
            _intersection_over_smaller(detection, peer, image_width, image_height)
            for peer in peers
            if peer is not detection
        ),
        default=0.0,
    )
    metrics["max_peer_overlap"] = peer_overlap
    isolation_penalty = 0.0
    if peer_overlap > 0.12:
        isolation_penalty = max(isolation_penalty, _score_between(peer_overlap, 0.12, 0.45))
        violations.append(
            CropQualityViolation(
                "overlaps_neighbour",
                "error" if peer_overlap >= 0.25 else "warning",
                "裁图与另一张候选卡明显重叠",
                {"intersection_over_smaller": round(peer_overlap, 4)},
            )
        )

    all_detections = [detection, *(peer for peer in peers if peer is not detection)]
    geometries = [_polygon_geometry(item, image_width, image_height) for item in all_detections]
    valid_geometries = [item for item in geometries if item[3] > 0]
    if len(valid_geometries) >= 4 and crop_area > 0:
        bounding_widths = np.array(
            [float(item[0][:, 0].max() - item[0][:, 0].min()) for item in valid_geometries]
        )
        bounding_heights = np.array(
            [float(item[0][:, 1].max() - item[0][:, 1].min()) for item in valid_geometries]
        )
        short_edges = np.array([item[1] for item in valid_geometries])
        long_edges = np.array([item[2] for item in valid_geometries])
        areas = np.array([item[3] for item in valid_geometries])
        target_points = valid_geometries[0][0]
        width_ratio = float(target_points[:, 0].max() - target_points[:, 0].min()) / max(
            1.0, float(np.median(bounding_widths))
        )
        height_ratio = float(target_points[:, 1].max() - target_points[:, 1].min()) / max(
            1.0, float(np.median(bounding_heights))
        )
        short_edge_ratio = crop_short_edge / max(1.0, float(np.median(short_edges)))
        long_edge_ratio = crop_long_edge / max(1.0, float(np.median(long_edges)))
        area_ratio = crop_area / max(1.0, float(np.median(areas)))
        # Retain the evidence flag in audit payloads, but make size decisions
        # orientation-invariant for every valid quadrilateral.
        orientation_invariant_peer_size = (
            "grading_label_association" in detection.evidence
            and "mixed_format_instance" in detection.evidence
            and 1.30 <= pixel_aspect_ratio <= 2.10
        )
        physical_span_ratio = max(short_edge_ratio, long_edge_ratio)
        metrics.update(
            {
                # Preserve scanner-space diagnostics for historical consumers;
                # they are deliberately not used for the merge decision.
                "width_to_peer_median": width_ratio,
                "height_to_peer_median": height_ratio,
                "short_edge_to_peer_median": short_edge_ratio,
                "long_edge_to_peer_median": long_edge_ratio,
                "orientation_invariant_peer_size": int(orientation_invariant_peer_size),
                "area_to_peer_median": area_ratio,
                # Keep the historical key while changing it from scanner axes
                # to the physical short/long axes of the crop polygon.
                "axis_span_to_peer_median": physical_span_ratio,
            }
        )
        multiple_error = (physical_span_ratio >= 1.65 and area_ratio >= 1.30) or area_ratio >= 1.85
        multiple_warning = physical_span_ratio >= 1.48 or area_ratio >= 1.60
        if multiple_error or multiple_warning:
            severity = "error" if multiple_error else "warning"
            isolation_penalty = max(
                isolation_penalty,
                0.82 if multiple_error else 0.45,
            )
            violations.append(
                CropQualityViolation(
                    "possible_multiple_cards",
                    severity,
                    "裁图跨度明显大于同版其他卡，疑似跨卡或包含两个主体",
                    {
                        "axis_span_to_peer_median": round(physical_span_ratio, 4),
                        "area_to_peer_median": round(area_ratio, 4),
                    },
                )
            )
    elif pixel_aspect_ratio > 2.15:
        isolation_penalty = max(isolation_penalty, 0.45)
        violations.append(
            CropQualityViolation(
                "unusual_crop_aspect",
                "warning",
                "裁图长宽比异常，可能包含额外区域",
                {"pixel_aspect_ratio": round(pixel_aspect_ratio, 4)},
            )
        )

    isolation_score = _clamp(1.0 - isolation_penalty)

    points = detection.points or (
        (detection.x, detection.y),
        (detection.x + detection.width, detection.y),
        (detection.x + detection.width, detection.y + detection.height),
        (detection.x, detection.y + detection.height),
    )
    coordinates = [coordinate for point in points for coordinate in point]
    outside_distance = max(
        [
            0.0,
            *(-coordinate for coordinate in coordinates),
            *(coordinate - 1.0 for coordinate in coordinates),
        ]
    )
    source_edges = {
        "left": min(point[0] for point in points) <= 0.002,
        "top": min(point[1] for point in points) <= 0.002,
        "right": max(point[0] for point in points) >= 0.998,
        "bottom": max(point[1] for point in points) >= 0.998,
    }
    # Object/contour geometry can contain NumPy coordinates. Counting their
    # comparison results directly produces np.int64, which also propagates to
    # scores and np.bool_ usability; neither integer nor boolean is JSON-safe.
    source_edge_count = int(sum(source_edges.values()))
    metrics["source_edge_count"] = source_edge_count
    metrics["outside_normalized_distance"] = outside_distance
    boundary_score = _clamp(1.0 - 0.13 * source_edge_count - min(0.7, outside_distance * 20))
    severe_source_clamp = "source_bounds_severe_overflow" in detection.evidence
    metrics["severe_source_clamp"] = int(severe_source_clamp)
    if severe_source_clamp:
        # Final output coordinates must be valid, but clipping them cannot
        # erase evidence that a material part of the original proposal was
        # outside the scan. Keep this distinct from ordinary edge contact.
        boundary_score = min(boundary_score, 0.30)
    boundary_continuity = _boundary_content_continuity(image, detection, bed_luma)
    metrics["max_boundary_content_continuity"] = boundary_continuity
    if boundary_continuity >= 0.30:
        boundary_score = min(boundary_score, 0.55)
        violations.append(
            CropQualityViolation(
                "content_crosses_crop_boundary",
                "warning",
                "裁图边界两侧存在连续卡面内容，疑似切断卡片或带入邻卡，请确认完整边界",
                {"boundary_content_continuity": round(boundary_continuity, 4)},
            )
        )
    if outside_distance > 0 or severe_source_clamp:
        violations.append(
            CropQualityViolation(
                "geometry_outside_source",
                "error",
                "裁图原始边界明显超出扫描范围，已限制为可用像素，请校正"
                if severe_source_clamp
                else "裁图坐标超出原始扫描范围",
                {
                    "outside_normalized_distance": round(outside_distance, 5),
                    "severe_source_clamp": int(severe_source_clamp),
                },
            )
        )
    elif source_edge_count:
        violations.append(
            CropQualityViolation(
                "source_edge_contact",
                "warning",
                "卡牌贴近扫描原图边界，可能缺少边缘留白",
                {
                    "edge_count": source_edge_count,
                    "edges": ",".join(name for name, present in source_edges.items() if present),
                },
            )
        )

    # A weighted mean is easy to interpret, while the limiter prevents two
    # excellent dimensions from hiding a blank crop or a clear two-card crop.
    weighted = 0.45 * presence_score + 0.35 * isolation_score + 0.20 * boundary_score
    usability_score = min(
        weighted,
        0.25 + 0.75 * presence_score,
        0.25 + 0.75 * isolation_score,
    )
    error_present = any(item.severity == "error" for item in violations)
    usable = bool(not error_present and usability_score >= 0.55)
    needs_review = bool(violations) or bool(usability_score < 0.75)

    # Stable precision keeps JSON/audit diffs readable without discarding the
    # unrounded diagnostic metrics.
    return CropQualityReport(
        presence_score=round(float(presence_score), 4),
        isolation_score=round(float(isolation_score), 4),
        boundary_score=round(float(boundary_score), 4),
        usability_score=round(float(usability_score), 4),
        usable=usable,
        needs_review=needs_review,
        violations=tuple(violations),
        metrics=metrics,
    )


def assess_sheet_crop_quality(
    image: np.ndarray, detections: Sequence[Detection]
) -> list[CropQualityReport]:
    """Share immutable page statistics; never rescan the whole bed per crop."""
    if not detections:
        return []
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a BGR image with three channels")
    bed_luma = _background_luma(image)
    return [
        assess_crop_quality(image, item, peers=detections, background_luma=bed_luma)
        for item in detections
    ]
