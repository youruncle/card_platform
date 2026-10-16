#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from card_platform.flatbed_crop_quality import assess_sheet_crop_quality
from card_platform.flatbed_detection import Detection, LayoutHypothesis, detect_layout


def _json_default(value):
    """Normalize NumPy scalars exposed by detector evidence for audit JSON."""
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def overlap(left: Detection, right: Detection) -> float:
    width = max(
        0.0,
        min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
    )
    height = max(
        0.0,
        min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
    )
    return (
        width
        * height
        / max(min(left.width * left.height, right.width * right.height), 1e-9)
    )


def _detection_polygon(item: Detection) -> np.ndarray:
    """Return the actual crop polygon, falling back to its axis-aligned bounds."""
    if item.points is not None and len(item.points) >= 3:
        points = np.asarray(item.points, dtype=np.float32)
        if np.isfinite(points).all():
            hull = cv2.convexHull(points).reshape(-1, 2)
            if len(hull) >= 3 and cv2.contourArea(hull) > 1e-9:
                return hull
    return np.asarray(
        [
            [item.x, item.y],
            [item.x + item.width, item.y],
            [item.x + item.width, item.y + item.height],
            [item.x, item.y + item.height],
        ],
        dtype=np.float32,
    )


def _intersection_coverage(crop: Detection, region: Detection) -> float:
    """Measure how much of a gold region is covered by the real crop polygon."""
    crop_polygon = cv2.convexHull(_detection_polygon(crop)).astype(np.float32)
    region_polygon = cv2.convexHull(_detection_polygon(region)).astype(np.float32)
    region_area = float(cv2.contourArea(region_polygon))
    if region_area <= 1e-9:
        return 0.0
    intersection_area, _ = cv2.intersectConvexConvex(crop_polygon, region_polygon)
    return max(0.0, float(intersection_area)) / region_area


def _region_detection(region: dict) -> Detection:
    """Build a gold region without discarding an optional convex polygon."""
    points = region.get("points")
    return Detection(
        region["x"],
        region["y"],
        region["width"],
        region["height"],
        0,
        1,
        points=(tuple((float(x), float(y)) for x, y in points) if points else None),
    )


def _expected_count(path: Path, manifest: dict) -> int:
    by_sheet = manifest.get("expected_count_by_sheet", {})
    by_session = manifest["expected_count_by_session"]
    return int(by_sheet.get(path.parts[-2], by_session[path.parts[-3]]))


def _recorded_dpi(path: Path, manifest: dict) -> int:
    """Return the DPI that production passed to the detector for this source."""
    by_sheet = manifest.get("dpi_by_sheet", {})
    by_session = manifest.get("dpi_by_session", {})
    value = by_sheet.get(path.parts[-2], by_session.get(path.parts[-3]))
    if value is None:
        raise KeyError(f"回放清单缺少扫描 DPI: {path}")
    dpi = int(value)
    if dpi <= 0:
        raise ValueError(f"回放清单扫描 DPI 必须为正数: {path}={value!r}")
    return dpi


def _source_key(path: Path, content: bytes, manifest: dict) -> tuple[str, int, int]:
    return (
        hashlib.sha256(content).hexdigest(),
        _expected_count(path, manifest),
        _recorded_dpi(path, manifest),
    )


def _validate_pinned_source(
    path: Path,
    content: bytes,
    image: np.ndarray,
    manifest: dict,
) -> None:
    """Keep human geometry labels bound to the exact immutable source pixels."""
    sheet_id = path.parts[-2]
    expected_sha = manifest.get("source_sha256_by_sheet", {}).get(sheet_id)
    actual_sha = hashlib.sha256(content).hexdigest()
    if expected_sha is not None and actual_sha != expected_sha:
        raise ValueError(
            f"回放原图 SHA 不匹配: {sheet_id} expected={expected_sha} actual={actual_sha}"
        )
    expected_dimensions = manifest.get("source_dimensions_by_sheet", {}).get(sheet_id)
    actual_dimensions = [int(image.shape[1]), int(image.shape[0])]
    if expected_dimensions is not None and actual_dimensions != list(
        expected_dimensions
    ):
        raise ValueError(
            "回放原图尺寸不匹配: "
            f"{sheet_id} expected={expected_dimensions} actual={actual_dimensions}"
        )


