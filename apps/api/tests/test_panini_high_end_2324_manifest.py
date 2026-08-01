from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_panini_high_end_manifest_is_publishable() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3] / "data" / "knowledge" / "panini_high_end_2324_v1.json"
    )
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert report.summary["covered_rows"] == 12
    assert all(report.replay.values())
