"""Generic capture contract against disposable PostgreSQL and real image storage."""

import os
from uuid import uuid4
import psycopg
import pytest
from test_mobile_postgres import account, photos
from test_mobile_postgres import client as client

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="disposable PostgreSQL required"
)


def upload(client, headers, sides=("front",), **data):
    return client.post(
        "/v1/capture/jobs",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={k: v for k, v in photos().items() if k in sides},
        data=data,
    )


def succeed(job):
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET status='succeeded' WHERE id=%s", (job["id"],)
        )


@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("path", [
    "/v1/capture/jobs/00000000-0000-0000-0000-000000000000/images/front",
    "/v1/capture/jobs/not-a-uuid/images/front",
    "/v1/portfolio/items/not-a-uuid/images/front",
])
def test_private_media_error_responses_are_not_cached(client, method, path):
    _, headers = account(client)
    response = client.request(method, path, headers=headers)
    assert response.status_code in {404, 422}
    assert response.headers.get("cache-control") == "private, no-store"
    vary = response.headers.get("vary", "").lower()
    assert "cookie" in vary and "authorization" in vary


@pytest.mark.parametrize("sides", [("front",), ("back",), ("front", "back")])
def test_capture_sides_confirm_and_legacy_isolation(client, sides):
    user, headers = account(client)
    response = upload(client, headers, sides)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["available_sides"] == list(sides)
    for side in ("front", "back"):
        image = job["images"].get(side)
        assert bool(image) == (side in sides)
        assert client.get(
            f"/v1/capture/jobs/{job['id']}/images/{side}", headers=headers
        ).status_code == (200 if side in sides else 404)
    if len(sides) == 1:
        assert client.get("/v1/scanner/jobs", headers=headers).json() == []
        assert client.get("/v1/mobile/jobs", headers=headers).json() == []
        assert client.get(f"/v1/scanner/jobs/{job['id']}", headers=headers).status_code == 409
    succeed(job)
    body = {
        "owner_id": user["id"],
        "card": {
            "player_name": "Test",
            "brand_name": "Test",
            "season_start": 2026,
            "product_name": "Test",
        },
    }
    saved = client.post(f"/v1/capture/jobs/{job['id']}/confirm", headers=headers, json=body)
    assert saved.status_code == 201, saved.text
    assert (
        client.post(f"/v1/capture/jobs/{job['id']}/confirm", headers=headers, json=body).json()
        == saved.json()
    )


