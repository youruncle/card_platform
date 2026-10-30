"""Local correction lifecycle against an isolated PostgreSQL, never a configured app DB."""

import asyncio
import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from card_platform.knowledge_browser import KnowledgeBrowser
from card_platform.knowledge_repository import KnowledgeRepository, KnowledgeConflictError
from card_platform import knowledge_atlas_corrections as corrections

DSN = os.environ.get("ATLAS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DSN, reason="requires explicit isolated ATLAS_TEST_DATABASE_URL"
)

SNAPSHOT = {
    "version": 1,
    "entities": [
        {
            "id": "product",
            "entity_type": "product",
            "canonical_name": "2025 Prizm",
            "attributes": {
                "manufacturer": "Panini",
                "product": "Prizm",
                "season_start": 2025,
                "season_end": 2026,
            },
        },
        {
            "id": "official:test:card",
            "entity_type": "card",
            "canonical_name": "Test Player #14",
            "attributes": {
                "manufacturer": "Panini",
                "product": "Prizm",
                "season_start": 2025,
                "season_end": 2026,
                "player": "Test Player",
                "card_number": "14",
                "section": "Base",
                "parallel": "Gold",
                "serial_total": 10,
                "is_autograph": False,
                "source_url": "https://example.org/checklist",
            },
        },
    ],
    "relations": [],
    "official_artifacts": [],
}


class FrozenPublicationRepository(KnowledgeRepository):
    async def build_snapshot(self, connection, workspace_id, version=None):
        return {**deepcopy(SNAPSHOT), "version": version}


@pytest.fixture
def database(tmp_path):
    schema = "atlas_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
        c.execute(f"SET search_path TO {schema},public")
        c.execute("CREATE TABLE app_user(id uuid PRIMARY KEY)")
        sql = (Path(__file__).parents[3] / "db/migrations/0015_card_knowledge_base.sql").read_text()
        c.execute(sql)
        c.execute(
            "CREATE TABLE knowledge_source(workspace_id uuid, source_key text, source_type text)"
        )
        c.execute("CREATE TABLE market_comp(id uuid PRIMARY KEY, listing_title text)")
        c.execute(
            "CREATE TABLE card_definition(id uuid PRIMARY KEY, insert_name text, parallel_name text, card_number text)"
        )
        c.execute("CREATE TABLE collection_item(id uuid PRIMARY KEY, card_definition_id uuid)")
        actor = uuid4()
        c.execute("INSERT INTO app_user VALUES (%s)", (actor,))
        workspace = c.execute("SELECT * FROM knowledge_workspace").fetchone()[0]
        c.execute("INSERT INTO knowledge_member VALUES (%s,%s,'admin',now())", (workspace, actor))
        version = c.execute(
            "INSERT INTO knowledge_version(workspace_id,version_number,snapshot,checksum,published_by) VALUES (%s,1,%s,'test',%s) RETURNING id",
            (workspace, Jsonb(SNAPSHOT), actor),
        ).fetchone()[0]
        c.execute("UPDATE knowledge_workspace SET published_version_id=%s", (version,))
        # Seed exact source entity so normal full publication includes it.
        for row in SNAPSHOT["entities"]:
            if row["entity_type"] == "product":
                c.execute(
                    "INSERT INTO knowledge_entity(workspace_id,entity_type,canonical_name,normalized_name,attributes,review_status,created_by) VALUES (%s,'product','2025 Prizm','2025prizm',%s,'approved',%s)",
                    (workspace, Jsonb(row["attributes"]), actor),
                )

    async def connect():
        conn = await psycopg.AsyncConnection.connect(DSN, row_factory=dict_row)
        await conn.execute(f"SET search_path TO {schema},public")
        return conn

    def browser(snapshot=SNAPSHOT, version_id=version):
        return KnowledgeBrowser(
            deepcopy(snapshot),
            version_id=str(version_id),
            cache_root=tmp_path / "cache",
            data_root=tmp_path,
            artifact_root=tmp_path,
        )

    yield connect, browser, actor, version
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {schema} CASCADE")


def run(coro):
    return asyncio.run(coro)


