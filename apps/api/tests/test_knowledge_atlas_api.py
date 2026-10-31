from contextlib import asynccontextmanager
from test_knowledge_atlas_corrections import payload, run, DSN, database as database
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from card_platform.auth import current_user
from card_platform.knowledge_api import router, _knowledge_connection
from card_platform.knowledge_browser_api import published_browser
from card_platform import knowledge_atlas_api as api
from test_knowledge_atlas_corrections import SNAPSHOT
from card_platform.knowledge_browser import KnowledgeBrowser


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(router)
    user = SimpleNamespace(id=uuid4(), role="admin")
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[_knowledge_connection] = lambda: None
    browser = KnowledgeBrowser(
        SNAPSHOT, version_id=str(uuid4()), cache_root=tmp_path / "cache", data_root=tmp_path
    )
    app.dependency_overrides[published_browser] = lambda: browser

    async def workspace(_):
        return {"id": uuid4()}

    async def role(_, workspace_id, actor_id, allowed):
        from card_platform.knowledge_repository import KnowledgePermissionError

        if user.role == "viewer":
            raise KnowledgePermissionError("无写权限")
        return "editor"

    monkeypatch.setattr(api.repository, "workspace", workspace)
    monkeypatch.setattr(api.repository, "require_role", role)

    @app.middleware("http")
    async def connection(request, call_next):
        request.state.connection = SimpleNamespace()
        return await call_next(request)

    with TestClient(app) as c:
        yield c, user, browser


def test_management_filters_are_global_and_do_not_hide_missing_images(client):
    c, _, _ = client
    page = c.get("/v1/knowledge/atlas/cards?image_state=none&identity_state=pending").json()
    assert page["total"] == 1 and page["items"][0]["card_number"] == "14"
    assert c.get("/v1/knowledge/atlas/cards?image_state=both").json()["total"] == 0
    assert c.get("/v1/knowledge/atlas/cards?identity_state=confirmed").json()["total"] == 0
    assert c.get("/v1/knowledge/atlas/cards?image_state=garbage").status_code == 422
    assert c.get("/v1/knowledge/atlas/cards?offset=-1").status_code == 422
    detail = c.get("/v1/knowledge/atlas/detail", params={"card_id": "official:test:card"}).json()
    assert detail["base_fingerprint"] and detail["version_id"]
    assert detail["field_sources"]["player"]["basis"] == "record_level_not_field_verified"
    summary = c.get("/v1/knowledge/atlas/summary").json()
    assert summary["global_unique_atlases"] is None and summary["source_records"] == 1


def test_permissions_come_from_session_and_unknown_role_is_readonly(client):
    c, user, _ = client
    assert c.get("/v1/knowledge/atlas/permissions").json()["can_propose"]
    # app role still admin; role membership can be read-only.
    user.role = "viewer"
    assert c.get("/v1/knowledge/atlas/permissions").status_code == 403


def test_missing_session_and_nonadmin_cannot_access_management(client):
    c, user, _ = client
    user.role = "user"
    assert c.get("/v1/knowledge/atlas/cards").status_code == 403
    assert (
        c.post(
            "/v1/knowledge/atlas/proposals", json={}, headers={"X-Knowledge-Actor": str(uuid4())}
        ).status_code
        == 403
    )


# Same real PostgreSQL fixture as service lifecycle, with HTTP authorization and transactions.


@pytest.mark.skipif(not DSN, reason="requires explicit isolated ATLAS_TEST_DATABASE_URL")
def test_actual_http_session_roles_idempotency_and_approved_drafts(database):
    connect, make_browser, actor, version = database
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=actor, role="admin")

    @asynccontextmanager
    async def connection():
        async with await connect() as c:
            yield c

    app.state.database = SimpleNamespace(connection=connection)
    b = make_browser()
    app.dependency_overrides[published_browser] = lambda: b
    p = payload(b, version)

    def member(role):
        async def update():
            async with await connect() as c:
                await c.execute(
                    "UPDATE knowledge_member SET role=%s WHERE user_id=%s", (role, actor)
                )

        run(update())

    with TestClient(app) as c:
        member("viewer")
        assert c.get("/v1/knowledge/atlas/permissions").json()["can_propose"] is False
        assert (
            c.get("/v1/knowledge/atlas/detail", params={"card_id": p["card_id"]}).status_code == 200
        )
        assert (
            c.post(
                "/v1/knowledge/atlas/proposals",
                json=p,
                headers={"X-Knowledge-Actor": str(uuid4()), "Idempotency-Key": "s1"},
            ).status_code
            == 403
        )
        member("editor")
        first = c.post("/v1/knowledge/atlas/proposals", json=p, headers={"Idempotency-Key": "s1"})
        assert first.status_code == 201, first.text
        assert (
            c.post(
                "/v1/knowledge/atlas/proposals", json=p, headers={"Idempotency-Key": "s1"}
            ).json()["id"]
            == first.json()["id"]
        )
        assert (
            c.post(
                "/v1/knowledge/atlas/proposals",
                json={**p, "changes": {"serial_numerator": 4}},
                headers={"Idempotency-Key": "s2"},
            ).status_code
            == 422
        )
        path = f"/v1/knowledge/atlas/proposals/{first.json()['id']}/review"
        body = {"decision": "approved", "reason": "核对原件通过"}
        assert c.post(path, json=body, headers={"Idempotency-Key": "r1"}).status_code == 403
        member("reviewer")
        approved = c.post(path, json=body, headers={"Idempotency-Key": "r1"})
        assert approved.status_code == 200, approved.text
        assert c.post(path, json=body, headers={"Idempotency-Key": "r1"}).json() == approved.json()
        assert (
            c.get("/v1/knowledge/atlas/detail", params={"card_id": p["card_id"]}).json()["card"][
                "serial_total"
            ]
            == 10
        )
        assert c.get("/v1/knowledge/atlas/proposals").json()["items"][0]["status"] == "approved"


def test_brand_navigation_and_filter_preserve_product_boundary(client):
    c, _, _ = client
    nav = c.get("/v1/knowledge/atlas/navigation").json()
    assert nav["products"][0]["name"] == "2025 Prizm"
    assert c.get("/v1/knowledge/atlas/cards?brand=nonexistent").json()["total"] == 0
