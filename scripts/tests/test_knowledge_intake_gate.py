import importlib.util
import json
from hashlib import sha256
from pathlib import Path

import pytest


def publisher():
    path = Path(__file__).resolve().parents[1] / "publish_knowledge_release.py"
    spec = importlib.util.spec_from_file_location("intake_publisher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return {"path": name, "sha256": sha256(path.read_bytes()).hexdigest()}


def fixture(root):
    baseline = {
        "checksum": "a" * 64,
        "products": [
            {"attributes": {"source_key": key, "reference_images": []}}
            for key in ("a", "b")
        ],
    }
    capture = save(root, "audit/baseline.json", baseline)
    evidence = save(
        root,
        "audit/query.json",
        {"query": "actual source query", "result": "observed reader response"},
    )
    text = save(
        root,
        "audit/text.json",
        {"products": [{"source_key": k, "status": "reviewed"} for k in ("a", "b")]},
    )
    rows = [
        {
            "source_key": key,
            "workflow_status": "review_complete",
            "outcome": "external_gap",
            "searches": [
                {
                    "family": family,
                    "query": "product " + family,
                    "capture": evidence,
                    "actual_outcome": "blocked",
                }
                for family in ("manufacturer", "specialist", "specimen", "distributor")
            ],
            "candidate_decisions": [],
            "blockers": [
                "Actual source returns403; alternate sources lack usable images"
            ],
            "next_action": "Retry when source restores public access",
            "text_audit": text,
        }
        for key in ("a", "b")
    ]
    ledger = {
        "contract": "knowledge-intake-audit-v1",
        "scope_keys": ["a", "b"],
        "baseline_capture": capture,
        "products": rows,
    }
    bundle = {
        "expected_previous_checksum": "a" * 64,
        "manifests": [],
        "intake_audit": save(root, "audit/ledger.json", ledger),
    }
    bundle["manifests"] = [
        save(
            root,
            f"release/{k}.json",
            {"entities": [{"attributes": {"source_key": k, "reference_images": []}}]},
        )["path"]
        for k in ("a", "b")
    ]
    reseal(root, ledger, bundle)
    return ledger, bundle


def reseal(root, ledger, bundle):
    bundle["intake_audit"] = save(root, "audit/ledger.json", ledger)
    bundle["files"] = {
        p.relative_to(root).as_posix(): {"sha256": sha256(p.read_bytes()).hexdigest()}
        for p in root.rglob("*")
        if p.is_file()
    }


def test_gate_rejects_the_previous_47_unsearched_failure_mode(tmp_path):
    ledger, bundle = fixture(tmp_path)
    ledger["products"].pop()
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="scope"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_gate_requires_real_routes_before_an_external_gap_is_accepted(tmp_path):
    ledger, bundle = fixture(tmp_path)
    ledger["products"][0]["searches"].pop()
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="source routes"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_gate_rejects_pending_candidates_even_after_all_searches(tmp_path):
    ledger, bundle = fixture(tmp_path)
    ledger["products"][0]["candidate_decisions"] = [
        {"sha256": "x", "decision": "pending"}
    ]
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="candidate"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_gate_does_not_accept_tampered_search_evidence(tmp_path):
    ledger, bundle = fixture(tmp_path)
    (tmp_path / "audit/query.json").write_text("modified")
    with pytest.raises(ValueError, match="checksum"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_gate_keeps_external_gaps_distinct_from_content_complete(tmp_path):
    ledger, bundle = fixture(tmp_path)
    result = publisher().validate_intake_audit(tmp_path, bundle)
    assert result == {
        "products": 2,
        "searched_missing_products": 2,
        "unsearched": 0,
        "pending_review": 0,
        "accepted_new_assets": 0,
        "external_gaps": 2,
    }


def test_publication_rejects_cloud_baseline_drift():
    with pytest.raises(ValueError, match="baseline"):
        publisher().validate_cloud_baseline(
            {"expected_previous_checksum": "a" * 64}, "b" * 64
        )


def test_gate_does_not_allow_unreviewed_images_to_be_closed_as_rejected(tmp_path):
    ledger, bundle = fixture(tmp_path)
    ledger["products"][0]["candidate_decisions"] = [
        {
            "sha256": "image",
            "decision": "rejected",
            "reason": "Extra image not yet visually reviewed",
        }
    ]
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="candidate"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_gate_rejects_frozen_scope_that_does_not_match_actual_cloud(tmp_path):
    ledger, bundle = fixture(tmp_path)
    actual = {
        "entities": [
            {
                "entity_type": "product",
                "attributes": {
                    "source_key": key,
                    "reference_images": [],
                    "official_artifact": {"checksum": "a" * 64},
                    "season_start": 2023,
                    "manufacturer": "Topps",
                },
            }
            for key in ("a", "b", "c")
        ]
    }
    with pytest.raises(ValueError, match="cloud scope"):
        publisher().validate_intake_audit(tmp_path, bundle, current_snapshot=actual)


def test_gate_requires_evidence_to_be_part_of_transferred_file_map(tmp_path):
    ledger, bundle = fixture(tmp_path)
    bundle["files"] = {}
    with pytest.raises(ValueError, match="sealed file map"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_gate_preserves_old_image_metadata_and_paths(tmp_path):
    ledger, bundle = fixture(tmp_path)
    old = {
        "asset_id": "old",
        "sha256": "1" * 64,
        "source_key": "a",
        "asset_path": "old-batch/media/old.jpg",
        "side": "front",
        "source_url": "https://source.example/cert",
    }
    baseline = json.loads((tmp_path / ledger["baseline_capture"]["path"]).read_text())
    baseline["products"][0]["attributes"]["reference_images"] = [old]
    ledger["baseline_capture"] = save(tmp_path, "audit/baseline.json", baseline)
    manifest = {
        "entities": [
            {
                "attributes": {
                    "source_key": "a",
                    "reference_images": [old | {"side": "back"}],
                }
            }
        ]
    }
    save(tmp_path, "release/a.json", manifest)
    bundle["manifests"] = ["release/a.json", "release/b.json"]
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="existing reviewed image"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_gate_rejects_manifest_outside_frozen_product_scope(tmp_path):
    ledger, bundle = fixture(tmp_path)
    save(
        tmp_path,
        "release/outside.json",
        {
            "entities": [
                {"attributes": {"source_key": "outside", "reference_images": []}}
            ]
        },
    )
    bundle["manifests"].append("release/outside.json")
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="manifest scope"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_gate_requires_new_image_source_capture(tmp_path):
    ledger, bundle = fixture(tmp_path)
    media = save(tmp_path, "media/new.jpg", {"actual": "image bytes"})
    asset = {
        "asset_id": "new",
        "sha256": media["sha256"],
        "source_key": "a",
        "asset_path": tmp_path.name + "/media/new.jpg",
        "source_url": "https://source.example/product",
        "source_artifact_sha256": "f" * 64,
    }
    save(
        tmp_path,
        "release/a.json",
        {
            "entities": [
                {"attributes": {"source_key": "a", "reference_images": [asset]}}
            ]
        },
    )
    ledger["products"][0]["candidate_decisions"] = [
        {
            "decision": "accepted",
            "review_completed": True,
            "asset_id": "new",
            "sha256": media["sha256"],
        }
    ]
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="source capture"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_verified_scan_cannot_skip_independent_identity_review(tmp_path):
    ledger, bundle = fixture(tmp_path)
    media = save(tmp_path, "media/new.jpg", {"actual": "image bytes"})
    source = save(tmp_path, "sources/capture.json", {"actual": "source capture"})
    asset = {
        "asset_id": "new",
        "sha256": media["sha256"],
        "source_key": "a",
        "asset_path": tmp_path.name + "/media/new.jpg",
        "source_url": "https://source.example/cert",
        "source_artifact_sha256": source["sha256"],
        "image_type": "verified_card_scan",
        "player": "Wrong Player",
        "card_number": "wrong",
        "identity_verification": {"card_id": "wrong"},
    }
    save(
        tmp_path,
        "release/a.json",
        {
            "entities": [
                {"attributes": {"source_key": "a", "reference_images": [asset]}}
            ]
        },
    )
    ledger["products"][0]["candidate_decisions"] = [
        {
            "decision": "accepted",
            "review_completed": True,
            "asset_id": "new",
            "sha256": media["sha256"],
            "source_url": asset["source_url"],
            "source_capture": source,
        }
    ]
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="identity review"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_design_reference_cannot_smuggle_unreviewed_card_identity(tmp_path):
    ledger, bundle = fixture(tmp_path)
    media = save(tmp_path, "media/new.jpg", {"actual": "image bytes"})
    source = save(tmp_path, "sources/capture.json", {"actual": "source capture"})
    asset = {
        "asset_id": "new",
        "sha256": media["sha256"],
        "source_key": "a",
        "asset_path": tmp_path.name + "/media/new.jpg",
        "source_url": "https://source.example/product",
        "source_artifact_sha256": source["sha256"],
        "image_type": "official_design",
        "player": "Unverified",
    }
    save(
        tmp_path,
        "release/a.json",
        {
            "entities": [
                {"attributes": {"source_key": "a", "reference_images": [asset]}}
            ]
        },
    )
    ledger["products"][0]["candidate_decisions"] = [
        {
            "decision": "accepted",
            "review_completed": True,
            "asset_id": "new",
            "sha256": media["sha256"],
            "source_url": asset["source_url"],
            "source_capture": source,
        }
    ]
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="cannot assert card identity"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_publication_rejects_draft_revision_drift_even_if_published_checksum_is_same():
    with pytest.raises(ValueError, match="revision"):
        publisher().validate_cloud_baseline(
            {"expected_previous_checksum": "a" * 64, "expected_previous_revision": 10},
            "a" * 64,
            current_revision=11,
        )


def focused_fixture(root):
    ledger, bundle = fixture(root)
    baseline = json.loads((root / "audit/baseline.json").read_text())
    baseline["products"][0]["attributes"]["product"] = "Chrome Hobby"
    baseline["products"][0]["attributes"]["season_start"] = 2023
    baseline["products"][0]["attributes"]["manufacturer"] = "Topps"
    ledger["baseline_capture"] = save(root, "audit/baseline.json", baseline)
    scope = {"existing_product_keys": ["a"], "annual_research_keys": ["c"]}
    ledger.update(
        contract="knowledge-intake-audit-v2",
        scope_keys=["a", "c"],
        frozen_work_scope=save(root, "audit/work-scope.json", scope),
        manifest_scope_keys=["a", "c"],
    )
    ledger["products"][1]["source_key"] = "c"
    text = save(
        root,
        "audit/text.json",
        {"products": [{"source_key": k, "status": "reviewed"} for k in ("a", "c")]},
    )
    for p in ledger["products"]:
        p["text_audit"] = text
    new = {
        "source_key": "c",
        "manufacturer": "Panini",
        "product": "Instant annual source directory",
        "season_start": 2025,
        "season_end": 2025,
        "reference_images": [],
        "knowledge_scope": "annual_source_directory",
    }
    proof = save(
        root,
        "audit/new-product-c.json",
        {
            "status": "approved",
            "source_key": "c",
            "source_url": "https://manufacturer.example/instant/2025",
            "manufacturer": "Panini",
            "season_start": 2025,
            "season_end": 2025,
            "product": "Instant annual source directory",
            "capture": ledger["products"][1]["searches"][0]["capture"],
            "locator": "2025 official physical product heading",
        },
    )
    ledger["new_product_reviews"] = {"c": proof}
    bundle["manifests"] = [
        save(
            root,
            "release/a.json",
            {
                "coverage": [],
                "entities": [{"attributes": baseline["products"][0]["attributes"]}],
            },
        )["path"],
        save(
            root, "release/c.json", {"coverage": [], "entities": [{"attributes": new}]}
        )["path"],
    ]
    reseal(root, ledger, bundle)
    return ledger, bundle


def test_focused_intake_accepts_subset_and_reviewed_new_directory_without_researching_unrelated_baseline(
    tmp_path,
):
    ledger, bundle = focused_fixture(tmp_path)
    result = publisher().validate_intake_audit(tmp_path, bundle)
    assert result["products"] == 2 and result["unsearched"] == 0


def test_focused_intake_requires_three_routes_even_when_product_already_has_old_image(
    tmp_path,
):
    ledger, bundle = focused_fixture(tmp_path)
    baseline = json.loads((tmp_path / "audit/baseline.json").read_text())
    old = {"asset_id": "old", "sha256": "b" * 64}
    baseline["products"][0]["attributes"]["reference_images"] = [old]
    ledger["baseline_capture"] = save(tmp_path, "audit/baseline.json", baseline)
    save(
        tmp_path,
        "release/a.json",
        {
            "coverage": [],
            "entities": [{"attributes": baseline["products"][0]["attributes"]}],
        },
    )
    ledger["products"][0]["searches"] = [ledger["products"][0]["searches"][0]]
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="source routes"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_focused_intake_does_not_allow_overwriting_existing_official_product_fields(
    tmp_path,
):
    ledger, bundle = focused_fixture(tmp_path)
    p = json.loads((tmp_path / "release/a.json").read_text())
    p["entities"][0]["attributes"]["product"] = "Chrome Retail"
    save(tmp_path, "release/a.json", p)
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="protected product attribute"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_new_directory_requires_approved_source_identity_and_sealed_capture(tmp_path):
    ledger, bundle = focused_fixture(tmp_path)
    ledger["new_product_reviews"] = {}
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="new product"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_unproved_annual_unit_can_only_close_as_evidenced_gap_not_silently_disappear(
    tmp_path,
):
    ledger, bundle = focused_fixture(tmp_path)
    ledger["manifest_scope_keys"] = ["a"]
    bundle["manifests"] = ["release/a.json"]
    ledger["new_product_reviews"] = {}
    ledger["products"][1]["outcome"] = "reviewed_sources"
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="unpublished research unit"):
        publisher().validate_intake_audit(tmp_path, bundle)
    ledger["products"][1]["outcome"] = "external_gap"
    reseal(tmp_path, ledger, bundle)
    result = publisher().validate_intake_audit(tmp_path, bundle)
    assert result["products"] == 2 and result["external_gaps"] == 2


def test_focused_intake_cannot_omit_a_frozen_annual_research_unit(tmp_path):
    ledger, bundle = focused_fixture(tmp_path)
    ledger["scope_keys"] = ["a"]
    ledger["products"] = ledger["products"][:1]
    ledger["manifest_scope_keys"] = ["a"]
    bundle["manifests"] = ["release/a.json"]
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="scope"):
        publisher().validate_intake_audit(tmp_path, bundle)


def parallel_fixture(root):
    ledger, bundle = focused_fixture(root)
    bundle["batch_id"] = "test-focused"
    cr = save(root, "sources/parallel.json", {"observed": "BASE CARDS Gold /50"})
    value = {
        "official_name": "Gold",
        "serial_total": 50,
        "box_formats": ["Hobby"],
        "availability_scope": "BASE CARDS",
    }
    evidence = {
        "intake_batch_id": "test-focused",
        "source_url": "https://manufacturer.example/odds.pdf",
        "source_artifact": root.name + "/" + cr["path"],
        "source_sha256": cr["sha256"],
        "facts": [{"claim_id": "gold", "field": "parallel_rule", "value": value}],
    }
    m = json.loads((root / "release/a.json").read_text())
    attrs = m["entities"][0]["attributes"]
    attrs["supplementary_evidence"] = [evidence]
    option = {
        "source_claim_id": "gold",
        "official_name": "Gold",
        "serial_total": 50,
        "box_formats": ["Hobby"],
        "brand": "Topps",
        "product": "Chrome Hobby",
        "season_start": 2023,
        "season_end": None,
        "source_url": evidence["source_url"],
        "source_checksum": cr["sha256"],
        "source_locator": "BASE CARDS row Gold",
        "availability_sections": ["BASE CARDS"],
        "availability_scope": "BASE CARDS",
        "source_scope_wording": "BASE CARDS",
    }
    attrs["parallel_options"] = [option]
    save(root, "release/a.json", m)
    ledger["source_fact_audit"] = save(
        root,
        "audit/facts.json",
        {
            "status": "reviewed",
            "records": [
                {
                    "record_id": "r",
                    "canonical_source_key": "a",
                    "source_capture_sha256": cr["sha256"],
                    "source_url": evidence["source_url"],
                    "fact_decisions": [
                        {
                            "decision": "accepted",
                            "fact_index": 0,
                            "field": "parallel_rule",
                            "value_as_collected": value,
                            "approved_availability_sections": ["BASE CARDS"],
                        }
                    ],
                }
            ],
        },
    )
    ledger["fact_publication_map"] = save(
        root,
        "audit/map.json",
        {
            "status": "reviewed",
            "records": [
                {
                    "audit_record_id": "r",
                    "fact_index": 0,
                    "claim_id": "gold",
                    "source_key": "a",
                    "source_capture": cr,
                }
            ],
        },
    )
    ledger["parallel_rule_audit"] = save(
        root,
        "audit/parallel.json",
        {
            "status": "reviewed",
            "records": [{"source_key": "a", "claim_id": "gold", "option": option}],
        },
    )
    reseal(root, ledger, bundle)
    return ledger, bundle


def test_parallel_options_must_keep_the_approved_number_and_product_scope(tmp_path):
    ledger, bundle = parallel_fixture(tmp_path)
    m = json.loads((tmp_path / "release/a.json").read_text())
    m["entities"][0]["attributes"]["parallel_options"][0]["serial_total"] = 99
    save(tmp_path, "release/a.json", m)
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="parallel"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_parallel_candidates_require_sealed_review_mapping_to_an_approved_source_claim(
    tmp_path,
):
    ledger, bundle = parallel_fixture(tmp_path)
    assert publisher().validate_intake_audit(tmp_path, bundle)["pending_review"] == 0
    ledger.pop("parallel_rule_audit")
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="parallel"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_parallel_scope_cannot_be_broadened_even_if_the_publication_audit_mirrors_it(
    tmp_path,
):
    ledger, bundle = parallel_fixture(tmp_path)
    m = json.loads((tmp_path / "release/a.json").read_text())
    option = m["entities"][0]["attributes"]["parallel_options"][0]
    option["availability_sections"] = ["BASE CARDS", "AUTOGRAPHS"]
    save(tmp_path, "release/a.json", m)
    ledger["parallel_rule_audit"] = save(
        tmp_path,
        "audit/parallel.json",
        {
            "status": "reviewed",
            "records": [{"source_key": "a", "claim_id": "gold", "option": option}],
        },
    )
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="parallel"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_parallel_source_scope_wording_must_match_the_collected_claim(tmp_path):
    ledger, bundle = parallel_fixture(tmp_path)
    m = json.loads((tmp_path / "release/a.json").read_text())
    option = m["entities"][0]["attributes"]["parallel_options"][0]
    option["source_scope_wording"] = "AUTOGRAPHS ONLY"
    save(tmp_path, "release/a.json", m)
    ledger["parallel_rule_audit"] = save(
        tmp_path,
        "audit/parallel.json",
        {
            "status": "reviewed",
            "records": [{"source_key": "a", "claim_id": "gold", "option": option}],
        },
    )
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="parallel"):
        publisher().validate_intake_audit(tmp_path, bundle)


