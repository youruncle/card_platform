import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock
from card_platform.flatbed_service import FlatbedService
from card_platform.card_orientation import AppleVisionOrientationClassifier

from card_platform.config import get_settings
from card_platform.main import app
from test_flatbed_postgres import register, sheet_image

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires migrated PostgreSQL')

@pytest.fixture
def capture(tmp_path, monkeypatch):
    monkeypatch.setattr(FlatbedService, "ocr_signals", AsyncMock(return_value={}))
    monkeypatch.setattr(AppleVisionOrientationClassifier, "classify", AsyncMock(return_value=None))
    os.environ['DATABASE_URL'] = os.environ['TEST_DATABASE_URL']
    os.environ['FLATBED_SCAN_DIR'] = str(tmp_path / 'flatbed')
    os.environ['SCANNER_JOB_DIR'] = str(tmp_path / 'jobs')
    get_settings.cache_clear()
    with TestClient(app) as client:
        register(client, 'source-pinning')
        sid = client.post('/v1/flatbed/sessions', json={'dpi': 300}).json()['id']
        for side in ('front', 'back'):
            response = client.post(f'/v1/flatbed/sessions/{sid}/sheets/{side}', files={'image': (side+'.png', sheet_image(), 'image/png')})
            assert response.status_code == 201, response.text
        response = client.post(f'/v1/flatbed/sessions/{sid}/pair')
        assert response.status_code == 200, response.text
        pairs = [{'front_region_id': p['front_id'], 'back_region_id': p['back_id']} for p in response.json()['pairs']]
        response = client.put(f'/v1/flatbed/sessions/{sid}/pairs', json={'pairs': pairs})
        assert response.status_code == 200, response.text
        yield client, sid, pairs, tmp_path / 'flatbed'
    get_settings.cache_clear()


def test_explicit_next_batch_does_not_reuse_unfinished_session(capture):
    client, sid, _, _ = capture
    same = client.post('/v1/flatbed/sessions', json={'dpi': 300})
    assert same.json()['id'] == sid
    new = client.post('/v1/flatbed/sessions', json={'dpi': 300, 'new_batch': True})
    assert new.status_code == 201
    assert new.json()['id'] != sid


def test_confirmed_sources_are_persisted_and_submit_is_idempotent(capture):
    client, sid, _, _ = capture
    restored = client.get(f'/v1/flatbed/sessions/{sid}').json()
    snapshot = restored.get('confirmed_sources')
    assert snapshot and snapshot['version'] == 1
    assert {s['side'] for s in snapshot['sheets']} == {'front', 'back'}
    assert all(len(p['front_sha256']) == len(p['back_sha256']) == 64 for p in snapshot['pairs'])
    first = client.post(f'/v1/flatbed/sessions/{sid}/submit')
    assert first.status_code == 200, first.text
    assert client.post(f'/v1/flatbed/sessions/{sid}/submit').json() == first.json()


def test_changed_crop_requires_reconfirmation_without_enqueuing(capture):
    client, sid, _, root = capture
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        relative = db.execute('SELECT r.crop_path FROM flatbed_scan_region r JOIN flatbed_scan_sheet s ON s.id=r.sheet_id WHERE s.session_id=%s LIMIT 1', (sid,)).fetchone()[0]
    path = root / relative
    path.write_bytes(path.read_bytes() + b'changed-after-review')
    response = client.post(f'/v1/flatbed/sessions/{sid}/submit')
    assert response.status_code == 409, response.text
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        assert db.execute('SELECT count(*) FROM flatbed_pair_proposal WHERE session_id=%s AND scanner_job_id IS NOT NULL', (sid,)).fetchone()[0] == 0


def test_legacy_confirmation_cannot_bypass_source_pinning(capture):
    client, sid, _, _ = capture
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        # Simulate a paired session saved by the previous release.
        db.execute("UPDATE flatbed_scan_session SET confirmed_sources=NULL WHERE id=%s", (sid,))
    response = client.post(f'/v1/flatbed/sessions/{sid}/submit')
    assert response.status_code == 409, response.text


def test_rescan_invalidates_confirmation_and_old_region_ids(capture):
    client, sid, old_pairs, _ = capture
    response = client.post(f'/v1/flatbed/sessions/{sid}/sheets/back', files={'image': ('new.png', sheet_image(10), 'image/png')})
    assert response.status_code == 201, response.text
    assert client.get(f'/v1/flatbed/sessions/{sid}').json().get('confirmed_sources') is None
    assert client.put(f'/v1/flatbed/sessions/{sid}/pairs', json={'pairs': old_pairs}).status_code == 404
    assert client.post(f'/v1/flatbed/sessions/{sid}/submit').status_code == 409
