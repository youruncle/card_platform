"""Run inside the cloud API container: temporary accounts + generated image only.

Never uses local collection files or prints passwords/tokens. Public registration
stays disabled. All test accounts/tasks/files are removed in finally.
"""

import json
from pathlib import Path
import secrets
import shutil
import time
from uuid import uuid4

import cv2
import httpx
import numpy as np
import psycopg
from psycopg.rows import dict_row

from card_platform.auth import password_hasher
from card_platform.config import get_settings

settings = get_settings()
accounts = []
checks = []
with psycopg.connect(settings.database_url, row_factory=dict_row) as db:
    baseline = db.execute("SELECT count(*) AS n FROM collection_item").fetchone()["n"]
    try:
        for _ in range(2):
            user_id, email, password = (
                uuid4(),
                f"mobile-smoke-{uuid4()}@example.invalid",
                secrets.token_urlsafe(32),
            )
            db.execute(
                "INSERT INTO app_user(id,email,display_name,base_currency,role) VALUES(%s,%s,'Mobile integration test','CNY','member')",
                (user_id, email),
            )
            db.execute(
                "INSERT INTO user_credential(user_id,password_hash) VALUES(%s,%s)",
                (user_id, password_hasher.hash(password)),
            )
            db.commit()
            accounts.append((user_id, email, password))
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30) as client:
            headers = []
            for _, email, password in accounts:
                response = client.post(
                    "/v1/mobile/auth/login", json={"email": email, "password": password}
                )
                assert response.status_code == 200, f"login HTTP {response.status_code}"
                assert "set-cookie" not in response.headers
                headers.append({"Authorization": "Bearer " + response.json()["token"]})
            checks.append("mobile_login")
            assert (
                client.get("/v1/portfolio/items/page", headers=headers[0]).json()[
                    "total"
                ]
                == 0
            )
            checks.append("empty_library")
            _, encoded = cv2.imencode(
                ".png", np.full((200, 140, 3), 160, dtype=np.uint8)
            )
            files = {
                side: (side + ".png", encoded.tobytes(), "image/png")
                for side in ("front", "back")
            }
            upload_headers = {**headers[0], "Idempotency-Key": str(uuid4())}
            response = client.post(
                "/v1/mobile/jobs", headers=upload_headers, files=files
            )
            assert response.status_code == 202, f"upload HTTP {response.status_code}"
            job = response.json()
            again = client.post("/v1/mobile/jobs", headers=upload_headers, files=files)
            assert again.status_code == 202 and again.json()["id"] == job["id"]
            checks.append("idempotent_upload")
            assert (
                client.get(job["front_image_url"], headers=headers[0]).status_code
                == 200
            )
            assert (
                client.get(job["front_image_url"], headers=headers[1]).status_code
                == 404
            )
            assert client.get(job["front_image_url"]).status_code == 401
            assert client.get("/v1/mobile/jobs", headers=headers[1]).json() == []
            checks.append("private_images_and_owner_isolation")
            state = "queued"
            for _ in range(20):
                state = client.get(
                    "/v1/scanner/jobs/" + job["id"], headers=headers[0]
                ).json()["status"]
                if state in ("failed", "succeeded"):
                    break
                time.sleep(0.5)
            if not settings.vision_api_key:
                assert state == "failed", (
                    "unconfigured vision should surface a failed task"
                )
                checks.append("missing_model_explicit_failure")
            assert client.post("/v1/auth/logout", headers=headers[0]).status_code == 204
            assert client.get("/v1/auth/me", headers=headers[0]).status_code == 401
            checks.append("logout_revokes_session")
        assert (
            db.execute("SELECT count(*) AS n FROM collection_item").fetchone()["n"]
            == baseline
        )
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
        for user_id, _, _ in accounts:
            directory = Path(settings.scanner_job_dir).resolve() / str(user_id)
            if directory.is_dir():
                shutil.rmtree(directory)
print(
    json.dumps(
        {
            "checks": checks,
            "temporary_accounts_removed": len(accounts),
            "local_collection_uploaded": False,
            "vision_configured": bool(settings.vision_api_key),
        },
        ensure_ascii=False,
    )
)
