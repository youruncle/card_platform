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
    *,
    check_polygon_coverage: bool = True,
    reference_set_complete: bool | None = None,
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
        if ("physical_object_envelope" in d.evidence or "independent_source_object" in d.evidence)
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
        if check_polygon_coverage and best < MIN_OBJECT_COVERAGE:
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
        if reference_set_complete is True or (reference_set_complete is None and len(objects) == len(source_objects)):
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


def audit_original_content_integrity(
    reports: Sequence[CropQualityReport],
    detections: Sequence[Detection],
    image: np.ndarray,
    source_objects: Sequence[Detection],
    search_regions: Sequence[Detection] = (),
) -> tuple[list[CropQualityReport], dict]:
    """Audit original visible content, never the resolver's own final quad.

    A partial object set constrains those objects only. Search evidence can
    detect an unexplained printed panel without asserting physical boundaries.
    """
    from card_platform.flatbed_object_content import build_content_references, content_preservation

    if len(reports) != len(detections):
        raise ValueError("crop_quality_count_mismatch")
    independent = build_content_references(image, source_objects, scene_regions=search_regions)
    search = build_content_references(image, [p for p in search_regions
        if not any(p == reference.source for reference in independent)], search_only=True,
        scene_regions=search_regions)
    references = (*independent, *search)
    if not references:
        return list(reports), {"status": "insufficient_evidence", "independent_reference_count": 0,
                               "search_reference_count": 0, "error_codes": []}
    per_crop: list[list[CropQualityViolation]] = [[] for _ in detections]
    output_indices = [i for i, detection in enumerate(detections)
        if reports[i].usable
        and not {'quad_unresolved', 'search_region_only'}.intersection(detection.evidence)]
    polygons = [_polygon(d).reshape(-1, 2) for d in detections]
    owners = []
    for reference in references:
        raw = [content_preservation(reference, polygon) for polygon in polygons]
        checks = [content_preservation(reference, polygon,
            explained_points=[polygons[j] for j in output_indices if j != i])
            for i, polygon in enumerate(polygons)]
        owner = max(output_indices, key=lambda i: (
            checks[i]['preserved'], checks[i]['content_coverage'], raw[i]['content_coverage'],
        )) if output_indices else None
        owners.append(owner if owner is not None and raw[owner]['content_coverage'] > 0 else None)

    # Keep each reference's owner stable. A crop rejected by any reference will
    # not be written, so it cannot explain a peer's content in this same audit.
    # Removal is monotonic and settles after at most one removal per crop.
    rejected = set()
    settled = []
    for _ in range(len(output_indices) + 1):
        changed = False
        settled = []
        for reference, owner in zip(references, owners, strict=True):
            best = content_preservation(reference, polygons[owner],
                explained_points=[polygons[j] for j in output_indices
                    if j != owner and j not in rejected]) if owner is not None else {
                        'preserved': False, 'content_coverage': 0.0,
                        'minimum_partition_coverage': 0.0,
                    }
            settled.append(best)
            if not best['preserved'] and owner is not None and owner not in rejected:
                rejected.add(owner)
                changed = True
        if not changed:
            break
    details = []
    errors = []
    for object_index, (reference, owner, best) in enumerate(zip(references, owners, settled, strict=True)):
        detail = {"source_index": object_index, "crop_index": owner, "reference_role": reference.role, **best}
        details.append(detail)
        if not best["preserved"]:
            code = "original_card_content_missing" if reference.role == "independent_object" else "unexplained_card_content"
            errors.append(code)
            if owner is not None:
                per_crop[owner].append(CropQualityViolation(
                    code, "error", "裁图遗漏原图中的卡片内容或评级标签，需要重新校正四角", detail,
                ))
    updated = [replace(report,
        usability_score=min(report.usability_score, .49) if extra else report.usability_score,
        usable=report.usable and not extra,
        needs_review=report.needs_review or bool(extra),
        violations=(*report.violations, *extra),
        metrics={**report.metrics, "original_content_error_count": len(extra)},
    ) for report, extra in zip(reports, per_crop, strict=True)]
    status = "requires_review" if errors else (
        "accepted" if len(independent) >= len(detections) else
        "partial_reference_checked" if independent else "search_content_checked"
    )
    return updated, {"status": status, "independent_reference_count": len(independent),
        "search_reference_count": len(search), "coverage_scope": "observed_source_regions",
        "minimum_content_coverage": min(d["content_coverage"] for d in details),
        "minimum_partition_coverage": min(d["minimum_partition_coverage"] for d in details),
        "error_codes": list(dict.fromkeys(errors)), "objects": details}


def restore_content_preserving_quads(
    image: np.ndarray, detections: Sequence[Detection], source_quads: Sequence[Detection],
    source_objects: Sequence[Detection], search_regions: Sequence[Detection] = (),
) -> tuple[list[Detection], dict]:
    """Discard destructive refinement only when original verified quads pass."""
    from card_platform.flatbed_crop_quality import assess_sheet_crop_quality
    from card_platform.flatbed_observed_perimeter import observed_perimeter_preservation
    if len(detections) != len(source_quads):
        return list(detections), {"fallback_indices": [], "reason": "geometry_count_changed"}
    final_reports, final_audit = audit_original_content_integrity(
        assess_sheet_crop_quality(image, detections), detections, image, source_objects, search_regions,
    )
    source_reports, _ = audit_original_content_integrity(
        assess_sheet_crop_quality(image, source_quads), source_quads, image, source_objects, search_regions,
    )
    restored = list(detections)
    indices = []
    perimeter_indices = []
    perimeters = []
    for index, (final_report, source_report, source_quad) in enumerate(zip(
        final_reports, source_reports, source_quads, strict=True,
    )):
        perimeter = observed_perimeter_preservation(image, source_quad, detections[index])
        perimeters.append({'crop_index': index, **perimeter})
        if ((any(v.code == "original_card_content_missing" for v in final_report.violations)
             or perimeter['status'] == 'needs_fallback')
            and source_report.usable and "original_content_verified" in source_quad.evidence
            and "quad_unresolved" not in source_quad.evidence):
            restored[index] = source_quad
            indices.append(index)
            if perimeter['status'] == 'needs_fallback':
                perimeter_indices.append(index)
    # A restored object must remain isolated from the other final objects.
    if indices:
        check_reports, _ = audit_original_content_integrity(
            assess_sheet_crop_quality(image, restored), restored, image, source_objects, search_regions,
        )
        for index in list(indices):
            if not check_reports[index].usable:
                restored[index] = detections[index]
                indices.remove(index)
    return restored, {"fallback_indices": indices, "fallback_count": len(indices),
                      "observed_perimeter_fallback_indices": [i for i in perimeter_indices if i in indices],
                      "observed_perimeters": perimeters,
                      "reason": "original_content_and_observed_perimeter_preservation",
                      "refined_content_audit": final_audit}
