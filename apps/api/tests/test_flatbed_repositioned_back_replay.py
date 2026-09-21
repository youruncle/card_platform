import importlib.util
import json
from pathlib import Path

from card_platform.flatbed_detection import Detection


REPOSITORY_ROOT = Path(__file__).parents[3]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "replay_flatbed_layouts.py"
SPEC = importlib.util.spec_from_file_location("repositioned_back_replay", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)

SESSION_ID = "2e149a28-f7f1-4333-850e-1e59d3225b38"
SHEET_ID = "815f4b75-2d54-4ed7-91af-5dcd80d8d27a"
SOURCE_SHA256 = "74f4ff17ef7b566a3c0b44f46b98e238a30c8c312def5e42a1b3b8addf985fe0"


def _manifest() -> dict:
    return json.loads((REPOSITORY_ROOT / "data/flatbed-replay-manifest.json").read_text())


def _box(x: float, y: float, width: float, height: float) -> Detection:
    return Detection(x, y, width, height, 0, 0.9)


def _polygon_box(
    x: float,
    y: float,
    width: float,
    height: float,
    points: tuple[tuple[float, float], ...],
) -> Detection:
    return Detection(x, y, width, height, 0, 0.9, points=points)


def test_manifest_pins_repositioned_back_source_and_database_dpi() -> None:
    manifest = _manifest()

    assert manifest["expected_count_by_session"][SESSION_ID] == 5
    assert manifest["dpi_by_session"][SESSION_ID] == 300
    assert manifest["source_sha256_by_sheet"][SHEET_ID] == SOURCE_SHA256
    assert manifest["source_dimensions_by_sheet"][SHEET_ID] == [2480, 3507]
    assert SHEET_ID in manifest["must_pass_sheets"]


def test_gold_requires_all_five_printed_backs_and_verified_bed_gaps() -> None:
    rule = _manifest()["geometry_rules_by_sheet"][SHEET_ID]
    printed = rule["required_content_regions"]

    assert len(printed) == 5
    assert min(region["minimum_coverage"] for region in printed) >= 0.96
    assert printed[1]["height"] > 0.42  # Complete Luka card plus PSA label.
    assert printed[3]["height"] > 0.33  # Complete Coward autograph-card back.
    assert len(printed[4]["points"]) == 4  # Rotated Gradey Dick printed face.
    assert len(rule["verified_blank_regions"]) == 2
    assert (
        max(region["maximum_detection_coverage"] for region in rule["verified_blank_regions"])
        <= 0.10
    )


def test_rotated_gold_quad_does_not_require_its_empty_axis_aligned_corners() -> None:
    rotated = _manifest()["geometry_rules_by_sheet"][SHEET_ID]["required_content_regions"][-1]
    crop = _polygon_box(
        rotated["x"],
        rotated["y"],
        rotated["width"],
        rotated["height"],
        tuple(tuple(point) for point in rotated["points"]),
    )

    assert SCRIPT._geometry_failures([crop], 0, [{"required_content_regions": [rotated]}]) == []


def test_polygon_blank_region_does_not_score_its_empty_axis_aligned_corners() -> None:
    blank = {
        "x": 0.1,
        "y": 0.1,
        "width": 0.4,
        "height": 0.4,
        "points": [[0.1, 0.3], [0.3, 0.1], [0.5, 0.3], [0.3, 0.5]],
        "maximum_detection_coverage": 0.1,
    }
    crop_in_empty_bbox_corner = _box(0.1, 0.1, 0.08, 0.08)

    assert (
        SCRIPT._geometry_failures(
            [crop_in_empty_bbox_corner], 0, [{"verified_blank_regions": [blank]}]
        )
        == []
    )


def test_current_independent_four_by_two_result_fails_gold_and_blank_regions() -> None:
    rule = _manifest()["geometry_rules_by_sheet"][SHEET_ID]
    current_eight = [
        _box(0.0125, 0.017109, 0.481855, 0.250927),
        _box(0.5, 0.0, 0.5, 0.25),
        _box(0.0, 0.25, 0.5, 0.25),
        _box(0.5, 0.25, 0.5, 0.25),
        _box(0.0, 0.5, 0.5, 0.25),
        _box(0.5, 0.5, 0.5, 0.25),
        _box(0.0, 0.75, 0.5, 0.25),
        _box(0.5, 0.75, 0.5, 0.25),
    ]

    failures = SCRIPT._geometry_failures(current_eight, 0, [rule])

    assert "required_content_region_fragmented" in failures
    assert "verified_blank_region_covered" in failures


def test_five_complete_repositioned_objects_pass_without_front_prior_or_count() -> None:
    rule = _manifest()["geometry_rules_by_sheet"][SHEET_ID]
    complete_objects = [
        _box(0.006048, 0.015683, 0.485887, 0.263758),
        _box(0.637097, 0.015683, 0.362903, 0.494725),
        _box(0.004032, 0.319361, 0.493952, 0.285144),
        _box(0.633065, 0.536071, 0.360887, 0.413459),
        _polygon_box(
            0.008065,
            0.67294,
            0.552419,
            0.307956,
            (
                (0.008065, 0.710009),
                (0.512097, 0.67294),
                (0.560484, 0.932421),
                (0.032258, 0.980896),
            ),
        ),
    ]

    assert SCRIPT._geometry_failures(complete_objects, 0, [rule]) == []
