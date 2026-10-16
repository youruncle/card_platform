# ruff: noqa: E402
import asyncio, json, sys
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api/src"))
from card_platform.config import Settings
from card_platform.db import Database
from card_platform.knowledge_repository import KnowledgeRepository
from card_platform.knowledge_runtime import KnowledgeRuntime
from uuid import uuid4


async def main():
    db = Database(Settings())
    await db.open()
    try:
        runtime = KnowledgeRuntime(db)
        r1 = await runtime.refresh()
        r2 = await runtime.refresh()
        assert r1 is r2
        for ref in r1.card_catalog.artifacts:
            blob = (r1.card_catalog.artifact_root / ref["path"]).read_bytes()
            assert sha256(blob).hexdigest() == ref["checksum"], ref["source_key"]
        async with db.connection() as c:
            repo = KnowledgeRepository()
            w = await repo.workspace(c)
            r = await c.execute(
                "SELECT count(*) AS n FROM knowledge_entity WHERE active AND (attributes->>'insert' IN ('73Ta-','Sell Sheet'))"
            )
            bad = (await r.fetchone())["n"]
            assert bad == 0, bad
            r = await c.execute(
                "SELECT user_id FROM knowledge_member WHERE workspace_id=%s AND role='admin' LIMIT 1",
                (w["id"],),
            )
            actor = (await r.fetchone())["user_id"]
            r = await c.execute(
                "SELECT source_key FROM knowledge_source WHERE source_type='official_checklist' LIMIT 1"
            )
            key = (await r.fetchone())["source_key"]
            async with c.transaction(force_rollback=True):
                name = "draft-isolation-test-" + str(uuid4())
                r = await c.execute(
                    "INSERT INTO knowledge_entity(workspace_id,entity_type,canonical_name,normalized_name,review_status,active,created_by) VALUES(%s,'attribute',%s,%s,'draft',true,%s) RETURNING id",
                    (w["id"], name, name, actor),
                )
                draft = (await r.fetchone())["id"]
                version = await repo.publish(
                    c,
                    w,
                    actor,
                    w["revision"],
                    "Rollback-only automated source isolation test",
                    None,
                    official_source_keys=[key],
                )
                assert version["snapshot"].get("official_artifacts", []) == r1.card_catalog.artifacts
                assert all(
                    str(e["id"]) != str(draft) for e in version["snapshot"]["entities"]
                )
                r = await c.execute(
                    "SELECT review_status,active FROM knowledge_entity WHERE id=%s",
                    (draft,),
                )
                state = await r.fetchone()
                assert state == {"review_status": "draft", "active": True}
            print(
                json.dumps(
                    {
                        "published_version": w["published_version"],
                        "verified_artifacts": len(r1.card_catalog.artifacts),
                        "artifact_retention_on_publication": True,
                        "bad_active_facts": bad,
                        "unrelated_draft_isolation": "passed_rolled_back",
                        "runtime_refresh_reuses_same_version": True,
                    }
                )
            )
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
