"""PostgreSQL task ledger, separate from published knowledge and user holdings."""
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from card_platform.knowledge_intake_sources import content_hash


class IntakeQueue:
    def __init__(self, connection):
        self.db = connection

    def batch(self, batch_id):
        row = self.db.execute('SELECT * FROM knowledge_intake_batch WHERE batch_id=%s',
                              (batch_id,)).fetchone()
        if not row:
            raise ValueError('unknown_intake_batch')
        return row

    def create_batch(self, batch_id, publication, plan, data_root):
        digest = content_hash(plan)
        root = str(Path(data_root).resolve())
        summary = {k: v for k, v in plan.items() if k != 'targets'}
        with self.db.transaction():
            self.db.execute('''INSERT INTO knowledge_intake_batch
                (batch_id,publication,plan_summary,plan_sha256,data_root) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (batch_id) DO NOTHING''',
                (batch_id, Jsonb(publication), Jsonb(summary), digest, root))
            current = self.batch(batch_id)
            if (current['publication'] != publication or current['plan_sha256'] != digest
                    or current['data_root'] != root):
                raise ValueError('frozen_batch_scope_or_cloud_baseline_changed')
            for priority, target in enumerate(plan['targets']):
                self.db.execute('''INSERT INTO knowledge_intake_task
                    (batch_id,target_hash,target,priority) VALUES (%s,%s,%s,%s)
                    ON CONFLICT (batch_id,target_hash) DO NOTHING''',
                    (batch_id, target['target_hash'], Jsonb(target), priority))
        return current

    def claim(self, batch_id, *, max_attempts=3, lease_seconds=3600):
        if not 1 <= max_attempts <= 10 or not 1 <= lease_seconds <= 7200:
            raise ValueError('invalid_queue_limits')
        self.batch(batch_id)
        self.db.execute('''UPDATE knowledge_intake_task SET status='error',lease_token=NULL,
            lease_expires_at=NULL,updated_at=now() WHERE batch_id=%s AND status='running'
            AND lease_expires_at<now() AND attempts >= %s''', (batch_id, max_attempts))
        return self.db.execute('''WITH selected AS (
            SELECT id FROM knowledge_intake_task WHERE batch_id=%s AND attempts<%s
            AND (status='queued' OR (status='error' AND available_at<=now())
                 OR (status='running' AND lease_expires_at<now()))
            ORDER BY priority FOR UPDATE SKIP LOCKED LIMIT 1)
            UPDATE knowledge_intake_task t SET status='running',attempts=t.attempts+1,
                lease_token=%s,lease_expires_at=now()+make_interval(secs=>%s),updated_at=now()
            FROM selected WHERE t.id=selected.id RETURNING t.*''',
            (batch_id, max_attempts, uuid4(), lease_seconds)).fetchone()

    def finish(self, task_id, lease_token, result):
        status = result.get('status')
        if status not in {'captured', 'no_results', 'blocked', 'error'}:
            raise ValueError('invalid_task_completion_status')
        with self.db.transaction():
            row = self.db.execute('''UPDATE knowledge_intake_task SET status=%s,result=%s,
                lease_token=NULL,lease_expires_at=NULL,
                available_at=now()+make_interval(secs=>60*power(2,attempts)::int),updated_at=now()
                WHERE id=%s AND status='running' AND lease_token=%s AND lease_expires_at>now()
                RETURNING id,attempts''', (status, Jsonb(result), task_id, lease_token)).fetchone()
            if not row:
                return False
            self.db.execute('''INSERT INTO knowledge_intake_attempt
                (task_id,attempt_number,result) VALUES (%s,%s,%s)''',
                (task_id, row['attempts'], Jsonb(result)))
            for candidate in result.get('candidates', []):
                self.db.execute('''INSERT INTO knowledge_intake_candidate
                    (task_id,content_sha256,payload) VALUES (%s,%s,%s)
                    ON CONFLICT (task_id,content_sha256) DO NOTHING''',
                    (task_id, candidate['content_sha256'], Jsonb(candidate)))
        return True

    def candidates(self, batch_id, *, decision=None):
        self.batch(batch_id)
        return self.db.execute('''SELECT c.*,t.target FROM knowledge_intake_candidate c
            JOIN knowledge_intake_task t ON t.id=c.task_id
            WHERE t.batch_id=%s AND (%s::text IS NULL OR c.decision=%s)
            ORDER BY t.priority,c.created_at,c.id''', (batch_id, decision, decision)).fetchall()

    def accepted(self, batch_id):
        return self.candidates(batch_id, decision='accepted')

    def review(self, batch_id, candidate_id, review):
        from card_platform.knowledge_intake_worker import validate_review
        with self.db.transaction():
            candidate = self.db.execute('''SELECT c.*,t.target,b.data_root FROM
                knowledge_intake_candidate c JOIN knowledge_intake_task t ON t.id=c.task_id
                JOIN knowledge_intake_batch b ON b.batch_id=t.batch_id
                WHERE c.id=%s AND t.batch_id=%s FOR UPDATE OF c''',
                (candidate_id, batch_id)).fetchone()
            if not candidate:
                raise ValueError('unknown_candidate')
            validate_review(candidate, review, Path(candidate['data_root']))
            self.db.execute('''INSERT INTO knowledge_intake_review_event
                (candidate_id,content_sha256,review) VALUES (%s,%s,%s)''',
                (candidate_id, candidate['content_sha256'], Jsonb(review)))
            self.db.execute('''UPDATE knowledge_intake_candidate SET decision=%s,review=%s,
                reviewed_at=now() WHERE id=%s''',
                (review['decision'], Jsonb(review), candidate_id))

    def report(self, batch_id):
        batch = self.batch(batch_id)
        tasks = {r['status']: r['n'] for r in self.db.execute('''SELECT status,count(*) n
            FROM knowledge_intake_task WHERE batch_id=%s GROUP BY status''', (batch_id,))}
        candidates = {r['decision']: r['n'] for r in self.db.execute('''SELECT decision,count(*) n
            FROM knowledge_intake_candidate c JOIN knowledge_intake_task t ON t.id=c.task_id
            WHERE t.batch_id=%s GROUP BY decision''', (batch_id,))}
        totals = self.db.execute('''SELECT count(DISTINCT image->>'sha256') AS images
            FROM knowledge_intake_candidate c JOIN knowledge_intake_task t ON t.id=c.task_id
            CROSS JOIN LATERAL jsonb_array_elements(c.payload->'images') image
            WHERE t.batch_id=%s''', (batch_id,)).fetchone()
        attempts = self.db.execute('''SELECT count(*) AS n,
            coalesce(sum((a.result->>'http_requests')::int),0) AS requests
            FROM knowledge_intake_attempt a JOIN knowledge_intake_task t ON t.id=a.task_id
            WHERE t.batch_id=%s''', (batch_id,)).fetchone()
        return dict(batch_id=batch_id, publication=batch['publication'],
                    plan=batch['plan_summary'], tasks=tasks, candidates=candidates,
                    captured_unique_images=totals['images'],
                    attempt_records=attempts['n'], http_requests=attempts['requests'],
                    accepted_candidates_unpublished=candidates.get('accepted', 0),
                    published_images=0, knowledge_complete=False,
                    meaning='acquisition/review ledger; cloud knowledge release is separate')
