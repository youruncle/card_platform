"""Real sessions, database ownership and image bytes, using a disposable database."""
import os
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from card_platform.config import get_settings
from card_platform.main import app

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires migrated test PostgreSQL')


@pytest.mark.parametrize("side", ["front", "back"])
def test_private_images_require_owner_and_preserve_view_rotate(monkeypatch, side):
    monkeypatch.setenv('DATABASE_URL', os.environ['TEST_DATABASE_URL'])
    for name in ('VALUATION_WORKER_ENABLED', 'RECOGNITION_WORKER_ENABLED', 'SCANNER_WORKER_ENABLED', 'PURCHASE_COST_WORKER_ENABLED'):
        monkeypatch.setenv(name, 'false')
    monkeypatch.setenv('AUTH_REGISTRATION_ENABLED', 'true')
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
    get_settings.cache_clear()
    image_dir = get_settings().resolved_upload_dir
    image_dir.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as owner:
        other, anonymous = TestClient(app), TestClient(app)
        for client in (owner, other):
            response = client.post('/v1/auth/register', json={'email': f'private-image-{uuid4()}@example.invalid', 'password': 'Test-password-long-enough!', 'display_name': 'Private image test'})
            assert response.status_code == 201, response.text
        owner_id = owner.get('/v1/auth/me').json()['id']
        response = owner.post('/v1/portfolio/items', json={'owner_id': owner_id, 'card': {'player_name': f'Private image {uuid4()}', 'brand_name': 'Test', 'season_start': 2026, 'product_name': 'Test'}})
        assert response.status_code == 201, response.text
        item = response.json()['id']
        pixels = np.full((150, 100, 3), 160, dtype=np.uint8)
        pixels[20:40, 10:30] = (20, 70, 210)
        ok, encoded = cv2.imencode('.png', pixels)
        assert ok
        response = owner.post(f'/v1/portfolio/items/{item}/images?side={side}', files={'image': ('card.png', encoded.tobytes(), 'image/png')})
        assert response.status_code == 201, response.text
        url = response.json()['image_url']
        # The old StaticFiles mount returns 200 to anonymous and other users.
        for method in ('GET', 'HEAD'):
            assert anonymous.request(method, url).status_code == 401
            assert other.request(method, url).status_code == 404
        downloaded = owner.get(url)
        assert downloaded.status_code == 200
        assert cv2.imdecode(np.frombuffer(downloaded.content, dtype=np.uint8), cv2.IMREAD_COLOR) is not None
        assert downloaded.headers['cache-control'] == 'private, no-store'
        assert 'cookie' in downloaded.headers['vary'].lower()
        assert owner.head(url).status_code == 200
        assert owner.head(url).content == b''
        assert other.get(url, headers={'Range': 'bytes=0-10', 'If-None-Match': downloaded.headers.get('etag', '')}).status_code == 404
        rotated = owner.post(f'/v1/portfolio/items/{item}/images/{side}/rotate?degrees=90')
        assert rotated.status_code == 200, rotated.text
        new_url = rotated.json()['image_url']
        assert owner.get(new_url).status_code == 200
        assert other.get(new_url).status_code == 404
        assert owner.get(url).status_code == 404
        # A filesystem orphan or a symlink must not expose files outside the upload directory.
        orphan = image_dir / f'orphan-{uuid4()}.png'
        orphan.write_bytes(encoded.tobytes())
        try:
            assert owner.get('/uploads/' + orphan.name).status_code == 404
            actual = image_dir / new_url.rsplit('/', 1)[-1]
            actual.unlink()
            actual.symlink_to(orphan)
            # Symlinks (including an in-root target) must not be served.
            assert owner.get(new_url).status_code == 404
            actual.unlink()
            actual.write_bytes(encoded.tobytes())
            assert owner.request('DELETE', f'/v1/portfolio/items/{item}', json={'owner_id': owner_id, 'reason': 'private image test'}).status_code == 204
            assert owner.get(new_url).status_code == 404
            owner.post('/v1/auth/logout')
            assert owner.get(new_url).status_code == 401
        finally:
            orphan.unlink(missing_ok=True)
            (image_dir / new_url.rsplit('/', 1)[-1]).unlink(missing_ok=True)
    get_settings.cache_clear()
