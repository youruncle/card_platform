import os
from uuid import uuid4
import psycopg
from psycopg.types.json import Jsonb
import pytest
from test_mobile_postgres import client as client, account, photos

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="disposable database required"
)


def seed(client):
    user, headers = account(client)
    job = client.post("/v1/scanner/jobs", headers=headers, files=photos()).json()
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET status='failed',attempt_count=3,error_message='recognition: timeout /private/secret' WHERE id=%s",
            (job["id"],),
        )
    return user, headers, job


def test_scanner_failures_visible_paginated_and_owner_scoped(client):
    user, h, j = seed(client)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        for _ in range(103):
            db.execute(
                """INSERT INTO scanner_recognition_job SELECT (jsonb_populate_record(NULL::scanner_recognition_job,to_jsonb(j)||jsonb_build_object('id',%s::text))).* FROM scanner_recognition_job j WHERE j.id=%s""",
                (str(uuid4()), j["id"]),
            )
    response = client.get("/v1/capture/tasks?state=failed&page=6&page_size=20", headers=h)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 104 and len(page["items"]) == 4
    assert page["counts"]["failed"] == 104
    assert all(t["display_status"] == "failed" and t["can_retry"] for t in page["items"])
    assert "/private" not in response.text
    _, other = account(client)
    assert client.get("/v1/capture/tasks", headers=other).json()["total"] == 0
    assert client.get("/v1/capture/tasks/" + j["id"], headers=other).status_code == 404


def test_history_survives_manual_retry_after_auto_limit_and_manual_confirmation(client):
    user, h, j = seed(client)
    path = "/v1/capture/tasks/" + j["id"]
    before = client.get(path, headers=h).json()
    assert any(e["display_status"] == "failed" for e in before["history"])
    retry = client.post(path + "/retry", headers=h)
    assert retry.status_code == 200, retry.text
    assert retry.json()["attempt_count"] == 3
    assert client.post(path + "/retry", headers=h).status_code == 409
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert db.execute(
            "SELECT manual_retry_limit FROM scanner_recognition_job WHERE id=%s", (j["id"],)
        ).fetchone() == (4,)
        db.execute(
            "UPDATE scanner_recognition_job SET status='failed',attempt_count=4,error_message='new failure' WHERE id=%s",
            (j["id"],),
        )
    task = client.get(path, headers=h).json()
    manual_name = f"Manual {uuid4()}"
    body = {
        "card": {
            "player_name": manual_name,
            "brand_name": "Panini",
            "product_name": "Prizm",
            "season_start": 2024,
            "is_rookie": None,
            "is_autograph": None,
            "is_memorabilia": None,
            "is_patch": None,
        },
        "pair_review": {"confirmed": True, "image_content_hashes": task["image_content_hashes"]},
    }
    bad = {
        **body,
        "pair_review": {
            "confirmed": True,
            "image_content_hashes": {"front": "a" * 64, "back": "b" * 64},
        },
    }
    assert client.post(path + "/confirm", headers=h, json=bad).status_code == 409
    saved = client.post(path + "/confirm", headers=h, json=body)
    assert saved.status_code == 201, saved.text
    assert client.post(path + "/confirm", headers=h, json=body).json() == saved.json()
    after = client.get(path, headers=h).json()
    assert after["display_status"] == "imported"
    assert after["result"]["player_name"] == manual_name
    assert sum(e["display_status"] == "failed" for e in after["history"]) >= 2
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert db.execute(
            """SELECT cd.is_rookie,cd.is_autograph,cd.is_memorabilia,cd.is_patch FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id WHERE ci.id=%s""",
            (saved.json()["id"],),
        ).fetchone() == (None, None, None, None)
    assert (
        client.get("/v1/portfolio/items/" + saved.json()["id"] + "/detail", headers=h).status_code
        == 200
    )


