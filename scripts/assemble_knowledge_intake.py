#!/usr/bin/env python3
"""Assemble this audited cloud-only continuation from frozen v21 and captured research.

No database connection. Existing cloud media dictionaries are retained verbatim.
The model index is built separately, then --seal closes the immutable file map.
"""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "apps/api/src"))
from card_platform.knowledge_manifest import validate_manifest  # noqa: E402
from card_platform.knowledge_release import attach_reference_images  # noqa: E402
from card_platform.knowledge_vectors import VectorIndex  # noqa: E402

ROOT = REPO / "data/knowledge-intake-20261022"
OLD = REPO / "data/knowledge-three-seasons-20261022"
BATCH = "knowledge-intake-2026-10-22"


def read(path):
    return json.loads(Path(path).read_text())


def save(relative, value):
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return dict(path=relative, sha256=sha256(path.read_bytes()).hexdigest())


def capture(path, relative=None, checksum=None):
    path = Path(path)
    if not path.is_absolute():
        path = REPO / path
    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    if checksum and digest != checksum:
        raise ValueError("Capture checksum mismatch: " + str(path))
    relative = relative or ("media/sources/" + digest + path.suffix)
    target = ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != payload:
        raise ValueError("Immutable capture collision")
    if not target.exists():
        shutil.copy2(path, target)
    return dict(path=relative, sha256=digest)


def actual_artifact(ref):
    candidates = [
        ROOT / "text/artifacts" / ref["path"],
        OLD / "text/artifacts" / ref["path"],
        REPO / "data/knowledge-topps-flagship-20261022/text/artifacts" / ref["path"],
        REPO / "data/knowledge-three-seasons/text/artifacts" / ref["path"],
    ]
    path = next(p for p in candidates if p.exists())
    capture(path, "text/artifacts/" + ref["path"], ref["checksum"])
    return ROOT / "text/artifacts" / ref["path"]


