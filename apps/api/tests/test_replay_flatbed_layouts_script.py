import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

from card_platform.flatbed_detection import Detection


SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "replay_flatbed_layouts.py"
SPEC = importlib.util.spec_from_file_location("replay_flatbed_layouts_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)


def test_must_pass_sheet_fails_even_when_aggregate_floor_can_still_pass() -> None:
    results = [
        {
            "sheet_ids": ["mixed-slab"],
            "count": 7,
            "expected": 6,
            "unusable_regions": 0,
            "geometry_failures": ["required_long_region_missing"],
            "passed": False,
        },
        {
            "sheet_ids": ["ordinary-grid"],
            "count": 8,
            "expected": 8,
            "unusable_regions": 0,
            "geometry_failures": [],
            "passed": True,
        },
    ]

    regressions = SCRIPT._must_pass_regressions(
        results, ["mixed-slab", "ordinary-grid", "missing-sheet"]
    )

    assert regressions == [
        "must_pass_sheet:mixed-slab:count=7:expected=6:unusable=0:"
        "geometry=required_long_region_missing",
        "must_pass_sheet:missing-sheet:missing",
    ]


def test_geometry_rule_can_require_the_fragmentation_guard_signal() -> None:
    boxes = [Detection(0.1, 0.1, 0.3, 0.45, 0, 0.9)]
    rules = [{"required_layout_signals": ["single_slab_grid_fragmentation_guard"]}]

    assert SCRIPT._geometry_failures(boxes, 0, rules, {}) == [
        "required_layout_signal_missing:single_slab_grid_fragmentation_guard"
    ]
    assert (
        SCRIPT._geometry_failures(
            boxes,
            0,
            rules,
            {"single_slab_grid_fragmentation_guard": 1},
        )
        == []
    )


def test_replay_requires_one_crop_to_cover_a_cross_slot_card():
    rules = [
        {
            "required_content_regions": [
                {
                    "x": 0.14,
                    "y": 0.77,
                    "width": 0.49,
                    "height": 0.23,
                }
            ]
        }
    ]
    fragments = [Detection(0, 0.75, 0.5, 0.25, 0, 0.9), Detection(0.5, 0.75, 0.5, 0.25, 0, 0.9)]
    assert SCRIPT._geometry_failures(fragments, 0, rules) == ["required_content_region_fragmented"]
    assert SCRIPT._geometry_failures([Detection(0.12, 0.75, 0.53, 0.25, 0, 0.9)], 0, rules) == []


def test_replay_does_not_accept_one_large_crop_covering_two_real_cards():
    rules = [
        {
            "require_distinct_content_matches": True,
            "max_neighbour_content_overlap": 0.03,
            "required_content_regions": [
                {"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.3},
                {"x": 0.5, "y": 0.1, "width": 0.3, "height": 0.3},
            ],
        }
    ]
    merged = [Detection(0.05, 0.05, 0.8, 0.4, 0, 0.99)]
    assert set(SCRIPT._geometry_failures(merged, 0, rules)) == {
        "multiple_content_regions_in_one_crop",
        "neighbour_content_contamination",
    }
    separate = [
        Detection(0.08, 0.08, 0.34, 0.34, 0, 0.9),
        Detection(0.48, 0.08, 0.34, 0.34, 0, 0.9),
    ]
    assert SCRIPT._geometry_failures(separate, 0, rules) == []


def test_recorded_dpi_prefers_sheet_override_and_is_part_of_source_key() -> None:
    path = Path("data/flatbed-scans/owner/session/sheet/original.png")
    manifest = {
        "expected_count_by_session": {"session": 8},
        "dpi_by_session": {"session": 600},
        "dpi_by_sheet": {"sheet": 300},
    }

    assert SCRIPT._recorded_dpi(path, manifest) == 300
    assert SCRIPT._source_key(path, b"same pixels", manifest)[1:] == (8, 300)

    manifest["dpi_by_sheet"] = {}
    assert SCRIPT._recorded_dpi(path, manifest) == 600
    assert SCRIPT._source_key(path, b"same pixels", manifest)[1:] == (8, 600)


def test_recorded_dpi_is_required_instead_of_inferred_from_expected_count() -> None:
    path = Path("data/flatbed-scans/owner/session/sheet/original.png")
    manifest = {"expected_count_by_session": {"session": 8}}

    with pytest.raises(KeyError, match="缺少扫描 DPI"):
        SCRIPT._recorded_dpi(path, manifest)


def test_manifest_records_database_dpi_for_every_replayed_session() -> None:
    manifest = json.loads(
        (Path(__file__).parents[3] / "data" / "flatbed-replay-manifest.json").read_text()
    )

    assert set(manifest["dpi_by_session"]) == set(manifest["expected_count_by_session"])
    assert manifest["dpi_by_session"]["dca1a79a-b1bf-4e4f-b74e-3bc43b0875d0"] == 600
    assert set(manifest["dpi_by_session"].values()) == {300, 600}


def test_replay_can_write_a_json_report_without_shell_redirection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    manifest_path.write_text(
        json.dumps(
            {
                "corpus_glob": "missing/*/original.png",
                "expected_count_by_session": {},
                "dpi_by_session": {},
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "replay_flatbed_layouts.py",
            "--manifest",
            str(manifest_path),
            "--json",
            "--output",
            str(report_path),
        ],
    )

    assert SCRIPT.main() == 0
    assert json.loads(report_path.read_text())["unique_files"] == 0


def test_replay_json_normalizes_numpy_detector_scalars() -> None:
    assert SCRIPT._json_default(np.int64(5)) == 5
    assert SCRIPT._json_default(np.float32(0.75)) == pytest.approx(0.75)
    with pytest.raises(TypeError, match="not JSON serializable"):
        SCRIPT._json_default(object())

def test_object_quad_replay_runs_resolver_without_labels(monkeypatch):
    from card_platform.flatbed_detection import LayoutHypothesis
    import card_platform.flatbed_object_quads as quads
    original = LayoutHypothesis((Detection(.1,.1,.4,.2,0,.8),),.8,0,0)
    resolved = LayoutHypothesis((Detection(.1,.1,.4,.2,15,.9),),.9,0,0)
    calls=[]
    monkeypatch.setattr(SCRIPT, "detect_layout", lambda content, *, dpi: original)
    def resolve(content, layout):
        calls.append((content,layout))
        return resolved
    monkeypatch.setattr(quads,"resolve_object_layout",resolve)
    assert SCRIPT._detect_standalone(b"image",300,object_quads=True) is resolved
    assert calls == [(b"image",original)]
    calls.clear()
    assert SCRIPT._detect_standalone(b"image",300) is original
    assert calls == []


def test_grouped_body_and_label_must_be_complete_in_the_same_crop() -> None:
    # A body crop plus an isolated label crop cannot count as one complete slab.
    rules = [{"required_content_regions": [
        {"object_id": "slab-a", "role": "body", "x": .20, "y": .10, "width": .30, "height": .20},
        {"object_id": "slab-a", "role": "label", "x": .10, "y": .10, "width": .07, "height": .20},
    ]}]
    split = [Detection(.20, .10, .30, .20, 0, .99), Detection(.10, .10, .07, .20, 0, .99)]
    assert SCRIPT._geometry_failures(split, 0, rules) == ["object_content_regions_split"]
    assert SCRIPT._geometry_failures([Detection(.09, .09, .42, .22, 0, .8)], 0, rules) == []


def test_grouped_label_is_required_even_when_card_body_and_count_are_correct() -> None:
    rules = [{"required_content_regions": [
        {"object_id": "slab-a", "role": "body", "x": .20, "y": .10, "width": .30, "height": .20},
        {"object_id": "slab-a", "role": "label", "x": .10, "y": .10, "width": .07, "height": .20},
    ]}]
    failures = SCRIPT._geometry_failures([Detection(.20, .10, .30, .20, 0, .99)], 0, rules)
    assert "required_content_region_fragmented" in failures
    assert "required_label_region_fragmented" in failures


def test_explicit_objects_require_distinct_crops_without_separating_own_label() -> None:
    rules = [{"required_content_regions": [
        {"object_id": "a", "role": "body", "x": .20, "y": .10, "width": .20, "height": .20},
        {"object_id": "a", "role": "label", "x": .10, "y": .10, "width": .07, "height": .20},
        {"object_id": "b", "role": "body", "x": .20, "y": .50, "width": .20, "height": .20},
        {"object_id": "b", "role": "label", "x": .10, "y": .50, "width": .07, "height": .20},
    ]}]
    merged = [Detection(.09, .09, .32, .62, 0, .99)]
    assert SCRIPT._geometry_failures(merged, 0, rules) == ["multiple_content_regions_in_one_crop"]
    separate = [Detection(.09, .09, .32, .22, 0, .9), Detection(.09, .49, .32, .22, 0, .9)]
    assert SCRIPT._geometry_failures(separate, 0, rules) == []


def test_grouped_neighbor_check_excludes_the_same_objects_label() -> None:
    rules = [{"max_neighbour_content_overlap": .01, "required_content_regions": [
        {"object_id": "a", "role": "body", "x": .20, "y": .10, "width": .20, "height": .20},
        {"object_id": "a", "role": "label", "x": .10, "y": .10, "width": .07, "height": .20},
        {"object_id": "b", "role": "body", "x": .20, "y": .50, "width": .20, "height": .20},
    ]}]
    clean = [Detection(.09, .09, .32, .22, 0, .9), Detection(.19, .49, .22, .22, 0, .9)]
    assert SCRIPT._geometry_failures(clean, 0, rules) == []
    contaminated = [Detection(.09, .09, .32, .50, 0, .9), clean[1]]
    assert SCRIPT._geometry_failures(contaminated, 0, rules) == ["neighbour_content_contamination"]


def test_gold_uses_final_source_crop_points_instead_of_initial_quad() -> None:
    rules = [{"required_content_regions": [{"x": .10, "y": .10, "width": .40, "height": .20}]}]
    boxes = [Detection(.10, .10, .40, .20, 0, .99)]
    partial = [[{"x": .30, "y": .10}, {"x": .50, "y": .10}, {"x": .50, "y": .30}, {"x": .30, "y": .30}]]
    assert SCRIPT._geometry_failures(boxes, 0, rules, source_crop_points=partial) == ["required_content_region_fragmented"]
    initial_partial = [Detection(.30, .10, .20, .20, 0, .99)]
    complete = [[(.10, .10), (.50, .10), (.50, .30), (.10, .30)]]
    assert SCRIPT._geometry_failures(initial_partial, 0, rules, source_crop_points=complete) == []


def test_invalid_final_source_crop_points_cannot_pass_gold_on_old_bounds() -> None:
    boxes = [Detection(.10, .10, .40, .20, 0, .99)]
    rules = [{"required_content_regions": [{"x": .10, "y": .10, "width": .40, "height": .20}]}]
    malformed = [[(.10, .10), (.50, .10), (float("nan"), .30), (.10, .30)]]
    assert "invalid_source_crop_geometry" in SCRIPT._geometry_failures(boxes, 0, rules, source_crop_points=malformed)


def test_real_graded_scan_gold_rejects_a_whole_body_crop_without_its_label() -> None:
    manifest = json.loads((Path(__file__).parents[3] / "data" / "flatbed-replay-manifest.json").read_text())
    rule = manifest["geometry_rules_by_sheet"].get("916c9c36-5059-4d40-b6e7-f0927aae47fe", {})
    # Full printed cards, manually measured from the immutable original;
    # both grading labels at x=.02.. .11 are deliberately omitted.
    bodies = [Detection(.16, .04, .44, .23, 0, .99), Detection(.16, .34, .44, .23, 0, .99)]
    assert "required_label_region_fragmented" in SCRIPT._geometry_failures(bodies, 0, [rule])
    full_slabs = [Detection(.01, .02, .64, .27, 0, .90), Detection(.01, .32, .64, .28, 0, .90)]
    assert SCRIPT._geometry_failures(full_slabs, 0, [rule]) == []


def test_real_back_gold_rejects_jason_kidd_and_derrick_white_inner_panels() -> None:
    manifest = json.loads((Path(__file__).parents[3] / "data" / "flatbed-replay-manifest.json").read_text())
    rule = manifest["geometry_rules_by_sheet"].get("c1cc16cb-790b-4fe1-b948-da61e3b3e7b9", {})
    # The actual v29 wrong inner panels, despite high confidence and correct count.
    panels = [Detection(.515752, .120051, .483249, .143323, 0, .92), Detection(.107440, .803763, .295156, .175674, 0, .92)]
    assert "required_content_region_fragmented" in SCRIPT._geometry_failures(panels, 0, [rule])
    complete_print = [Detection(.57, .04, .43, .23, 0, .90), Detection(.05, .79, .45, .21, 0, .90)]
    assert SCRIPT._geometry_failures(complete_print, 0, [rule]) == []
