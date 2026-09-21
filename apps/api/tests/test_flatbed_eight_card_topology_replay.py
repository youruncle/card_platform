import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from card_platform.flatbed_detection import Detection


REPOSITORY_ROOT = Path(__file__).parents[3]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "replay_flatbed_layouts.py"
SPEC = importlib.util.spec_from_file_location("eight_card_topology_replay", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)

TARGET_SESSION = "ac1e9883-889e-4491-8bc5-9da0dfd8e723"
SUCCESS_SESSION = "fac8e6df-e6aa-4eda-a6bf-3ea668dc5cb6"
TARGET_SHEETS = {
    "6fb14844-b7ba-495d-aa7c-edbf2b23579a": (
        "53bfce329c87e9d92e23a80c547500897fb123681d5d10ef4794b43abb06f649",
        [2480, 3507],
    ),
    "9632a555-7fa1-48f8-9606-d2afb278d4fc": (
        "a6625dd9eef80d774d389f575fc155bc5ce2f619186c55db30dfab31fa740bef",
        [1700, 2338],
    ),
    "1c0a8308-7850-4adb-aaf2-4aa692747940": (
        "02a60ab3cd630c1f979233939d4167744825cec6bca808da9b8053b6e8fcd20e",
        [2480, 3507],
    ),
}
SUCCESS_SHEETS = {
    "5b49a25a-f577-4715-9cd9-a363761d4786",
    "6c5c91bc-c5fe-463a-affa-cf326723bac0",
}


def _manifest() -> dict:
    return json.loads((REPOSITORY_ROOT / "data/flatbed-replay-manifest.json").read_text())


def test_manifest_pins_the_three_failure_sources_and_a_successful_pair() -> None:
    manifest = _manifest()

    assert manifest["expected_count_by_session"][TARGET_SESSION] == 8
    assert manifest["dpi_by_session"][TARGET_SESSION] == 300
    assert manifest["expected_count_by_session"][SUCCESS_SESSION] == 8
    assert manifest["dpi_by_session"][SUCCESS_SESSION] == 300
    assert set(TARGET_SHEETS) | SUCCESS_SHEETS <= set(manifest["must_pass_sheets"])
    for sheet_id, (sha256, dimensions) in TARGET_SHEETS.items():
        assert manifest["source_sha256_by_sheet"][sheet_id] == sha256
        assert manifest["source_dimensions_by_sheet"][sheet_id] == dimensions
        rule = manifest["geometry_rules_by_sheet"][sheet_id]
        assert len(rule["required_content_regions"]) == 8
        assert rule["require_distinct_content_matches"] is True
        assert rule["max_neighbour_content_overlap"] <= 0.03


def test_standalone_replay_passes_only_pixels_and_recorded_dpi(monkeypatch) -> None:
    sentinel = object()
    calls = []

    def fake_detect(content: bytes, **kwargs):
        calls.append((content, kwargs))
        return sentinel

    monkeypatch.setattr(SCRIPT, "detect_layout", fake_detect)

    assert SCRIPT._detect_standalone(b"pixels", 300) is sentinel
    assert calls == [(b"pixels", {"dpi": 300})]


def test_pinned_source_rejects_changed_pixels_or_dimensions() -> None:
    path = Path("data/flatbed-scans/owner/session/sheet/original.png")
    content = b"immutable pixels"
    image = np.zeros((20, 10, 3), dtype=np.uint8)
    manifest = {
        "source_sha256_by_sheet": {
            "sheet": hashlib.sha256(content).hexdigest(),
        },
        "source_dimensions_by_sheet": {"sheet": [10, 20]},
    }

    SCRIPT._validate_pinned_source(path, content, image, manifest)
    with pytest.raises(ValueError, match="SHA 不匹配"):
        SCRIPT._validate_pinned_source(path, b"changed", image, manifest)
    with pytest.raises(ValueError, match="尺寸不匹配"):
        SCRIPT._validate_pinned_source(path, content, image[:19], manifest)


def test_eight_interior_regions_reject_four_two_card_crops() -> None:
    manifest = _manifest()
    rule = manifest["geometry_rules_by_sheet"]["9632a555-7fa1-48f8-9606-d2afb278d4fc"]
    merged_pairs = [Detection(x, y, 0.5, 0.5, 0, 0.9) for y in (0.0, 0.5) for x in (0.0, 0.5)]
    failures = SCRIPT._geometry_failures(merged_pairs, 0, [rule])

    assert "region_too_tall" in failures
    assert "multiple_content_regions_in_one_crop" in failures
    assert "neighbour_content_contamination" in failures

    independent_cards = [
        Detection(x, y, 0.5, 0.25, 0, 0.9) for y in (0.0, 0.25, 0.5, 0.75) for x in (0.0, 0.5)
    ]
    assert SCRIPT._geometry_failures(independent_cards, 0, [rule]) == []