def test_replacement_preserves_old_images_and_history_and_is_idempotent(client):
    user, h, j = seed(client)
    path = "/v1/capture/tasks/" + j["id"]
    key = {**h, "Idempotency-Key": str(uuid4())}
    files = {"back": photos(200)["back"]}
    new = client.post(path + "/images", headers=key, files=files)
    assert new.status_code == 202, new.text
    assert new.json()["id"] != j["id"]
    repeat = client.post(path + "/images", headers=key, files=files)
    assert repeat.status_code == 202 and repeat.json()["id"] == new.json()["id"]
    assert (
        client.post(path + "/images", headers=key, files={"back": photos(210)["back"]}).status_code
        == 409
    )
    old = client.get(path, headers=h).json()
    assert old["display_status"] == "cancelled" and old["replacement_job_id"] == new.json()["id"]
    assert any(e["display_status"] == "failed" for e in old["history"])
    assert client.get(old["back_image_url"], headers=h).status_code == 200


def test_manual_confirm_cannot_bypass_stored_conflict(client):
    user, h, j = seed(client)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET result=%s WHERE id=%s",
            (
                Jsonb(
                    {
                        "confidence": ".9",
                        "pair_validation": {
                            "status": "conflict",
                            "reason_codes": ["subject_conflict"],
                        },
                    }
                ),
                j["id"],
            ),
        )
    task = client.get("/v1/capture/tasks/" + j["id"], headers=h).json()
    body = {
        "card": {
            "player_name": "Manual",
            "brand_name": "Panini",
            "product_name": "Prizm",
            "season_start": 2024,
        },
        "pair_review": {"confirmed": True, "image_content_hashes": task["image_content_hashes"]},
    }
    assert (
        client.post("/v1/capture/tasks/" + j["id"] + "/confirm", headers=h, json=body).status_code
        == 409
    )


def test_failed_retry_retains_current_pair_conflict(client):
    user, h, j = seed(client)
    path = "/v1/capture/tasks/" + j["id"]
    hashes = client.get(path, headers=h).json()["image_content_hashes"]
    raw = {
        "confidence": ".9",
        "pair_validation": {
            "status": "conflict",
            "reason_codes": ["subject_conflict"],
            "image_content_hashes": hashes,
        },
    }
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET result=%s WHERE id=%s", (Jsonb(raw), j["id"])
        )
    assert client.post(path + "/retry", headers=h).status_code == 200
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET status='failed',error_message='timeout' WHERE id=%s",
            (j["id"],),
        )
    task = client.get(path, headers=h).json()
    body = {
        "card": {
            "player_name": "Manual",
            "brand_name": "Panini",
            "product_name": "Prizm",
            "season_start": 2024,
        },
        "pair_review": {"confirmed": True, "image_content_hashes": hashes},
    }
    assert client.post(path + "/confirm", headers=h, json=body).status_code == 409
    assert task["result"]["pair_validation"]["status"] == "conflict"


def test_imported_history_cannot_be_resurrected_by_replacement(client):
    user, h, j = seed(client)
    path = "/v1/capture/tasks/" + j["id"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET status='succeeded',imported_at=now() WHERE id=%s",
            (j["id"],),
        )
    task = client.get(path, headers=h).json()
    assert task["display_status"] == "cancelled" and not task["can_retry"]
    response = client.post(
        path + "/images",
        headers={**h, "Idempotency-Key": str(uuid4())},
        files={"front": photos(200)["front"]},
    )
    assert response.status_code == 409