def stage():
    baseline = read(REPO / ".runtime/knowledge-intake-20261022/cloud-baseline.json")
    capture(
        REPO / ".runtime/knowledge-intake-20261022/cloud-baseline.json",
        "audit/cloud-baseline.json",
    )
    refs = {r["source_key"]: r for r in read(ROOT / "text/official-artifacts.json")}
    text = read(ROOT / "audit/text-audit.json")
    audits = {r["source_key"]: r for r in text["products"]}
    assets = []
    current_images = {
        a["asset_id"]: a
        for p in baseline["products"]
        for a in p["attributes"].get("reference_images", [])
    }
    for line in (OLD / "media/assets.jsonl").read_text().splitlines():
        asset = json.loads(line)
        if asset["asset_id"] not in current_images:
            continue
        image = current_images[asset["asset_id"]]
        assert asset["sha256"] == image["sha256"]
        # Only paths are staged; all published metadata comes from the cloud dictionary.
        for field in ("path", "source_artifact_path", "review_artifact_path"):
            if asset.get(field):
                name = "media/originals/" if field == "path" else "media/sources/"
                ref = capture(
                    asset[field],
                    name
                    + sha256(Path(asset[field]).read_bytes()).hexdigest()
                    + Path(asset[field]).suffix,
                )
                asset[field] = str(ROOT / ref["path"])
        for field in (
            "caption",
            "side",
            "pair_id",
            "player",
            "card_number",
            "variant",
            "identity_verification",
        ):
            if field in image:
                asset[field] = deepcopy(image[field])
        assets.append(asset)
    assert len(assets) == len(current_images) == 550
    new = [
        json.loads(s)
        for s in (ROOT / "media/new-assets.jsonl").read_text().splitlines()
        if s
    ]
    identity = read(ROOT / "research/identity-audit.json")
    assert identity["status"] in {"reviewed", "complete_independent_read_only_audit"}, (
        "Independent identity audit remains incomplete"
    )
    # Source wrappers explicitly preserve the upstream reader/link captures; they are never called raw HTML.
    for record in identity["records"]:
        if record["status"] not in {
            "core_identity_matched_variant_unresolved",
            "exact_official_section_matched",
        }:
            continue
        matches = [
            a
            for a in new
            if a["source_key"] == record["source_key"]
            and a["sha256"] in {i["sha256"] for i in record["image_evidence"]}
        ]
        if not matches:
            continue
        assert len(matches) == 2 and {a["side"] for a in matches} == {"front", "back"}
        source = record["source_identity"]
        upstream = capture(
            source["source_artifact_path"], checksum=source["source_artifact_sha256"]
        )
        cert = record["certification_number"]
        observed = record["observed_link_evidence"]
        reader_ref = capture(
            observed["reader_capture_path"], checksum=observed["reader_capture_sha256"]
        )
        assert cert in (ROOT / reader_ref["path"]).read_text()
        links = []
        for binding in observed["observed_link_bindings"]:
            observed_ref = capture(
                binding["observed_link_capture_path"],
                checksum=binding["observed_link_capture_sha256"],
            )
            assert binding["image_url"] in (ROOT / observed_ref["path"]).read_text()
            links.append(
                dict(
                    side=binding["side"],
                    image_url=binding["image_url"],
                    capture=observed_ref,
                    reader_link_id=binding["reader_image_link_id"],
                    reader_page_reference=binding["reader_page_reference"],
                )
            )
        assert len(links) == 2, "Identity proof has no captured observed-link evidence"
        wrapper = dict(
            contract="normalized-certification-evidence-v1",
            cert=cert,
            page_url=source["source_page_url"],
            capture_kind="normalized_evidence_wrapper",
            upstream_capture=upstream,
            upstream_kind=source["capture_kind"],
            observed_link_evidence=dict(reader=reader_ref, resolved_images=links),
            observed_link_bindings=[
                dict(url=i["source_url"], side=i["side"], sha256=i["sha256"])
                for i in record["image_evidence"]
            ],
            derivation="Independent actual certificate/image review; upstream captured reader and link evidence retained separately.",
        )
        sr = save("media/sources/cert-" + cert + ".json", wrapper)
        review = deepcopy(record)
        review["source_identity"] = dict(
            source_page_url=source["source_page_url"],
            source_artifact_path=str(ROOT / sr["path"]),
            source_artifact_sha256=sr["sha256"],
            capture_kind="normalized_evidence_wrapper",
            upstream_source_identity=deepcopy(source),
            upstream_capture=upstream,
        )
        review["independent_audit_capture"] = dict(
            path="research/identity-audit.json",
            sha256=sha256(
                (ROOT / "research/identity-audit.json").read_bytes()
            ).hexdigest(),
        )
        rr = save(
            "media/sources/identity-" + cert + ".json",
            dict(contract="independent-card-identity-review-v1", records=[review]),
        )
        official = refs[record["source_key"]]
        for asset in matches:
            assert asset["source_key"] == record["source_key"]
            asset.update(
                image_type="verified_card_scan",
                player=record["canonical_player"],
                card_number=record["card_number"],
                variant=record.get("variant"),
                source_url=source["source_page_url"],
                source_artifact_path=str(ROOT / sr["path"]),
                source_artifact_sha256=sr["sha256"],
                review_artifact_path=str(ROOT / rr["path"]),
                review_artifact_sha256=rr["sha256"],
                pair_id="psa:" + cert,
                pair_evidence=dict(
                    record_type="certification",
                    record_id=cert,
                    record_url=source["source_page_url"],
                ),
                identity_verification=dict(
                    status="verified",
                    artifact_path=str(actual_artifact(official)),
                    artifact_sha256=official["checksum"],
                    source_url=source["source_page_url"],
                    source_sha256=sr["sha256"],
                    source_locator=source["capture_locator"],
                    card_id=record.get("chosen_card_id"),
                    candidate_card_ids=record["candidate_card_ids"],
                    verification_scope=record.get("binding_scope"),
                ),
            )
    assets += new
    assert len({a["asset_id"] for a in assets}) == len(assets)
    (ROOT / "media/assets.jsonl").write_text(
        "".join(json.dumps(a, ensure_ascii=False) + "\n" for a in assets)
    )
    products, manifests = [], []
    baseline_by_key = {p["attributes"]["source_key"]: p for p in baseline["products"]}
    source_fact_audit = (
        read(ROOT / "research/source-fact-audit.json")
        if (ROOT / "research/source-fact-audit.json").exists()
        else None
    )
    approved_evidence = {key: {} for key in baseline_by_key}
    fact_map = []
    if source_fact_audit is not None:
        assert (
            source_fact_audit["status"] == "reviewed"
            and len(source_fact_audit["records"]) == 137
        )
        year_results = {
            str(y): read(ROOT / f"research/year{y}/result.json")["products"]
            for y in (2023, 2024, 2025)
        }
        for record in source_fact_audit["records"]:
            source = year_results[record["year_group"]][record["product_index"]][
                "sources"
            ][record["source_index"]]
            assert source["sha256"] == record["source_capture_sha256"]
            approved = record["approved_fact_indices"]
            assert set(approved) | set(record["rejected_fact_indices"]) == set(
                range(len(source["extracted_facts"]))
            )
            assert not set(approved) & set(record["rejected_fact_indices"])
            if not approved:
                continue
            key = record["canonical_source_key"]
            sr = capture(source["capture_path"], checksum=source["sha256"])
            group_key = (sr["sha256"], source["url"])
            evidence = approved_evidence[key].setdefault(
                group_key,
                dict(
                    source_url=source["url"],
                    authority=source.get("authority"),
                    capture_kind=source.get("capture_kind"),
                    source_artifact=ROOT.name + "/" + sr["path"],
                    source_sha256=sr["sha256"],
                    intake_batch_id=BATCH,
                    audit_record_ids=[],
                    facts=[],
                    limits="Audited attributed source assertions only; source year and design labels do not override official season, identity or parallel.",
                ),
            )
            evidence["audit_record_ids"].append(record["record_id"])
            for index in approved:
                fact = source["extracted_facts"][index]
                decision = next(
                    f for f in record["fact_decisions"] if f["fact_index"] == index
                )
                assert (
                    decision["decision"] in {"accepted", "rebound"}
                    and decision["value_as_collected"] == fact["value"]
                )
                identity = json.dumps(
                    [key, sr["sha256"], source["url"], fact["field"], fact["value"]],
                    ensure_ascii=False,
                    sort_keys=True,
                )
                claim_id = "intake-claim:" + sha256(identity.encode()).hexdigest()[:24]
                if claim_id not in {f["claim_id"] for f in evidence["facts"]}:
                    evidence["facts"].append(
                        deepcopy(fact)
                        | dict(
                            claim_id=claim_id,
                            approved_semantics=decision["approved_semantics"],
                        )
                    )
                fact_map.append(
                    dict(
                        audit_record_id=record["record_id"],
                        fact_index=index,
                        claim_id=claim_id,
                        source_key=key,
                        source_capture=sr,
                        source_url=source["url"],
                    )
                )
        save(
            "audit/fact-publication-map.json",
            dict(
                status="reviewed",
                records=fact_map,
                approved_occurrences=len(fact_map),
                unique_claims=len({r["claim_id"] for r in fact_map}),
            ),
        )
    for key, original in baseline_by_key.items():
        manifest = read(OLD / "text/manifests" / f"{key}.json")
        entity = manifest["entities"][0]
        attrs = entity["attributes"] = deepcopy(original["attributes"])
        old_ref = attrs["official_artifact"]
        ref = refs[key]
        artifact_path = actual_artifact(ref)
        attrs["official_artifact"] = ref
        audit = audits[key]
        receipt = manifest["sources"][0]["metadata"]["capture_receipt"]
        raw = Path(receipt["path"])
        if not raw.exists():
            raw = REPO / "data/knowledge-three-seasons/text/raw" / raw.name
        raw_ref = capture(
            raw, "text/raw/" + raw.name, manifest["sources"][0]["checksum"]
        )
        receipt["path"] = ROOT.name + "/" + raw_ref["path"]
        parsed = REPO / "data/knowledge-three-seasons/text/parsed" / f"{key}.json"
        capture(parsed, "text/original-parsed/" + parsed.name)
        evidence = deepcopy(attrs["text_evidence"])
        evidence.update(
            status="partial" if audit["unresolved_source_issues"] else "compiled",
            quarantined_rows=audit["unresolved_source_issues"],
            original_parser_issues=audit["original_issue_count"],
            issue_dispositions=audit["disposition_counts"],
            audit_status="reviewed",
            audit_artifact=ROOT.name + "/audit/text-audit.json",
            complete_for_captured_source=not audit["unresolved_source_issues"],
            completeness_scope="this_product_within_captured_document; other product rows explicitly excluded",
            card_fact_count=ref["card_count"],
            parsed_row_count=ref["card_count"],
        )
        if ref["checksum"] != old_ref["checksum"]:
            cards = json.loads(gzip.decompress(artifact_path.read_bytes()))
            evidence["section_counts"] = dict(
                Counter(c["attributes"]["section"] for c in cards)
            )
            vf = ("section", "insert", "parallel", "serial_total")
            variants = Counter(tuple(c["attributes"].get(k) for k in vf) for c in cards)
            evidence["explicit_variant_counts"] = [
                dict(zip(vf, variant)) | {"card_count": count}
                for variant, count in variants.items()
            ]
            attrs["official_artifact_lineage"] = attrs.get(
                "official_artifact_lineage", []
            ) + [old_ref]
            attrs["text_recovery"] = dict(
                compiler="official-drawing-recovery-v1",
                audit=ROOT.name + "/audit/text-audit.json",
                prior_facts_retained_in_immutable_artifact=old_ref["checksum"],
            )
        attrs["text_evidence"] = evidence
        for field in ("text_status", "quarantined_rows"):
            manifest["sources"][0]["metadata"][field] = (
                evidence["status"] if field == "text_status" else evidence[field]
            )
        appended = attach_reference_images(
            manifest, new, ROOT / "media", asset_prefix=ROOT.name + "/media"
        )
        new_images = appended["entities"][0]["attributes"]["reference_images"]
        attrs["reference_images"] = (
            deepcopy(original["attributes"].get("reference_images", [])) + new_images
        )
        pairs = {a["pair_id"] for a in attrs["reference_images"] if a.get("pair_id")}
        attrs["knowledge_media_summary"] = dict(
            reference_images=len(attrs["reference_images"]),
            paired_cards=len(pairs),
            verified_card_images=sum(
                a["image_type"] == "verified_card_scan"
                for a in attrs["reference_images"]
            ),
            design_reference_images=sum(
                a["image_type"] != "verified_card_scan"
                for a in attrs["reference_images"]
            ),
            coverage_basis="reviewed design and specimen samples; full set per-card image coverage unknown",
        )
        attrs.setdefault("supplementary_evidence", []).extend(
            approved_evidence[key].values()
        )
        attrs["knowledge_intake_audit"] = dict(
            batch_id=BATCH,
            ledger=ROOT.name + "/audit/ledger.json",
            workflow_status="review_complete",
        )
        attrs["knowledge_content_release"] = dict(
            batch_id=BATCH,
            model_fingerprint="pending_vector_seal",
            indexed_reference_images=len(attrs["reference_images"]),
            query_access="offline source-linked learned-vector candidates; application retrieval evaluation remains separate",
        )
        assert validate_manifest(manifest).publishable, key
        name = "release/manifests/" + key + ".json"
        save(name, manifest)
        manifests.append(name)
        products.append(
            dict(
                source_key=key,
                title=entity["canonical_name"],
                publisher=attrs["manufacturer"],
                product=attrs["product"],
                season_start=attrs["season_start"],
                season_end=attrs["season_end"],
                reference_images=len(attrs["reference_images"]),
                paired_cards=len(pairs),
                card_fact_count=ref["card_count"],
                text_status=evidence["status"],
                unresolved_source_issues=audit["unresolved_source_issues"],
            )
        )
    save(
        "release/staged.json",
        dict(
            batch_id=BATCH,
            expected_previous_checksum=baseline["checksum"],
            expected_previous_revision=baseline["revision"],
            products=products,
            manifests=manifests,
            official_artifacts=list(refs.values()),
            image_assets=len(assets),
            vector_records=2 * len(assets),
            paired_cards=sum(p["paired_cards"] for p in products),
        ),
    )
    print(
        json.dumps(
            dict(
                status="staged",
                products=len(products),
                images=len(assets),
                new_images=len(new),
                products_with_images=sum(p["reference_images"] > 0 for p in products),
            )
        )
    )


