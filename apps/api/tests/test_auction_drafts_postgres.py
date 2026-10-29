"""Real auth, PostgreSQL and photos; opt-in disposable database only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from io import BytesIO
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import psycopg
import pytest


@pytest.fixture(scope='session')
def auction_dsn():
    dsn = os.environ.get('AUCTION_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('requires disposable AUCTION_TEST_DATABASE_URL')
    if not urlsplit(dsn).path.lstrip('/').startswith('auction_test_'):
        pytest.fail('auction tests require a disposable database named auction_test_*')
    migrations = Path(__file__).resolve().parents[3] / 'db/migrations'
    with psycopg.connect(dsn, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('public.auction_draft')").fetchone()[0] is None:
            for file in sorted(migrations.glob('*.sql')):
                conn.execute(file.read_text())
    return dsn


@pytest.fixture
def clients(auction_dsn, tmp_path, monkeypatch):
    from card_platform.api import router as core_router
    from card_platform.auction_api import router as auction_router
    from card_platform.auth_api import router as auth_router
    from card_platform.config import get_settings
    from card_platform.db import Database
    monkeypatch.setenv('DATABASE_URL', auction_dsn)
    monkeypatch.setenv('UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setenv('PURCHASE_COST_ARCHIVE_DIR', str(tmp_path / 'archive'))
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
    get_settings.cache_clear()

    @asynccontextmanager
    async def lifespan(app):
        app.state.database = Database(get_settings())
        await app.state.database.open()
        yield
        await app.state.database.close()

    app = FastAPI(lifespan=lifespan)
    for router in (auth_router, core_router, auction_router):
        app.include_router(router)
    with TestClient(app) as owner:
        other, anon = TestClient(app), TestClient(app)
        for client in (owner, other):
            response = client.post('/v1/auth/register', json=dict(
                email=f'auction-{uuid4()}@example.invalid', password='Test-password-long-enough!', display_name='Auction test'))
            assert response.status_code == 201, response.text
        yield owner, other, anon
    get_settings.cache_clear()


def picture():
    output = BytesIO()
    Image.new('RGB', (60, 90), 'navy').save(output, format='PNG')
    return output.getvalue()


def card(client, *, with_photo=True):
    owner_id = client.get('/v1/auth/me').json()['id']
    result = client.post('/v1/portfolio/items', json=dict(owner_id=owner_id, card=dict(
        player_name=f'Auction {uuid4()}', brand_name='Topps', product_name='Chrome', season_start=2025)))
    assert result.status_code == 201, result.text
    item_id = result.json()['id']
    if with_photo:
        result = client.post(f'/v1/portfolio/items/{item_id}/images?side=front', files={'image': ('front.png', picture(), 'image/png')})
        assert result.status_code == 201, result.text
    return item_id


def draft(client, item):
    response = client.post(f'/v1/auction/items/{item}/draft')
    assert response.status_code == 200, response.text
    return response.json()


def test_idempotence_owner_isolation_private_media_and_original_unchanged(clients):
    owner, other, anon = clients
    item = card(owner)
    before = owner.get(f'/v1/portfolio/items/{item}/detail').json()
    created = draft(owner, item)
    assert draft(owner, item)['id'] == created['id']
    assert created['status'] == 'draft' and created['external']['supported'] is False
    assert 'filename' not in created['photos'][0]
    base = f'/v1/auction/drafts/{created["id"]}'
    image_url = created['photos'][0]['url']
    for path in (base, image_url):
        assert owner.get(path).status_code == 200
        assert other.get(path).status_code == 404
        assert anon.get(path).status_code == 401
        assert owner.get(path).headers['cache-control'] == 'private, no-store'
    assert owner.get(image_url).content == picture()
    assert owner.head(image_url).content == b''
    assert other.post(f'/v1/auction/items/{item}/draft').status_code == 404
    assert owner.get(f'/v1/portfolio/items/{item}/detail').json() == before
    assert draft(owner, card(owner))['id'] != created['id']
    # Later portfolio photo replacement cannot silently replace the auction snapshot.
    replacement = BytesIO()
    Image.new('RGB', (60, 90), 'red').save(replacement, format='PNG')
    changed = owner.post(f'/v1/portfolio/items/{item}/images?side=front', files={'image': ('red.png', replacement.getvalue(), 'image/png')})
    assert changed.status_code == 201
    assert owner.get(image_url).content == picture()
    owner_id = owner.get('/v1/auth/me').json()['id']
    assert owner.request('DELETE', f'/v1/portfolio/items/{item}', json=dict(owner_id=owner_id, reason='test')).status_code == 204
    assert owner.get(image_url).status_code == 404


def test_generate_edit_upload_preserves_revision_and_rejects_stale_update(clients, monkeypatch):
    owner, _, _ = clients
    from card_platform import auction_api
    async def failed_examples(*args):
        return [], 'unavailable'
    monkeypatch.setattr(auction_api, 'load_order_examples', failed_examples)
    created = draft(owner, card(owner))
    base = f'/v1/auction/drafts/{created["id"]}'
    response = owner.post(base+'/generate', json=dict(revision=created['revision'], selected_photo_ids=created['selected_photo_ids'], condition_notes='背面有白点', use_order_examples=True))
    assert response.status_code == 200, response.text
    generated = response.json()
    assert '背面有白点' in generated['description']
    assert generated['generation_basis']['examples_status'] == 'unavailable'
    response = owner.patch(base, json=dict(revision=created['revision'], title='旧版本覆盖'))
    assert response.status_code == 409
    response = owner.patch(base, json=dict(revision=generated['revision'], title='我的人工标题', description='人工描述'))
    assert response.status_code == 200, response.text
    edited = response.json()
    uploaded = owner.post(base+'/images', data=dict(revision=edited['revision'], side='back', source='redmi'), files={'image': ('new.png', picture(), 'image/png')})
    assert uploaded.status_code == 200, uploaded.text
    current = uploaded.json()
    assert current['title'] == '我的人工标题' and len(current['photos']) == 2
    assert current['photos'][1]['source'] == 'redmi'
    assert current['revision'] == edited['revision'] + 1
    assert current['selected_photo_ids'][-1] == current['photos'][1]['id']
    assert owner.get(base).json()['description'] == '人工描述'
    assert owner.patch(base, json=dict(revision=current['revision'], selected_photo_ids=[str(uuid4())])).status_code == 422
    assert owner.patch(base, json=dict(revision=current['revision'], status='published')).status_code == 422
    assert owner.post(base+'/cardhobby').status_code == 409
    assert owner.get(base).json()['status'] == 'draft'


def test_no_images_deleted_cards_and_upload_validation(clients):
    owner, other, _ = clients
    item = card(owner, with_photo=False)
    created = draft(owner, item)
    base = f'/v1/auction/drafts/{created["id"]}'
    payload = dict(revision=created['revision'], selected_photo_ids=[], condition_notes='', use_order_examples=False)
    assert owner.post(base+'/generate', json=payload).status_code == 422
    assert other.post(base+'/images', data=dict(revision=1, side='front', source='upload'), files={'image': ('new.png', picture(), 'image/png')}).status_code == 404
    assert owner.post(base+'/images', data=dict(revision=1, side='front', source='existing'), files={'image': ('new.png', picture(), 'image/png')}).status_code == 422
    assert owner.post(base+'/images', data=dict(revision=1, side='front', source='upload'), files={'image': ('bad.png', b'not an image', 'image/png')}).status_code == 422
    assert owner.get(base).json()['revision'] == 1
    owner_id = owner.get('/v1/auth/me').json()['id']
    assert owner.request('DELETE', f'/v1/portfolio/items/{item}', json=dict(owner_id=owner_id, reason='test')).status_code == 204
    assert owner.get(base).status_code == 404
    assert owner.patch(base, json=dict(revision=1, title='已删除卡片')).status_code == 404


def test_concurrent_creation_and_revision_only_one_update_wins(clients):
    owner, _, _ = clients
    item = card(owner)
    with ThreadPoolExecutor(2) as pool:
        copies = list(pool.map(lambda _: draft(owner, item), range(2)))
    assert copies[0]['id'] == copies[1]['id']
    base = f'/v1/auction/drafts/{copies[0]["id"]}'
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda text: owner.patch(base, json=dict(revision=1, title=text)), ['A', 'B']))
    assert sorted(r.status_code for r in responses) == [200, 409]


def test_automatic_generation_preserves_saved_and_unsaved_manual_text(clients):
    owner, _, _ = clients
    created = draft(owner, card(owner))
    base = f'/v1/auction/drafts/{created["id"]}'
    saved = owner.patch(base, json=dict(revision=1, title='已保存人工标题', description='已保存的瑕疵说明')).json()
    generated = owner.post(base+'/generate', json=dict(revision=saved['revision'], selected_photo_ids=saved['selected_photo_ids'],
                                                      automatic=True, use_order_examples=False))
    assert generated.status_code == 200, generated.text
    assert generated.json()['title'] == '已保存人工标题'
    assert generated.json()['description'] == '已保存的瑕疵说明'
    current = generated.json()
    updated = owner.post(base+'/generate', json=dict(revision=current['revision'], selected_photo_ids=current['selected_photo_ids'],
                                                    automatic=True, edited_title='本页未保存标题', edited_description='本页未保存说明', use_order_examples=False))
    assert updated.status_code == 200, updated.text
    assert owner.get(base).json()['title'] == '本页未保存标题'
    assert owner.get(base).json()['description'] == '本页未保存说明'
    assert set(updated.json()['generation_basis']['manual_fields']) == {'title', 'description'}
    explicit = owner.post(base+'/generate', json=dict(revision=updated.json()['revision'], selected_photo_ids=current['selected_photo_ids'], use_order_examples=False))
    assert explicit.status_code == 200 and explicit.json()['title'] != '本页未保存标题'


def test_upload_response_loss_replay_does_not_duplicate_photo_or_hide_conflict(clients):
    owner, _, _ = clients
    created = draft(owner, card(owner))
    base = f'/v1/auction/drafts/{created["id"]}'
    key = str(uuid4())
    def upload(request_id, revision=1, side='back'):
        return owner.post(base+'/images', data=dict(revision=revision, side=side, source='upload', request_id=request_id),
                          files={'image': ('back.png', picture(), 'image/png')})
    first = upload(key)
    assert first.status_code == 200, first.text
    replay = upload(key)
    assert replay.status_code == 200, replay.text
    assert replay.json()['revision'] == first.json()['revision']
    assert len(replay.json()['photos']) == 2
    assert upload(key, side='detail').status_code == 422
    assert upload(str(uuid4())).status_code == 409
    changed = owner.patch(base, json=dict(revision=first.json()['revision'], title='另一端修改'))
    assert changed.status_code == 200
    assert upload(key).status_code == 409