def test_unknown_copy_does_not_change_known_shared_definition(client):
    user, h, j = seed(client)
    body = {
        "owner_id": user["id"],
        "card": {
            "player_name": "Shared",
            "brand_name": "Panini",
            "product_name": "Prizm",
            "season_start": 2024,
            "card_number": "1",
        },
    }
    item = client.post("/v1/portfolio/items", headers=h, json=body)
    assert item.status_code == 201, item.text
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        old = db.execute(
            "SELECT to_jsonb(cd) FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id WHERE ci.id=%s",
            (item.json()["id"],),
        ).fetchone()[0]
    task = client.get("/v1/capture/tasks/" + j["id"], headers=h).json()
    unknown = {
        **body,
        "card": {
            **body["card"],
            "is_rookie": None,
            "is_autograph": None,
            "has_inscription": None,
            "is_memorabilia": None,
            "is_patch": None,
        },
        "pair_review": {"confirmed": True, "image_content_hashes": task["image_content_hashes"]},
    }
    saved = client.post("/v1/capture/tasks/" + j["id"] + "/confirm", headers=h, json=unknown)
    assert saved.status_code == 201, saved.text
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        after = db.execute(
            "SELECT to_jsonb(cd) FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id WHERE ci.id=%s",
            (item.json()["id"],),
        ).fetchone()[0]
    old.pop("updated_at", None)
    after.pop("updated_at", None)
    assert old == after


def test_inconclusive_retry_does_not_erase_conflict_through_legacy_confirm(client):
    user, h, j = seed(client)
    path = "/v1/capture/tasks/" + j["id"]
    hashes = client.get(path, headers=h).json()["image_content_hashes"]
    raw = {
        "confidence": ".9",
        "pair_validation": {
            "status": "conflict",
            "reason_codes": ["subject_conflict"],
            "image_content_hashes": hashes,
        },
    }
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET result=%s WHERE id=%s", (Jsonb(raw), j["id"])
        )
    assert client.post(path + "/retry", headers=h).status_code == 200
    unknown = {
        "confidence": ".9",
        "pair_validation": {"status": "unknown", "image_content_hashes": hashes},
    }
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET status='succeeded',result=%s,require_confirmation=true WHERE id=%s",
            (Jsonb(unknown), j["id"]),
        )
    body = {
        "card": {
            "player_name": "Manual",
            "brand_name": "Panini",
            "product_name": "Prizm",
            "season_start": 2024,
        },
        "pair_review": {"confirmed": True, "image_content_hashes": hashes},
    }
    assert (
        client.post("/v1/capture/jobs/" + j["id"] + "/confirm", headers=h, json=body).status_code
        == 409
    )
    assert client.post(path + "/confirm", headers=h, json=body).status_code == 409
    task = client.get(path, headers=h).json()
    assert task["result"]["pair_validation"]["status"] == "conflict"


def test_invalid_replacement_is_explained_without_server_error(client):
    _, h, j = seed(client)
    response = client.post(
        "/v1/capture/tasks/" + j["id"] + "/images",
        headers={**h, "Idempotency-Key": str(uuid4())},
        files={"front": ("x.png", b"not a png", "image/png")},
    )
    assert response.status_code == 422


def test_missing_retained_photo_requests_upload_instead_of_server_error(client):
    from card_platform.config import get_settings
    from card_platform.scanner_queue import ScannerQueueService

    _, h, j = seed(client)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        path = db.execute(
            "SELECT back_image_path FROM scanner_recognition_job WHERE id=%s", (j["id"],)
        ).fetchone()[0]
    ScannerQueueService(get_settings()).image_path(path).unlink()
    response = client.post(
        "/v1/capture/tasks/" + j["id"] + "/images",
        headers={**h, "Idempotency-Key": str(uuid4())},
        files={"front": photos(200)["front"]},
    )
    assert response.status_code == 409 and "缺失" in response.text


def test_unchanged_photos_cannot_create_replacement_task(client):
    from card_platform.config import get_settings
    from card_platform.scanner_queue import ScannerQueueService

    _, h, j = seed(client)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        path = db.execute(
            "SELECT front_image_path FROM scanner_recognition_job WHERE id=%s", (j["id"],)
        ).fetchone()[0]
    content = ScannerQueueService(get_settings()).image_path(path).read_bytes()
    response = client.post(
        "/v1/capture/tasks/" + j["id"] + "/images",
        headers={**h, "Idempotency-Key": str(uuid4())},
        files={"front": ("same.png", content, "image/png")},
    )
    assert response.status_code == 409 and "相同" in response.text