def seal():
    bundle = read(ROOT / "release/staged.json")
    assets = [
        json.loads(s)
        for s in (ROOT / "media/assets.jsonl").read_text().splitlines()
        if s
    ]
    index = VectorIndex.load(ROOT / "vectors")
    report = read(ROOT / "vectors/build-report.json")
    spec = importlib.util.spec_from_file_location(
        "prepare", REPO / "scripts/prepare_knowledge_release.py"
    )
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    prep.validate_vector_generation(
        assets,
        index,
        report,
        sha256((ROOT / "media/assets.jsonl").read_bytes()).hexdigest(),
    )
    assert report["failed_assets"] == report["failed_embeddings"] == 0
    smoke = read(ROOT / "vectors/query-smoke.json")
    assert (
        smoke["status"] == "passed"
        and smoke["assets_sha256"] == report["assets_sha256"]
    )
    assert set(smoke["directions"]) == {
        "image_to_image",
        "image_to_text",
        "text_to_image",
        "text_to_text",
    }
    assert smoke["model_fingerprint"] == index.model.fingerprint
    assert smoke["knowledge_version"] == index.knowledge_version
    assert (
        smoke["vector_manifest_sha256"]
        == sha256((ROOT / "vectors/manifest.json").read_bytes()).hexdigest()
    )
    assert set(smoke["filters"]) == {
        "season",
        "side",
        "wrong_year_empty",
        "wrong_source_empty",
    }
    assert all(smoke["filters"].values())
    assert len(smoke["queries"]) >= 12 and all(q["hits"] for q in smoke["queries"])
    assert {q["direction"] for q in smoke["queries"]} == set(smoke["directions"])
    assert (ROOT / "research/source-fact-audit.json").is_file(), (
        "Supplemental facts remain unreviewed"
    )
    for name in bundle["manifests"]:
        manifest = read(ROOT / name)
        attrs = manifest["entities"][0]["attributes"]
        attrs["knowledge_content_release"].update(
            model_fingerprint=index.model.fingerprint,
            vector_manifest_path=ROOT.name + "/vectors/manifest.json",
            model_id=index.model.model_id,
            model_revision=index.model.revision,
            dimension=index.model.dimension,
            vector_manifest_sha256=sha256(
                (ROOT / "vectors/manifest.json").read_bytes()
            ).hexdigest(),
        )
        save(name, manifest)
    decisions = read(ROOT / "audit/media-decisions.json")
    baseline = read(ROOT / "audit/cloud-baseline.json")
    by_key = {p["source_key"]: p for p in bundle["products"]}
    new_assets = {
        a["asset_id"]: a
        for a in assets
        if a["asset_id"]
        not in {
            i["asset_id"]
            for p in baseline["products"]
            for i in p["attributes"].get("reference_images", [])
        }
    }
    # Every actual query/candidate is materialized, including failures and rebinding decisions.
    rows = {
        key: dict(
            source_key=key,
            searches=[],
            candidate_decisions=[],
            outcome="existing_media_retained",
        )
        for key in by_key
    }
    aliases = {
        "reference_checklist": "specialist",
        "physical_card": "specimen",
        "manufacturer": "manufacturer",
        "official": "manufacturer",
        "reference": "specialist",
        "dealer_sell_sheet": "distributor",
        "physical": "specimen",
        "physical_card_exact": "specimen",
        "physical_card_followup": "specimen",
        "reference_exact": "specialist",
        "dealer_sell_sheet_exact": "distributor",
        "specialist": "specialist",
        "specimen": "specimen",
        "distributor": "distributor",
    }
    for year in (2023, 2024, 2025):
        for product in read(ROOT / f"research/year{year}/result.json")["products"]:
            row = rows[product["source_key"]]
            row.update(
                outcome="reviewed_reference_samples",
                blockers=product.get("blockers", []),
                next_action=product.get("next_action"),
            )
            for attempt in product["searches"]:
                path = attempt.get("capture_path") or attempt.get("capture", {}).get(
                    "path"
                )
                digest = (
                    attempt.get("sha256")
                    or attempt.get("capture_sha256")
                    or attempt.get("capture", {}).get("sha256")
                )
                evidence = capture(path, checksum=digest)
                family = aliases.get(attempt["family"], attempt["family"])
                row["searches"].append(
                    dict(
                        family=family,
                        query=attempt["query"],
                        capture=evidence,
                        actual_outcome=attempt.get("actual_outcome")
                        or attempt.get("outcome"),
                        queried_at=attempt.get("queried_at"),
                    )
                )
    batch_artifact_decisions = []
    for candidate in decisions["candidates"]:
        candidate = deepcopy(candidate)
        if candidate.get("source_key") is None:
            assert candidate["decision"] == "rejected" and candidate.get("reason")
            batch_artifact_decisions.append(candidate)
            continue
        row = rows[candidate["source_key"]]
        if candidate["decision"] == "accepted":
            a = new_assets[candidate["asset_id"]]
            candidate.update(
                sha256=a["sha256"],
                source_url=a["source_url"],
                source_capture=dict(
                    path=Path(a["source_artifact_path"]).relative_to(ROOT).as_posix(),
                    sha256=a["source_artifact_sha256"],
                ),
            )
            if a.get("review_artifact_sha256"):
                candidate["identity_review"] = dict(
                    path=Path(a["review_artifact_path"]).relative_to(ROOT).as_posix(),
                    sha256=a["review_artifact_sha256"],
                )
        row["candidate_decisions"].append(candidate)
    text_ref = dict(
        path="audit/text-audit.json",
        sha256=sha256((ROOT / "audit/text-audit.json").read_bytes()).hexdigest(),
    )
    for key, row in rows.items():
        row.update(workflow_status="review_complete", text_audit=text_ref)
        assert by_key[key]["reference_images"] > 0, (
            "Newly observed external gap requires explicit external_gap report"
        )
    ledger = dict(
        contract="knowledge-intake-audit-v1",
        scope_keys=sorted(by_key),
        products=[rows[k] for k in sorted(by_key)],
        batch_artifact_decisions=batch_artifact_decisions,
        source_fact_audit=dict(
            path="research/source-fact-audit.json",
            sha256=sha256(
                (ROOT / "research/source-fact-audit.json").read_bytes()
            ).hexdigest(),
        ),
        fact_publication_map=dict(
            path="audit/fact-publication-map.json",
            sha256=sha256(
                (ROOT / "audit/fact-publication-map.json").read_bytes()
            ).hexdigest(),
        ),
        baseline_capture=dict(
            path="audit/cloud-baseline.json",
            sha256=sha256(
                (ROOT / "audit/cloud-baseline.json").read_bytes()
            ).hexdigest(),
        ),
        scope_definition=dict(
            season_start_labels=[2023, 2024, 2025],
            manufacturers=["Topps", "Panini"],
            basis="frozen current cloud official source inventory",
        ),
        conclusion="All frozen products processed; this is not an exhaustive annual product universe or all-card image completeness claim.",
    )
    bundle["intake_audit"] = save("audit/ledger.json", ledger)
    for file in (
        "docs/knowledge-intake-sop.md",
        "specs/009-official-checklist-knowledge/contracts/intake.md",
        "scripts/publish_knowledge_release.py",
        "scripts/assemble_knowledge_intake.py",
        "scripts/prepare_intake_media.py",
        "apps/api/src/card_platform/knowledge_pdf_recovery.py",
    ):
        capture(
            REPO / file,
            "audit/method/"
            + sha256((REPO / file).read_bytes()).hexdigest()[:16]
            + "-"
            + Path(file).name,
        )
    capture(
        REPO / ".runtime/knowledge-intake-20261022/recover_text.py",
        "audit/method/recover_text.py",
    )
    for name in ("query_smoke.py", "verify_cloud.py"):
        capture(
            REPO / ".runtime/knowledge-intake-20261022" / name, "audit/method/" + name
        )
    bundle["files"] = {
        p.relative_to(ROOT).as_posix(): dict(
            sha256=sha256(p.read_bytes()).hexdigest(), bytes=p.stat().st_size
        )
        for p in ROOT.rglob("*")
        if p.is_file() and p.relative_to(ROOT).as_posix() != "release/bundle.json"
    }
    bundle["completeness"] = (
        "All 126 frozen products processed; sampled card-image coverage remains partial; source-level text gaps explicitly quarantined."
    )
    bundle["unique_image_bytes"] = len({a["sha256"] for a in assets})
    bundle["prepared_at"] = datetime.now(timezone.utc).isoformat()
    save("release/bundle.json", bundle)
    module_spec = importlib.util.spec_from_file_location(
        "publisher", REPO / "scripts/publish_knowledge_release.py"
    )
    pub = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(pub)
    print(json.dumps(pub.validate_intake_audit(ROOT, bundle), ensure_ascii=False))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seal", action="store_true")
    args = parser.parse_args()
    seal() if args.seal else stage()
