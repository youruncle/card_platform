"""Verify a running release with disposable accounts; never use existing credentials."""

import json
import os
import secrets
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

from card_platform.auth import password_hasher
from card_platform.config import get_settings

settings = get_settings()
accounts = []
checks = []
with psycopg.connect(settings.database_url, row_factory=dict_row) as db:

    def counts():
        return db.execute("""SELECT (SELECT count(*) FROM app_user) AS users,
          (SELECT count(*) FROM collection_item) AS cards,
          (SELECT count(*) FROM card_transaction) AS transactions""").fetchone()

    baseline = counts()
    assert not settings.auth_registration_enabled, (
        "Public registration must stay disabled"
    )
    try:
        for _ in range(2):
            account = (
                uuid4(),
                f"price-release-{uuid4()}@example.invalid",
                secrets.token_urlsafe(32),
            )
            user_id, email, password = account
            db.execute(
                """INSERT INTO app_user(id,email,display_name,base_currency,role)
              VALUES(%s,%s,'Release verification','CNY','member')""",
                (user_id, email),
            )
            db.execute(
                "INSERT INTO user_credential(user_id,password_hash) VALUES(%s,%s)",
                (user_id, password_hasher.hash(password)),
            )
            accounts.append(account)
        db.commit()
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
            assert client.get("/health/ready").status_code == 200
            assert client.get("/openapi.json").json()["info"]["version"] == os.getenv("EXPECTED_RELEASE_VERSION", "0.6.1")
            checks.append("readiness_and_release_version")
            assert client.get("/v1/player-prices").status_code == 401
            assert client.post("/v1/player-prices/refresh").status_code == 401
            checks.append("anonymous_price_access_denied")
            capabilities = client.get("/v1/client-capabilities").json()
            assert all(
                capabilities[key]
                for key in (
                    "single_side",
                    "supplement",
                    "image_variants",
                    "portfolio_sort",
                )
            )
            checks.append("mobile_capabilities")
            for _, email, password in accounts:
                login = client.post(
                    "/v1/mobile/auth/login", json={"email": email, "password": password}
                )
                assert login.status_code == 200 and "set-cookie" not in login.headers
                headers = {"Authorization": "Bearer " + login.json()["token"]}
                board = client.get("/v1/player-prices", headers=headers)
                assert board.status_code == 200 and board.json()["players"] == []
                assert board.json()["worker_enabled"] is True
                assert (
                    client.get("/v1/portfolio/items/page", headers=headers).json()[
                        "total"
                    ]
                    == 0
                )
                assert client.get("/v1/capture/jobs", headers=headers).json() == []
                assert (
                    client.get(
                        "/v1/player-prices?date=2099-01-01", headers=headers
                    ).status_code
                    == 422
                )
                queued = client.post("/v1/player-prices/refresh", headers=headers)
                assert queued.status_code == 202 and queued.json()["queued"] == 0
                assert (
                    client.post("/v1/auth/logout", headers=headers).status_code == 204
                )
                assert (
                    client.get("/v1/player-prices", headers=headers).status_code == 401
                )
            checks.extend(
                [
                    "mobile_bearer_login",
                    "existing_portfolio_hidden_from_test_accounts",
                    "price_owner_scope",
                    "refresh_and_date_validation",
                    "session_revocation",
                ]
            )
            _, email, password = accounts[0]
            origin = settings.cors_origins[0]
            login = client.post(
                "/v1/auth/login",
                json={"email": email, "password": password},
                headers={"Origin": origin},
            )
            assert (
                login.status_code == 200
                and "httponly" in login.headers["set-cookie"].lower()
            )
            if not settings.auth_cookie_secure:
                assert client.get("/v1/player-prices").status_code == 200
                assert (
                    client.post(
                        "/v1/auth/logout", headers={"Origin": origin}
                    ).status_code
                    == 204
                )
            checks.append("web_login_cookie")
    finally:
        db.rollback()
        for user_id, email, _ in accounts:
            db.execute(
                "DELETE FROM auth_audit_log WHERE user_id=%s OR email=%s",
                (user_id, email),
            )
            db.execute(
                "DELETE FROM app_user WHERE id=%s AND email=%s", (user_id, email)
            )
        db.commit()
    assert counts() == baseline, "Existing account/card/transaction counts changed"

print(
    json.dumps(
        {
            "checks": checks,
            "temporary_accounts_removed": len(accounts),
            "existing_data_counts_unchanged": True,
            "local_data_uploaded": False,
        },
        ensure_ascii=False,
    )
)
