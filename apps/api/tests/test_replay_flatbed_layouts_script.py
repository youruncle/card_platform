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
