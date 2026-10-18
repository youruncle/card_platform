import os
from uuid import uuid4

import cv2
import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient

from card_platform.config import get_settings
from card_platform.main import app


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="requires a migrated PostgreSQL database",
)


def sheet_image(offset: int = 0) -> bytes:
    image = np.full((1400, 1000, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (80 + offset, 90), (420 + offset, 590), (25, 40, 180), -1)
    cv2.rectangle(image, (540 - offset, 700), (880 - offset, 1240), (170, 45, 30), -1)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def register(client: TestClient, prefix: str) -> dict:
    response = client.post(
        "/v1/auth/register",
        json={
            "email": f"{prefix}-{uuid4()}@example.com",
            "password": "A-safe-integration-password-2026!",
            "display_name": prefix,
            "base_currency": "CNY",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_flatbed_session_isolated_immutable_manual_and_idempotent(tmp_path) -> None:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["VALUATION_WORKER_ENABLED"] = "false"
    os.environ["RECOGNITION_WORKER_ENABLED"] = "false"
    os.environ["SCANNER_WORKER_ENABLED"] = "false"
    os.environ["FLATBED_SCAN_DIR"] = str(tmp_path / "flatbed")
    os.environ["SCANNER_JOB_DIR"] = str(tmp_path / "jobs")
    get_settings.cache_clear()

    with TestClient(app) as client:
        owner = register(client, "flatbed-owner")
        created = client.post("/v1/flatbed/sessions", json={"dpi": 300})
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]

        front = client.post(
            f"/v1/flatbed/sessions/{session_id}/sheets/front",
            files={"image": ("front.png", sheet_image(), "image/png")},
        )
        back = client.post(
            f"/v1/flatbed/sessions/{session_id}/sheets/back",
            files={"image": ("back.png", sheet_image(10), "image/png")},
        )
        assert front.status_code == 201, front.text
        assert back.status_code == 201, back.text
        assert len(front.json()["regions"]) == len(back.json()["regions"]) == 2

        proposed = client.post(f"/v1/flatbed/sessions/{session_id}/pair")
        assert proposed.status_code == 200, proposed.text
        assert len(proposed.json()["pairs"]) == 2
        assert len(proposed.json()["candidates"]) == 4
        restored = client.get(f"/v1/flatbed/sessions/{session_id}")
        assert restored.status_code == 200
        assert len(restored.json()["regions"]) == 4
        assert len(restored.json()["pairs"]) == 4
        assert {sheet["side"] for sheet in restored.json()["sheets"]} == {"front", "back"}
        front_sheet = front.json()["id"]
        original = client.get(f"/v1/flatbed/sheets/{front_sheet}/image")
        assert original.status_code == 200
        assert original.headers["content-type"] == "image/png"

        manual_pairs = [
            {
                "front_region_id": pair["front_id"],
                "back_region_id": pair["back_id"],
            }
            for pair in reversed(proposed.json()["pairs"])
        ]
        confirmed = client.put(
            f"/v1/flatbed/sessions/{session_id}/pairs", json={"pairs": manual_pairs}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["skipped"] == {"front": 0, "back": 0}

        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            candidate_count, selected_count, confirmed_count = connection.execute(
                """SELECT count(*),count(*) FILTER (WHERE selected),
                          count(*) FILTER (WHERE selected AND manual_override)
                   FROM flatbed_pair_proposal WHERE session_id=%s""",
                (session_id,),
            ).fetchone()
            # Confirmation annotates existing scored candidates; it must not
            # add fake score=1/high rows that erase the original risk signals.
            assert (candidate_count, selected_count, confirmed_count) == (4, 2, 2)

        submitted = client.post(f"/v1/flatbed/sessions/{session_id}/submit", json={"confirmation_token": confirmed.json()["confirmation_token"]})
        repeated = client.post(f"/v1/flatbed/sessions/{session_id}/submit")
        assert submitted.status_code == repeated.status_code == 200
        assert len(submitted.json()["job_ids"]) == 2
        assert repeated.json()["job_ids"] == submitted.json()["job_ids"]

        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as connection:
            sheet = connection.execute(
                "SELECT id,original_path,sha256 FROM flatbed_scan_sheet WHERE session_id=%s ORDER BY created_at LIMIT 1",
                (session_id,),
            ).fetchone()
            with pytest.raises(psycopg.errors.RaiseException):
                connection.execute(
                    "UPDATE flatbed_scan_sheet SET sha256='changed' WHERE id=%s", (sheet[0],)
                )
            assert (tmp_path / "flatbed" / sheet[1]).is_file()

        register(client, "flatbed-other")
        assert client.get(f"/v1/flatbed/sessions/{session_id}").status_code == 404
        assert client.get(f"/v1/flatbed/sheets/{front_sheet}/image").status_code == 404
        assert client.get("/v1/flatbed/sessions").json() == []

        with psycopg.connect(os.environ["TEST_DATABASE_URL"], autocommit=True) as connection:
            count = connection.execute(
                "SELECT count(*) FROM flatbed_scan_session WHERE id=%s AND owner_id=%s",
                (session_id, owner["id"]),
            ).fetchone()[0]
            assert count == 1
