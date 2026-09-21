import importlib.util
import json
from pathlib import Path

from card_platform.flatbed_detection import Detection


REPOSITORY_ROOT = Path(__file__).parents[3]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "replay_flatbed_layouts.py"
SPEC = importlib.util.spec_from_file_location("mixed_format_replay", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)

TARGET_SESSION = "2e149a28-f7f1-4333-850e-1e59d3225b38"
TARGET_SHEET = "58da166f-c6ce-4dc1-a27f-09a23655883f"
TARGET_SHA = "9cd8297acc7f54d8eacce1ad0746f053dad577c34987b83a70c7dd3884bd6365"


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


def test_manifest_pins_mixed_format_sources_and_database_dpi() -> None:
    manifest = _manifest()

    assert manifest["expected_count_by_session"][TARGET_SESSION] == 5
    assert manifest["dpi_by_session"][TARGET_SESSION] == 300
    assert manifest["source_sha256_by_sheet"][TARGET_SHEET] == TARGET_SHA
    assert manifest["source_dimensions_by_sheet"][TARGET_SHEET] == [2480, 3507]
    assert TARGET_SHEET in manifest["must_pass_sheets"]

    historical = {
        "5bf6682a-7f9b-4fc7-95d0-fc32ac3dcaf4": "5af18e108ee260025cadcb1c7e3c60a42f5db0ac2a0f869b3b1dfc15ab4057e2",
        "69b56186-7879-48f2-84a7-6da5ae85b158": "7c835a99f65651e63cd1919ccda314b8555eb9572a40e427b0a40ba021ef1764",
        "83b49571-3533-4f5a-a57c-28c0573409f0": "7c835a99f65651e63cd1919ccda314b8555eb9572a40e427b0a40ba021ef1764",
        "bb91de95-1451-4ffc-b3ed-f02b437c8083": "dbc3d09c81ba80238a77ed9d44f80e8d5cb9ed12181675892b3bb1ebe3e83db4",
    }
    for sheet_id, sha256 in historical.items():
        assert manifest["source_sha256_by_sheet"][sheet_id] == sha256
        assert manifest["source_dimensions_by_sheet"][sheet_id] == [2480, 3507]
        assert sheet_id in manifest["must_pass_sheets"]


def test_gold_covers_full_psa_label_and_full_signed_card_not_just_centres() -> None:
    rule = _manifest()["geometry_rules_by_sheet"][TARGET_SHEET]
    psa, signed = rule["required_content_regions"][-2:]

    assert len(rule["required_content_regions"]) == 5
    assert psa["y"] < 0.03
    assert psa["height"] > 0.40
    assert signed["y"] < 0.57
    assert signed["height"] > 0.32
    assert min(item["minimum_coverage"] for item in rule["required_content_regions"]) >= 0.96
    assert rule["verified_blank_regions"] == [
        {
            "x": 0.032258,
            "y": 0.81266,
            "width": 0.391129,
            "height": 0.151126,
            "maximum_detection_coverage": 0.1,
        }
    ]


def test_fixed_four_by_two_grid_fails_complete_objects_and_verified_blank() -> None:
    rule = _manifest()["geometry_rules_by_sheet"][TARGET_SHEET]
    fixed_grid = [_box(x, y, 0.5, 0.25) for y in (0.0, 0.25, 0.5, 0.75) for x in (0.0, 0.5)]

    assert set(SCRIPT._geometry_failures(fixed_grid, 0, [rule])) == {
        "required_content_region_fragmented",
        "verified_blank_region_covered",
    }


def test_five_complete_mixed_format_objects_pass_without_count_or_grid_oracle() -> None:
    rule = _manifest()["geometry_rules_by_sheet"][TARGET_SHEET]
    complete_objects = [
        _box(0.0, 0.0, 0.475806, 0.253778),
        _box(0.0, 0.249501, 0.479839, 0.268035),
        _box(0.0, 0.514685, 0.524194, 0.253778),
        _box(0.594758, 0.0, 0.405242, 0.486171),
        _box(0.635081, 0.513259, 0.356855, 0.399202),
    ]

    assert SCRIPT._geometry_failures(complete_objects, 0, [rule]) == []

    phantom_blank = _box(0.0, 0.75, 0.5, 0.25)
    assert SCRIPT._geometry_failures(complete_objects + [phantom_blank], 0, [rule]) == [
        "verified_blank_region_covered"
    ]


def test_polygon_coverage_does_not_let_a_matching_bbox_hide_a_clipped_crop() -> None:
    rule = {
        "required_content_regions": [
            {
                "x": 0.1,
                "y": 0.1,
                "width": 0.8,
                "height": 0.8,
                "minimum_coverage": 0.96,
            }
        ]
    }
    diamond_inside_matching_bbox = _polygon_box(
        0.1,
        0.1,
        0.8,
        0.8,
        ((0.5, 0.1), (0.9, 0.5), (0.5, 0.9), (0.1, 0.5)),
    )

    assert SCRIPT._geometry_failures([diamond_inside_matching_bbox], 0, [rule]) == [
        "required_content_region_fragmented"
    ]


def test_polygon_coverage_drives_neighbour_and_verified_blank_checks() -> None:
    rule = {
        "required_content_regions": [
            {
                "x": 0.0,
                "y": 0.0,
                "width": 0.4,
                "height": 0.4,
                "minimum_coverage": 0.95,
            },
            {
                "x": 0.6,
                "y": 0.0,
                "width": 0.4,
                "height": 0.4,
                "minimum_coverage": 0.95,
            },
        ],
        "require_distinct_content_matches": True,
        "max_neighbour_content_overlap": 0.03,
        "verified_blank_regions": [
            {
                "x": 0.4,
                "y": 0.6,
                "width": 0.2,
                "height": 0.2,
                "maximum_detection_coverage": 0.1,
            }
        ],
    }
    # Both bboxes extend across the sheet, but their real polygons are disjoint
    # and stay outside the verified blank. Bbox-only scoring would falsely flag
    # neighbour contamination and a phantom detection over the blank area.
    left = _polygon_box(
        0.0,
        0.0,
        1.0,
        1.0,
        ((0.0, 0.0), (0.4, 0.0), (0.4, 0.4), (0.0, 0.4)),
    )
    right = _polygon_box(
        0.0,
        0.0,
        1.0,
        1.0,
        ((0.6, 0.0), (1.0, 0.0), (1.0, 0.4), (0.6, 0.4)),
    )

    assert SCRIPT._geometry_failures([left, right], 0, [rule]) == []
