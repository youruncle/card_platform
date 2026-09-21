#!/usr/bin/env python3
"""Replay one private, manifest-pinned scan under identity-preserving changes.

Usage (from the repository root, with the API environment):
    PYTHONPATH=apps/api/src python scripts/check_flatbed_transform_robustness.py \
        /path/to/SESSION/SHEET/original.png --output-dir /tmp/flatbed-transform-run

Source images remain private and unchanged. Counts and transformed human gold
regions score the results only; the detector receives image bytes and DPI.
The default JSON report contains geometry/metrics, not image data. Opt in to
local failing-image artifacts with --save-failed-inputs. Exit 1 means a
supported-input regression was found; exit 2 means the fixture or configuration
was invalid. Deliberately wrong preview DPI is a separate fault-injection
diagnostic, never counted as a successful or failed supported transformation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np

import card_platform.flatbed_detection as detector
from card_platform.flatbed_crop_quality import assess_sheet_crop_quality
from card_platform.flatbed_detection import Detection
from replay_flatbed_layouts import (
    _detect_standalone,
    _expected_count,
    _geometry_failures,
    _intersection_coverage,
    _recorded_dpi,
    _region_detection,
    _validate_pinned_source,
)

VARIANTS = {
    "original": {},
    "rotate90": {"quarter_turns_ccw": 1},
    "rotate180": {"quarter_turns_ccw": 2},
    "rotate270": {"quarter_turns_ccw": 3},
    "flip_horizontal": {"flip_horizontal": True},
    "brightness_minus12": {"offset": -12},
    "brightness_plus12": {"offset": 12},
    "gain_090": {"gain": 0.90},
    "gain_110": {"gain": 1.10},
    "gradient_x_30": {"gradient_x": 30},
    "gradient_y_30": {"gradient_y": 30},
    "rotate90_brightness_minus12": {"quarter_turns_ccw": 1, "offset": -12},
    "flip_gradient_x_30": {"flip_horizontal": True, "gradient_x": 30},
    "preview_two_thirds_native": {"scale": 2 / 3, "scale_dpi": True},
    "preview_two_thirds_nominal": {
        "scale": 2 / 3,
        "scale_dpi": False,
        "metadata_fault_injection": True,
    },
}


def _json_scalar(value):
    """Keep NumPy-produced diagnostic types numeric/boolean in the report."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Unsupported report value: {type(value).__name__}")


def transform_image(image: np.ndarray, parameters: dict) -> np.ndarray:
    result = np.rot90(image, parameters.get("quarter_turns_ccw", 0)).copy()
    if parameters.get("flip_horizontal"):
        result = cv2.flip(result, 1)
    height, width = result.shape[:2]
    if "scale" in parameters:
        result = cv2.resize(
            result,
            (
                max(1, round(width * parameters["scale"])),
                max(1, round(height * parameters["scale"])),
            ),
            interpolation=cv2.INTER_AREA,
        )
    height, width = result.shape[:2]
    result = result.astype(np.float32) * parameters.get("gain", 1) + parameters.get(
        "offset", 0
    )
    if "gradient_x" in parameters:
        span = parameters["gradient_x"] / 2
        result += np.linspace(-span, span, width, dtype=np.float32)[None, :, None]
    if "gradient_y" in parameters:
        span = parameters["gradient_y"] / 2
        result += np.linspace(-span, span, height, dtype=np.float32)[:, None, None]
    return np.clip(result, 0, 255).astype(np.uint8)


def transformed_rules(rules: dict, parameters: dict) -> dict:
    # Pixel-space axis limits and route-specific flags do not stay invariant
    # after rotation. Keep the human content/blank annotations and geometric
    # ownership criteria; never require a particular detector route to win.
    result = {
        key: copy.deepcopy(value)
        for key, value in rules.items()
        if key
        in {
            "required_content_regions",
            "verified_blank_regions",
            "max_unusable_regions",
            "require_distinct_content_matches",
            "max_neighbour_content_overlap",
        }
    }
    for key in ("required_content_regions", "verified_blank_regions"):
        for region in result.get(key, []):
            polygon = region.get("points")
            if polygon:
                # Human rotated-card quads describe content, while their AABB
                # also includes empty corners. Transform the actual gold, not
                # a rectangle that silently changes its area and ownership.
                points = np.asarray(polygon, dtype=float)
                if (
                    points.ndim != 2 or points.shape[1] != 2 or len(points) < 3
                    or not np.isfinite(points).all()
                ):
                    raise ValueError("Gold polygon points must be finite [x, y] pairs")
                if cv2.contourArea(cv2.convexHull(points.astype(np.float32))) <= 1e-12:
                    raise ValueError("Gold polygon points must describe a nonzero area")
            else:
                x, y, width, height = (
                    region[field] for field in ("x", "y", "width", "height")
                )
                points = np.array(
                    [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]
                )
            for _ in range(parameters.get("quarter_turns_ccw", 0) % 4):
                points = np.column_stack((points[:, 1], 1 - points[:, 0]))
            if parameters.get("flip_horizontal"):
                points[:, 0] = 1 - points[:, 0]
            lower, upper = points.min(axis=0), points.max(axis=0)
            region.update(
                x=float(lower[0]),
                y=float(lower[1]),
                width=float(upper[0] - lower[0]),
                height=float(upper[1] - lower[1]),
            )
            if polygon:
                region["points"] = points.tolist()
    return result


