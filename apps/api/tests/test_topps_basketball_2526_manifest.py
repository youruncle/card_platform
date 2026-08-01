from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_topps_basketball_core_manifest_is_publishable() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3] / "data" / "knowledge" / "topps_basketball_2526_core_v1.json"
    )
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert report.summary["covered_rows"] == 2
    assert report.summary["coverage_rows"] == 3
    assert all(report.replay.values())
