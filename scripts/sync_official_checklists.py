#!/usr/bin/env python3
# ruff: noqa: E402
"""Resume official basketball checklist ingestion; no manual approval queue."""

import argparse
import fcntl
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))
from card_platform.official_checklists import (
    PARSER_VERSION,
    build_manifest,
    discover_topps_links,
    official_url,
    parse_document,
    progress_summary,
    window_status,
)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    temporary.replace(path)


async def run(args):
    import httpx
    from card_platform.config import Settings
    from card_platform.db import Database
    from card_platform.knowledge_batch import apply_manifest
    from card_platform.knowledge_manifest import validate_manifest
    from card_platform.knowledge_repository import KnowledgeRepository

    catalog_path = Path(args.catalog)
    catalog = json.loads(catalog_path.read_text())
    root = catalog_path.parent
    products = catalog["products"]

    def save():
        write_json(catalog_path, catalog)
        write_json(
            root / "status.json",
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "sport": "basketball",
                "window": {"start": args.start, "end": args.end},
                "engineering_percent": catalog.get("engineering_percent", 5),
                "summary": progress_summary(
                    products,
                    discovery_complete=catalog.get("discovery_complete", False),
                ),
                "known_products_percent": round(
                    100
                    * sum(p.get("status") == "published" for p in products)
                    / len(products),
                    1,
                )
                if products
                else 0,
                "source_blockers": catalog.get("source_blockers", []),
                "products": products,
            },
        )

    save()
    async with httpx.AsyncClient(timeout=60) as client:
        if args.discover:
            directory = "https://www.topps.com/pages/checklists"
            try:
                response = await client.get(directory)
                response.raise_for_status()
                discovered = discover_topps_links(response.text, directory)
                if not discovered:
                    raise ValueError("No public basketball document links found")
                # Keep the full directory as evidence. Import scope stays in the
                # frozen catalog; an archive season is not a release date.
                write_json(
                    root / "live-discovery.json",
                    {
                        "products": discovered,
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                catalog["discovery_status"] = {
                    "status": "fetched",
                    "links": len(discovered),
                }
            except Exception as exc:
                catalog["discovery_status"] = {"status": "blocked", "error": str(exc)}
            save()
        for p in products:
            if p.get("artifact"):
                # Version attachments are managed by sync_official_history; never
                # expand them back into millions of per-version database entities.
                continue
            previous_status = p.get("status")
            previous_checksum = p.get("manifest_checksum")
            p["window_status"] = window_status(
                p.get("release_date"), args.start, args.end
            )
            if p["window_status"] == "outside":
                p["status"] = "excluded"
                save()
                continue
            if not p.get("source_url"):
                p["status"] = "blocked"
                save()
                continue
            try:
                if not official_url(p["source_url"]):
                    raise ValueError("Untrusted document origin")
                raw = root / "raw" / f"{p['key']}.pdf"
                if not raw.exists():
                    response = await client.get(p["source_url"])
                    response.raise_for_status()
                    raw.parent.mkdir(parents=True, exist_ok=True)
                    raw.write_bytes(response.content)
                p["checksum"] = sha256(raw.read_bytes()).hexdigest()
                p.setdefault("retrieved_at", datetime.now(timezone.utc).isoformat())
                parsed_path = root / "parsed" / f"{p['key']}.json"
                parsed = (
                    json.loads(parsed_path.read_text()) if parsed_path.exists() else {}
                )
                if (
                    parsed.get("parser_version") != PARSER_VERSION
                    or parsed.get("checksum") != p["checksum"]
                ):
                    rows, issues, pages = parse_document(raw)
                    parsed = {
                        "rows": rows,
                        "issues": issues,
                        "pages": pages,
                        "checksum": p["checksum"],
                        "parser_version": PARSER_VERSION,
                    }
                    write_json(parsed_path, parsed)
                p.update(
                    row_count=len(parsed["rows"]),
                    issue_count=len(parsed["issues"]),
                    status="parsed",
                )
                if not parsed["rows"]:
                    raise ValueError("No structured card facts extracted; raw retained")
                manifest = build_manifest(p, parsed["rows"])
                report = validate_manifest(manifest)
                if not report.publishable:
                    raise ValueError(f"Manifest conflicts: {report.conflicts}")
                write_json(root / "manifests" / f"{p['key']}.json", manifest)
                if (
                    previous_status == "published"
                    and previous_checksum == report.checksum
                ):
                    p["status"] = "published"
                p["manifest_checksum"] = report.checksum
                p.pop("error", None)
            except Exception as exc:
                p.update(status="failed", error=str(exc))
            save()
    if not args.apply:
        return
    database = Database(Settings())
    await database.open()
    repository = KnowledgeRepository()
    try:
        async with database.connection() as connection:
            lock = await connection.execute("SELECT pg_try_advisory_xact_lock(hashtext('card-platform-knowledge-publish')) AS acquired")
            if not (await lock.fetchone())["acquired"]:
                raise RuntimeError("Another knowledge sync or publication holds the database lock")
            workspace = await repository.workspace(connection)
            result = await connection.execute(
                "SELECT user_id FROM knowledge_member WHERE workspace_id=%s AND role=%s ORDER BY user_id LIMIT 1",
                (workspace["id"], "admin"),
            )
            actor = await result.fetchone()
            if not actor:
                raise RuntimeError("No existing knowledge administrator identity")
            published_result = await connection.execute(
                "SELECT snapshot FROM knowledge_version WHERE id=%s",
                (workspace["published_version_id"],),
            )
            published_row = await published_result.fetchone()
            published_cards = {
                entity["canonical_name"]: entity.get("attributes", {})
                for entity in (
                    published_row["snapshot"]["entities"] if published_row else []
                )
                if entity["entity_type"] == "card"
            }
            changed = []
            for p in products:
                if p.get("artifact"):
                    continue
                if p["status"] not in {"parsed", "published"}:
                    continue
                manifest = json.loads(
                    (root / "manifests" / f"{p['key']}.json").read_text()
                )
                report = validate_manifest(manifest)
                try:
                    async with connection.transaction():
                        existing = await connection.execute(
                            "SELECT status FROM knowledge_import_batch WHERE workspace_id=%s AND batch_key=%s AND manifest_checksum=%s AND NOT dry_run",
                            (workspace["id"], manifest["batch_key"], report.checksum),
                        )
                        previous = await existing.fetchone()
                        if not previous or previous["status"] != "applied":
                            # Replace this importer-owned source atomically so a
                            # corrected section/name cannot leave stale active facts.
                            await connection.execute(
                                """UPDATE knowledge_entity SET active=false, updated_at=now()
                                   WHERE workspace_id=%s AND source->>'source_key'=%s
                                     AND source->>'batch_key'=%s
                                     AND entity_type IN ('card','insert')""",
                                (workspace["id"], p["key"], manifest["batch_key"]),
                            )
                            batch = await apply_manifest(
                                connection,
                                workspace["id"],
                                actor["user_id"],
                                manifest,
                                report,
                                dry_run=False,
                            )
                            if batch["status"] == "failed":
                                raise ValueError(str(batch["conflict_report"]))
                            changed.append(p["key"])
                        elif any(
                            published_cards.get(entity["canonical_name"])
                            != entity.get("attributes", {})
                            for entity in manifest["entities"]
                            if entity["entity_type"] == "card"
                        ):
                            changed.append(p["key"])
                        p["status"] = "applied"
                except Exception as exc:
                    p.update(status="failed", error=str(exc))
            # Publish and apply atomically; failed publication cannot claim active facts.
            workspace = await repository.workspace(connection)
            if changed:
                version = await repository.publish(
                    connection,
                    workspace,
                    actor["user_id"],
                    workspace["revision"],
                    "Automatic official basketball checklist ingestion; source facts only; unknown release dates tracked separately.",
                    None,
                    official_source_keys=changed,
                )
                version_number = version["version_number"]
            else:
                version_number = workspace["published_version"]
        for p in products:
            if p["status"] == "applied":
                p.update(status="published", knowledge_version=version_number)
        save()
    except Exception:
        for p in products:
            if p["status"] == "applied":
                p["status"] = "parsed"
        save()
        raise
    finally:
        await database.close()
    print(
        json.dumps(
            {
                "version": version_number,
                "products": len(changed),
                "summary": progress_summary(
                    products,
                    discovery_complete=catalog.get("discovery_complete", False),
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog", default=str(ROOT / "data/official-checklists/catalog.json")
    )
    parser.add_argument("--start", default="2006-10-04")
    parser.add_argument("--end", default="2026-10-04")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Refresh official directory evidence without changing frozen release scope",
    )
    args = parser.parse_args()
    lock_path = Path(args.catalog).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(args))