def payload(browser, version, **overrides):
    card = browser.card("official:test:card")["card"]
    return dict(
        card_id=card["id"],
        base_version_id=str(version),
        base_fingerprint=corrections.fingerprint(card),
        changes={"serial_total": 25},
        reason="官方总编印刷错误，见附件",
        evidence=["https://example.org/evidence"],
        **overrides,
    )


def test_submit_retry_content_binding_and_preview_does_not_write(database):
    async def scenario():
        connect, make_browser, actor, version = database
        async with await connect() as c:
            service = corrections.CorrectionRepository()
            b = make_browser()
            p = payload(b, version)
            ws = await KnowledgeRepository().workspace(c)
            first = await service.submit(c, ws, actor, p, "submit-1", b)
            assert first["status"] == "pending"
            same = await service.submit(c, ws, actor, p, "submit-1", b)
            assert same["id"] == first["id"]
            with pytest.raises(KnowledgeConflictError):
                await service.submit(c, ws, actor, {**p, "reason": "changed"}, "submit-1", b)
            assert len(await service.list(c, ws["id"], p["card_id"])) == 1
            assert b.card(p["card_id"])["card"]["serial_total"] == 10

    run(scenario())


def test_approve_draft_publish_and_rollback_are_separate(database):
    async def scenario():
        connect, make_browser, actor, version = database
        async with await connect() as c:
            repo = FrozenPublicationRepository()
            service = corrections.CorrectionRepository()
            ws = await repo.workspace(c)
            b = make_browser()
            p = payload(b, version)
            item = await service.submit(c, ws, actor, p, "s1", b)
            approved = await service.review(
                c, ws, actor, item["id"], "approved", "已核对原件", "r1", b
            )
            assert approved["status"] == "approved"
            assert b.card(p["card_id"])["card"]["serial_total"] == 10
            # Official scoped publication keeps the frozen external source entities.
            ws = await repo.workspace(c)
            published = await repo.publish(
                c,
                ws,
                actor,
                ws["revision"],
                "atlas correction",
                "pub",
            )
            assert len(published["snapshot"]["atlas_corrections"]) == 1
            corrected = make_browser(published["snapshot"], published["id"])
            detail = corrected.card(p["card_id"])
            assert detail["card"]["serial_total"] == 25
            assert detail["card"]["field_sources"]["serial_total"]["proposal_id"] == item["id"]
            assert corrected.cards(player="Test Player", card_number="14")["total"] == 1
            ws = await repo.workspace(c)
            await repo.rollback(c, ws, actor, version, ws["revision"], "rollback")
            assert make_browser().card(p["card_id"])["card"]["serial_total"] == 10

    run(scenario())


def test_stale_and_competing_proposals_conflict_and_rejection_is_inert(database):
    async def scenario():
        connect, make_browser, actor, version = database
        async with await connect() as c:
            repo = FrozenPublicationRepository()
            service = corrections.CorrectionRepository()
            ws = await repo.workspace(c)
            b = make_browser()
            p = payload(b, version)
            a = await service.submit(c, ws, actor, p, "s1", b)
            d = await service.submit(c, ws, actor, p, "s2", b)
            await service.review(c, ws, actor, a["id"], "approved", "核对通过", "r1", b)
            with pytest.raises(KnowledgeConflictError):
                await service.review(c, ws, actor, d["id"], "approved", "核对通过", "r2", b)
            rejected = await service.review(c, ws, actor, d["id"], "rejected", "版本冲突", "r3", b)
            assert rejected["status"] == "rejected"
            retry = await service.review(c, ws, actor, d["id"], "rejected", "版本冲突", "r3", b)
            assert retry["id"] == d["id"]
            with pytest.raises(KnowledgeConflictError):
                await service.submit(c, ws, actor, {**p, "base_version_id": str(uuid4())}, "s3", b)

    run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"serial_total": 0},
        {"serial_total": True},
        {"is_autograph": "false"},
        {"season_start": True},
        {"player": " "},
        {"front_image_url": "https://evil.org"},
        {"serial_numerator": 7},
    ],
)
def test_invalid_identity_changes_rejected(database, changes):
    _, make_browser, _, version = database
    p = payload(make_browser(), version)
    p["changes"] = changes
    with pytest.raises(ValueError):
        corrections.validate_proposal(p, make_browser().card(p["card_id"])["card"])


