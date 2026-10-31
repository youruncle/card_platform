"""Real PostgreSQL, isolated schema, no application/knowledge database fixtures."""
import importlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest


@pytest.fixture
def store(tmp_path):
    url = os.environ.get('INTAKE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires isolated INTAKE_TEST_DATABASE_URL')
    schema = 'intake_test_' + uuid4().hex
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as connection:
        connection.execute(f'CREATE SCHEMA {schema}')
        connection.execute(f'SET search_path TO {schema},public')
        migration = Path(__file__).resolve().parents[3] / 'db/migrations/0087_knowledge_intake_queue.sql'
        assert migration.is_file(), 'durable intake queue migration is missing'
        connection.execute(migration.read_text())
        queue = importlib.import_module('card_platform.knowledge_intake_queue').IntakeQueue(connection)
        target = dict(target_hash='a' * 64, identity={'player_name': 'Alpha'}, card_id='official:1')
        queue.create_batch('pilot', dict(id=str(uuid4()), version_number=23, checksum='b' * 64),
                           dict(targets=[target], selected_targets=1), tmp_path)
        try:
            yield queue, connection
        finally:
            connection.execute(f'DROP SCHEMA {schema} CASCADE')


def test_repeated_init_preserves_finished_tasks_and_rejects_changed_scope(store):
    queue, _ = store
    batch = queue.batch('pilot')
    task = queue.claim('pilot')
    assert queue.finish(task['id'], task['lease_token'], dict(status='no_results', attempts=[], candidates=[]))
    plan = dict(targets=[task['target']], selected_targets=1)
    queue.create_batch('pilot', batch['publication'], plan, batch['data_root'])
    assert queue.claim('pilot') is None
    with pytest.raises(ValueError, match='frozen'):
        queue.create_batch('pilot', {**batch['publication'], 'checksum': 'c' * 64}, plan, batch['data_root'])


def test_expired_lease_is_reclaimed_and_old_worker_cannot_finish(store):
    queue, connection = store
    first = queue.claim('pilot')
    connection.execute("UPDATE knowledge_intake_task SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                       (first['id'],))
    second = queue.claim('pilot')
    assert second['id'] == first['id'] and second['lease_token'] != first['lease_token']
    assert not queue.finish(first['id'], first['lease_token'], dict(status='no_results'))
    assert queue.finish(second['id'], second['lease_token'], dict(status='no_results', candidates=[]))


def test_active_lease_is_not_claimed_twice(store):
    queue, _ = store
    assert queue.claim('pilot') is not None
    assert queue.claim('pilot') is None


def test_blocked_source_does_not_spin_and_reports_pending_candidates(store):
    queue, _ = store
    task = queue.claim('pilot')
    assert queue.finish(task['id'], task['lease_token'], dict(status='blocked', attempts=[{'outcome': 'blocked'}]))
    assert queue.claim('pilot') is None
    report = queue.report('pilot')
    assert report['tasks'] == {'blocked': 1}
    assert report['published_images'] == 0 and report['knowledge_complete'] is False


def test_candidate_cannot_be_exported_until_reviewed(store):
    queue, _ = store
    task = queue.claim('pilot')
    result = dict(status='captured', candidates=[dict(content_sha256='d' * 64, images=[], source_url='x')])
    assert queue.finish(task['id'], task['lease_token'], result)
    assert queue.report('pilot')['candidates'] == {'pending_review': 1}
    assert queue.accepted('pilot') == []


def test_error_retry_budget_is_finite(store):
    queue, connection = store
    for _ in range(3):
        task = queue.claim('pilot', max_attempts=3)
        assert task is not None
        assert queue.finish(task['id'], task['lease_token'], dict(status='error', diagnostic='temporary'))
        connection.execute("UPDATE knowledge_intake_task SET available_at=now()-interval '1 second'")
    assert queue.claim('pilot', max_attempts=3) is None


def test_retry_keeps_prior_attempt_evidence_and_actual_request_counts(store):
    queue, connection = store
    first = queue.claim('pilot')
    queue.finish(first['id'], first['lease_token'], dict(status='error', http_requests=2,
                 attempts=[{'query': 'first', 'diagnostic': 'timeout'}]))
    connection.execute("UPDATE knowledge_intake_task SET available_at=now()-interval '1 second'")
    second = queue.claim('pilot')
    queue.finish(second['id'], second['lease_token'], dict(status='no_results', http_requests=3,
                 attempts=[{'query': 'second', 'outcome': 'no_results'}]))
    report = queue.report('pilot')
    assert report['http_requests'] == 5 and report['attempt_records'] == 2
    events = connection.execute('SELECT result FROM knowledge_intake_attempt ORDER BY created_at').fetchall()
    assert events[0]['result']['attempts'][0]['diagnostic'] == 'timeout'


def test_review_file_cannot_modify_a_candidate_from_another_batch(store, tmp_path):
    queue, _ = store
    from card_platform.knowledge_intake_sources import content_hash
    batch = queue.batch('pilot')
    task = queue.claim('pilot')
    queue.create_batch('other', batch['publication'],
                       dict(targets=[task['target']], selected_targets=1), tmp_path / 'other')
    other = queue.claim('other')
    payload = dict(source_url='https://cardhobby.com.cn/market/item/123', images=[])
    payload['content_sha256'] = content_hash(payload)
    queue.finish(other['id'], other['lease_token'], dict(status='captured', candidates=[payload]))
    candidate = queue.candidates('other')[0]
    review = dict(candidate_sha256=payload['content_sha256'], decision='rejected',
                  reviewer='auditor', reason='wrong product')
    with pytest.raises(ValueError, match='unknown_candidate'):
        queue.review('pilot', candidate['id'], review)
    assert queue.candidates('other')[0]['decision'] == 'pending_review'
