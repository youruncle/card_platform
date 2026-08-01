from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest


MANIFEST = (
    Path(__file__).parents[3] / "data" / "knowledge" / "basketball_mvp_v3.json"
)


def test_mvp_manifest_is_publishable_and_replay_complete() -> None:
    report = validate_manifest(load_manifest(MANIFEST))
    assert report.publishable, report.conflicts
    assert all(report.replay.values())
    assert report.summary["covered_rows"] == 3
    assert report.summary["pending_source_rows"] >= 10


def test_covered_row_without_source_is_rejected() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["coverage"][0].pop("source_key")
    report = validate_manifest(manifest)
    assert not report.publishable
    assert any(row["code"] == "covered_without_source" for row in report.conflicts)


def test_ambiguous_alias_is_rejected() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["entities"][1]["aliases"].append(
        {"value": "帕尼尼", "language": "zh", "alias_type": "translation"}
    )
    report = validate_manifest(manifest)
    assert any(row["code"] == "ambiguous_alias" for row in report.conflicts)


def test_unknown_relation_type_is_rejected_before_database_apply() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["relations"][0]["relation_type"] = "operated_by"
    report = validate_manifest(manifest)
    assert not report.publishable
    assert any(row["code"] == "invalid_relation_type" for row in report.conflicts)


def test_unknown_source_type_is_rejected_before_database_apply() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["sources"][0]["source_type"] = "official_history"
    report = validate_manifest(manifest)
    assert not report.publishable
    assert any(row["code"] == "invalid_source_type" for row in report.conflicts)


def test_unknown_alias_type_is_rejected_before_database_apply() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["entities"][1]["aliases"][0]["alias_type"] = "expanded_form"
    report = validate_manifest(manifest)
    assert not report.publishable
    assert any(row["code"] == "invalid_alias_type" for row in report.conflicts)


def test_unknown_coverage_entity_is_rejected_before_database_apply() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["coverage"][0]["product"] = "missing-product"
    report = validate_manifest(manifest)
    assert not report.publishable
    assert any(row["code"] == "missing_coverage_entity" for row in report.conflicts)


def test_coverage_retirement_requires_exact_scope_and_reason() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["retire_coverage"] = [{"manufacturer_name": "Panini"}]
    report = validate_manifest(manifest)
    assert not report.publishable
    codes = {row["code"] for row in report.conflicts}
    assert "invalid_coverage_retirement" in codes
    assert "coverage_retirement_without_reason" in codes


def test_unknown_applicability_status_is_rejected_before_database_apply() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["applicability"] = [
        {
            "entity": "brand-bowman",
            "product": "product-donruss",
            "status": "needs_review",
        }
    ]
    report = validate_manifest(manifest)
    assert not report.publishable
    assert any(row["code"] == "invalid_applicability_status" for row in report.conflicts)


def test_product_coverage_migration_and_entity_retirement_are_validated() -> None:
    manifest = load_manifest(MANIFEST)
    manifest["reassign_product_coverage"] = [{"previous_product": "product-donruss", "product": "brand-bowman"}]
    manifest["retire_entities"] = [{"entity": "product-donruss", "source_key": "beckett-2023-donruss", "reason": "superseded"}]
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts

    manifest["reassign_product_coverage"][0]["product"] = "missing-product"
    manifest["retire_entities"][0]["reason"] = ""
    report = validate_manifest(manifest)
    codes = {row["code"] for row in report.conflicts}
    assert "missing_product_coverage_reassignment_entity" in codes
    assert "retired_entity_without_reason" in codes