def test_new_directory_manifest_requires_explicit_empty_coverage_for_runtime_importer(
    tmp_path,
):
    ledger, bundle = focused_fixture(tmp_path)
    m = json.loads((tmp_path / "release/c.json").read_text())
    m.pop("coverage")
    save(tmp_path, "release/c.json", m)
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="coverage"):
        publisher().validate_intake_audit(tmp_path, bundle)


def verified_media_fixture(root, *, single=False):
    """Use actual JPEGs, a compressed checklist and sealed source/identity reviews."""
    from card_platform.knowledge_release import attach_reference_images

    path = Path(__file__).resolve().parents[2] / "apps/api/tests/test_knowledge_release.py"
    spec = importlib.util.spec_from_file_location("release_media_fixtures", path)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    ledger, bundle = fixture(root)
    (root / "media").mkdir()
    manifest, front, back, now = fixtures._verified_pair(root / "media")
    manifest["entities"][0]["attributes"].update(source_key="topps-a")
    artifact = Path(front["identity_verification"]["artifact_path"])
    target = root / "text/artifacts/checklist.json.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(artifact.read_bytes())
    manifest["entities"][0]["attributes"]["official_artifact"]["path"] = target.name
    assets = [front] if single else [front, back]
    attached = attach_reference_images(
        manifest, assets, root / "media", now=now, asset_prefix=root.name + "/media"
    )
    save(root, "release/a.json", attached)
    baseline = json.loads((root / "audit/baseline.json").read_text())
    baseline["products"][0]["attributes"]["source_key"] = "topps-a"
    ledger["baseline_capture"] = save(root, "audit/baseline.json", baseline)
    ledger["scope_keys"][0] = "topps-a"
    ledger["products"][0].update(source_key="topps-a", outcome="references_acquired")
    text = save(root, "audit/text.json", {
        "products": [{"source_key": k, "status": "reviewed"} for k in ("topps-a", "b")]
    })
    for product in ledger["products"]:
        product["text_audit"] = text
    ledger["products"][0]["candidate_decisions"] = [
        dict(decision="accepted", review_completed=True, asset_id=a["asset_id"],
             sha256=a["sha256"], source_url=a["source_url"],
             source_capture={"path": str(Path(a["source_artifact_path"]).relative_to(root)),
                             "sha256": a["source_artifact_sha256"]},
             identity_review={"path": str(Path(a["review_artifact_path"]).relative_to(root)),
                              "sha256": a["review_artifact_sha256"]})
        for a in assets
    ]
    reseal(root, ledger, bundle)
    return ledger, bundle


