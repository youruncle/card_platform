from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_card_identity_one_one_manifest_is_publishable() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3] / "data" / "knowledge" / "card_identity_one_one_2324_v1.json"
    )
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert all(report.replay.values())
