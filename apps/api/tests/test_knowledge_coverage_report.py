from copy import deepcopy

from test_knowledge_source_inputs import fixture, load

from card_platform.knowledge_intake_plan import build_plan


def test_unknown_denominators_and_registry_do_not_erase_snapshot(tmp_path):
    from card_platform.knowledge_coverage_report import build_coverage
    fixture(tmp_path)
    inputs = load(tmp_path)
    report = build_coverage(inputs, build_plan(inputs, []))
    assert report["known_baseline"]["known_product_count"] == 2
    assert report["work_scope"]["work_scope_product_count"] == 2
    assert report["annual_research_unit_count"] == 1
    for field in ["annual_product_denominator", "distinct_base_cards",
                  "full_card_image_denominator", "full_card_image_percent",
                  "freshly_verified_specimen_pairs"]:
        assert report["known_baseline"][field] is None
    assert report["known_baseline"]["annual_discovery_complete"] is False
    assert report["known_baseline"]["observed_verified_checklist_records"] == 1
    assert report["known_baseline"]["checklist_variant_records"] is None
    assert report["known_baseline"]["missing_artifact_count"] == 1
    assert report["coverage_registry"] == {"status": "available", "row_count": 0}
    assert report["workflow"]["search_evidence_not_provided"] == 2
    assert report["workflow"]["external_blocked"] == 0
    inputs["baseline_cards"]["optic"] = deepcopy(inputs["baseline_cards"]["donruss"]) * 2
    inputs["baseline_artifact_issues"] = []
    assert build_coverage(inputs, build_plan(inputs, []))["known_baseline"]["checklist_variant_records"] == 3
    inputs["scope"]["existing_product_keys"] = ["donruss"]
    limited = build_coverage(inputs, build_plan(inputs, []))
    assert limited["known_baseline"]["known_product_count"] == 2
    assert limited["work_scope"]["work_scope_product_count"] == 1
    assert limited["by_season_manufacturer"][0]["season_start"] == 2024


def test_media_records_not_physical_card_coverage(tmp_path):
    from card_platform.knowledge_coverage_report import build_coverage
    fixture(tmp_path)
    inputs = load(tmp_path)
    images = [{"asset_id": "design", "sha256": "d" * 64, "image_type": "design_reference"}]
    for index, (pair, front, back) in enumerate([("same", "a", "a"), ("real", "b", "c")]):
        images.extend({"asset_id": f"{index}-{side}", "sha256": digest * 64,
                       "image_type": "verified_card_scan", "pair_id": pair, "side": side}
                      for side, digest in [("front", front), ("back", back)])
    images.extend([{"asset_id": "conflict", "sha256": "e" * 64, "image_type": "unknown"},
                   {"asset_id": "conflict", "sha256": "f" * 64, "image_type": "unknown"}])
    inputs["products"]["donruss"]["attributes"]["reference_images"] = images
    report = build_coverage(inputs, build_plan(inputs, []))
    counts = report["known_baseline"]
    assert counts["reference_image_records"] == 6
    assert counts["unique_image_hashes"] == 6
    assert counts["image_type_counts"]["design_reference"] == 1
    assert counts["declared_specimen_pair_records"] == 1
    assert any(i["code"] == "image_asset_hash_conflict" for i in report["issues"])
    images[4]["pair_id"] = "other"
    assert build_coverage(inputs, build_plan(inputs, []))["known_baseline"]["declared_specimen_pair_records"] == 0
