from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_national_treasures_wembanyama_identity_manifest_is_publishable() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3]
        / "data"
        / "knowledge"
        / "card_identity_national_treasures_wembanyama_2324_v1.json"
    )

    report = validate_manifest(manifest)

    assert report.publishable, report.conflicts
    assert all(report.replay.values())
    assert report.summary["covered_rows"] == 1
