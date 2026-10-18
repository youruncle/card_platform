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


def _token(client, sid):
    import hashlib, json
    snapshot = client.get(f'/v1/flatbed/sessions/{sid}').json().get('confirmed_sources')
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


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
    first = client.post(f'/v1/flatbed/sessions/{sid}/submit', json={'confirmation_token': _token(client, sid)})
    assert first.status_code == 200, first.text
    assert client.post(f'/v1/flatbed/sessions/{sid}/submit').json() == first.json()


def test_changed_crop_requires_reconfirmation_without_enqueuing(capture):
    client, sid, _, root = capture
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        relative = db.execute('SELECT r.crop_path FROM flatbed_scan_region r JOIN flatbed_scan_sheet s ON s.id=r.sheet_id WHERE s.session_id=%s LIMIT 1', (sid,)).fetchone()[0]
    path = root / relative
    path.write_bytes(path.read_bytes() + b'changed-after-review')
    response = client.post(f'/v1/flatbed/sessions/{sid}/submit', json={'confirmation_token': _token(client, sid)})
    assert response.status_code == 409, response.text
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        assert db.execute('SELECT count(*) FROM flatbed_pair_proposal WHERE session_id=%s AND scanner_job_id IS NOT NULL', (sid,)).fetchone()[0] == 0


def test_legacy_confirmation_cannot_bypass_source_pinning(capture):
    client, sid, _, _ = capture
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        # Simulate a paired session saved by the previous release.
        db.execute("UPDATE flatbed_scan_session SET confirmed_sources=NULL WHERE id=%s", (sid,))
    response = client.post(f'/v1/flatbed/sessions/{sid}/submit', json={'confirmation_token': _token(client, sid)})
    assert response.status_code == 409, response.text


def test_rescan_invalidates_confirmation_and_old_region_ids(capture):
    client, sid, old_pairs, _ = capture
    response = client.post(f'/v1/flatbed/sessions/{sid}/sheets/back', files={'image': ('new.png', sheet_image(10), 'image/png')})
    assert response.status_code == 201, response.text
    assert client.get(f'/v1/flatbed/sessions/{sid}').json().get('confirmed_sources') is None
    assert client.put(f'/v1/flatbed/sessions/{sid}/pairs', json={'pairs': old_pairs}).status_code == 404
    assert client.post(f'/v1/flatbed/sessions/{sid}/submit').status_code == 409


def test_late_submit_cannot_use_another_tabs_new_confirmation(capture):
    import hashlib, json
    client, sid, _, _ = capture
    old = client.get(f'/v1/flatbed/sessions/{sid}').json()['confirmed_sources']
    old_token = hashlib.sha256(json.dumps(old, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    response = client.post(f'/v1/flatbed/sessions/{sid}/sheets/back', files={'image': ('new.png', sheet_image(10), 'image/png')})
    assert response.status_code == 201
    proposed = client.post(f'/v1/flatbed/sessions/{sid}/pair').json()
    pairs = [{'front_region_id': p['front_id'], 'back_region_id': p['back_id']} for p in proposed['pairs']]
    fresh = client.put(f'/v1/flatbed/sessions/{sid}/pairs', json={'pairs': pairs})
    assert fresh.status_code == 200, fresh.text
    response = client.post(f'/v1/flatbed/sessions/{sid}/submit', json={'confirmation_token': old_token})
    assert response.status_code == 409, response.text
    accepted = client.post(f'/v1/flatbed/sessions/{sid}/submit', json={'confirmation_token': fresh.json()['confirmation_token']})
    assert accepted.status_code == 200, accepted.text


def test_restored_selected_pairs_keep_copy_ambiguity(capture, monkeypatch):
    from card_platform.flatbed_matching import propose_pairs
    client, sid, _, _ = capture
    def ambiguous(*args):
        result = propose_pairs(*args)
        for candidate in result['candidates']:
            candidate['identity_status'] = 'consistent'
            candidate['identity_reason_codes'] = []
        for pair in result['pairs']:
            pair['identity_status'] = 'unknown'
            pair['identity_reason_codes'] = ['same_design_copy_unverified']
            pair['requires_review'] = True
        return result
    monkeypatch.setattr('card_platform.flatbed_service.propose_pairs', ambiguous)
    assert client.post(f'/v1/flatbed/sessions/{sid}/pair').status_code == 200
    restored = client.get(f'/v1/flatbed/sessions/{sid}').json()
    selected = [p for p in restored['pairs'] if p['selected']]
    assert selected and all(p['identity_status'] == 'unknown' for p in selected)
    assert all('same_design_copy_unverified' in p['identity_reason_codes'] for p in selected)


def test_missing_confirmation_token_cannot_submit_new_jobs(capture):
    client, sid, _, _ = capture
    response = client.post(f'/v1/flatbed/sessions/{sid}/submit')
    assert response.status_code == 409, response.text


def test_manual_switch_does_not_inherit_old_candidates_consistent_status(capture):
    client, sid, pairs, _ = capture
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as db:
        db.execute("UPDATE flatbed_pair_proposal SET identity_status='consistent',identity_reason_codes='[]' WHERE session_id=%s", (sid,))
    switched = [{'front_region_id': pair['front_region_id'], 'back_region_id': pairs[-i-1]['back_region_id']} for i,pair in enumerate(pairs)]
    assert client.put(f'/v1/flatbed/sessions/{sid}/pairs', json={'pairs': switched}).status_code == 200
    selected = [p for p in client.get(f'/v1/flatbed/sessions/{sid}').json()['pairs'] if p['selected']]
    assert all(p['identity_status'] == 'unknown' and 'manual_pair_requires_recheck' in p['identity_reason_codes'] for p in selected)


@pytest.mark.parametrize('operation', ['layout', 'reprocess'])
def test_old_layout_or_reprocess_cannot_resurrect_replaced_scan(capture, operation):
    client, sid, _, _ = capture
    old = next(s for s in client.get(f'/v1/flatbed/sessions/{sid}').json()['sheets'] if s['side']=='back')
    new = client.post(f'/v1/flatbed/sessions/{sid}/sheets/back', files={'image': ('new.png', sheet_image(10), 'image/png')})
    assert new.status_code == 201
    if operation == 'layout':
        response = client.put(f"/v1/flatbed/sheets/{old['id']}/layout", json={'regions':[{'points':[{'x':.1,'y':.1},{'x':.4,'y':.1},{'x':.4,'y':.5},{'x':.1,'y':.5}]}]})
    else:
        response = client.post(f"/v1/flatbed/sheets/{old['id']}/reprocess")
    assert response.status_code == 409, response.text
    current = next(s for s in client.get(f'/v1/flatbed/sessions/{sid}').json()['sheets'] if s['side']=='back')
    assert current['id'] == new.json()['id']
