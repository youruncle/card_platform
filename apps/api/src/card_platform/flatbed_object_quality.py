"""Audit final crops against independent, pre-refinement physical objects.

Presence/texture inside a crop cannot establish completeness: half a card can
look convincingly like a card. This check keeps object ownership separate from
segmentation confidence, while retaining every output for manual correction.
"""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Sequence

import cv2
import numpy as np

from card_platform.flatbed_crop_quality import CropQualityReport, CropQualityViolation
from card_platform.flatbed_detection import Detection

MIN_OBJECT_COVERAGE = 0.95


def _polygon(detection: Detection) -> np.ndarray:
    points = detection.points or (
        (detection.x, detection.y),
        (detection.x + detection.width, detection.y),
        (detection.x + detection.width, detection.y + detection.height),
        (detection.x, detection.y + detection.height),
    )
    values = np.asarray(points, dtype=np.float32)
    if values.shape != (4, 2) or not np.isfinite(values).all():
        raise ValueError("invalid_object_audit_geometry")
    return cv2.convexHull(values)


def audit_object_crop_integrity(
    reports: Sequence[CropQualityReport],
    detections: Sequence[Detection],
    source_objects: Sequence[Detection],
) -> tuple[list[CropQualityReport], dict]:
    """Only verified whole-object envelopes may constrain refined crops.

    Coarse grid cells and user quads are not automatic object truth. A partial
    set of verified objects can detect fragmentation of those objects, but may
    not label other crops as false positives merely for lacking a reference.
    """
    if len(reports) != len(detections):
        raise ValueError("crop_quality_count_mismatch")
    objects = [
        d
        for d in source_objects
        if "physical_object_envelope" in d.evidence
        and not set(d.evidence).intersection(
            {
                "front_to_back_prior",
                "slot_prompt_only",
                "manual_override",
                "user_confirmed_quad",
                "user_confirmed_quads",
            }
        )
    ]
    if not objects:
        return list(reports), {"status": "not_applicable", "reference_count": 0}
    reference_polygons = [_polygon(d) for d in objects]
    crop_polygons = [_polygon(d) for d in detections]
    reference_areas = [abs(cv2.contourArea(p)) for p in reference_polygons]
    crop_areas = [abs(cv2.contourArea(p)) for p in crop_polygons]
    intersections = [
        [max(0.0, float(cv2.intersectConvexConvex(p, crop)[0])) for crop in crop_polygons]
        for p in reference_polygons
    ]
    coverage = [
        [min(1.0, area / max(reference_areas[i], 1e-12)) for area in row]
        for i, row in enumerate(intersections)
    ]
    per_crop: list[list[CropQualityViolation]] = [[] for _ in detections]
    violations: list[dict] = []

    def record(code: str, message: str, indices: list[int], evidence: dict) -> None:
        item = CropQualityViolation(code, "error", message, evidence)
        violations.append({**item.as_dict(), "crop_indices": indices})
        for index in indices:
            per_crop[index].append(item)

    missing_count = 0
    for object_index, row in enumerate(coverage):
        best = max(row, default=0.0)
        best_index = int(np.argmax(row)) if row and best > 0 else None
        if best < MIN_OBJECT_COVERAGE:
            missing_count += int(best_index is None)
            record(
                "incomplete_card_object",
                "裁图未完整覆盖已定位的实体卡片，可能截断卡面、评级标签或签字区",
                [best_index] if best_index is not None else [],
                {"object_index": object_index, "maximum_object_coverage": round(best, 5)},
            )
        parts = [
            index
            for index, value in enumerate(row)
            if value >= 0.18
            and intersections[object_index][index] / max(crop_areas[index], 1e-12) >= 0.5
        ]
        if len(parts) > 1:
            record(
                "object_split_or_duplicate",
                "同一张实体卡片被多个裁图占用，可能被切成数段或重复识别",
                parts,
                {"object_index": object_index, "crop_count": len(parts)},
            )
    for index in range(len(detections)):
        owned_objects = [i for i, row in enumerate(coverage) if row[index] >= 0.35]
        if len(owned_objects) > 1:
            record(
                "multiple_card_objects",
                "一个裁图覆盖多张独立实体卡片",
                [index],
                {"object_count": len(owned_objects)},
            )
        if len(objects) == len(source_objects):
            support = max(
                (row[index] / max(crop_areas[index], 1e-12) for row in intersections), default=0.0
            )
            if support < 0.35:
                record(
                    "crop_without_card_object",
                    "裁图没有对应的完整卡片对象，可能是空白底板或残片",
                    [index],
                    {"maximum_object_support": round(support, 5)},
                )
    updated = []
    for index, (report, extra) in enumerate(zip(reports, per_crop, strict=True)):
        updated.append(
            replace(
                report,
                usability_score=min(report.usability_score, 0.49)
                if extra
                else report.usability_score,
                usable=report.usable and not extra,
                needs_review=report.needs_review or bool(extra),
                violations=(*report.violations, *extra),
                metrics={**report.metrics, "object_integrity_error_count": len(extra)},
            )
        )
    return updated, {
        "status": "requires_review" if violations else "accepted",
        "reference_count": len(objects),
        "missing_object_count": missing_count,
        "minimum_object_coverage": round(
            min((max(row, default=0.0) for row in coverage), default=0.0), 5
        ),
        "error_codes": list(dict.fromkeys(item["code"] for item in violations)),
        "violations": violations,
    }
