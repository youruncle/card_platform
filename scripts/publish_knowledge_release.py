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
        if sha256((artifact_root / ref["path"]).read_bytes()).hexdigest() != ref["checksum"]:
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
            previous = await c.execute("SELECT snapshot FROM knowledge_version WHERE id=%s", (w["published_version_id"],))
            snapshot = (await previous.fetchone())["snapshot"]
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
                      "vector_records": bundle["vector_records"]}
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
