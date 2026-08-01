from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_historical_brand_era_manifest_is_publishable() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3] / "data" / "knowledge" / "historical_brand_era_v1.json"
    )
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert report.summary["covered_rows"] == 1
    assert all(report.replay.values())
