#!/usr/bin/env python3
# ruff: noqa: E402
"""Read-only portfolio replay against published official facts, without valuations writes."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api/src"))
from card_platform.config import Settings
from card_platform.db import Database
from card_platform.knowledge import KnowledgeResolver


async def run():
    db = Database(Settings())
    await db.open()
    try:
        async with db.connection() as connection:
            r = await connection.execute(
                "SELECT kv.snapshot FROM knowledge_workspace kw JOIN knowledge_version kv ON kv.id=kw.published_version_id WHERE kw.name='Card Platform'"
            )
            snapshot = (await r.fetchone())["snapshot"]
            if args.preview:
                catalog = json.loads((ROOT / "data/official-checklists/catalog.json").read_text())
                snapshot["official_artifacts"] = [p["artifact"] for p in catalog["products"] if p.get("artifact") and p.get("status") in {"parsed", "published"}]
            resolver = KnowledgeResolver(snapshot)
            r = await connection.execute("""SELECT ci.id, ci.display_id, p.full_name AS player_name,b.name AS brand_name,
     cs.product_name,cs.season_start,cs.season_end,cd.card_number,cd.insert_name,cd.parallel_name,
     ci.serial_total, confirmed.identity AS confirmed_identity
     FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id
     JOIN player p ON p.id=cd.player_id JOIN card_set cs ON cs.id=cd.card_set_id JOIN brand b ON b.id=cs.brand_id
     LEFT JOIN LATERAL (SELECT v.identity FROM card_identity_version v WHERE v.collection_item_id=ci.id
     AND v.review_status='confirmed' AND NOT EXISTS (SELECT 1 FROM card_identity_version rev WHERE rev.supersedes_id=v.id AND rev.review_status='rejected') ORDER BY v.version DESC LIMIT 1) confirmed ON true""")
            rows = await r.fetchall()
            matches = []
            enriched = []
            negative_probes = 0
            for row in rows:
                identity = dict(row)
                confirmed = identity.pop("confirmed_identity", None)
                if confirmed:
                    identity.update(confirmed)
                aliases = resolver.aliases_for("player", identity.get("player_name"))
                candidates = resolver.card_catalog.lookup(
                    identity, player_aliases=aliases
                )
                after, evidence = resolver.card_catalog.enrich(
                    identity, player_aliases=aliases
                )
                if candidates:
                    matches.append(
                        {
                            "display_id": row["display_id"],
                            "candidate_count": len(candidates),
                        }
                    )
                if evidence:
                    for override in ({"product_name": "__unknown_official_product__"}, {"player_name": "__wrong_player__"}, {"season_start": 1900, "season_end": 1901}, {"card_number": "__wrong_number__"}):
                        assert not resolver.card_catalog.lookup(identity | override)
                        negative_probes += 1
                    enriched.append(
                        {
                            "display_id": row["display_id"],
                            "before": {
                                k: identity.get(k)
                                for k in [
                                    "product_name",
                                    "player_name",
                                    "season_start",
                                    "card_number",
                                    "insert_name",
                                ]
                            },
                            "evidence": evidence,
                            "query_plan": [
                                str(s) for s in resolver.build_query_plan(after)
                            ],
                        }
                    )
            result = {
                "version": resolver.version,
                "portfolio_count": len(rows),
                "matched_count": len(matches),
                "enriched_count": len(enriched),
                "matches": matches,
                "enriched": enriched,
                "database_writes": 0,
                "negative_probes_checked": negative_probes,
                "preview": args.preview,
                "artifact_errors": resolver.card_catalog.artifact_errors,
            }
            path = ROOT / ("data/official-checklists/portfolio-preview.json" if args.preview else "data/official-checklists/portfolio-replay.json")
            path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str)
            )
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in result.items()
                        if k not in {"matches", "enriched"}
                    }
                )
            )
    finally:
        await db.close()


parser = argparse.ArgumentParser()
parser.add_argument("--preview", action="store_true")
args = parser.parse_args()
asyncio.run(run())
