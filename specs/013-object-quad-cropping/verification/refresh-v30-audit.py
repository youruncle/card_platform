"""Verify release crops against exact v30 baseline SAM input dataclasses.

Frozen baseline content/reference/quad implementations rebuild the original
unrounded detector-to-SAM inputs. A changed input or ambiguous restoration,
review, or unresolved state reruns real SAM. Every cached final crop also runs
the current content and observed-perimeter restoration before final audits.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import runpy
import sys

import cv2
import numpy as np

from card_platform.config import get_settings
from card_platform.flatbed_detection import Detection
from card_platform.flatbed_crop_quality import assess_sheet_crop_quality
from card_platform.flatbed_ml import refine_with_sam2
from card_platform.flatbed_object_quality import (
    audit_object_crop_integrity,
    audit_original_content_integrity,
    restore_content_preserving_quads,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
REPLAY = runpy.run_path(str(REPO / "scripts/replay_flatbed_layouts.py"))
BASELINES = {
    name: ROOT / f"baseline-v30-{name}.py"
    for name in ("flatbed_object_content", "flatbed_object_quads", "flatbed_object_references")
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_snapshot() -> dict[str, str]:
    names = [
        "flatbed_detection.py", "flatbed_object_quads.py", "flatbed_object_references.py",
        "flatbed_object_content.py", "flatbed_ml.py", "flatbed_crop_quality.py",
        "flatbed_object_quality.py", "flatbed_observed_perimeter.py",
    ]
    base = REPO / "apps/api/src/card_platform"
    paths = [base / name for name in names]
    paths += [REPO / "scripts/replay_flatbed_layouts.py", Path(__file__).resolve()]
    paths += list(BASELINES.values())
    return {str(path.relative_to(REPO)): sha256(path) for path in paths}


def load_baseline_resolver(report: dict):
    """Rebuild the exact old resolver environment without replacing live imports."""
    previous_hashes = report["methodology"]["audit_refresh_code_sha256"]
    for name, path in BASELINES.items():
        original_path = f"apps/api/src/card_platform/{name}.py"
        if sha256(path) != previous_hashes.get(original_path):
            raise ValueError(f"baseline_implementation_not_bound_to_input_report:{name}")
    name = "_flatbed_v30_content_baseline"
    spec = importlib.util.spec_from_file_location(name, BASELINES["flatbed_object_content"])
    if spec is None or spec.loader is None:
        raise ValueError("cannot_load_baseline_content")
    content_module = importlib.util.module_from_spec(spec)
    # dataclass resolves its defining module while the class is created.
    sys.modules[name] = content_module
    spec.loader.exec_module(content_module)
    reference_globals = runpy.run_path(str(BASELINES["flatbed_object_references"]))
    quad_globals = runpy.run_path(str(BASELINES["flatbed_object_quads"]))
    resolver = quad_globals["resolve_object_layout"]
    resolver.__globals__.update(
        build_content_references=content_module.build_content_references,
        content_preservation=content_module.content_preservation,
        independent_object_references=reference_globals["independent_object_references"],
    )
    return resolver


def cached_detection(raw: dict) -> Detection:
    points = raw.get("points")
    if points is not None:
        points = tuple((float(x), float(y)) for x, y in points)
    # Compact reports retain final corners, not the unused angle metadata.
    # Both source-content and legacy audits operate on the true polygon.
    return Detection(
        *map(float, raw["bounds"]), float(raw.get("angle", 0)),
        float(raw["confidence"]), raw.get("failure"), points,
        tuple(raw.get("evidence", ())),
    )


def compact_detection(item: Detection) -> dict:
    return {
        "bounds": [round(value, 6) for value in (item.x, item.y, item.width, item.height)],
        "confidence": round(item.confidence, 4),
        "failure": item.failure,
        "evidence": list(item.evidence),
        "points": [[round(x, 6), round(y, 6)] for x, y in item.points] if item.points else None,
    }


def check_source(result: dict, manifest: dict) -> tuple[bytes, np.ndarray]:
    path = REPO / result["path"]
    content = path.read_bytes()
    actual_sha = hashlib.sha256(content).hexdigest()
    if actual_sha != result["sha256"]:
        raise ValueError(f"source_sha_changed:{result['path']}")
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"source_decode_failed:{result['path']}")
    expected_key = (actual_sha, result["expected"], result["dpi"])
    for sheet_id in result["sheet_ids"]:
        matches = list((path.parent.parent / sheet_id).glob("original.*"))
        if len(matches) != 1:
            raise ValueError(f"equivalent_source_missing_or_ambiguous:{sheet_id}")
        equivalent = matches[0]
        equivalent_bytes = equivalent.read_bytes()
        relative = equivalent.relative_to(REPO)
        if REPLAY["_source_key"](relative, equivalent_bytes, manifest) != expected_key:
            raise ValueError(f"equivalent_source_key_changed:{sheet_id}")
        REPLAY["_validate_pinned_source"](relative, equivalent_bytes, image, manifest)
    return content, image


def audit(image: np.ndarray, boxes: list[Detection], layout):
    reports = assess_sheet_crop_quality(image, boxes)
    reports, legacy = audit_object_crop_integrity(
        reports, boxes, layout.object_references,
        check_polygon_coverage=False, reference_set_complete=False,
    )
    reports, source = audit_original_content_integrity(
        reports, boxes, image, layout.object_references, layout.search_regions,
    )
    return reports, legacy, source


def rerun_reasons(result: dict, boxes: list[Detection], layout, source_audit: dict) -> list[str]:
    reasons = []
    if result.get("unresolved_quads", 0) or any(
        {"quad_unresolved", "search_region_only"}.intersection(item.evidence)
        for item in [*boxes, *layout.detections]
    ):
        reasons.append("unresolved_quad_present")
    old = result.get("original_content_integrity", {})
    if old.get("status") == "requires_review":
        reasons.append("old_original_content_requires_review")
    if source_audit.get("status") == "requires_review":
        reasons.append("new_original_content_requires_review")
    restoration = result.get("content_refinement", {})
    if restoration.get("fallback_count", 0) or restoration.get("fallback_indices"):
        # Stronger than the required search-regions + fallback condition:
        # no restored geometry is assumed to be an untouched SAM final quad.
        reasons.append("old_content_restoration_fallback")
    if restoration.get("refined_content_audit", {}).get("status") == "requires_review":
        reasons.append("old_refined_content_requires_review")
    if result.get("ml_refinement", {}).get("status") != "accepted":
        reasons.append("old_sam_result_not_accepted")
    if not old.get("status") or "content_refinement" not in result:
        reasons.append("insufficient_cached_audit_provenance")
    return reasons


def grade(result: dict, boxes: list[Detection], reports: list, layout, manifest: dict):
    unusable = sum(not report.usable for report in reports)
    duplicates = sum(
        REPLAY["overlap"](left, right) > .15
        for index, left in enumerate(boxes) for right in boxes[index + 1:]
    )
    whole = any(item.width * item.height > .35 for item in boxes)
    unresolved = sum("quad_unresolved" in item.evidence for item in boxes)
    rules = [manifest["geometry_rules_by_sheet"][sheet_id] for sheet_id in result["sheet_ids"]
             if sheet_id in manifest["geometry_rules_by_sheet"]]
    # No service padding is applied by this offline pipeline. The actual final
    # SAM/restored polygon is the crop; never substitute its bounding rectangle.
    failures = REPLAY["_geometry_failures"](boxes, unusable, rules, layout.signals)
    result.update(
        count=len(boxes), score=layout.score, transform=layout.transform,
        duplicates=duplicates, whole_sheet_candidate=whole,
        unusable_regions=unusable, unresolved_quads=unresolved,
        crop_quality_errors=sorted({v.code for report in reports for v in report.violations if v.severity == "error"}),
        boundary_continuity_warnings=sum(any(v.code == "content_crosses_crop_boundary" for v in report.violations) for report in reports),
        geometry_failures=failures,
        passed=len(boxes) == result["expected"] and not (unusable or duplicates or whole or unresolved or failures),
        detections=[compact_detection(item) for item in boxes],
        frozen_reference_count=len(layout.object_references),
        search_region_count=len(layout.search_regions),
    )


def summary(report: dict, manifest: dict, verification: dict):
    results = report["results"]
    critical = [item for item in results if any(
        sheet_id in manifest["geometry_rules_by_sheet"] for sheet_id in item["sheet_ids"]
    )]
    count = len(results)
    region_count = sum(item["count"] for item in results)
    metrics = {
        "standalone_count_recall": sum(item["count"] == item["expected"] for item in results) / max(count, 1),
        "duplicate_free_rate": sum(not item["duplicates"] for item in results) / max(count, 1),
        "whole_sheet_rejection_rate": sum(not item["whole_sheet_candidate"] for item in results) / max(count, 1),
        "usable_region_rate": 1 - sum(item["unusable_regions"] for item in results) / max(region_count, 1),
        "critical_geometry_pass_rate": sum(item["passed"] for item in critical) / max(len(critical), 1),
    }
    report.update(metrics=metrics, passed=sum(item["passed"] for item in results))
    report["regressions"] = [
        f"{key}:{metrics.get(key, 0):.4f}<{float(floor):.4f}"
        for key, floor in manifest["minimum_metrics"].items() if metrics.get(key, 0) < float(floor)
    ]
    report["regressions"] += REPLAY["_must_pass_regressions"](results, manifest["must_pass_sheets"])
    report["methodology"].update(verification)


async def main(args):
    input_path = args.input.resolve()
    manifest_path = args.manifest.resolve()
    input_sha = sha256(input_path)
    manifest_sha = sha256(manifest_path)
    snapshot = code_snapshot()
    report = json.loads(input_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if not report.get("methodology", {}).get("sam2_refinement"):
        raise ValueError("input_report_did_not_run_real_sam")
    if not report.get("methodology", {}).get("object_quad_resolution"):
        raise ValueError("input_report_did_not_run_object_quads")
    baseline_resolver = load_baseline_resolver(report)
    previous_hashes = report["methodology"]["audit_refresh_code_sha256"]
    provenance_reasons = []
    # No frozen raw detector or adapter is present. If either changed, rebuild
    # both comparisons on current pixels but prohibit ALL cached SAM reuse.
    for module in ("flatbed_detection.py", "flatbed_ml.py"):
        relative = f"apps/api/src/card_platform/{module}"
        if snapshot[relative] != previous_hashes.get(relative):
            provenance_reasons.append(f"unfrozen_input_dependency_changed:{module}")
    settings = get_settings()
    worker = Path(settings.flatbed_ml_worker_script).expanduser().resolve()
    worker_sha = sha256(worker)
    if worker_sha != report["methodology"].get("audit_refresh_sam_worker_sha256"):
        provenance_reasons.append("sam_worker_implementation_changed")
    reused, rerun = 0, 0
    exact_equal_count = 0
    cached_adapter_fallback_count = 0
    reason_counts = Counter()
    for index, result in enumerate(report["results"], 1):
        if code_snapshot() != snapshot:
            raise ValueError("verification_implementation_changed_mid_run")
        content, image = check_source(result, manifest)
        proposals = REPLAY["detect_layout"](content, dpi=result["dpi"])
        previous_layout = baseline_resolver(content, proposals)
        from card_platform.flatbed_object_quads import resolve_object_layout
        layout = resolve_object_layout(content, proposals)
        inputs_equal = previous_layout.detections == layout.detections
        exact_equal_count += int(inputs_equal)
        cached_boxes = [cached_detection(item) for item in result["detections"]]
        boxes, restoration = restore_content_preserving_quads(
            image, cached_boxes, layout.detections, layout.object_references, layout.search_regions,
        )
        reports, legacy, source = audit(image, boxes, layout)
        reasons = rerun_reasons(result, cached_boxes, layout, source)
        reasons.extend(provenance_reasons)
        if not inputs_equal:
            reasons.append("exact_baseline_sam_input_dataclasses_changed")
        if reasons:
            rerun += 1
            reason_counts.update(reasons)
            boxes, ml = await refine_with_sam2(
                content, list(layout.detections),
                python_path=settings.flatbed_ml_python,
                worker_script=settings.flatbed_ml_worker_script,
                checkpoint=settings.flatbed_ml_checkpoint,
                timeout_seconds=settings.flatbed_ml_timeout_seconds,
                content_guarded=True,
            )
            boxes, restoration = restore_content_preserving_quads(
                image, boxes, layout.detections, layout.object_references, layout.search_regions,
            )
            result.update(ml_refinement=ml)
            reports, legacy, source = audit(image, boxes, layout)
        else:
            reused += 1
            cached_adapter_fallback_count += int(result["ml_refinement"].get("fallback_count", 0) > 0)
        result.update(
            content_refinement=restoration,
            object_integrity=legacy, original_content_integrity=source,
            audit_refresh={"sam_result": "rerun" if reasons else "reused", "rerun_reasons": reasons,
                "sam_input_dataclasses_exactly_equal": inputs_equal,
                "cached_final_points_precision_decimals": 6 if not reasons else None},
        )
        grade(result, boxes, reports, layout, manifest)
        print(f"{index}/{len(report['results'])} {result['sheet_ids'][0]} "
              f"{'rerun' if reasons else 'reused'} passed={result['passed']} "
              f"source={source['status']} gold={result['geometry_failures']}", flush=True)
    if sha256(input_path) != input_sha or sha256(manifest_path) != manifest_sha:
        raise ValueError("input_report_or_manifest_changed_mid_run")
    if sha256(worker) != worker_sha or code_snapshot() != snapshot:
        raise ValueError("verification_implementation_changed_mid_run")
    verification = {
        "audit_refreshed_with_current_original_content_fixed_point": True,
        "audit_refresh_input_report_sha256": input_sha,
        "audit_refresh_manifest_sha256": manifest_sha,
        "audit_refresh_source_sha256_checked": True,
        "audit_refresh_code_sha256": snapshot,
        "audit_refresh_sam_worker_sha256": worker_sha,
        "audit_refresh_original_content_function_sha256": hashlib.sha256(inspect.getsource(audit_original_content_integrity).encode()).hexdigest(),
        "audit_refresh_sam_reused_count": reused,
        "audit_refresh_sam_rerun_count": rerun,
        "baseline_exact_sam_input_dataclass_comparison": True,
        "baseline_exact_sam_inputs_equal_count": exact_equal_count,
        "baseline_exact_sam_inputs_changed_count": len(report["results"]) - exact_equal_count,
        "baseline_implementation_sha256": {name: sha256(path) for name, path in BASELINES.items()},
        "current_restore_ran_for_every_cached_final_crop": True,
        "cached_adapter_partial_fallback_source_count": cached_adapter_fallback_count,
        "audit_refresh_rerun_reasons": dict(reason_counts),
        "cached_final_points_precision_decimals": 6,
        "cached_sam_input_identity_basis": (
            "Baseline content/reference/quad snapshots are SHA-bound to the input verified report; "
            "baseline/current Detection dataclasses are compared exactly before SAM, without rounding. "
            "Changed/unresolved/review/restored/nonaccepted cases rerun real SAM. All cached finals "
            "also run current content plus observed-perimeter restoration. Accepted adapter-internal "
            "partial fallbacks are reusable only when inputs and adapter/worker hashes match: their "
            "original-quad fallback precedes the audit and no old content restoration occurred."
        ),
        "offline_crop_geometry": "actual final SAM/restored source polygon; service output padding not run",
    }
    summary(report, manifest, verification)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=REPLAY["_json_default"]) + "\n")
    print(f"final {report['passed']}/{len(report['results'])} reused={reused} rerun={rerun} "
          f"regressions={report['regressions']}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "replay-v30-verified.json")
    parser.add_argument("--manifest", type=Path, default=REPO / "data/flatbed-replay-manifest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "replay-v30-release.json")
    asyncio.run(main(parser.parse_args()))
