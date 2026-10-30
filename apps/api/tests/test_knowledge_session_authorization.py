"""Real local auth_session + knowledge role boundary. No auth overrides/production DB."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from card_platform.auth import create_session
from card_platform.config import get_settings
from card_platform import knowledge_api
from card_platform.knowledge_runtime import KnowledgeRuntime
from test_knowledge_atlas_corrections import (
    DSN,
    SNAPSHOT,
    FrozenPublicationRepository,
    database as database,
    payload,
    run,
)

pytestmark = pytest.mark.skipif(not DSN, reason="requires explicit isolated test DB")


@pytest.fixture
def authenticated(database, tmp_path, monkeypatch):
    connect, make_browser, actor, version = database
    users, tokens = {}, {}

    async def seed():
        async with await connect() as c:
            # Test-only reproduction of existing production auth schema, no new migration.
            await c.execute(
                "ALTER TABLE app_user ADD COLUMN email text, ADD COLUMN display_name text, ADD COLUMN base_currency text DEFAULT 'CNY'"
            )
            await c.execute(
                (Path(__file__).parents[3] / "db/migrations/0052_auth_sessions.sql").read_text()
            )
            await c.execute(
                "CREATE TABLE knowledge_import_batch(workspace_id uuid, status text, published_version_id uuid, dry_run boolean)"
            )
            ws = await FrozenPublicationRepository().workspace(c)
            for role in (
                "viewer",
                "editor",
                "reviewer",
                "publisher",
                "admin",
                "nonadmin",
                "outsider",
            ):
                uid = actor if role == "admin" else uuid4()
                if uid != actor:
                    await c.execute("INSERT INTO app_user(id) VALUES(%s)", (uid,))
                await c.execute(
                    "UPDATE app_user SET email=%s,display_name=%s,role=%s WHERE id=%s",
                    (
                        role + "@example.test",
                        role,
                        "member" if role == "nonadmin" else "admin",
                        uid,
                    ),
                )
                if role not in {"admin", "outsider"}:
                    await c.execute(
                        "INSERT INTO knowledge_member(workspace_id,user_id,role) VALUES(%s,%s,%s)",
                        (ws["id"], uid, "publisher" if role == "nonadmin" else role),
                    )
                request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 12345)})
                tokens[role], _ = await create_session(c, request, uid)
                users[role] = uid

    run(seed())

    @asynccontextmanager
    async def connection():
        async with await connect() as c:
            yield c

    app = FastAPI()
    app.include_router(knowledge_api.router)
    app.state.database = SimpleNamespace(connection=connection)
    app.state.knowledge_runtime = KnowledgeRuntime(app.state.database)
    app.state.knowledge_runtime.activate(SNAPSHOT, version)
    app.state.knowledge_browser_paths = {
        "data_root": tmp_path,
        "artifact_root": tmp_path,
        "cache_root": tmp_path / "http-cache",
    }
    monkeypatch.setattr(knowledge_api, "repository", FrozenPublicationRepository())
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            connect=connect,
            browser=make_browser,
            version=version,
            users=users,
            tokens=tokens,
        )


def headers(a, role, **extra):
    return {"Authorization": "Bearer " + a.tokens[role], **extra}


def revision(a):
    return a.client.get("/v1/knowledge/workspace", headers=headers(a, "admin")).json()["revision"]


def test_publish_and_rollback_cannot_impersonate_other_knowledge_member(authenticated):
    a = authenticated
    for role in ("viewer", "editor", "reviewer", "outsider"):
        for claimed in ("publisher", "admin"):
            h = headers(a, role, **{"X-Knowledge-Actor": str(a.users[claimed])})
            assert (
                a.client.post(
                    "/v1/knowledge/publish", json={"expected_revision": 1}, headers=h
                ).status_code
                == 403
            )
            assert (
                a.client.post(
                    "/v1/knowledge/rollback",
                    json={"target_version_id": str(a.version), "expected_revision": 1},
                    headers=h,
                ).status_code
                == 403
            )
    # An authorized publisher may not choose somebody else for the audit either.
    h = headers(a, "publisher", **{"X-Knowledge-Actor": str(a.users["admin"])})
    assert (
        a.client.post("/v1/knowledge/publish", json={"expected_revision": 1}, headers=h).status_code
        == 403
    )

    async def unchanged():
        async with await a.connect() as c:
            versions = await c.execute("SELECT count(*) AS n FROM knowledge_version")
            assert (await versions.fetchone())["n"] == 1
            audits = await c.execute("SELECT count(*) AS n FROM knowledge_audit_log")
            assert (await audits.fetchone())["n"] == 0

    run(unchanged())


def test_missing_invalid_revoked_expired_disabled_and_nonadmin_sessions_are_denied(authenticated):
    a = authenticated
    body = {"expected_revision": 1}
    for h in (
        {},
        {"X-Knowledge-Actor": str(a.users["admin"])},
        {"Authorization": "Bearer invalid"},
        headers(a, "nonadmin"),
    ):
        assert a.client.post("/v1/knowledge/publish", json=body, headers=h).status_code in {
            401,
            403,
        }
        assert a.client.post(
            "/v1/knowledge/rollback", json={**body, "target_version_id": str(a.version)}, headers=h
        ).status_code in {401, 403}
    for sql in (
        "UPDATE auth_session SET revoked_at=now() WHERE user_id=%s",
        "UPDATE auth_session SET revoked_at=NULL,expires_at=now()-interval '1 second' WHERE user_id=%s",
        "UPDATE auth_session SET expires_at=now()+interval '1 day' WHERE user_id=%s",
    ):

        async def change():
            async with await a.connect() as c:
                await c.execute(sql, (a.users["publisher"],))
                if sql.startswith("UPDATE auth_session SET expires_at"):
                    await c.execute(
                        "UPDATE app_user SET disabled_at=now() WHERE id=%s", (a.users["publisher"],)
                    )

        run(change())
        assert (
            a.client.post(
                "/v1/knowledge/publish", json=body, headers=headers(a, "publisher")
            ).status_code
            == 401
        )


def test_real_cookie_and_bearer_roles_are_separated_through_publication(authenticated):
    a = authenticated
    p = payload(a.browser(), a.version)
    for role in ("viewer", "reviewer", "publisher", "outsider"):
        r = a.client.post(
            "/v1/knowledge/atlas/proposals",
            json=p,
            headers=headers(a, role, **{"Idempotency-Key": "deny-" + role}),
        )
        assert r.status_code == 403, r.text
    a.client.cookies.set(get_settings().auth_cookie_name, a.tokens["editor"])
    created = a.client.post(
        "/v1/knowledge/atlas/proposals", json=p, headers={"Idempotency-Key": "session-submit"}
    )
    assert created.status_code == 201, created.text
    a.client.cookies.clear()
    path = "/v1/knowledge/atlas/proposals/" + created.json()["id"] + "/review"
    body = {"decision": "approved", "reason": "source verified"}
    for role in ("viewer", "editor", "publisher", "outsider"):
        assert (
            a.client.post(
                path,
                json=body,
                headers=headers(a, role, **{"Idempotency-Key": "deny-review-" + role}),
            ).status_code
            == 403
        )
    approved = a.client.post(
        path, json=body, headers=headers(a, "reviewer", **{"Idempotency-Key": "session-review"})
    )
    assert approved.status_code == 200, approved.text
    for role in ("viewer", "editor", "reviewer", "outsider"):
        assert (
            a.client.post(
                "/v1/knowledge/publish",
                json={"expected_revision": revision(a)},
                headers=headers(a, role),
            ).status_code
            == 403
        )
    published = a.client.post(
        "/v1/knowledge/publish",
        json={"expected_revision": revision(a)},
        headers=headers(a, "publisher", **{"Idempotency-Key": "session-publish"}),
    )
    assert published.status_code == 200, published.text
    card = a.client.get(
        "/v1/knowledge/atlas/detail", params={"card_id": p["card_id"]}, headers=headers(a, "viewer")
    ).json()["card"]
    assert card["serial_total"] == 25
    assert published.json()["published_by"] == str(a.users["publisher"])
    rolled = a.client.post(
        "/v1/knowledge/rollback",
        json={"target_version_id": str(a.version), "expected_revision": revision(a)},
        headers=headers(a, "publisher", **{"X-Knowledge-Actor": str(a.users["publisher"])}),
    )
    assert rolled.status_code == 200, rolled.text
    restored = a.client.post(
        "/v1/knowledge/rollback",
        json={"target_version_id": published.json()["id"], "expected_revision": revision(a)},
        headers=headers(a, "admin"),
    )
    assert restored.status_code == 200, restored.text


def test_bootstrap_only_binds_the_authenticated_admin(authenticated):
    a = authenticated

    async def clear():
        async with await a.connect() as c:
            await c.execute("DELETE FROM knowledge_member")

    run(clear())
    bad = a.client.post(
        "/v1/knowledge/bootstrap",
        headers=headers(a, "admin", **{"X-Knowledge-Actor": str(a.users["viewer"])}),
    )
    assert bad.status_code == 403
    good = a.client.post("/v1/knowledge/bootstrap", headers=headers(a, "admin"))
    assert good.status_code == 200, good.text
    assert good.json()["user_id"] == str(a.users["admin"])


def test_frontend_only_rollback_on_compatible_api_preserves_corrections_on_republish(authenticated):
    """Run again from the prepared compatible API overlay in a fresh interpreter.

    Old frontend uses the same /publish contract. Removing its proposal controls
    does not change the API snapshot carrier; publication never needs to stop.
    """
    a = authenticated
    p = payload(a.browser(), a.version)
    made = a.client.post(
        "/v1/knowledge/atlas/proposals",
        json=p,
        headers=headers(a, "editor", **{"Idempotency-Key": "compat-submit"}),
    )
    assert made.status_code == 201, made.text
    approved = a.client.post(
        "/v1/knowledge/atlas/proposals/" + made.json()["id"] + "/review",
        json={"decision": "approved", "reason": "compatibility proof"},
        headers=headers(a, "reviewer", **{"Idempotency-Key": "compat-review"}),
    )
    assert approved.status_code == 200, approved.text
    published = a.client.post(
        "/v1/knowledge/publish",
        json={"expected_revision": revision(a)},
        headers=headers(
            a,
            "publisher",
            **{"X-Knowledge-Actor": str(a.users["publisher"]), "Idempotency-Key": "compat-first"},
        ),
    )
    assert published.status_code == 200, published.text
    # A fresh HTTP client represents the old web and has no atlas UI state.
    with TestClient(a.client.app) as legacy:
        carried = legacy.post(
            "/v1/knowledge/publish",
            json={
                "expected_revision": revision(a),
                "release_notes": "legacy web publication after web rollback",
            },
            headers=headers(
                a,
                "publisher",
                **{
                    "X-Knowledge-Actor": str(a.users["publisher"]),
                    "Idempotency-Key": "compat-legacy",
                },
            ),
        )
        assert carried.status_code == 200, carried.text
        assert (
            carried.json()["snapshot"]["atlas_corrections"]
            == published.json()["snapshot"]["atlas_corrections"]
        )
        detail = legacy.get(
            "/v1/knowledge/browse/cards/official:test:card", headers=headers(a, "viewer")
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["card"]["serial_total"] == 25

    # New drafts can exist after the frontend rollback. The compatibility API
    # keeps their audit history even while automatic publications continue.
    current = a.client.get(
        "/v1/knowledge/atlas/detail",
        params={"card_id": p["card_id"]},
        headers=headers(a, "editor"),
    ).json()
    draft = {
        **p,
        "base_version_id": current["version_id"],
        "base_fingerprint": current["base_fingerprint"],
    }
    pending = a.client.post(
        "/v1/knowledge/atlas/proposals",
        json={**draft, "changes": {"serial_total": 30}},
        headers=headers(a, "editor", **{"Idempotency-Key": "compat-pending"}),
    )
    assert pending.status_code == 201, pending.text
    waiting = a.client.post(
        "/v1/knowledge/atlas/proposals",
        json={**draft, "changes": {"serial_total": 40}},
        headers=headers(a, "editor", **{"Idempotency-Key": "compat-approved-draft"}),
    )
    assert waiting.status_code == 201, waiting.text
    reviewed = a.client.post(
        "/v1/knowledge/atlas/proposals/" + waiting.json()["id"] + "/review",
        json={"decision": "approved", "reason": "manual draft held for publication"},
        headers=headers(a, "reviewer", **{"Idempotency-Key": "compat-draft-review"}),
    )
    assert reviewed.status_code == 200, reviewed.text

    # A DB service caller still carries overlays without consuming newer manual drafts.
    async def automatic():
        async with await a.connect() as c:
            repo = FrozenPublicationRepository()
            ws = await repo.workspace(c)
            return await repo.publish(
                c,
                ws,
                a.users["admin"],
                ws["revision"],
                "service caller after frontend rollback",
                "compat-auto",
                official_source_keys=["official:test"],
            )

    auto = run(automatic())
    assert (
        auto["snapshot"]["atlas_corrections"] == published.json()["snapshot"]["atlas_corrections"]
    )
    saved = a.client.get(
        "/v1/knowledge/atlas/proposals",
        params={"card_id": p["card_id"]},
        headers=headers(a, "viewer"),
    )
    items = {item["id"]: item for item in saved.json()["items"]}
    assert items[pending.json()["id"]]["status"] == "pending"
    assert items[waiting.json()["id"]]["status"] == "approved"
    assert items[waiting.json()["id"]]["published_once"] is False
    assert items[waiting.json()["id"]]["lifecycle"]["state"] == "stale_draft"

    async def application_count():
        async with await a.connect() as c:
            result = await c.execute(
                "SELECT count(*) AS n FROM knowledge_audit_log WHERE action='atlas_applied' AND object_id=ANY(%s)",
                ([pending.json()["id"], waiting.json()["id"]],),
            )
            return (await result.fetchone())["n"]

    assert run(application_count()) == 0


def test_local_history_load_keeps_current_proposal_and_bounded_page(authenticated):
    """Stress existing audit/version schema locally; no migration or production samples."""
    import json
    import os
    from time import perf_counter
    from psycopg.types.json import Jsonb

    a = authenticated
    p = payload(a.browser(), a.version)
    made = a.client.post(
        "/v1/knowledge/atlas/proposals",
        json=p,
        headers=headers(a, "admin", **{"Idempotency-Key": "history-submit"}),
    ).json()
    approved = a.client.post(
        "/v1/knowledge/atlas/proposals/" + made["id"] + "/review",
        json={"decision": "approved", "reason": "history load source"},
        headers=headers(a, "admin", **{"Idempotency-Key": "history-review"}),
    )
    assert approved.status_code == 200, approved.text
    first = a.client.post(
        "/v1/knowledge/publish",
        json={"expected_revision": revision(a)},
        headers=headers(a, "publisher"),
    )
    assert first.status_code == 200, first.text

    async def seed_history():
        async with await a.connect() as c:
            ws = await FrozenPublicationRepository().workspace(c)
            # 500 later immutable versions and publication events retaining the correction.
            await c.execute(
                """WITH versions AS (
                INSERT INTO knowledge_version(workspace_id,version_number,snapshot,checksum,published_by)
                SELECT %s, n, %s, 'synthetic-local-history-'||n, %s FROM generate_series(3,502) n
                RETURNING id,workspace_id
              ) INSERT INTO knowledge_audit_log(workspace_id,actor_id,action,object_type,object_id,after_value)
                SELECT workspace_id,%s,'publish','version',id,'{}'::jsonb FROM versions""",
                (
                    ws["id"],
                    Jsonb(first.json()["snapshot"]),
                    a.users["publisher"],
                    a.users["publisher"],
                ),
            )
            await c.execute(
                "UPDATE knowledge_workspace SET published_version_id=(SELECT id FROM knowledge_version WHERE workspace_id=%s ORDER BY version_number DESC LIMIT 1) WHERE id=%s",
                (ws["id"], ws["id"]),
            )
            await c.execute(
                """INSERT INTO knowledge_audit_log(workspace_id,actor_id,action,object_type,object_id,after_value)
                SELECT %s,%s,'unrelated_history','entity',gen_random_uuid(),'{}'::jsonb FROM generate_series(1,20000)""",
                (ws["id"], a.users["admin"]),
            )
            await c.execute(
                """WITH proposals AS (SELECT gen_random_uuid() id,n FROM generate_series(1,2000) n)
                INSERT INTO knowledge_audit_log(workspace_id,actor_id,action,object_type,object_id,after_value)
                SELECT %s,%s,'atlas_submit','atlas_proposal',id,jsonb_build_object(
                    'id',id::text,'card_id','synthetic-local-'||n,'status','pending',
                    'base_version_id',%s::text,'submitted_at',clock_timestamp()::text)
                FROM proposals""",
                (ws["id"], a.users["editor"], str(a.version)),
            )

    run(seed_history())
    start = perf_counter()
    response = a.client.get("/v1/knowledge/atlas/proposals", headers=headers(a, "viewer"))
    page_ms = (perf_counter() - start) * 1000
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 100
    start = perf_counter()
    response = a.client.get(
        "/v1/knowledge/atlas/proposals",
        params={"card_id": p["card_id"]},
        headers=headers(a, "viewer"),
    )
    detail_ms = (perf_counter() - start) * 1000
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["id"] == made["id"] and item["lifecycle"]["state"] == "current"
    assert len(item["lifecycle"]["application_versions"]) == 501
    metrics = {
        "local_only": True,
        "unrelated_audits": 20000,
        "other_proposals": 2000,
        "later_versions": 500,
        "page_size": 100,
        "page_ms": round(page_ms, 3),
        "single_card_history_ms": round(detail_ms, 3),
    }
    if os.environ.get("ATLAS_SECURITY_METRICS_PATH"):
        Path(os.environ["ATLAS_SECURITY_METRICS_PATH"]).write_text(
            json.dumps(metrics, indent=2) + "\n"
        )
    # Broad enough for loaded CI, catches an accidental unbounded per-event snapshot scan.
    assert page_ms < 10000 and detail_ms < 10000, metrics