def _detect_standalone(content: bytes, dpi: int, *, object_quads: bool = False) -> LayoutHypothesis:
    """Run pixels plus recorded DPI only; labels and historical priors stay out."""
    layout = detect_layout(content, dpi=dpi)
    if object_quads:
        from card_platform.flatbed_object_quads import resolve_object_layout
        layout = resolve_object_layout(content, layout)
    return layout


def _gold_crop_detections(
    boxes: list[Detection], source_crop_points: list | None
) -> tuple[list[Detection], list[str]]:
    """Score final source pixels when the service supplied a derived crop quad."""
    if source_crop_points is None:
        return boxes, []
    failures = []
    if len(source_crop_points) != len(boxes):
        failures.append("invalid_source_crop_geometry")
    actual = []
    for index, item in enumerate(boxes):
        raw = source_crop_points[index] if index < len(source_crop_points) else None
        if raw is None:
            actual.append(item)
            continue
        try:
            points = np.asarray(
                [(point["x"], point["y"]) if isinstance(point, dict) else point for point in raw],
                dtype=np.float32,
            )
            if (
                points.shape != (4, 2)
                or not np.isfinite(points).all()
                or (points < 0).any()
                or (points > 1).any()
                or len(cv2.convexHull(points)) != 4
                or cv2.contourArea(cv2.convexHull(points)) <= 1e-9
            ):
                raise ValueError("invalid_quad")
            left, top = points.min(axis=0)
            right, bottom = points.max(axis=0)
            actual.append(replace(
                item, x=float(left), y=float(top),
                width=float(right - left), height=float(bottom - top),
                points=tuple((float(x), float(y)) for x, y in points),
            ))
        except (KeyError, TypeError, ValueError):
            # A malformed derived quad cannot silently pass using old bounds.
            failures.append("invalid_source_crop_geometry")
            actual.append(item)
    return actual, failures


def _content_geometry_failures(boxes: list[Detection], rule: dict) -> list[str]:
    """Require every part of one object in one crop, and isolate peer objects."""
    failures = []
    regions = rule.get("required_content_regions", [])
    coverage = [
        [_intersection_coverage(box, _region_detection(region)) for box in boxes]
        for region in regions
    ]
    groups: dict[tuple, list[int]] = {}
    for index, (region, row) in enumerate(zip(regions, coverage, strict=True)):
        key = ("object", str(region["object_id"])) if "object_id" in region else ("legacy", index)
        groups.setdefault(key, []).append(index)
        if max(row, default=0.0) < region.get("minimum_coverage", 0.98):
            failures.append("required_content_region_fragmented")
            if region.get("role") == "label":
                failures.append("required_label_region_fragmented")

    candidates = {}
    matched = {}
    for key, members in groups.items():
        scores = [min(coverage[member][index] for member in members) for index in range(len(boxes))]
        matched[key] = int(np.argmax(scores)) if scores else -1
        candidates[key] = [
            index for index in range(len(boxes))
            if all(coverage[member][index] >= regions[member].get("minimum_coverage", 0.98) for member in members)
        ]
        if not candidates[key] and len(members) > 1 and all(
            max(coverage[member], default=0.0) >= regions[member].get("minimum_coverage", 0.98)
            for member in members
        ):
            failures.append("object_content_regions_split")

    distinct = [
        key for key in groups
        if (rule.get("require_distinct_content_matches") or key[0] == "object") and candidates[key]
    ]

    def assign(keys: list[tuple], used: set[int], selected: dict) -> dict | None:
        if not keys:
            return selected
        key = keys[0]
        for index in candidates[key]:
            if index not in used:
                result = assign(keys[1:], used | {index}, {**selected, key: index})
                if result is not None:
                    return result
        return None

    assignment = assign(sorted(distinct, key=lambda key: len(candidates[key])), set(), {})
    if assignment is None:
        failures.append("multiple_content_regions_in_one_crop")
    else:
        matched.update(assignment)

    if "max_neighbour_content_overlap" in rule:
        limit = float(rule["max_neighbour_content_overlap"])
        for key, index in matched.items():
            if index >= 0 and any(
                coverage[member][index] > limit
                for peer_key, members in groups.items() if peer_key != key
                for member in members
            ):
                failures.append("neighbour_content_contamination")
    return failures


