import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "flatbed_transform_check", SCRIPTS / "check_flatbed_transform_robustness.py"
)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.mark.parametrize(
    "parameters, expected",
    [
        ({}, [0.1, 0.2, 0.3, 0.4]),
        ({"quarter_turns_ccw": 1}, [0.2, 0.6, 0.4, 0.3]),
        ({"quarter_turns_ccw": 2}, [0.6, 0.4, 0.3, 0.4]),
        ({"quarter_turns_ccw": 3}, [0.4, 0.1, 0.4, 0.3]),
        ({"flip_horizontal": True}, [0.6, 0.2, 0.3, 0.4]),
        ({"quarter_turns_ccw": 1, "flip_horizontal": True}, [0.4, 0.6, 0.4, 0.3]),
    ],
)
def test_human_content_and_blank_annotations_follow_pixel_transforms(parameters, expected):
    region = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    rules = {
        "required_content_regions": [region],
        "verified_blank_regions": [region],
        "max_region_height": 0.5,
        "required_layout_signals": ["old_route"],
    }
    result = check.transformed_rules(rules, parameters)
    for field in ("required_content_regions", "verified_blank_regions"):
        transformed = result[field][0]
        assert [transformed[k] for k in ("x", "y", "width", "height")] == pytest.approx(expected)
    assert "max_region_height" not in result
    assert "required_layout_signals" not in result
    assert region == {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}


def test_pixel_rotations_are_lossless_and_lighting_does_not_wrap():
    image = np.arange(120, dtype=np.uint8).reshape(4, 10, 3)
    assert np.array_equal(check.transform_image(image, {"quarter_turns_ccw": 1}), np.rot90(image))
    assert np.array_equal(
        check.transform_image(image, {"flip_horizontal": True}), cv2.flip(image, 1)
    )
    assert check.transform_image(image, {"offset": -300}).max() == 0
    assert check.transform_image(image, {"offset": 300}).min() == 255


@pytest.mark.parametrize(
    "parameters, expected_points",
    [
        ({}, [[0.1, 0.2], [0.45, 0.25], [0.4, 0.7], [0.15, 0.6]]),
        ({"quarter_turns_ccw": 1}, [[0.2, 0.9], [0.25, 0.55], [0.7, 0.6], [0.6, 0.85]]),
        ({"quarter_turns_ccw": 2}, [[0.9, 0.8], [0.55, 0.75], [0.6, 0.3], [0.85, 0.4]]),
        ({"quarter_turns_ccw": 3}, [[0.8, 0.1], [0.75, 0.45], [0.3, 0.4], [0.4, 0.15]]),
        ({"quarter_turns_ccw": -1}, [[0.8, 0.1], [0.75, 0.45], [0.3, 0.4], [0.4, 0.15]]),
        ({"flip_horizontal": True}, [[0.9, 0.2], [0.55, 0.25], [0.6, 0.7], [0.85, 0.6]]),
        (
            {"quarter_turns_ccw": 1, "flip_horizontal": True},
            [[0.8, 0.9], [0.75, 0.55], [0.3, 0.6], [0.4, 0.85]],
        ),
        (
            {"scale": 2 / 3, "gain": 0.9, "gradient_x": 30},
            [[0.1, 0.2], [0.45, 0.25], [0.4, 0.7], [0.15, 0.6]],
        ),
    ],
)
def test_polygon_gold_preserves_corners_area_and_thresholds_through_transforms(
    parameters, expected_points
):
    points = [[0.1, 0.2], [0.45, 0.25], [0.4, 0.7], [0.15, 0.6]]
    original_points = [point[:] for point in points]
    region = {
        "x": 0.1, "y": 0.2, "width": 0.35, "height": 0.5, "points": points,
        "minimum_coverage": 0.96, "maximum_detection_coverage": 0.10,
    }
    rules = {"required_content_regions": [region], "verified_blank_regions": [region]}
    result = check.transformed_rules(rules, parameters)
    expected = np.asarray(expected_points)
    original_area = cv2.contourArea(np.asarray(points, dtype=np.float32))
    for key in ("required_content_regions", "verified_blank_regions"):
        actual = result[key][0]
        np.testing.assert_allclose(actual["points"], expected, atol=1e-10)
        assert actual["minimum_coverage"] == 0.96
        assert actual["maximum_detection_coverage"] == 0.10
        assert [actual[field] for field in ("x", "y", "width", "height")] == pytest.approx(
            [*expected.min(axis=0), *np.ptp(expected, axis=0)]
        )
        assert cv2.contourArea(np.asarray(actual["points"], dtype=np.float32)) == pytest.approx(
            original_area, abs=1e-7
        )
    assert region["points"] == original_points
    assert region["points"] is not result["required_content_regions"][0]["points"]


def test_polygon_gold_coverage_divides_by_polygon_area_not_bounding_rectangle():
    diamond = Detection(
        0.1, 0.1, 0.8, 0.8, 0, 0.9,
        points=((0.5, 0.1), (0.9, 0.5), (0.5, 0.9), (0.1, 0.5)),
    )
    rules = {"required_content_regions": [{
        "x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8,
        "points": [list(point) for point in diamond.points], "minimum_coverage": 0.96,
    }]}
    assert check.content_coverage([diamond], rules)["gold_best_coverage"] == [1.0]
    assert not check._geometry_failures([diamond], 0, [rules])
    inner_square = Detection(0.3, 0.3, 0.4, 0.4, 0, 0.9)
    assert check.content_coverage([inner_square], rules)["gold_best_coverage"] == [0.5]
    assert "required_content_region_fragmented" in check._geometry_failures(
        [inner_square], 0, [rules]
    )