def test_capture_empty_idempotency_and_supplement(client):
    user, headers = account(client)
    _, other = account(client)
    h = {**headers, "Idempotency-Key": str(uuid4())}
    assert client.post("/v1/capture/jobs", headers=h).status_code == 422
    files = {"front": photos()["front"]}
    first = client.post("/v1/capture/jobs", headers=h, files=files)
    assert first.status_code == 202, first.text
    assert (
        client.post("/v1/capture/jobs", headers=h, files=files).json()["id"] == first.json()["id"]
    )
    assert (
        client.post("/v1/capture/jobs", headers=h, files={"back": photos()["back"]}).status_code
        == 409
    )
    job = first.json()
    succeed(job)
    body = {
        "owner_id": user["id"],
        "card": {
            "player_name": "Human confirmed",
            "brand_name": "Test",
            "season_start": 2026,
            "product_name": "Test",
        },
    }
    item_id = client.post(
        f"/v1/capture/jobs/{job['id']}/confirm", headers=headers, json=body
    ).json()["id"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        revision = (
            db.execute("SELECT updated_at FROM collection_item WHERE id=%s", (item_id,))
            .fetchone()[0]
            .isoformat()
        )
    assert (
        upload(
            client, other, ("back",), target_item_id=item_id, expected_revision=revision
        ).status_code
        == 404
    )
    supplement = upload(
        client, headers, ("back",), target_item_id=item_id, expected_revision=revision
    )
    assert supplement.status_code == 202, supplement.text
    supplement = supplement.json()
    succeed(supplement)
    saved = client.post(f"/v1/capture/jobs/{supplement['id']}/confirm", headers=headers, json={})
    assert saved.status_code == 201, saved.text
    assert saved.json()["id"] == item_id
    item = client.get(f"/v1/portfolio/items/{item_id}/detail", headers=headers).json()["item"]
    assert item["player_name"] == "Human confirmed"
    assert item["image_front_url"] and item["image_back_url"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert db.execute(
            "SELECT count(*) FROM collection_item WHERE owner_id=%s", (user["id"],)
        ).fetchone() == (1,)
    assert (
        upload(
            client, headers, ("back",), target_item_id=item_id, expected_revision=revision
        ).status_code
        == 409
    )


@pytest.mark.parametrize("side", ["front", "back"])
def test_single_worker_uses_single_vision_and_keeps_missing_side_absent(client, monkeypatch, side):
    from card_platform.main import app
    from card_platform.scanner_queue import ScannerRecognitionWorker
    from card_platform.image_service import CardVisionService
    from card_platform.card_orientation import OrientationDecision
    from card_platform.schemas import CardRecognitionResult
    from card_platform.config import get_settings

    _, headers = account(client)
    response = upload(client, headers, (side,))
    assert response.status_code == 202, response.text
    job = response.json()

    async def recognize(self, image):
        assert await image.read()
        return CardRecognitionResult(confidence="0.5", player_name="Single image")

    async def orient(*args, **kwargs):
        return OrientationDecision(0, 1.0, "vision_model")

    monkeypatch.setattr(CardVisionService, "recognize", recognize)
    monkeypatch.setattr(ScannerRecognitionWorker, "_orient_side", orient)
    worker = ScannerRecognitionWorker(app.state.database, get_settings())
    client.portal.call(worker._process, job["id"])
    result = client.get(f"/v1/capture/jobs/{job['id']}", headers=headers).json()
    assert result["status"] == "succeeded", result
    assert result["available_sides"] == [side]
    assert result["collection_item_id"] is None
    assert result["result"]["player_name"] == "Single image"


def test_retry_ownership_source_provenance_and_explicit_identity_acceptance(client):
    import json

    user, headers = account(client)
    _, other = account(client)
    files = {"front": photos()["front"], "front_source": photos(180)["front"]}
    response = client.post(
        "/v1/capture/jobs",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files=files,
        data={"crop_recipe": json.dumps({"front": {"quad": None, "rotation": 0}})},
    )
    assert response.status_code == 202, response.text
    job = response.json()
    assert (
        client.get(job["images"]["front"]["source_url"], headers=headers).content
        == photos(180)["front"][1]
    )
    for method, path, kwargs in [
        ("get", f"/v1/capture/jobs/{job['id']}", {}),
        ("post", f"/v1/capture/jobs/{job['id']}/retry", {}),
        ("post", f"/v1/capture/jobs/{job['id']}/confirm", {"json": {}}),
    ]:
        assert getattr(client, method)(path, headers=other, **kwargs).status_code == 404
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE scanner_recognition_job SET status='failed' WHERE id=%s", (job["id"],))
    retry = client.post(f"/v1/capture/jobs/{job['id']}/retry", headers=headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["revision"] > job["revision"]
    assert client.get(job["front_image_url"], headers=headers).status_code == 409
    succeed(job)
    card = {
        "player_name": "First",
        "brand_name": "Test",
        "season_start": 2026,
        "product_name": "Test",
    }
    item = client.post(
        f"/v1/capture/jobs/{job['id']}/confirm",
        headers=headers,
        json={"owner_id": user["id"], "card": card},
    ).json()["id"]
    target = client.get(f"/v1/capture/items/{item}", headers=headers).json()
    assert target["available_sides"] == ["front"]
    supplement = upload(
        client, headers, ("back",), target_item_id=item, expected_revision=target["revision"]
    ).json()
    succeed(supplement)
    confirm = f"/v1/capture/jobs/{supplement['id']}/confirm"
    assert client.post(confirm, headers=headers, json={"card": card}).status_code == 422
    accepted = client.post(
        confirm,
        headers=headers,
        json={"accepted_identity": {**card, "player_name": "Explicitly accepted"}},
    )
    assert accepted.status_code == 201, accepted.text
    detail = client.get(f"/v1/portfolio/items/{item}/detail", headers=headers).json()["item"]
    assert detail["player_name"] == "Explicitly accepted"


def test_supplement_stale_at_confirmation_and_parallel_upload(client):
    from concurrent.futures import ThreadPoolExecutor

    user, headers = account(client)
    h = {**headers, "Idempotency-Key": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: client.post(
                    "/v1/capture/jobs", headers=h, files={"front": photos()["front"]}
                ),
                range(2),
            )
        )
    assert [r.status_code for r in results] == [202, 202]
    assert results[0].json()["id"] == results[1].json()["id"]
    job = results[0].json()
    succeed(job)
    card = {
        "player_name": "Stable",
        "brand_name": "Test",
        "season_start": 2026,
        "product_name": "Test",
    }
    item = client.post(
        f"/v1/capture/jobs/{job['id']}/confirm",
        headers=headers,
        json={"owner_id": user["id"], "card": card},
    ).json()["id"]
    target = client.get(f"/v1/capture/items/{item}", headers=headers).json()
    supplement = upload(
        client, headers, ("back",), target_item_id=item, expected_revision=target["revision"]
    ).json()
    succeed(supplement)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE collection_item SET updated_at=now() WHERE id=%s", (item,))
    assert (
        client.post(
            f"/v1/capture/jobs/{supplement['id']}/confirm", headers=headers, json={}
        ).status_code
        == 409
    )


def test_task_image_variants_are_private_and_enforce_revision(client, monkeypatch):
    from card_platform import image_variants

    _, headers = account(client)
    _, other = account(client)
    job = upload(client, headers).json()
    url = job["images"]["front"]["thumb_url"]
    assert client.get(url, headers=other).status_code == 404
    assert client.get(url).status_code == 401
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert client.head(url, headers=headers).status_code == 200

    def busy(*args, **kwargs):
        raise TimeoutError("busy")

    monkeypatch.setattr(image_variants, "get_image_variant", busy)
    overloaded = client.get(url, headers=headers)
    assert overloaded.status_code == 503
    assert overloaded.headers["retry-after"] == "2"


def test_crop_recipe_source_hash_and_quad_are_validated(client):
    import json

    _, headers = account(client)
    for recipe in (
        {"source_hash": "wrong", "quad": None},
        {"quad": [[0, 0], [1, 1], [1, 0], [0, 1]]},
    ):
        response = client.post(
            "/v1/capture/jobs",
            headers={**headers, "Idempotency-Key": str(uuid4())},
            files={"front": photos()["front"], "front_source": photos()["front"]},
            data={"crop_recipe": json.dumps({"front": recipe})},
        )
        assert response.status_code == 422, response.text


@pytest.mark.parametrize("field", ["front", "front_source"])
def test_capture_rejects_oversized_header_before_pixel_decode(client, field):
    import struct
    import zlib

    _, headers = account(client)
    content = bytearray(photos()["front"][1])
    content[16:24] = struct.pack(">II", 10000, 10000)
    content[29:33] = struct.pack(">I", zlib.crc32(content[12:29]))
    files = {"front": photos()["front"], field: ("huge.png", bytes(content), "image/png")}
    response = client.post(
        "/v1/capture/jobs", headers={**headers, "Idempotency-Key": str(uuid4())}, files=files
    )
    assert response.status_code == 422, response.text


def test_deleted_supplement_target_does_not_block_deletion_or_recreate_card(client):
    user, headers = account(client)
    job = upload(client, headers).json()
    succeed(job)
    body = {
        "owner_id": user["id"],
        "card": {
            "player_name": "Delete synthetic",
            "brand_name": "Test",
            "product_name": "Test",
            "season_start": 2026,
        },
    }
    item = client.post(f"/v1/capture/jobs/{job['id']}/confirm", headers=headers, json=body).json()[
        "id"
    ]
    target = client.get(f"/v1/capture/items/{item}", headers=headers).json()
    supplement = upload(
        client, headers, ("back",), target_item_id=item, expected_revision=target["revision"]
    ).json()
    succeed(supplement)
    assert (
        client.request(
            "DELETE", f"/v1/portfolio/items/{item}", headers=headers, json={"owner_id": user["id"]}
        ).status_code
        == 204
    )
    deleted = client.request(
        "DELETE",
        f"/v1/portfolio/items/{item}/permanent",
        headers=headers,
        json={"owner_id": user["id"]},
    )
    assert deleted.status_code == 204, deleted.text
    for old_job in (job, supplement):
        assert (
            client.post(
                f"/v1/capture/jobs/{old_job['id']}/confirm", headers=headers, json=body
            ).status_code
            == 409
        )
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert db.execute(
            "SELECT count(*) FROM collection_item WHERE owner_id=%s", (user["id"],)
        ).fetchone() == (0,)


def test_capture_target_returns_complete_confirmed_identity(client):
    from card_platform.schemas import CardIdentityInput

    user, headers = account(client)
    job = upload(client, headers).json()
    succeed(job)
    identity = {
        "player_name": "Full identity",
        "player_birth_date": "1990-01-02",
        "brand_name": "Complete unique " + str(uuid4()),
        "manufacturer": "Original manufacturer",
        "brand_tier": 2,
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Product",
        "set_name": "Set",
        "card_number": "42",
        "parallel_name": "Gold",
        "insert_name": "Insert",
        "is_rookie": True,
        "is_autograph": True,
        "autograph_type": "on_card",
        "has_inscription": True,
        "inscription_text": "MVP",
        "is_memorabilia": True,
        "memorabilia_types": ["patch"],
        "memorabilia_usage": "game_used",
        "memorabilia_player": "Full identity",
        "is_patch": True,
        "is_case_hit": True,
        "print_run": 99,
    }
    item = client.post(
        f"/v1/capture/jobs/{job['id']}/confirm",
        headers=headers,
        json={"owner_id": user["id"], "card": identity},
    ).json()["id"]
    response = client.get(f"/v1/capture/items/{item}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["identity"] == CardIdentityInput.model_validate(identity).model_dump(
        mode="json"
    )


def test_supplement_selected_identity_does_not_mutate_shared_cards_or_reuse_conflicting_definition(
    client,
):
    user, headers = account(client)
    identity = {
        "player_name": "Shared " + str(uuid4()),
        "brand_name": "Test",
        "season_start": 2026,
        "product_name": "Test",
        "card_number": "A",
        "is_rookie": True,
        "is_autograph": True,
        "autograph_type": "on_card",
        "has_inscription": True,
        "inscription_text": "MVP",
    }

    def create(card):
        job = upload(client, headers).json()
        succeed(job)
        return client.post(
            f"/v1/capture/jobs/{job['id']}/confirm", headers=headers, json={"card": card}
        ).json()["id"]

    first, second = create(identity), create(identity)
    conflict = create({**identity, "card_number": "B", "is_rookie": False})
    current = client.get(f"/v1/capture/items/{first}", headers=headers).json()
    job = upload(
        client, headers, ("back",), target_item_id=first, expected_revision=current["revision"]
    ).json()
    succeed(job)
    payload = {**current["identity"], "card_number": "B"}
    denied = client.post(
        f"/v1/capture/jobs/{job['id']}/confirm",
        headers=headers,
        json={"accepted_identity": payload},
    )
    assert denied.status_code == 409, denied.text
    assert (
        client.get(f"/v1/capture/items/{conflict}", headers=headers).json()["identity"]["is_rookie"]
        is False
    )
    accepted = client.post(
        f"/v1/capture/jobs/{job['id']}/confirm",
        headers=headers,
        json={"accepted_identity": {**current["identity"], "card_number": "C"}},
    )
    assert accepted.status_code == 201, accepted.text
    assert client.get(f"/v1/capture/items/{first}", headers=headers).json()["identity"] == {
        **current["identity"],
        "card_number": "C",
    }
    assert (
        client.get(f"/v1/capture/items/{second}", headers=headers).json()["identity"]
        == current["identity"]
    )


def test_confirmed_capture_preserves_bytes_orientation_and_mime_in_worker(client, monkeypatch):
    import json
    from card_platform.main import app
    from card_platform.scanner_queue import ScannerRecognitionWorker
    from card_platform.image_service import CardVisionService
    from card_platform.schemas import CardRecognitionResult
    from card_platform.config import get_settings

    _, headers = account(client)
    photo = photos()["front"]
    job = client.post(
        "/v1/capture/jobs",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        files={"front": photo, "front_source": photo},
        data={"crop_recipe": json.dumps({"front": {"quad": None, "confirmed": True}})},
    ).json()

    async def forbidden(*args, **kwargs):
        raise AssertionError("user-confirmed image must not be reoriented")

    async def recognize(self, image):
        assert image.content_type == "image/png"
        assert await image.read() == photo[1]
        return CardRecognitionResult(confidence="0.5", player_name="Frozen source")

    monkeypatch.setattr(ScannerRecognitionWorker, "_orient_side", forbidden)
    monkeypatch.setattr(CardVisionService, "recognize", recognize)
    client.portal.call(
        ScannerRecognitionWorker(app.state.database, get_settings())._process, job["id"]
    )
    result = client.get(f"/v1/capture/jobs/{job['id']}", headers=headers).json()
    assert result["status"] == "succeeded", result
    assert client.get(result["front_image_url"], headers=headers).content == photo[1]


@pytest.mark.parametrize("binding_kind", ["market", "identity", "manual", "running"])
def test_bound_identity_only_allows_photo_supplement(client, binding_kind):
    from psycopg.types.json import Jsonb

    user, headers = account(client)
    job = upload(client, headers).json()
    succeed(job)
    card = {
        "player_name": "Bound " + str(uuid4()),
        "brand_name": "Test",
        "product_name": "Test",
        "season_start": 2026,
    }
    item = client.post(
        f"/v1/capture/jobs/{job['id']}/confirm", headers=headers, json={"card": card}
    ).json()["id"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        if binding_kind == "market":
            db.execute(
                "INSERT INTO market_identity_cache(identity_fingerprint,normalized_identity) VALUES (%s,%s)",
                ("test-bound-" + item, Jsonb(card)),
            )
            db.execute(
                "INSERT INTO market_identity_binding(collection_item_id,identity_fingerprint) VALUES (%s,%s)",
                (item, "test-bound-" + item),
            )
        elif binding_kind == "identity":
            db.execute(
                "INSERT INTO card_identity_version(collection_item_id,version,identity,review_status) VALUES (%s,1,%s,'confirmed')",
                (item, Jsonb(card)),
            )
        elif binding_kind == "manual":
            db.execute(
                "INSERT INTO valuation_snapshot(collection_item_id,method,currency,low_value,fair_value,high_value) VALUES (%s,'manual','CNY',10,10,10)",
                (item,),
            )
        else:
            db.execute(
                "UPDATE valuation_job SET status='identifying' WHERE collection_item_id=%s", (item,)
            )
    current = client.get(f"/v1/capture/items/{item}", headers=headers).json()
    assert current["can_accept_identity"] is False
    assert current["identity_acceptance_reason"]
    supplement = upload(
        client, headers, ("back",), target_item_id=item, expected_revision=current["revision"]
    ).json()
    succeed(supplement)
    path = f"/v1/capture/jobs/{supplement['id']}/confirm"
    rejected = client.post(
        path,
        headers=headers,
        json={"accepted_identity": {**current["identity"], "card_number": "new"}},
    )
    assert rejected.status_code == 409, rejected.text
    assert client.post(path, headers=headers, json={}).status_code == 201
    assert (
        client.get(f"/v1/capture/items/{item}", headers=headers).json()["identity"]
        == current["identity"]
    )


@pytest.mark.parametrize("accept", [False, True])
def test_valuation_load_identity_uses_human_fields_after_supplement(client, accept):
    from card_platform.main import app
    from card_platform.config import get_settings
    from card_platform.valuation_worker import AutomatedValuationWorker
    from psycopg.types.json import Jsonb

    user, headers = account(client)
    job = upload(client, headers, ("front", "back")).json()
    succeed(job)
    card = {
        "player_name": "Valuation " + str(uuid4()),
        "brand_name": "Test",
        "product_name": "Test",
        "season_start": 2026,
        "card_number": "HUMAN-A",
        "parallel_name": "Gold",
    }
    item = client.post(
        f"/v1/capture/jobs/{job['id']}/confirm", headers=headers, json={"card": card}
    ).json()["id"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        # A real persisted historical desktop result remains an input until a human capture confirms.
        db.execute(
            "UPDATE scanner_recognition_job SET require_confirmation=false,result=%s WHERE id=%s",
            (
                Jsonb({"confidence": ".8", "card_number": "MODEL-A", "parallel_name": "Silver"}),
                job["id"],
            ),
        )
        valuation_job = db.execute(
            "SELECT id FROM valuation_job WHERE collection_item_id=%s ORDER BY created_at DESC LIMIT 1",
            (item,),
        ).fetchone()[0]
    worker = AutomatedValuationWorker(app.state.database, get_settings())
    assert client.portal.call(worker._load_identity, valuation_job)["card_number"] == "MODEL-A"
    current = client.get(f"/v1/capture/items/{item}", headers=headers).json()
    supplement = upload(
        client, headers, ("back",), target_item_id=item, expected_revision=current["revision"]
    ).json()
    succeed(supplement)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE scanner_recognition_job SET result=%s WHERE id=%s",
            (
                Jsonb(
                    {"confidence": ".8", "card_number": "MODEL-SUP", "parallel_name": "Unselected"}
                ),
                supplement["id"],
            ),
        )
    body = (
        {"accepted_identity": {**current["identity"], "card_number": "ACCEPTED"}} if accept else {}
    )
    response = client.post(
        f"/v1/capture/jobs/{supplement['id']}/confirm", headers=headers, json=body
    )
    assert response.status_code == 201, response.text
    identity = client.portal.call(worker._load_identity, valuation_job)
    assert identity["card_number"] == ("ACCEPTED" if accept else "HUMAN-A"), identity
    assert identity["parallel_name"] == "Gold"


def test_capture_decode_uses_shared_generation_budget_and_is_retryable(client, monkeypatch):
    from contextlib import contextmanager
    from card_platform import image_variants

    _, headers = account(client)

    @contextmanager
    def busy_slot():
        raise TimeoutError("generation budget exhausted")
        yield

    monkeypatch.setattr(image_variants, "generation_slot", busy_slot)
    response = upload(client, headers)
    assert response.status_code == 503, response.text
    assert response.headers["retry-after"] == "2"