def _geometry_failures(
    boxes: list[Detection],
    unusable_count: int,
    rules: list[dict],
    layout_signals: dict | None = None,
    *,
    source_crop_points: list | None = None,
) -> list[str]:
    boxes, failures = _gold_crop_detections(boxes, source_crop_points)
    layout_signals = layout_signals or {}
    for rule in rules:
        if boxes and max(item.height for item in boxes) > rule.get(
            "max_region_height", 1.0
        ):
            failures.append("region_too_tall")
        if boxes and max(item.height for item in boxes) < rule.get(
            "min_max_region_height", 0.0
        ):
            failures.append("required_long_region_missing")
        if boxes and max(item.y + item.height for item in boxes) > rule.get(
            "max_region_bottom", 1.0
        ):
            failures.append("region_extends_into_unused_bed")
        if unusable_count > rule.get("max_unusable_regions", len(boxes)):
            failures.append("unusable_region_limit_exceeded")
        for signal in rule.get("required_layout_signals", []):
            if not layout_signals.get(signal):
                failures.append(f"required_layout_signal_missing:{signal}")
        failures.extend(_content_geometry_failures(boxes, rule))
        for region in rule.get("verified_blank_regions", []):
            blank = _region_detection(region)
            maximum_coverage = float(region.get("maximum_detection_coverage", 0.10))
            coverage = max(
                (_intersection_coverage(box, blank) for box in boxes),
                default=0.0,
            )
            if coverage > maximum_coverage:
                failures.append("verified_blank_region_covered")
    return list(dict.fromkeys(failures))