def test_overlay_source_drift_marks_conflict_and_search_updates(database):
    _, make_browser, _, version = database
    b = make_browser()
    p = payload(b, version)
    correction = {**p, "id": str(uuid4()), "before": {"serial_total": 10}, "status": "approved"}
    snapshot = deepcopy(SNAPSHOT)
    snapshot["atlas_corrections"] = [correction]
    snapshot["entities"][1]["attributes"]["serial_total"] = 50
    conflict = make_browser(snapshot).card(p["card_id"])["card"]
    assert conflict["serial_total"] == 50 and conflict["atlas"]["identity_status"] == "conflict"
    correction["changes"] = {"player": "Corrected Player"}
    correction["before"] = {"player": "Test Player"}
    snapshot = deepcopy(SNAPSHOT)
    snapshot["atlas_corrections"] = [correction]
    corrected = make_browser(snapshot)
    assert corrected.cards(player="Corrected Player")["total"] == 1
    assert corrected.cards(q="Corrected Player")["total"] == 1
    assert corrected.cards(player="Test Player")["total"] == 0


def test_concurrent_submit_and_approve_are_serialized(database):
    async def scenario():
        connect, make_browser, actor, version = database
        service = corrections.CorrectionRepository()
        b = make_browser()
        p = payload(b, version)

        async def submit(key):
            async with await connect() as c:
                async with c.transaction():
                    ws = await KnowledgeRepository().workspace(c)
                    return await service.submit(c, ws, actor, p, key, b)

        first, second = await asyncio.gather(submit("same-key"), submit("same-key"))
        assert first["id"] == second["id"]
        other = await submit("different-key")

        async def approve(item, key):
            try:
                async with await connect() as c:
                    async with c.transaction():
                        ws = await KnowledgeRepository().workspace(c)
                        await service.review(
                            c, ws, actor, item["id"], "approved", "核对原件通过", key, b
                        )
                return "approved"
            except KnowledgeConflictError:
                return "conflict"

        assert sorted(await asyncio.gather(approve(first, "r1"), approve(other, "r2"))) == [
            "approved",
            "conflict",
        ]

    run(scenario())


def test_republish_after_rollback_does_not_resurrect_consumed_correction(database):
    async def scenario():
        connect, make_browser, actor, version = database
        service = corrections.CorrectionRepository()
        repo = FrozenPublicationRepository()
        b = make_browser()
        async with await connect() as c:
            ws = await repo.workspace(c)
            item = await service.submit(c, ws, actor, payload(b, version), "s1", b)
            await service.review(c, ws, actor, item["id"], "approved", "原件正确", "r1", b)
            ws = await repo.workspace(c)
            await repo.publish(c, ws, actor, ws["revision"], "first", "p1")
            ws = await repo.workspace(c)
            await repo.rollback(c, ws, actor, version, ws["revision"], "rb")
            ws = await repo.workspace(c)
            next_version = await repo.publish(c, ws, actor, ws["revision"], "second", "p2")
            assert not next_version["snapshot"].get("atlas_corrections")

    run(scenario())


def test_automatic_official_publication_leaves_new_atlas_approval_unpublished(database):
    async def scenario():
        connect, make_browser, actor, version = database
        repo = KnowledgeRepository()
        service = corrections.CorrectionRepository()
        browser = make_browser()
        async with await connect() as c:
            ws = await repo.workspace(c)
            item = await service.submit(c, ws, actor, payload(browser, version), "s1", browser)
            await service.review(
                c, ws, actor, item["id"], "approved", "原件核验通过", "r1", browser
            )
            ws = await repo.workspace(c)
            automatic = await repo.publish(
                c,
                ws,
                actor,
                ws["revision"],
                "official automation",
                "auto",
                official_source_keys=["unrelated"],
            )
            assert not automatic["snapshot"].get("atlas_corrections"), (
                "Automation must not publish a manual atlas draft"
            )
            applied = await c.execute(
                "SELECT count(*) AS n FROM knowledge_audit_log WHERE action='atlas_applied'"
            )
            assert (await applied.fetchone())["n"] == 0

    run(scenario())