def test_gate_accepts_verified_single_face_without_claiming_a_pair(tmp_path):
    _, bundle = verified_media_fixture(tmp_path, single=True)
    result = publisher().validate_intake_audit(tmp_path, bundle)
    assert result["accepted_new_assets"] == 1
    reference = json.loads((tmp_path / "release/a.json").read_text())["entities"][0]["attributes"]
    assert reference["reference_images"][0]["pair_id"] is None
    assert reference["knowledge_media_summary"]["paired_cards"] == 0


def test_gate_accepts_a_complete_independently_reviewed_pair(tmp_path):
    _, bundle = verified_media_fixture(tmp_path)
    assert publisher().validate_intake_audit(tmp_path, bundle)["accepted_new_assets"] == 2


def test_gate_rejects_a_resealed_pair_when_one_face_is_missing(tmp_path):
    ledger, bundle = verified_media_fixture(tmp_path)
    manifest = json.loads((tmp_path / "release/a.json").read_text())
    manifest["entities"][0]["attributes"]["reference_images"].pop()
    ledger["products"][0]["candidate_decisions"].pop()
    save(tmp_path, "release/a.json", manifest)
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="pair"):
        publisher().validate_intake_audit(tmp_path, bundle)


@pytest.mark.parametrize("mutation", ["different_pair_ids", "different_evidence", "false_summary"])
def test_gate_rechecks_pair_declarations_after_resealing(tmp_path, mutation):
    ledger, bundle = verified_media_fixture(tmp_path)
    manifest = json.loads((tmp_path / "release/a.json").read_text())
    attrs = manifest["entities"][0]["attributes"]
    if mutation == "different_pair_ids":
        attrs["reference_images"][1]["pair_id"] = "psa:another"
    elif mutation == "different_evidence":
        attrs["reference_images"][1]["pair_evidence"]["locator"] = "unrelated specimen"
    else:
        attrs["knowledge_media_summary"]["paired_cards"] = 2
    save(tmp_path, "release/a.json", manifest)
    reseal(tmp_path, ledger, bundle)
    with pytest.raises(ValueError, match="pair"):
        publisher().validate_intake_audit(tmp_path, bundle)
