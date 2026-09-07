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
            "evidence": self.evidence,
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
            "presence_score": self.presence_score,
            "isolation_score": self.isolation_score,
            "boundary_score": self.boundary_score,
            "usability_score": self.usability_score,
            "usable": self.usable,
            "needs_review": self.needs_review,
            "violations": [item.as_dict() for item in self.violations],
            "metrics": self.metrics,
        }


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def _score_between(value: float, low: float, high: float) -> float:
    return _clamp((value - low) / max(1e-9, high - low))


def _rectangle(detection: Detection, image_width: int, image_height: int) -> tuple[int, int, int, int]:
    left = max(0, min(image_width, round(detection.x * image_width)))
    top = max(0, min(image_height, round(detection.y * image_height)))
    right = max(left, min(image_width, round((detection.x + detection.width) * image_width)))
    bottom = max(top, min(image_height, round((detection.y + detection.height) * image_height)))
    return left, top, right, bottom


def _background_luma(image: np.ndarray) -> float:
    """Estimate a pale scanner bed without assuming it is pure white."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1]
    bright_cutoff = float(np.quantile(gray, 0.65))
    candidates = gray[(gray >= bright_cutoff) & (saturation < 45)]
    if candidates.size < 100:
        candidates = gray[gray >= bright_cutoff]
    return float(np.median(candidates)) if candidates.size else float(np.median(gray))


def _pixel_metrics(image: np.ndarray, detection: Detection) -> dict[str, float | int]:
    image_height, image_width = image.shape[:2]
    left, top, right, bottom = _rectangle(detection, image_width, image_height)
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
        }

    # Bound CPU and memory for 600-DPI originals while retaining far more than
    # enough detail to distinguish text/artwork from an empty scanner bed.
    if crop.shape[0] * crop.shape[1] > 1_200_000:
        scale = (1_200_000 / (crop.shape[0] * crop.shape[1])) ** 0.5
        crop = cv2.resize(
            crop,
            (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1]
    bed_luma = _background_luma(image)
    content = (saturation > 26) | (gray < bed_luma - 24)
    edges = cv2.Canny(gray, 45, 120) > 0

    # Presence must cover both axes.  This is what separates a real pale card
    # (whose border/text is spatially distributed) from a blank cell containing
    # only a narrow sliver of its neighbour.
    row_signal = (content.mean(axis=1) > 0.025) | (edges.mean(axis=1) > 0.008)
    column_signal = (content.mean(axis=0) > 0.025) | (edges.mean(axis=0) > 0.008)
    row_coverage = float(row_signal.mean())
    column_coverage = float(column_signal.mean())

    def signal_span(signal: np.ndarray) -> float:
        active = np.flatnonzero(signal)
        if not active.size:
            return 0.0
        return float((active[-1] - active[0] + 1) / signal.size)

    row_span = signal_span(row_signal)
    column_span = signal_span(column_signal)

    return {
        "crop_width_px": right - left,
        "crop_height_px": bottom - top,
        "content_ratio": float(content.mean()),
        "edge_ratio": float(edges.mean()),
        "luma_stddev": float(gray.std()),
        "row_content_coverage": row_coverage,
        "column_content_coverage": column_coverage,
        "row_content_span": row_span,
        "column_content_span": column_span,
        "content_axis_coverage": min(row_span, column_span),
    }


def _intersection_over_smaller(left: Detection, right: Detection) -> float:
    intersection_width = max(
        0.0,
        min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
    )
    intersection_height = max(
        0.0,
        min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
    )
    smaller = min(left.width * left.height, right.width * right.height)
    return intersection_width * intersection_height / smaller if smaller else 0.0


def assess_crop_quality(
    image: np.ndarray,
    detection: Detection,
    *,
    peers: Sequence[Detection] = (),
) -> CropQualityReport:
    """Return an explainable quality report for one proposed crop.

    Thresholds intentionally favour recall for pale/low-contrast card backs.
    The report is advisory: ``usable=False`` records strong pixel evidence but
    does not delete, hide, or block a region from reaching the editing UI.
    """
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("image must be a BGR image with three channels")

    image_height, image_width = image.shape[:2]
    metrics = _pixel_metrics(image, detection)
    violations: list[CropQualityViolation] = []

    content_score = _score_between(float(metrics["content_ratio"]), 0.015, 0.16)
    edge_score = _score_between(float(metrics["edge_ratio"]), 0.001, 0.022)
    texture_score = _score_between(float(metrics["luma_stddev"]), 4.0, 30.0)
    coverage_score = _score_between(float(metrics["content_axis_coverage"]), 0.08, 0.48)
    presence_score = _clamp(
        0.25 * content_score
        + 0.20 * edge_score
        + 0.15 * texture_score
        + 0.40 * coverage_score
    )

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
    elif float(metrics["content_axis_coverage"]) < 0.28 or presence_score < 0.22:
        violations.append(
            CropQualityViolation(
                "blank_or_fragment",
                "error",
                "区域基本为空，或只包含邻卡的一条窄边",
                {
                    "presence_score": round(presence_score, 4),
                    "content_axis_coverage": round(float(metrics["content_axis_coverage"]), 4),
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

    crop_width = float(metrics["crop_width_px"])
    crop_height = float(metrics["crop_height_px"])
    crop_area = crop_width * crop_height
    pixel_aspect_ratio = max(crop_width, crop_height) / max(1.0, min(crop_width, crop_height))
    metrics["pixel_aspect_ratio"] = pixel_aspect_ratio

    peer_overlap = max(
        (_intersection_over_smaller(detection, peer) for peer in peers if peer is not detection),
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
    if len(all_detections) >= 4 and crop_area > 0:
        widths = np.array([item.width * image_width for item in all_detections])
        heights = np.array([item.height * image_height for item in all_detections])
        areas = widths * heights
        width_ratio = crop_width / max(1.0, float(np.median(widths)))
        height_ratio = crop_height / max(1.0, float(np.median(heights)))
        area_ratio = crop_area / max(1.0, float(np.median(areas)))
        axis_span_ratio = max(width_ratio, height_ratio)
        metrics.update(
            {
                "width_to_peer_median": width_ratio,
                "height_to_peer_median": height_ratio,
                "area_to_peer_median": area_ratio,
                "axis_span_to_peer_median": axis_span_ratio,
            }
        )
        multiple_error = (axis_span_ratio >= 1.65 and area_ratio >= 1.30) or area_ratio >= 1.85
        multiple_warning = axis_span_ratio >= 1.48 or area_ratio >= 1.60
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
                        "axis_span_to_peer_median": round(axis_span_ratio, 4),
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
        [0.0, *(-coordinate for coordinate in coordinates), *(coordinate - 1.0 for coordinate in coordinates)]
    )
    source_edges = {
        "left": min(point[0] for point in points) <= 0.002,
        "top": min(point[1] for point in points) <= 0.002,
        "right": max(point[0] for point in points) >= 0.998,
        "bottom": max(point[1] for point in points) >= 0.998,
    }
    source_edge_count = sum(source_edges.values())
    metrics["source_edge_count"] = source_edge_count
    metrics["outside_normalized_distance"] = outside_distance
    boundary_score = _clamp(1.0 - 0.13 * source_edge_count - min(0.7, outside_distance * 20))
    if outside_distance > 0:
        violations.append(
            CropQualityViolation(
                "geometry_outside_source",
                "error",
                "裁图坐标超出原始扫描范围",
                {"outside_normalized_distance": round(outside_distance, 5)},
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
    usable = not error_present and usability_score >= 0.55
    needs_review = bool(violations) or usability_score < 0.75

    # Stable precision keeps JSON/audit diffs readable without discarding the
    # unrounded diagnostic metrics.
    return CropQualityReport(
        presence_score=round(presence_score, 4),
        isolation_score=round(isolation_score, 4),
        boundary_score=round(boundary_score, 4),
        usability_score=round(usability_score, 4),
        usable=usable,
        needs_review=needs_review,
        violations=tuple(violations),
        metrics=metrics,
    )