def _must_pass_regressions(
    results: list[dict], must_pass_sheets: list[str]
) -> list[str]:
    by_sheet = {
        sheet_id: result
        for result in results
        for sheet_id in result.get("sheet_ids", [])
    }
    regressions: list[str] = []
    for sheet_id in must_pass_sheets:
        result = by_sheet.get(sheet_id)
        if result is None:
            regressions.append(f"must_pass_sheet:{sheet_id}:missing")
        elif not result["passed"]:
            regressions.append(
                "must_pass_sheet:"
                f"{sheet_id}:count={result['count']}:expected={result['expected']}:"
                f"unusable={result['unusable_regions']}:"
                f"geometry={','.join(result['geometry_failures']) or 'none'}"
            )
    return regressions


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Leak-free offline LiDE 400 layout replay"
    )
    parser.add_argument("--manifest", default="data/flatbed-replay-manifest.json")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="return non-zero when the manifest regression floors are missed",
    )
    parser.add_argument(
        "--include-detections",
        action="store_true",
        help="include compact bounds/evidence in JSON for geometry diagnosis",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the JSON report to this path instead of stdout",
    )
    parser.add_argument("--object-quads", action="store_true", help="Resolve production current-pixel quads before auditing")
    parser.add_argument("--sam2", action="store_true", help="Run configured production SAM refinement and object integrity audit (requires --object-quads)")
    args = parser.parse_args()
    if args.sam2 and not args.object_quads:
        parser.error("--sam2 requires --object-quads")
    manifest = json.loads(Path(args.manifest).read_text())
    all_paths = [
        path
        for path in sorted(Path().glob(manifest["corpus_glob"]))
        if path.parts[-3] in manifest["expected_count_by_session"]
    ]

    # Immutable sources are copied into every derived revision. Evaluating all
    # copies makes a single scan look like many successes and can leak a prior
    # from the answer back into the test. Group by bytes + expected count + the
    # recorded detector DPI and run once, standalone, with no oracle count or
    # generated prior. DPI is an actual production input, not an inferred label.
    unique: dict[tuple[str, int, int], list[Path]] = {}
    content_by_key: dict[tuple[str, int, int], bytes] = {}
    for path in all_paths:
        content = path.read_bytes()
        key = _source_key(path, content, manifest)
        unique.setdefault(key, []).append(path)
        content_by_key.setdefault(key, content)

    geometry_rules = manifest.get("geometry_rules_by_sheet", {})
    results = []
    for key, equivalent_paths in unique.items():
        content = content_by_key[key]
        expected = key[1]
        dpi = key[2]
        image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"无法解码回放图像: {equivalent_paths[0]}")
        for path in equivalent_paths:
            _validate_pinned_source(path, content, image, manifest)
        layout = _detect_standalone(content, dpi, object_quads=True) if args.object_quads else _detect_standalone(content, dpi)
        boxes = list(layout.detections)
        ml_metadata = {"status": "not_run"}
        if args.sam2:
            from card_platform.config import get_settings
            from card_platform.flatbed_ml import refine_with_sam2
            settings = get_settings()
            boxes, ml_metadata = asyncio.run(refine_with_sam2(
                content, boxes,
                python_path=settings.flatbed_ml_python,
                worker_script=settings.flatbed_ml_worker_script,
                checkpoint=settings.flatbed_ml_checkpoint,
                timeout_seconds=settings.flatbed_ml_timeout_seconds,
                content_guarded=True,
            ))
        reports = assess_sheet_crop_quality(image, boxes)
        if args.sam2:
            from card_platform.flatbed_object_quality import (
                audit_object_crop_integrity, audit_original_content_integrity, restore_content_preserving_quads,
            )
            boxes, content_refinement = restore_content_preserving_quads(
                image, boxes, layout.detections, layout.object_references, layout.search_regions,
            )
            reports = assess_sheet_crop_quality(image, boxes)
            reports, _ = audit_object_crop_integrity(reports, boxes, layout.object_references,
                check_polygon_coverage=False, reference_set_complete=False)
            reports, original_content_integrity = audit_original_content_integrity(
                reports, boxes, image, layout.object_references, layout.search_regions,
            )
        if not args.sam2:
            from card_platform.flatbed_object_quality import audit_original_content_integrity
            reports, original_content_integrity = audit_original_content_integrity(
                reports, boxes, image, layout.object_references, layout.search_regions,
            ) if args.object_quads else (reports, {"status": "not_run"})
            content_refinement = {"fallback_count": 0}
        duplicates = sum(
            overlap(left, right) > 0.15
            for index, left in enumerate(boxes)
            for right in boxes[index + 1 :]
        )
        whole_sheet = any(box.width * box.height > 0.35 for box in boxes)
        unusable_count = sum(not report.usable for report in reports)
        rules = [
            geometry_rules[path.parts[-2]]
            for path in equivalent_paths
            if path.parts[-2] in geometry_rules
        ]
        geometry_failures = _geometry_failures(
            boxes, unusable_count, rules, layout.signals
        )
        unresolved_count = sum("quad_unresolved" in item.evidence for item in boxes)
        passed = (
            not unresolved_count
            and
            len(boxes) == expected
            and not duplicates
            and not whole_sheet
            and unusable_count == 0
            and not geometry_failures
        )
        result = {
            "path": str(equivalent_paths[0]),
            "sheet_ids": sorted({path.parts[-2] for path in equivalent_paths}),
            "sha256": key[0],
            "equivalent_revision_count": len(equivalent_paths),
            "count": len(boxes),
            "expected": expected,
            "dpi": dpi,
            "score": layout.score,
            "transform": layout.transform,
            "duplicates": duplicates,
            "whole_sheet_candidate": whole_sheet,
            "unusable_regions": unusable_count,
            "unresolved_quads": unresolved_count,
            "ml_refinement": ml_metadata,
            "content_refinement": content_refinement,
            "original_content_integrity": original_content_integrity,
            "crop_quality_errors": sorted(
                {
                    violation.code
                    for report in reports
                    for violation in report.violations
                    if violation.severity == "error"
                }
            ),
            "boundary_continuity_warnings": sum(
                any(
                    v.code == "content_crosses_crop_boundary" for v in report.violations
                )
                for report in reports
            ),
            "geometry_failures": geometry_failures,
            "passed": passed,
        }
        if args.include_detections:
            result["detections"] = [
                {
                    "bounds": [
                        round(item.x, 6),
                        round(item.y, 6),
                        round(item.width, 6),
                        round(item.height, 6),
                    ],
                    "confidence": round(item.confidence, 4),
                    "failure": item.failure,
                    "evidence": list(item.evidence),
                    "points": (
                        [
                            [round(point_x, 6), round(point_y, 6)]
                            for point_x, point_y in item.points
                        ]
                        if item.points is not None
                        else None
                    ),
                }
                for item in boxes
            ]
        results.append(result)

    file_count = len(results)
    region_count = sum(item["count"] for item in results)
    critical_results = [
        item
        for item in results
        if any(
            path.parts[-2] in geometry_rules
            for path in unique[(item["sha256"], item["expected"], item["dpi"])]
        )
    ]
    metrics = {
        "standalone_count_recall": sum(
            item["count"] == item["expected"] for item in results
        )
        / max(file_count, 1),
        "duplicate_free_rate": sum(not item["duplicates"] for item in results)
        / max(file_count, 1),
        "whole_sheet_rejection_rate": sum(
            not item["whole_sheet_candidate"] for item in results
        )
        / max(file_count, 1),
        "usable_region_rate": (
            region_count - sum(item["unusable_regions"] for item in results)
        )
        / max(region_count, 1),
        "critical_geometry_pass_rate": sum(item["passed"] for item in critical_results)
        / max(len(critical_results), 1),
    }
    floors = manifest.get("minimum_metrics", {})
    regressions = [
        f"{name}:{metrics.get(name, 0.0):.4f}<{float(floor):.4f}"
        for name, floor in floors.items()
        if metrics.get(name, 0.0) < float(floor)
    ]
    regressions.extend(
        _must_pass_regressions(results, manifest.get("must_pass_sheets", []))
    )
    summary = {
        "corpus_files": len(all_paths),
        "unique_files": file_count,
        "duplicate_revisions_removed": len(all_paths) - file_count,
        "passed": sum(item["passed"] for item in results),
        "metrics": metrics,
        "regressions": regressions,
        "methodology": {
            "object_quad_resolution": args.object_quads,
            "sam2_refinement": args.sam2,
            "deduplicated_by_sha256": True,
            "deduplication_key_includes_expected_count_and_dpi": True,
            "detector_receives_expected_count": False,
            "detector_receives_generated_prior": False,
            "detector_receives_recorded_dpi": True,
            "pinned_sources_verified_before_detection": True,
            "crop_quality_independent_of_detector_confidence": True,
        },
        "results": results,
    }
    output = json.dumps(
        summary,
        ensure_ascii=False,
        indent=2 if args.json else None,
        default=_json_default,
    )
    if args.output is not None:
        args.output.write_text(f"{output}\n")
        print(args.output)
    else:
        print(output)
    return 1 if args.enforce and regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
