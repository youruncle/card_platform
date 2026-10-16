"""Locked accounts must receive a usable retry deadline, including at the threshold."""

import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from card_platform.config import get_settings
from card_platform.main import app

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires disposable PostgreSQL"
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    for name in ("VALUATION", "RECOGNITION", "SCANNER", "PURCHASE_COST", "PLAYER_PRICE"):
        monkeypatch.setenv(f"{name}_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


@pytest.mark.parametrize("path", ["/v1/auth/login", "/v1/mobile/auth/login"])
def test_lock_response_includes_deadline_and_expired_lock_starts_a_new_attempt_window(client, path):
    credentials = {
        "email": f"lock-{uuid4()}@example.invalid",
        "password": "Test-only-password-123!",
    }
    r = client.post("/v1/auth/register", json={**credentials, "display_name": "Lock regression"})
    assert r.status_code == 201
    uid = r.json()["id"]
    client.cookies.clear()
    wrong = {**credentials, "password": "not-the-correct-password"}
    for _ in range(4):
        r = client.post(path, json=wrong)
        assert r.status_code == 401
        assert isinstance(r.json()["detail"], str)
    r = client.post(path, json=wrong)
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["code"] == "account_temporarily_locked"
    assert 1 <= detail["retry_after_seconds"] <= 900
    assert int(r.headers["Retry-After"]) == detail["retry_after_seconds"]
    assert "重试" in detail["message"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        before = db.execute(
            "SELECT failed_login_count,locked_until FROM user_credential WHERE user_id=%s", (uid,)
        ).fetchone()
    assert client.post(path, json=credentials).status_code == 429
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert (
            db.execute(
                "SELECT failed_login_count,locked_until FROM user_credential WHERE user_id=%s",
                (uid,),
            ).fetchone()
            == before
        )
        db.execute(
            "UPDATE user_credential SET locked_until=now()-interval '1 second' WHERE user_id=%s",
            (uid,),
        )
    assert client.post(path, json=wrong).status_code == 401
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert db.execute(
            "SELECT failed_login_count,locked_until FROM user_credential WHERE user_id=%s", (uid,)
        ).fetchone() == (1, None)
    assert client.post(path, json=credentials).status_code == 200
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert db.execute(
            "SELECT failed_login_count,locked_until FROM user_credential WHERE user_id=%s", (uid,)
        ).fetchone() == (0, None)


@pytest.mark.parametrize("operation", ["logout", "revoke_session"])
def test_logout_revocation_is_committed_before_dependency_connection_closes(client, operation):
    """Real HTTP clients can retry before yield-dependency teardown commits."""
    from starlette.requests import Request
    from starlette.responses import Response
    from uuid import UUID
    from card_platform.auth import AuthenticatedUser
    from card_platform.auth_api import logout, revoke_session

    credentials = {
        "email": f"logout-{uuid4()}@example.invalid",
        "password": "Temporary-logout-password!",
    }
    user = client.post(
        "/v1/auth/register", json={**credentials, "display_name": "Logout transaction"}
    ).json()
    session = next(s for s in client.get("/v1/auth/sessions").json() if s["current"])
    principal = AuthenticatedUser(**user, session_id=UUID(session["id"]))

    async def check_before_teardown():
        async with app.state.database.connection() as connection:
            if operation == "logout":
                await logout(
                    Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1)}),
                    Response(),
                    principal,
                    connection,
                )
            else:
                await revoke_session(UUID(session["id"]), principal, connection)
            with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as other:
                revoked = other.execute(
                    "SELECT revoked_at IS NOT NULL FROM auth_session WHERE id=%s", (session["id"],)
                ).fetchone()[0]
            assert revoked, "Logout returned before its revocation was visible to the next request"

    client.portal.call(check_before_teardown)
