#!/usr/bin/env python3
"""Publish a prepared annual content bundle using installed knowledge mechanisms.

Dry run is the default. No application code, configuration or collection rows change.
Run inside the API environment after immutable bundle files have been synchronized.
"""

from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import re


def validate_cloud_baseline(bundle: dict, current_checksum: str, *, current_revision: int | None = None) -> None:
    if current_revision is not None and bundle.get("expected_previous_revision") != current_revision:
        raise ValueError("Cloud draft revision changed; preserve and rebase current edits before publishing")
    expected = bundle.get("expected_previous_checksum")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or expected != current_checksum:
        raise ValueError("Cloud baseline changed or was not frozen; rebase the intake before publishing")


def validate_intake_audit(root: Path, bundle: dict, *, current_snapshot: dict | None = None) -> dict:
    """Reject a partial intake even when its successful product manifests validate."""
    root = root.resolve()
    cache = {}

    def read(ref):
        if not isinstance(ref, dict) or not ref.get("path") or not ref.get("sha256"):
            raise ValueError("Intake evidence requires a captured path and checksum")
        if bundle.get("files", {}).get(ref["path"], {}).get("sha256") != ref["sha256"]:
            raise ValueError("Intake evidence is not in the sealed file map")
        path = (root / ref["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Intake evidence path is outside the sealed batch or missing")
        key = (str(path), ref["sha256"])
        if key not in cache:
            data = path.read_bytes()
            if not data or sha256(data).hexdigest() != ref["sha256"]:
                raise ValueError("Intake evidence checksum mismatch")
            cache[key] = data
        return cache[key]

    ledger = json.loads(read(bundle.get("intake_audit")))
    focused = ledger.get("contract") == "knowledge-intake-audit-v2"
    if not focused and ledger.get("contract") != "knowledge-intake-audit-v1":
        raise ValueError("Unknown intake audit contract")
    baseline = json.loads(read(ledger.get("baseline_capture")))
    validate_cloud_baseline(bundle, baseline.get("checksum"))
    if current_snapshot is not None:
        actual = {p["attributes"]["source_key"]: p for p in current_snapshot["entities"]
                  if p.get("entity_type") == "product" and p.get("attributes", {}).get("official_artifact")
                  and p["attributes"].get("season_start") in (2023, 2024, 2025)
                  and p["attributes"].get("manufacturer") in ("Topps", "Panini")}
        frozen = {p["attributes"]["source_key"]: p for p in baseline["products"]}
        if set(actual) != set(frozen) or any(
                any(actual[key].get(field) != value for field, value in product.items())
                for key, product in frozen.items()):
            raise ValueError("Frozen cloud scope or product evidence differs from actual published snapshot")
    products = ledger.get("products", [])
    keys = [p.get("source_key") for p in products]
    baseline_keys = {p["attributes"]["source_key"] for p in baseline["products"]}
    scope = ledger.get("scope_keys", [])
    manifest_scope = scope
    if focused:
        work_scope = json.loads(read(ledger.get("frozen_work_scope")))
        existing = work_scope.get("existing_product_keys", [])
        annual = work_scope.get("annual_research_keys", [])
        if (not existing or not set(existing) <= baseline_keys or set(existing) & set(annual)
                or len(existing + annual) != len(set(existing + annual))
                or set(scope) != set(existing + annual)):
            raise ValueError("Focused intake scope differs from frozen work units")
        manifest_scope = ledger.get("manifest_scope_keys", [])
        if (len(manifest_scope) != len(set(manifest_scope)) or not set(existing) <= set(manifest_scope)
                or not set(manifest_scope) <= set(scope)):
            raise ValueError("Focused intake publication scope is missing or outside reviewed work units")
    if (not scope or len(scope) != len(set(scope)) or (not focused and set(scope) != baseline_keys)
            or len(keys) != len(set(keys)) or set(keys) != set(scope)):
        raise ValueError("Intake scope has missing, duplicate or unreviewed products")
    missing = {p["attributes"]["source_key"] for p in baseline["products"]
               if not p["attributes"].get("reference_images")}
    old_references = {a["asset_id"]: a
                      for p in baseline["products"] if p["attributes"]["source_key"] in manifest_scope
                      for a in p["attributes"].get("reference_images", [])}
    old_assets = {a["asset_id"]: (a["sha256"], p["attributes"]["source_key"])
                  for p in baseline["products"] if p["attributes"]["source_key"] in manifest_scope
                  for a in p["attributes"].get("reference_images", [])}
    released = {}
    released_references = {}
    manifest_keys = []
    manifest_attributes = {}
    for name in bundle.get("manifests", []):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Intake manifest outside batch")
        manifest = json.loads(read({"path": name, "sha256": bundle.get("files", {}).get(name, {}).get("sha256")}))
        if focused and manifest.get("coverage") != []:
            raise ValueError("Focused content manifests require explicit empty coverage for the runtime importer")
        for entity in manifest["entities"]:
            manifest_keys.append(entity["attributes"]["source_key"])
            manifest_attributes[entity["attributes"]["source_key"]] = entity["attributes"]
            for asset in entity["attributes"].get("reference_images", []):
                value = (asset["sha256"], entity["attributes"]["source_key"])
                if asset["asset_id"] in released and released[asset["asset_id"]] != value:
                    raise ValueError("Intake candidate asset identity collision")
                released[asset["asset_id"]] = value
                released_references[asset["asset_id"]] = asset
                if asset["asset_id"] not in old_assets:
                    prefix = root.name + "/"
                    if not asset.get("asset_path", "").startswith(prefix):
                        raise ValueError("New intake media must be inside this sealed batch")
                    read({"path": asset["asset_path"][len(prefix):], "sha256": asset["sha256"]})
    if len(manifest_keys) != len(set(manifest_keys)) or set(manifest_keys) != set(manifest_scope):
        raise ValueError("Intake manifest scope differs from frozen product scope")
    baseline_by_key = {p["attributes"]["source_key"]: p for p in baseline["products"]}
    if focused:
        mutable = {"reference_images", "supplementary_evidence", "knowledge_media_summary",
                   "knowledge_intake_audit", "knowledge_content_release", "knowledge_enrichment", "parallel_options"}
        for key in manifest_scope:
            attrs = manifest_attributes[key]
            if key in baseline_by_key:
                previous_attrs = baseline_by_key[key]["attributes"]
                if any(attrs.get(field) != value for field, value in previous_attrs.items() if field not in mutable):
                    raise ValueError("Focused intake altered a protected product attribute")
            else:
                ref = ledger.get("new_product_reviews", {}).get(key)
                if not ref:
                    raise ValueError("Intake new product lacks approved source identity")
                proof = json.loads(read(ref))
                if (proof.get("status") != "approved" or proof.get("source_key") != key
                        or not proof.get("source_url", "").startswith("https://") or not proof.get("locator")
                        or any(proof.get(field) != attrs.get(field) for field in
                               ("manufacturer", "product", "season_start", "season_end"))):
                    raise ValueError("Intake new product source identity conflicts with its manifest")
                read(proof.get("capture"))
                if current_snapshot is not None and any(
                        p.get("entity_type") == "product" and p.get("attributes", {}).get("source_key") == key
                        for p in current_snapshot["entities"]):
                    raise ValueError("Intake new product already exists outside frozen baseline")
    # Source assertions have their own review gate, separate from official PDF rows and image approval.
    new_claims = {}
    for key in manifest_scope:
        previous_claims = baseline_by_key.get(key, {}).get("attributes", {}).get("supplementary_evidence", [])
        current_claims = manifest_attributes[key].get("supplementary_evidence", [])
        if current_claims[:len(previous_claims)] != previous_claims:
            raise ValueError("Intake altered a previously reviewed source assertion")
        for evidence in current_claims[len(previous_claims):]:
            if evidence.get("intake_batch_id") != bundle.get("batch_id"):
                raise ValueError("New source assertions require this audited intake batch")
            prefix = root.name + "/"
            if not evidence.get("source_artifact", "").startswith(prefix):
                raise ValueError("New source assertion capture is outside the batch")
            read({"path": evidence["source_artifact"][len(prefix):], "sha256": evidence["source_sha256"]})
            for claim in evidence["facts"]:
                cid = claim.get("claim_id")
                if not cid or cid in new_claims:
                    raise ValueError("New source assertion has a missing or duplicate claim ID")
                new_claims[cid] = (key, claim["field"], json.dumps(claim["value"], ensure_ascii=False, sort_keys=True),
                                   evidence["source_sha256"], evidence["source_url"])
    approved_rule_scopes = {}
    if new_claims or ledger.get("source_fact_audit"):
        fact_audit = json.loads(read(ledger.get("source_fact_audit")))
        fact_map = json.loads(read(ledger.get("fact_publication_map")))
        if fact_audit.get("status") != "reviewed" or fact_map.get("status") != "reviewed":
            raise ValueError("Supplemental facts remain unreviewed")
        expected = {}
        rule_scopes = {}
        for record in fact_audit["records"]:
            for fact in record["fact_decisions"]:
                if fact["decision"] not in {"accepted", "rebound", "rejected"}:
                    raise ValueError("Supplemental fact remains pending")
                if fact["decision"] != "rejected":
                    if fact["field"] == "parallel_rule":
                        rule_scopes[(record["record_id"], fact["fact_index"])] = fact.get("approved_availability_sections")
                    expected[(record["record_id"], fact["fact_index"])] = (
                        record["canonical_source_key"], fact["field"],
                        json.dumps(fact["value_as_collected"], ensure_ascii=False, sort_keys=True),
                        record["source_capture_sha256"], record["source_url"])
        seen, admitted = set(), set()
        for mapping in fact_map["records"]:
            identity = (mapping["audit_record_id"], mapping["fact_index"])
            claim = new_claims.get(mapping["claim_id"])
            if identity in seen or identity not in expected or claim != expected[identity]:
                raise ValueError("Approved source assertion is unpublished, altered or bound to another product")
            if mapping["source_key"] != claim[0] or mapping["source_capture"]["sha256"] != claim[3]:
                raise ValueError("Source assertion publication map has conflicting provenance")
            read(mapping["source_capture"])
            if identity in rule_scopes:
                cid = mapping["claim_id"]
                if cid in approved_rule_scopes and approved_rule_scopes[cid] != rule_scopes[identity]:
                    raise ValueError("Intake parallel claim has conflicting independently reviewed sections")
                approved_rule_scopes[cid] = rule_scopes[identity]
            seen.add(identity)
            admitted.add(mapping["claim_id"])
        if seen != set(expected) or admitted != set(new_claims):
            raise ValueError("Intake has unreviewed or unpublished supplemental facts")
    if focused:
        new_options = {}
        for key, attrs in manifest_attributes.items():
            previous = baseline_by_key.get(key, {}).get("attributes", {}).get("parallel_options", [])
            current = attrs.get("parallel_options", [])
            if current[:len(previous)] != previous:
                raise ValueError("Intake altered a previously reviewed parallel rule")
            for option in current[len(previous):]:
                cid = option.get("source_claim_id")
                if cid in new_options or cid not in new_claims:
                    raise ValueError("Intake parallel option lacks a unique approved source claim")
                source_key, field, encoded, checksum, url = new_claims[cid]
                value = json.loads(encoded)
                if (source_key != key or field != "parallel_rule" or not isinstance(value, dict)
                        or option.get("official_name") != value.get("official_name")
                        or option.get("serial_total") != value.get("serial_total")
                        or option.get("box_formats") != value.get("box_formats")
                        or option.get("source_scope_wording") != value.get("availability_scope")
                        or option.get("availability_sections") != approved_rule_scopes.get(cid)
                        or option.get("availability_members")
                        or option.get("source_url") != url or option.get("source_checksum") != checksum
                        or not option.get("source_locator") or not option.get("availability_sections")
                        or option.get("brand") != attrs.get("manufacturer")
                        or any(option.get(f) != attrs.get(f) for f in ("product", "season_start", "season_end"))):
                    raise ValueError("Intake parallel rule differs from reviewed claim or product scope")
                new_options[cid] = {"source_key": key, "claim_id": cid, "option": option}
        if new_options or ledger.get("parallel_rule_audit"):
            if not ledger.get("parallel_rule_audit"):
                raise ValueError("Intake parallel options require their sealed review mapping")
            audit = json.loads(read(ledger["parallel_rule_audit"]))
            records = audit.get("records", [])
            mapped = {r.get("claim_id"): r for r in records}
            if audit.get("status") != "reviewed" or len(records) != len(mapped) or mapped != new_options:
                raise ValueError("Intake parallel rules are unreviewed, altered or unpublished")
    accepted = {}
    gaps = 0
    for product in products:
        key = product["source_key"]
        if product.get("workflow_status") != "review_complete":
            raise ValueError("Intake product is unsearched or pending review")
        for candidate in product.get("source_candidate_decisions", []):
            if (candidate.get("decision") not in {"accepted", "rejected"}
                    or candidate.get("review_completed") is not True or not candidate.get("reason")):
                raise ValueError("Intake source capture candidate remains unreviewed")
        text = json.loads(read(product.get("text_audit")))
        if not any(p.get("source_key") == key and p.get("status") == "reviewed" for p in text.get("products", [])):
            raise ValueError("Intake product text audit is incomplete")
        local_accepted = 0
        for candidate in product.get("candidate_decisions", []):
            if candidate.get("review_completed") is not True:
                raise ValueError("Intake candidate remains unreviewed, even if marked rejected")
            if candidate.get("decision") == "accepted":
                asset_id = candidate.get("asset_id")
                value = (candidate.get("sha256"), key)
                if not asset_id or released.get(asset_id) != value or asset_id in accepted:
                    raise ValueError("Approved intake candidate is unpublished, duplicated or differs from released bytes")
                reference = released_references[asset_id]
                source_capture = candidate.get("source_capture")
                if (not isinstance(source_capture, dict)
                        or source_capture.get("sha256") != reference.get("source_artifact_sha256")
                        or candidate.get("source_url") != reference.get("source_url")):
                    raise ValueError("Intake accepted image requires its sealed source capture and URL")
                read(source_capture)
                verified = reference.get("image_type") == "verified_card_scan"
                if not verified and any(reference.get(field) is not None for field in
                                        ("player", "card_number", "variant", "pair_id", "identity_verification", "pair_evidence")):
                    raise ValueError("Unverified intake design cannot assert card identity or pairing")
                if verified and not reference.get("review_artifact_sha256"):
                    raise ValueError("Verified intake scan requires an independent identity review")
                if reference.get("review_artifact_sha256"):
                    review = candidate.get("identity_review")
                    if not isinstance(review, dict) or review.get("sha256") != reference["review_artifact_sha256"]:
                        raise ValueError("Intake accepted image requires its sealed identity review")
                    read(review)
                if verified:
                    from card_platform.knowledge_release import _verified_identity
                    attrs = manifest_attributes[key]
                    artifact = attrs["official_artifact"]
                    artifact_ref = {"path": "text/artifacts/" + artifact["path"], "sha256": artifact["checksum"]}
                    read(artifact_ref)
                    validated = dict(reference, season_start=attrs["season_start"], season_end=attrs["season_end"],
                                     source_artifact_path=str(root / source_capture["path"]),
                                     review_artifact_path=str(root / review["path"]),
                                     identity_verification=reference["identity_verification"] | {"artifact_path": str(root / artifact_ref["path"])})
                    _verified_identity(validated, {}, artifact)
                accepted[asset_id] = value
                local_accepted += 1
            elif candidate.get("decision") != "rejected" or not candidate.get("reason"):
                raise ValueError("Intake candidate remains pending or has no rejection reason")
        if focused and key not in manifest_scope:
            if (product.get("outcome") != "external_gap" or not product.get("blockers")
                    or not product.get("next_action") or local_accepted):
                raise ValueError("Intake unpublished research unit lacks an evidenced external gap")
        if focused or key in missing:
            routes = set()
            for attempt in product.get("searches", []):
                if not attempt.get("query") or not attempt.get("actual_outcome"):
                    raise ValueError("Intake source routes require actual query outcomes")
                read(attempt.get("capture"))
                routes.add(attempt.get("family"))
            required = {"manufacturer", "specialist", "specimen"}
            old_count = len(baseline_by_key.get(key, {}).get("attributes", {}).get("reference_images", []))
            if not local_accepted and (not focused or not old_count):
                required.add("distributor")
            if not required <= routes:
                raise ValueError("Intake source routes are incomplete")
            if not local_accepted and not old_count and (product.get("outcome") != "external_gap"
                                      or not product.get("blockers") or not product.get("next_action")):
                raise ValueError("Intake external gap requires evidence, blockers and recovery action")
        gaps += product.get("outcome") == "external_gap"
    for candidate in ledger.get("batch_artifact_decisions", []):
        if (candidate.get("decision") != "rejected" or candidate.get("review_completed") is not True
                or not candidate.get("reason")):
            raise ValueError("Batch-only intake artifact remains unreviewed")
    if any(released_references.get(key) != value for key, value in old_references.items()):
        raise ValueError("Intake removed or altered an existing reviewed image")
    if {key: value for key, value in released.items() if key not in old_assets} != accepted:
        raise ValueError("Intake has unreviewed or unpublished new media candidates")
    pair_ids = set()
    for asset_id in accepted:
        reference = released_references[asset_id]
        pair_id = reference.get("pair_id")
        if pair_id is not None or reference.get("pair_evidence") is not None:
            if not isinstance(pair_id, str) or not pair_id:
                raise ValueError("Intake pair evidence requires a declared pair ID")
            pair_ids.add(pair_id)
    for pair_id in pair_ids:
        faces = [r for r in released_references.values() if r.get("pair_id") == pair_id]
        fields = ("source_key", "player", "card_number", "variant", "source_url",
                  "source_artifact_sha256", "identity_verification", "pair_evidence")
        if (len(faces) != 2 or {r.get("side") for r in faces} != {"front", "back"}
                or faces[0]["sha256"] == faces[1]["sha256"]
                or any(faces[0].get(field) != faces[1].get(field) for field in fields)
                or any(r.get("image_type") != "verified_card_scan" for r in faces)):
            raise ValueError("Intake pair requires two distinct reviewed faces with the same source and identity")
        evidence = faces[0].get("pair_evidence") or {}
        if (evidence.get("record_type") not in {"certification", "public_specimen_listing"}
                or not evidence.get("record_id") or evidence.get("record_url") != faces[0].get("source_url")):
            raise ValueError("Intake pair lacks same-physical-card source evidence")
    for key, attrs in manifest_attributes.items():
        references = attrs.get("reference_images", [])
        summary = attrs.get("knowledge_media_summary")
        if summary is not None and any(r["asset_id"] in accepted for r in references):
            count = len({r["pair_id"] for r in references if r.get("pair_id")})
            if summary.get("paired_cards") != count:
                raise ValueError("Intake pair summary differs from the published pairs")
    return {"products": len(products), "searched_missing_products": len(missing), "unsearched": 0,
            "pending_review": 0, "accepted_new_assets": len(accepted), "external_gaps": gaps}


def preserve_unaffected_snapshot(previous: dict, current: dict, changed_ids: set[str]) -> dict:
    """Publish only explicitly owned IDs; preserve previous facts behind drafts."""
    entities = ([e for e in previous.get("entities", []) if str(e["id"]) not in changed_ids]
                + [e for e in current.get("entities", []) if str(e["id"]) in changed_ids])
    active = {str(e["id"]) for e in entities}
    relations = [r for r in previous.get("relations", [])
                 if str(r["subject_entity_id"]) in active and str(r["object_entity_id"]) in active]
    return {**current, "entities": entities, "relations": relations}


async def publish(root: Path, *, apply: bool = False) -> dict:
    from card_platform.config import Settings
    from card_platform.db import Database
    from card_platform.knowledge import normalize_knowledge_text
    from card_platform.knowledge_batch import apply_manifest
    from card_platform.knowledge_manifest import validate_manifest
    from card_platform.knowledge_repository import KnowledgeRepository

    root = root.resolve()
    bundle = json.loads((root / "release/bundle.json").read_text())
    for relative, evidence in bundle["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Unsafe bundle path")
        if sha256(path.read_bytes()).hexdigest() != evidence["sha256"]:
            raise ValueError("Bundle file checksum mismatch: " + relative)
    artifact_root = root.parent / "official-checklists/artifacts"
    for ref in bundle["official_artifacts"]:
        artifact_path = (artifact_root / ref["path"]).resolve()
        if not artifact_path.is_relative_to(artifact_root.resolve()):
            raise ValueError("Unsafe runtime artifact path")
        if sha256(artifact_path.read_bytes()).hexdigest() != ref["checksum"]:
            raise ValueError("Runtime artifact checksum mismatch")
    manifests = [json.loads((root / path).read_text()) for path in bundle["manifests"]]
    if any(len(m["sources"]) != 1 or len(m["entities"]) != 1
           or m["entities"][0]["entity_type"] != "product" for m in manifests):
        raise ValueError("Annual content manifests must be bounded product updates")
    allowed = {"batch_key", "manifest_version", "catalog_enabled", "required_regressions", "sources", "entities"}
    for manifest in manifests:
        if manifest.get("catalog_enabled") or any(value for key, value in manifest.items() if key not in allowed):
            raise ValueError("Annual content cannot mutate catalog, relations or coverage")
        entity = manifest["entities"][0]
        if entity.get("aliases") or entity.get("parent_id") or entity.get("replace_attributes"):
            raise ValueError("Annual content cannot replace aliases, ancestry or unrelated attributes")
    checksums = [validate_manifest(m) for m in manifests]
    if any(not report.publishable for report in checksums):
        raise ValueError("Unpublishable content manifest")
    content_id = sha256(json.dumps(sorted(r.checksum for r in checksums)).encode()).hexdigest()
    request_id = "annual-content:" + content_id

    async def mark_published(connection, workspace_id, version_id):
        for manifest, report in zip(manifests, checksums, strict=True):
            await connection.execute(
                "UPDATE knowledge_import_batch SET status='published',published_version_id=%s "
                "WHERE workspace_id=%s AND batch_key=%s AND manifest_checksum=%s AND NOT dry_run AND status IN ('applied','published')",
                (version_id, workspace_id, manifest["batch_key"], report.checksum),
            )

    db = Database(Settings())
    repo = KnowledgeRepository()
    await db.open()
    try:
        async with db.connection() as c:
            lock = await c.execute("SELECT pg_try_advisory_xact_lock(hashtext('card-platform-knowledge-publish')) AS acquired")
            if not (await lock.fetchone())["acquired"]:
                raise RuntimeError("Another knowledge publisher holds the lock")
            w = await repo.workspace(c)
            duplicate = await c.execute(
                "SELECT kv.id,kv.version_number FROM knowledge_audit_log al JOIN knowledge_version kv ON kv.id=al.object_id "
                "WHERE al.workspace_id=%s AND al.request_id=%s AND al.action='publish'",
                (w["id"], request_id),
            )
            if row := await duplicate.fetchone():
                if apply:
                    await mark_published(c, w["id"], row["id"])
                return {"status": "already_published", "version": row["version_number"], "content_id": content_id}
            previous = await c.execute("SELECT snapshot,checksum FROM knowledge_version WHERE id=%s", (w["published_version_id"],))
            previous = await previous.fetchone()
            validate_cloud_baseline(bundle, previous["checksum"], current_revision=w["revision"])
            snapshot = previous["snapshot"]
            intake_audit = validate_intake_audit(root, bundle, current_snapshot=snapshot)
            refs = {ref["source_key"]: ref for ref in snapshot.get("official_artifacts", [])}
            keys = [m["sources"][0]["key"] for m in manifests]
            # A manual/draft or different-source product with the same name is a
            # real collision; do not silently suppress it or overwrite its owner.
            for manifest in manifests:
                entity = manifest["entities"][0]
                conflict = await c.execute(
                    "SELECT id FROM knowledge_entity WHERE workspace_id=%s AND entity_type='product' AND normalized_name=%s "
                    "AND (review_status<>'approved' OR source->>'source_key' IS DISTINCT FROM %s)",
                    (w["id"], normalize_knowledge_text(entity["canonical_name"]), manifest["sources"][0]["key"]),
                )
                if await conflict.fetchone():
                    raise ValueError("Protected product collision: " + manifest["sources"][0]["key"])
            # Only supersede untouched, approved cards from this exact importer.
            # An edit after import makes a row ineligible. Prior versions retain
            # the previous immutable facts; other sources and drafts stay intact.
            retire_where = """workspace_id=%s AND entity_type='card' AND active AND review_status='approved'
                AND source->>'source_key'=ANY(%s)
                AND source->>'batch_key'='official-' || (source->>'source_key')
                AND attributes->>'authority'='official_checklist'
                AND attributes->>'approval_method'='automatic_official_source'
                AND NOT EXISTS (SELECT 1 FROM knowledge_alias a
                  WHERE a.entity_id=knowledge_entity.id AND a.active)
                AND NOT EXISTS (SELECT 1 FROM knowledge_relation r
                  WHERE (r.subject_entity_id=knowledge_entity.id OR r.object_entity_id=knowledge_entity.id) AND r.active)
                AND NOT EXISTS (SELECT 1 FROM knowledge_entity child
                  WHERE child.parent_id=knowledge_entity.id AND child.active)
                AND EXISTS (SELECT 1 FROM knowledge_import_batch b
                  WHERE b.workspace_id=knowledge_entity.workspace_id
                    AND b.batch_key=knowledge_entity.source->>'batch_key'
                    AND NOT b.dry_run AND b.status='applied'
                    AND COALESCE((b.summary->>'entities')::integer,0)>1
                    AND b.applied_at=knowledge_entity.updated_at)"""
            artifact_keys = [ref["source_key"] for ref in bundle["official_artifacts"]]
            counts = await c.execute("SELECT count(*) AS count FROM knowledge_entity WHERE " + retire_where, (w["id"], artifact_keys))
            eligible = (await counts.fetchone())["count"]
            result = {"status": "dry_run", "previous_version": w["published_version"],
                      "revision": w["revision"], "manifests": len(manifests),
                      "artifacts": len(artifact_keys), "untouched_import_cards_superseded": eligible,
                      "content_id": content_id, "image_assets": bundle["image_assets"],
                      "vector_records": bundle["vector_records"], "intake_audit": intake_audit}
            if not apply:
                return result
            actor_result = await c.execute("SELECT user_id FROM knowledge_member WHERE workspace_id=%s AND role='admin' ORDER BY user_id LIMIT 1", (w["id"],))
            actor = (await actor_result.fetchone())["user_id"]
            # Evaluate the importer/edit timestamp guard before creating today's
            # batch with the same source key. Otherwise that new batch could
            # incorrectly make a later manual edit look like an untouched import.
            retired = await c.execute("UPDATE knowledge_entity SET active=false,updated_at=now() WHERE " + retire_where + " RETURNING id", (w["id"], artifact_keys))
            retired_ids = {str(row["id"]) for row in await retired.fetchall()}
            for manifest, report in zip(manifests, checksums, strict=True):
                batch = await apply_manifest(c, w["id"], actor, manifest, report, dry_run=False)
                if batch["status"] == "failed":
                    raise ValueError(str(batch["conflict_report"]))
            refs.update({ref["source_key"]: ref for ref in bundle["official_artifacts"]})
            product_names = [normalize_knowledge_text(m["entities"][0]["canonical_name"]) for m in manifests]
            product_rows = await c.execute("SELECT id FROM knowledge_entity WHERE workspace_id=%s AND entity_type='product' AND source->>'source_key'=ANY(%s) AND normalized_name=ANY(%s)", (w["id"], keys, product_names))
            product_ids = {str(row["id"]) for row in await product_rows.fetchall()}
            changed_ids = retired_ids | product_ids

            class ScopedPublicationRepository(KnowledgeRepository):
                async def build_snapshot(self, connection, workspace_id, version):
                    current = await super().build_snapshot(connection, workspace_id, version)
                    return preserve_unaffected_snapshot(snapshot, current, changed_ids)

            repo = ScopedPublicationRepository()
            w = await repo.workspace(c)
            version = await repo.publish(c, w, actor, w["revision"],
                "2023–2025 season-start basketball knowledge; official checklist facts and attributed specialist evidence, reviewed image samples and learned-vector batch. Whole-set coverage unknown.",
                request_id, official_source_keys=keys, official_artifacts=list(refs.values()))
            published = {str(e["id"]): e for e in version["snapshot"]["entities"]}
            preserved = [e for e in snapshot["entities"] if str(e["id"]) not in changed_ids]
            if any(published.get(str(e["id"])) != e for e in preserved):
                raise RuntimeError("Publication altered a protected prior fact; transaction rolled back")
            await mark_published(c, w["id"], version["id"])
            result.update(status="published", version=version["version_number"],
                          retired_cards=len(retired_ids), entity_count=len(version["snapshot"]["entities"]),
                          preserved_published_entities=len(preserved),
                          checksum=version["checksum"])
            return result
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(publish(args.root, apply=args.apply)), ensure_ascii=False, default=str))