def test_stale_approved_unpublished_draft_can_be_rejected_then_replaced(database):
    async def scenario():
        connect, make_browser, actor, version = database
        repo = KnowledgeRepository()
        service = corrections.CorrectionRepository()
        async with await connect() as c:
            ws = await repo.workspace(c)
            newer = uuid4()
            await c.execute(
                "INSERT INTO knowledge_version(id,workspace_id,version_number,snapshot,checksum,published_by) VALUES(%s,%s,2,%s,'newer',%s)",
                (newer, ws["id"], Jsonb(SNAPSHOT), actor),
            )
            await c.execute("UPDATE knowledge_workspace SET published_version_id=%s", (newer,))
            b = make_browser(SNAPSHOT, newer)
            item = await service.submit(c, ws, actor, payload(b, newer), "s1", b)
            await service.review(c, ws, actor, item["id"], "approved", "原件核验通过", "r1", b)
            ws = await repo.workspace(c)
            await repo.rollback(c, ws, actor, version, ws["revision"], "rollback")
            b = make_browser()
            ws = await repo.workspace(c)
            rejected = await service.review(
                c, ws, actor, item["id"], "rejected", "已回滚，撤销未发布草稿", "withdraw", b
            )
            assert rejected["status"] == "rejected"
            replacement = await service.submit(c, ws, actor, payload(b, version), "s2", b)
            await service.review(
                c, ws, actor, replacement["id"], "approved", "新版本核验通过", "r2", b
            )
            items = await service.list(c, ws["id"])
            assert {row["id"]: row["status"] for row in items}[item["id"]] == "rejected"

    run(scenario())


def test_fingerprint_binds_source_artifact_and_origin():
    card = {
        "player": "Same",
        "card_number": "14",
        "source_key": "source-a",
        "source_checksum": "a" * 64,
    }
    assert corrections.fingerprint(card) != corrections.fingerprint(
        {**card, "source_checksum": "b" * 64}
    )
    assert corrections.fingerprint(card) != corrections.fingerprint(
        {**card, "source_key": "source-b"}
    )


def test_image_face_pair_does_not_imply_reviewed_target_binding(tmp_path):
    from test_knowledge_atlas_browser import source_data, external_image, browser

    data, card, ref = source_data(tmp_path)
    for name, side in [("front.jpg", "front"), ("back.jpg", "back")]:
        image = external_image(tmp_path, data, card, ref, name=name, side=side)
        image["serial_total"] = 99
    detail = browser(tmp_path, data).card(card["id"])
    assert detail["card"]["atlas"]["pair_binding_status"] == "needs_review"
    assert (
        detail["card"]["atlas"]["image_status"] == "paired"
    )  # physical faces are still one source record
    assert any("serial_total_conflict" in m.get("reasons", []) for m in detail["source_mappings"])


def test_changed_source_bytes_quarantine_old_reviewed_overlay(tmp_path):
    from test_knowledge_atlas_browser import source_data, browser
    from test_knowledge_browser import artifact

    data, card, ref = source_data(tmp_path)
    original = browser(tmp_path, data).card(card["id"])["card"]
    data["atlas_corrections"] = [
        {
            "id": str(uuid4()),
            "card_id": card["id"],
            "base_fingerprint": corrections.fingerprint(original),
            "changes": {"serial_total": 50},
            "reason": "旧来源核验",
            "evidence": ["https://example.org/old"],
        }
    ]
    card["attributes"]["source_checksum"] = "f" * 64
    card["attributes"]["raw_text"] = "new attachment bytes with unchanged displayed fields"
    data["entities"][3]["attributes"]["official_artifact"] = artifact(tmp_path, [card])
    current = browser(tmp_path, data).card(card["id"])["card"]
    assert current["serial_total"] == original["serial_total"]
    assert current["atlas"]["identity_status"] == "conflict"
    assert current["correction_conflict"]
