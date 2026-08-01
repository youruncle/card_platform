from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


def test_unsourced_seasonless_pending_retirement_manifest_is_publishable() -> None:
    manifest = load_manifest(
        Path(__file__).parents[3]
        / "data"
        / "knowledge"
        / "coverage_unsourced_seasonless_pending_retirement_v1.json"
    )

    report = validate_manifest(manifest)

    assert report.publishable
    assert report.conflicts == ()
    assert report.summary["retired_coverage_rows"] == 9
