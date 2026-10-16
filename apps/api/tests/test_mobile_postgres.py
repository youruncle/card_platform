"""Mobile sessions and capture use real PostgreSQL, image files and ownership checks."""

import os
from uuid import uuid4

import cv2
import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.main import app

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires disposable PostgreSQL"
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("AUTH_REGISTRATION_ENABLED", "true")
    for name in ("VALUATION", "RECOGNITION", "SCANNER", "PURCHASE_COST"):
        monkeypatch.setenv(f"{name}_WORKER_ENABLED", "false")
    get_settings.cache_clear()
    with TestClient(app) as value:
        yield value
    get_settings.cache_clear()


def account(client):
    credentials = {
        "email": f"mobile-{uuid4()}@example.invalid",
        "password": "Test-only-password-long!",
    }
    created = client.post("/v1/auth/register", json={**credentials, "display_name": "Mobile test"})
    assert created.status_code == 201, created.text
    user = created.json()
    client.cookies.clear()
    response = client.post("/v1/mobile/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    assert "set-cookie" not in response.headers
    assert "no-store" in response.headers["cache-control"]
    return user, {"Authorization": f"Bearer {response.json()['token']}"}


def photos(shade=140):
    pixels = np.full((160, 100, 3), shade, dtype=np.uint8)
    ok, data = cv2.imencode(".png", pixels)
    assert ok
    return {side: (f"{side}.png", data.tobytes(), "image/png") for side in ("front", "back")}


def test_mobile_session_revocation_and_invalid_bearer_never_falls_back(client):
    user, headers = account(client)
    assert client.get("/v1/auth/me", headers=headers).json()["id"] == user["id"]
    assert client.get("/v1/auth/me").status_code == 401
    # Even a valid browser session must not override a bad explicit mobile credential.
    client.cookies.set(get_settings().auth_cookie_name, headers["Authorization"][7:])
    assert client.get("/v1/auth/me", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert client.get("/v1/auth/me", headers={"Authorization": "Basic invalid"}).status_code == 401
    client.cookies.clear()
    sessions = client.get("/v1/auth/sessions", headers=headers).json()
    assert len(sessions) >= 1
    assert client.post("/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/v1/auth/me", headers=headers).status_code == 401


def test_capture_is_idempotent_private_and_requires_confirmation(client):
    user, headers = account(client)
    _, other = account(client)
    key = str(uuid4())
    upload_headers = {**headers, "Idempotency-Key": key}
    response = client.post("/v1/mobile/jobs", headers=upload_headers, files=photos())
    assert response.status_code == 202, response.text
    job = response.json()
    assert (
        client.post("/v1/mobile/jobs", headers=upload_headers, files=photos()).json()["id"]
        == job["id"]
    )
    assert (
        client.post("/v1/mobile/jobs", headers=upload_headers, files=photos(200)).status_code == 409
    )
    assert client.get(job["front_image_url"], headers=other).status_code == 404
    assert client.get(job["front_image_url"], headers=headers).status_code == 200
    body = {
        "owner_id": user["id"],
        "card": {
            "player_name": "Synthetic test",
            "brand_name": "Test",
            "season_start": 2026,
            "product_name": "Test",
        },
    }
    confirm = f"/v1/mobile/jobs/{job['id']}/confirm"
    assert client.post(confirm, headers=other, json=body).status_code == 404
    assert client.post(confirm, headers=headers, json=body).status_code == 409
    # External model output is a boundary fixture; creation, files, transaction and access are real.
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        row = db.execute(
            "SELECT require_confirmation FROM scanner_recognition_job WHERE id=%s", (job["id"],)
        ).fetchone()
        assert row == (True,)
        db.execute(
            "UPDATE scanner_recognition_job SET status='succeeded',result=%s WHERE id=%s",
            (Jsonb({"confidence": "0.7", "player_name": "Synthetic test"}), job["id"]),
        )
    saved = client.post(confirm, headers=headers, json=body)
    assert saved.status_code == 201, saved.text
    assert client.post(confirm, headers=headers, json=body).json()["id"] == saved.json()["id"]
    detail_url = f"/v1/portfolio/items/{saved.json()['id']}/detail"
    detail = client.get(detail_url, headers=headers).json()["item"]
    assert detail["player_name"] == "Synthetic test"
    for side in ("front", "back"):
        image_url = detail[f"image_{side}_url"]
        assert client.get(image_url, headers=headers).status_code == 200
        assert client.get(image_url, headers=other).status_code == 404
        assert client.get(image_url).status_code == 401
    assert client.get(detail_url, headers=other).status_code == 404
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert db.execute(
            "SELECT count(*) FROM collection_item WHERE owner_id=%s", (user["id"],)
        ).fetchone() == (1,)


def test_mobile_worker_keeps_result_pending_confirmation(client, monkeypatch):
    from card_platform.scanner_queue import ScannerRecognitionWorker
    from card_platform.image_service import CardVisionService
    from card_platform.card_orientation import OrientationDecision
    from card_platform.schemas import CardRecognitionResult

    _, headers = account(client)
    job = client.post(
        "/v1/mobile/jobs", headers={**headers, "Idempotency-Key": str(uuid4())}, files=photos()
    ).json()
    assert "id" in job, job

    async def recognize(*args, **kwargs):
        return CardRecognitionResult(confidence="0.5", player_name="Synthetic test")

    async def orient(*args, **kwargs):
        return OrientationDecision(rotation_degrees=0, confidence=1.0, method="vision_model")

    monkeypatch.setattr(CardVisionService, "recognize_pair_contents", recognize)
    monkeypatch.setattr(ScannerRecognitionWorker, "_orient_side", orient)
    worker = ScannerRecognitionWorker(app.state.database, get_settings())
    client.portal.call(worker._process, job["id"])
    result = client.get(f"/v1/scanner/jobs/{job['id']}", headers=headers).json()
    assert result["status"] == "succeeded", result
    assert result["collection_item_id"] is None
    assert result["result"]["player_name"] == "Synthetic test"


def test_mobile_list_excludes_desktop_jobs_and_other_owners(client):
    _, headers = account(client)
    _, other = account(client)
    mobile = client.post(
        "/v1/mobile/jobs", headers={**headers, "Idempotency-Key": str(uuid4())}, files=photos()
    ).json()
    desktop = client.post("/v1/scanner/jobs", headers=headers, files=photos()).json()
    response = client.get("/v1/mobile/jobs", headers=headers)
    assert response.status_code == 200, response.text
    ids = [row["id"] for row in response.json()]
    assert mobile["id"] in ids
    assert desktop["id"] not in ids
    assert client.get("/v1/mobile/jobs", headers=other).json() == []


def test_parallel_duplicate_uploads_create_one_task(client):
    from concurrent.futures import ThreadPoolExecutor

    _, headers = account(client)
    headers = {**headers, "Idempotency-Key": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: client.post("/v1/mobile/jobs", headers=headers, files=photos()), range(2)
            )
        )
    assert [r.status_code for r in results] == [202, 202]
    assert results[0].json()["id"] == results[1].json()["id"]