def content_coverage(boxes: list[Detection], rules: dict) -> dict:
    matrix = []
    for region in rules.get("required_content_regions", []):
        required = _region_detection(region)
        matrix.append([_intersection_coverage(box, required) for box in boxes])
    return {
        "gold_best_coverage": [round(max(row, default=0.0), 6) for row in matrix],
        "gold_best_box": [int(np.argmax(row)) if row else -1 for row in matrix],
        "coverage_matrix": [[round(value, 6) for value in row] for row in matrix],
    }


def run(
    source: Path,
    manifest: dict,
    output_dir: Path,
    cases: list[str],
    save_failed_inputs: bool,
) -> dict:
    sheet_id = source.parent.name
    rules = manifest.get("geometry_rules_by_sheet", {}).get(sheet_id)
    if not rules or not rules.get("required_content_regions"):
        raise ValueError(
            "Source requires full-content geometry annotations in the replay manifest"
        )
    if sheet_id not in manifest.get("source_sha256_by_sheet", {}):
        raise ValueError("Source must be pinned by SHA-256 in the replay manifest")
    content = source.read_bytes()
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Source image cannot be decoded")
    _validate_pinned_source(source, content, image, manifest)
    count, native_dpi = (
        _expected_count(source, manifest),
        _recorded_dpi(source, manifest),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.json"
    if report_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing report: {report_path}")
    report = {
        "source": str(source),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "detector_version": detector.DETECTOR_VERSION,
        "detector_sha256": hashlib.sha256(
            Path(detector.__file__).read_bytes()
        ).hexdigest(),
        "recorded_dpi": native_dpi,
        "expected_count": count,
        "gold_usage": "Human annotations transformed for scoring only; never detector input",
        "results": [],
    }
    for name in cases:
        parameters = VARIANTS[name]
        current = transform_image(image, parameters)
        dpi = (
            round(native_dpi * parameters["scale"])
            if parameters.get("scale_dpi")
            else native_dpi
        )
        encoded_ok, encoded = cv2.imencode(".png", current)
        if not encoded_ok:
            raise ValueError(f"Cannot encode transformed input: {name}")
        input_bytes = encoded.tobytes()
        started = time.monotonic()
        layout = _detect_standalone(input_bytes, dpi)
        boxes = list(layout.detections)
        quality = assess_sheet_crop_quality(current, boxes)
        gold = transformed_rules(rules, parameters)
        failures = _geometry_failures(
            boxes, sum(not item.usable for item in quality), [gold], layout.signals
        )
        if len(boxes) != count:
            failures.insert(0, "object_count_mismatch")
        item = {
            "name": name,
            "parameters": parameters,
            "dpi": dpi,
            "category": "metadata_fault_injection"
            if parameters.get("metadata_fault_injection")
            else "supported_transform",
            "dimensions": [current.shape[1], current.shape[0]],
            "count": len(boxes),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "failures": failures,
            "geometry": content_coverage(boxes, gold),
            "layout": layout.as_dict(),
            "detections": [
                {
                    "x": d.x,
                    "y": d.y,
                    "width": d.width,
                    "height": d.height,
                    "points": d.points,
                    "evidence": d.evidence,
                }
                for d in boxes
            ],
            "quality": [item.as_dict() for item in quality],
        }
        if failures:
            item["transformed_gold"] = gold
            if save_failed_inputs:
                failed_path = output_dir / f"{name}.png"
                with failed_path.open("xb") as stream:
                    stream.write(input_bytes)
                item["failure_input"] = str(failed_path)
        report["results"].append(item)
        print(
            json.dumps(
                {key: item[key] for key in ("name", "count", "failures", "geometry")}
            ),
            flush=True,
        )
    supported = [
        item for item in report["results"] if item["category"] == "supported_transform"
    ]
    report.update(
        passed=sum(not item["failures"] for item in supported),
        total=len(supported),
        evaluated_case_count=len(cases),
        metadata_fault_injection_count=len(cases) - len(supported),
    )
    # Serialize first so an unsupported metric cannot leave a truncated file
    # that looks like a completed regression report.
    serialized = json.dumps(report, ensure_ascii=False, indent=2, default=_json_scalar)
    with report_path.open("x") as stream:
        stream.write(serialized)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "data/flatbed-replay-manifest.json",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=list(VARIANTS))
    parser.add_argument("--save-failed-inputs", action="store_true")
    args = parser.parse_args()
    try:
        report = run(
            args.source.resolve(),
            json.loads(args.manifest.read_text()),
            args.output_dir.resolve(),
            args.case or list(VARIANTS),
            args.save_failed_inputs,
        )
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": report["total"],
                "report": str(args.output_dir / "report.json"),
            }
        )
    )
    return int(report["passed"] != report["total"])


if __name__ == "__main__":
    raise SystemExit(main())