@pytest.mark.parametrize("points", [
    [[0, 0], [1, 1]],
    [[0, 0], [1, 1], [float("nan"), 0]],
    [[0, 0], [0.5, 0.5], [1, 1]],
])
def test_invalid_polygon_gold_is_not_silently_replaced_with_a_bbox(points):
    rules = {"required_content_regions": [{
        "x": 0, "y": 0, "width": 1, "height": 1, "points": points,
    }]}
    with pytest.raises(ValueError, match="Gold polygon"):
        check.transformed_rules(rules, {})


def test_numpy_report_values_preserve_their_real_types():
    values = {
        "usable": np.bool_(True),
        "count": np.int64(5),
        "score": np.float32(0.5),
        "points": np.array([0.1, 0.2]),
    }
    result = json.loads(json.dumps(values, default=check._json_scalar))
    assert result == {"usable": True, "count": 5, "score": 0.5, "points": [0.1, 0.2]}
    with pytest.raises(TypeError):
        json.dumps({"unsupported": object()}, default=check._json_scalar)


def test_diagnostic_coverage_uses_the_same_polygon_as_pass_fail_scoring():
    diamond = Detection(
        0.1,
        0.1,
        0.8,
        0.8,
        0,
        0.9,
        points=((0.5, 0.1), (0.9, 0.5), (0.5, 0.9), (0.1, 0.5)),
    )
    rules = {"required_content_regions": [{"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}]}
    assert check.content_coverage([diamond], rules)["gold_best_coverage"] == [0.5]
    assert "required_content_region_fragmented" in check._geometry_failures([diamond], 0, [rules])


def fixture(tmp_path):
    source = tmp_path / "session" / "sheet" / "original.png"
    source.parent.mkdir(parents=True)
    image = np.random.default_rng(7).integers(0, 255, (120, 100, 3), dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(".png", image)
    assert encoded_ok
    source.write_bytes(encoded.tobytes())
    manifest = {
        "expected_count_by_session": {"session": 1},
        "dpi_by_session": {"session": 300},
        "source_sha256_by_sheet": {"sheet": hashlib.sha256(source.read_bytes()).hexdigest()},
        "source_dimensions_by_sheet": {"sheet": [100, 120]},
        "geometry_rules_by_sheet": {
            "sheet": {
                "required_content_regions": [{"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}]
            }
        },
    }
    return source, manifest


def test_gold_is_not_detector_input_and_wrong_dpi_is_separate_diagnostic(tmp_path, monkeypatch):
    source, manifest = fixture(tmp_path)
    source_before = source.read_bytes()
    calls = []

    def detect(content, dpi):
        calls.append(dpi)
        # Signature deliberately accepts no gold count or geometry parameters.
        boxes = (Detection(0.1, 0.1, 0.8, 0.8, 0, 0.9),) if dpi == 200 else ()
        return LayoutHypothesis(boxes, 0.9, 1, 1)

    monkeypatch.setattr(check, "_detect_standalone", detect)
    output = tmp_path / "report"
    report = check.run(
        source, manifest, output, ["preview_two_thirds_native", "preview_two_thirds_nominal"], False
    )
    assert calls == [200, 300]
    assert report["passed"] == report["total"] == 1
    assert report["metadata_fault_injection_count"] == 1
    assert report["results"][1]["failures"]  # Do not disguise injected-fault failures as passes.
    assert report["results"][1]["category"] == "metadata_fault_injection"
    assert list(output.glob("*.png")) == []
    assert source.read_bytes() == source_before
    with pytest.raises(FileExistsError):
        check.run(source, manifest, output, ["original"], False)


def test_refuses_unpinned_or_mismatched_private_source(tmp_path):
    source, manifest = fixture(tmp_path)
    manifest["source_sha256_by_sheet"] = {}
    with pytest.raises(ValueError, match="pinned"):
        check.run(source, manifest, tmp_path / "out", ["original"], False)
    manifest["source_sha256_by_sheet"] = {"sheet": "wrong"}
    with pytest.raises(ValueError, match="SHA"):
        check.run(source, manifest, tmp_path / "out", ["original"], False)


@pytest.mark.skipif(
    not os.environ.get("FLATBED_TRANSFORM_FIXTURE"),
    reason="Optional private manifest-pinned source not configured",
)
def test_optional_private_scan_transform_regression(tmp_path):
    source = Path(os.environ["FLATBED_TRANSFORM_FIXTURE"]).resolve()
    manifest_path = Path(
        os.environ.get(
            "FLATBED_TRANSFORM_MANIFEST", SCRIPTS.parent / "data/flatbed-replay-manifest.json"
        )
    )
    report = check.run(
        source, json.loads(manifest_path.read_text()), tmp_path, list(check.VARIANTS), False
    )
    assert report["passed"] == report["total"], [
        (item["name"], item["failures"])
        for item in report["results"]
        if item["category"] == "supported_transform" and item["failures"]
    ]
