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


def test_unknown_identity_can_be_imported_without_guesses_or_pair_checkbox(client):
    _, h, j = seed(client)
    response = client.post('/v1/capture/tasks/' + j['id'] + '/confirm', headers=h, json={'card': {'player_name': '', 'brand_name': None, 'product_name': '', 'season_start': None, 'is_rookie': None, 'is_autograph': None, 'is_memorabilia': None, 'is_patch': None, 'has_inscription': None}, 'is_numbered': None})
    assert response.status_code == 201, response.text
    item_id = response.json()['id']
    view = client.get('/v1/portfolio/items/' + item_id + '/detail', headers=h)
    assert view.status_code == 200, view.text
    item = view.json()['item']
    assert item['player_name'] is None and item['brand_name'] is None
    assert item['product_name'] is None and item['season_start'] is None
    assert item['identity_review_required'] is True
    assert set(item['identity_missing_fields']) >= {'player_name', 'brand_name', 'product_name', 'season_start', 'pair_validation'}
    assert item['image_front_url'] and item['image_back_url']
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        assert db.execute('SELECT count(*) FROM valuation_job WHERE collection_item_id=%s', (item_id,)).fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM automatic_purchase_cost WHERE item_id=%s', (item_id,)).fetchone()[0] == 0
        stored = db.execute('SELECT p.full_name,b.name,cs.product_name,cs.season_start FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id JOIN player p ON p.id=cd.player_id JOIN card_set cs ON cs.id=cd.card_set_id JOIN brand b ON b.id=cs.brand_id WHERE ci.id=%s', (item_id,)).fetchone()
        assert stored == (None, None, None, None)


def test_anonymous_cards_have_separate_catalog_rows_and_can_later_be_completed(client):
    user, h = account(client)
    created = [client.post('/v1/portfolio/items',headers=h,json={'owner_id':user['id'],'card':{}}) for _ in range(2)]
    assert all(r.status_code==201 for r in created), [r.text for r in created]
    ids = [r.json()['id'] for r in created]
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        rows = db.execute('SELECT card_definition_id FROM collection_item WHERE id=ANY(%s::uuid[])',(ids,)).fetchall()
        assert len({r[0] for r in rows})==2
    full = {'player_name':f'Known {uuid4()}','brand_name':'Topps','product_name':'Chrome','season_start':2025,'is_rookie':False,'is_autograph':False,'is_memorabilia':False,'is_patch':False,'has_inscription':False,'is_numbered':False,'autograph_type':'none'}
    corrected = client.post('/v1/portfolio/identity-review/items/'+ids[0]+'/confirm',headers=h,json={'identity':full,'reason':'根据真实卡片补全身份'})
    assert corrected.status_code==201,corrected.text
    item = client.get('/v1/portfolio/items/'+ids[0]+'/detail',headers=h).json()['item']
    assert item['player_name']==full['player_name']
    assert not set(item['identity_missing_fields']) & {'player_name','brand_name','product_name','season_start'}
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        assert db.execute('SELECT count(*) FROM automatic_purchase_cost WHERE item_id=%s',(ids[0],)).fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM valuation_job WHERE collection_item_id=%s',(ids[0],)).fetchone()[0]==1
    other = client.get('/v1/portfolio/items/'+ids[1]+'/detail',headers=h).json()['item']
    assert other['player_name'] is None and other['identity_review_required']


@pytest.mark.parametrize('empty_field',['brand_name','player_name','product_name'])
def test_skybox_owner_can_clear_uncertain_identity_and_still_save(client, empty_field):
    from test_pair_integrity import skybox_manufacturer_layers
    _,h,j=seed(client)
    path='/v1/capture/tasks/'+j['id']
    hashes=client.get(path,headers=h).json()['image_content_hashes']
    raw=skybox_manufacturer_layers();raw['confidence']='.9';raw['pair_validation']['image_content_hashes']=hashes
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        db.execute("UPDATE scanner_recognition_job SET status='succeeded',require_confirmation=true,result=%s WHERE id=%s",(Jsonb(raw),j['id']))
    card={k:raw.get(k) for k in ['player_name','brand_name','product_name','season_start','season_end','card_number','insert_name']}
    card[empty_field]=None
    saved=client.post(path+'/confirm',headers=h,json={'card':card})
    assert saved.status_code==201,saved.text
    item=client.get('/v1/portfolio/items/'+saved.json()['id']+'/detail',headers=h).json()['item']
    assert item[empty_field] is None and item['identity_review_required']


@pytest.mark.parametrize('only_missing',[False,True])
@pytest.mark.parametrize('prior_pending',[False,True])
def test_supplement_completing_core_identity_resumes_deferred_jobs_once(client,only_missing,prior_pending):
    from test_capture_postgres import upload,succeed
    user,h=account(client)
    card={'player_name':f'Partial {uuid4()}','brand_name':f'Brand {uuid4()}','product_name':'Test','season_start':None}
    created=client.post('/v1/portfolio/items',headers=h,json={'owner_id':user['id'],'card':card});assert created.status_code==201,created.text
    item=created.json()['id'];target=client.get('/v1/capture/items/'+item,headers=h).json()
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        assert db.execute('SELECT count(*) FROM valuation_job WHERE collection_item_id=%s',(item,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM automatic_purchase_cost WHERE item_id=%s',(item,)).fetchone()[0]==0
    if prior_pending:
        with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
            db.execute("INSERT INTO valuation_job(collection_item_id,status,result_summary) VALUES(%s,'insufficient_evidence',%s)",(item,Jsonb({'identity_resolution_status':'pending'})))
    job=upload(client,h,('back',),target_item_id=item,expected_revision=target['revision']).json();succeed(job)
    body={'accepted_identity':{'season_start':2025} if only_missing else {**target['identity'],'season_start':2025}}
    saved=client.post('/v1/capture/jobs/'+job['id']+'/confirm',headers=h,json=body)
    assert saved.status_code==201,saved.text
    assert client.post('/v1/capture/jobs/'+job['id']+'/confirm',headers=h,json=body).status_code==201
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        assert db.execute("SELECT count(*) FROM valuation_job WHERE collection_item_id=%s AND status='queued'",(item,)).fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM valuation_job WHERE collection_item_id=%s',(item,)).fetchone()[0]==(2 if prior_pending else 1)
        assert db.execute('SELECT count(*) FROM automatic_purchase_cost WHERE item_id=%s',(item,)).fetchone()[0]==1
