#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from card_platform.flatbed_crop_quality import assess_crop_quality
from card_platform.flatbed_detection import Detection, detect_layout


def overlap(left: Detection, right: Detection) -> float:
    width = max(
        0.0,
        min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
    )
    height = max(
        0.0,
        min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
    )
    return width * height / max(
        min(left.width * left.height, right.width * right.height), 1e-9
    )


def _expected_count(path: Path, manifest: dict) -> int:
    by_sheet = manifest.get("expected_count_by_sheet", {})
    by_session = manifest["expected_count_by_session"]
    return int(by_sheet.get(path.parts[-2], by_session[path.parts[-3]]))


def _geometry_failures(
    boxes: list[Detection], unusable_count: int, rules: list[dict]
) -> list[str]:
    failures: list[str] = []
    for rule in rules:
        if boxes and max(item.height for item in boxes) > rule.get(
            "max_region_height", 1.0
        ):
            failures.append("region_too_tall")
        if boxes and max(item.y + item.height for item in boxes) > rule.get(
            "max_region_bottom", 1.0
        ):
            failures.append("region_extends_into_unused_bed")
        if unusable_count > rule.get("max_unusable_regions", len(boxes)):
            failures.append("unusable_region_limit_exceeded")
    return list(dict.fromkeys(failures))


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
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text())
    all_paths = [
        path
        for path in sorted(Path().glob(manifest["corpus_glob"]))
        if path.parts[-3] in manifest["expected_count_by_session"]
    ]

    # Immutable sources are copied into every derived revision. Evaluating all
    # copies makes a single scan look like many successes and can leak a prior
    # from the answer back into the test. Group by bytes + expected count and
    # run the detector once, standalone, with no oracle count or generated prior.
    unique: dict[tuple[str, int], list[Path]] = {}
    content_by_key: dict[tuple[str, int], bytes] = {}
    for path in all_paths:
        content = path.read_bytes()
        key = (hashlib.sha256(content).hexdigest(), _expected_count(path, manifest))
        unique.setdefault(key, []).append(path)
        content_by_key.setdefault(key, content)

    geometry_rules = manifest.get("geometry_rules_by_sheet", {})
    results = []
    for key, equivalent_paths in unique.items():
        content = content_by_key[key]
        expected = key[1]
        layout = detect_layout(content)
        boxes = list(layout.detections)
        image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"无法解码回放图像: {equivalent_paths[0]}")
        reports = [
            assess_crop_quality(image, item, peers=boxes) for item in boxes
        ]
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
        geometry_failures = _geometry_failures(boxes, unusable_count, rules)
        passed = (
            len(boxes) == expected
            and not duplicates
            and not whole_sheet
            and unusable_count == 0
            and not geometry_failures
        )
        results.append(
            {
                "path": str(equivalent_paths[0]),
                "sha256": key[0],
                "equivalent_revision_count": len(equivalent_paths),
                "count": len(boxes),
                "expected": expected,
                "score": layout.score,
                "transform": layout.transform,
                "duplicates": duplicates,
                "whole_sheet_candidate": whole_sheet,
                "unusable_regions": unusable_count,
                "crop_quality_errors": sorted(
                    {
                        violation.code
                        for report in reports
                        for violation in report.violations
                        if violation.severity == "error"
                    }
                ),
                "geometry_failures": geometry_failures,
                "passed": passed,
            }
        )

    file_count = len(results)
    region_count = sum(item["count"] for item in results)
    critical_results = [
        item
        for item in results
        if any(
            path.parts[-2] in geometry_rules
            for path in unique[(item["sha256"], item["expected"])]
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
        "critical_geometry_pass_rate": sum(
            not item["geometry_failures"] for item in critical_results
        )
        / max(len(critical_results), 1),
    }
    floors = manifest.get("minimum_metrics", {})
    regressions = [
        f"{name}:{metrics.get(name, 0.0):.4f}<{float(floor):.4f}"
        for name, floor in floors.items()
        if metrics.get(name, 0.0) < float(floor)
    ]
    summary = {
        "corpus_files": len(all_paths),
        "unique_files": file_count,
        "duplicate_revisions_removed": len(all_paths) - file_count,
        "passed": sum(item["passed"] for item in results),
        "metrics": metrics,
        "regressions": regressions,
        "methodology": {
            "deduplicated_by_sha256": True,
            "detector_receives_expected_count": False,
            "detector_receives_generated_prior": False,
            "crop_quality_independent_of_detector_confidence": True,
        },
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.json else None))
    return 1 if args.enforce and regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
