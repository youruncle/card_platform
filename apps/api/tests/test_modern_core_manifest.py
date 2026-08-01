from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_modern_core_manifest_is_publishable_and_scoped() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3] / "data" / "knowledge" / "modern_core_checklists_v1.json"
    )
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert report.summary["covered_rows"] == 2
    assert all(report.replay.values())
    assert any(row["review_status"] == "draft" for row in manifest["entities"])
