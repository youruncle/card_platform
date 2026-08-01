from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_select_animal_core_manifest_is_publishable_and_scoped() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3] / "data" / "knowledge" / "select_2425_animal_core_v1.json"
    )
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert report.summary["covered_rows"] == 3
    assert all(report.replay.values())
