"""Real sessions + temporary PostgreSQL validate authorization before cached media."""
import os
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from card_platform.config import get_settings
from card_platform.main import app

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires migrated test PostgreSQL')


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', os.environ['TEST_DATABASE_URL'])
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
    monkeypatch.setenv('AUTH_REGISTRATION_ENABLED', 'true')
    get_settings.cache_clear()
    with TestClient(app) as owner:
        other, anonymous = TestClient(app), TestClient(app)
        for client in (owner, other):
            response = client.post('/v1/auth/register', json={'email':f'media-{uuid4()}@example.invalid','password':'Test-password-long-enough!','display_name':'Media test'})
            assert response.status_code == 201, response.text
        yield owner, other, anonymous
    get_settings.cache_clear()


def picture():
    pixels = np.full((1800,1200,3), 127, np.uint8)
    pixels[100:200,200:400] = (40,60,200)
    return cv2.imencode('.png',pixels)[1].tobytes()


def test_crop_api_is_private_and_source_immutable(clients):
    owner, other, anonymous = clients
    source = picture()
    response = owner.post('/v1/capture/crop-proposals', files={'image':('source.png',source,'image/png')})
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert owner.get(proposal['source_url']).content == source
    for url in (proposal['source_url'],proposal['preview_url']):
        for method in ('GET','HEAD'):
            assert anonymous.request(method,url).status_code == 401
            assert other.request(method,url).status_code == 404
            good = owner.request(method,url)
            assert good.status_code == 200
            assert good.headers['cache-control'] == 'private, no-store'
        assert owner.head(url).content == b''
    apply_url = '/v1/capture/crop-proposals/'+proposal['proposal_id']+'/apply'
    assert other.post(apply_url,json={'quad':None}).status_code == 404
    assert owner.post(apply_url,json={'quad':[[0,0],[1,1],[1,0],[0,1]]}).status_code == 422
    assert owner.post(apply_url,json={'quad':None,'source_path':'/etc/passwd'}).status_code == 422
    restored = owner.post(apply_url,json={'quad':None})
    assert restored.status_code == 200, restored.text
    assert owner.get(restored.json()['preview_url']).content == source
    assert owner.get(proposal['source_url']).content == source
    owner.post('/v1/auth/logout')
    assert owner.get(proposal['preview_url']).status_code == 401


def test_variants_recheck_owner_deletion_revision_and_head(clients):
    owner, other, anonymous = clients
    owner_id = owner.get('/v1/auth/me').json()['id']
    response = owner.post('/v1/portfolio/items',json={'owner_id':owner_id,'card':{'player_name':f'Media {uuid4()}','brand_name':'Test','season_start':2026,'product_name':'Test'}})
    assert response.status_code == 201, response.text
    item_id = response.json()['id']
    uploaded = owner.post(f'/v1/portfolio/items/{item_id}/images?side=front', files={'image':('source.png',picture(),'image/png')})
    assert uploaded.status_code == 201, uploaded.text
    revision = uploaded.json()['image_url'].rsplit('/',1)[-1]
    base = f'/v1/portfolio/items/{item_id}/images/front'
    url = f'{base}?variant=thumb&revision={revision}'
    for method in ('GET','HEAD'):
        assert anonymous.request(method,url).status_code == 401
        assert other.request(method,url).status_code == 404
    downloaded = owner.get(url)
    assert downloaded.status_code == 200, downloaded.text
    assert cv2.imdecode(np.frombuffer(downloaded.content,np.uint8),cv2.IMREAD_COLOR).shape[:2] == (320,213)
    assert downloaded.headers['cache-control'] == 'private, no-store'
    assert owner.head(url).content == b''
    assert int(owner.head(url).headers['content-length']) == len(downloaded.content)
    assert other.get(url,headers={'Range':'bytes=0-10','If-None-Match':downloaded.headers.get('etag','')}).status_code == 404
    assert owner.get(base+'?variant=arbitrary').status_code == 422
    rotated = owner.post(f'/v1/portfolio/items/{item_id}/images/front/rotate?degrees=90')
    assert rotated.status_code == 200
    for method in ('GET','HEAD'):
        stale = owner.request(method,url)
        assert stale.status_code == 409
        assert stale.headers['cache-control'] == 'private, no-store'
    new_url = base+'?variant=preview&revision='+rotated.json()['image_url'].rsplit('/',1)[-1]
    assert owner.get(new_url).status_code == 200
    assert owner.request('DELETE',f'/v1/portfolio/items/{item_id}',json={'owner_id':owner_id,'reason':'test'}).status_code == 204
    for method in ('GET','HEAD'):
        assert owner.request(method,new_url).status_code == 404


def test_crop_target_and_oversize_header_contract(clients):
    import struct
    import zlib
    owner, _, _ = clients
    response = owner.post('/v1/capture/crop-proposals',data={'target':'slab'},files={'image':('source.png',picture(),'image/png')})
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert proposal['target'] == 'slab' and proposal['quad'] is None
    assert proposal['status'] == 'manual_required'
    assert proposal['algorithm_version'] == 'card-quad-v1'
    assert proposal['target_semantics'] == 'framing_hint'
    assert owner.get(proposal['preview_url']).content == picture()
    invalid = owner.post('/v1/capture/crop-proposals',data={'target':'unsafe'},files={'image':('source.png',picture(),'image/png')})
    assert invalid.status_code == 422
    body = struct.pack('>IIBBBBB',10000,5000,8,2,0,0,0)
    huge = b'\x89PNG\r\n\x1a\n'+struct.pack('>I',len(body))+b'IHDR'+body+struct.pack('>I',zlib.crc32(b'IHDR'+body))+b'\0\0\0\0IEND\xaeB`\x82'
    rejected = owner.post('/v1/capture/crop-proposals',files={'image':('large.png',huge,'image/png')})
    assert rejected.status_code == 422, rejected.text
    assert '40 megapixels' in rejected.json()['detail']
