from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_prizm_parallel_core_manifest_is_publishable_and_scoped() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3] / "data" / "knowledge" / "prizm_2324_parallel_core_v1.json"
    )
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert report.summary["covered_rows"] == 6
    assert all(report.replay.values())
