BEGIN;
CREATE TABLE IF NOT EXISTS knowledge_intake_batch (
    batch_id text PRIMARY KEY,
    publication jsonb NOT NULL,
    plan_summary jsonb NOT NULL,
    plan_sha256 text NOT NULL CHECK (plan_sha256 ~ '^[a-f0-9]{64}$'),
    data_root text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS knowledge_intake_task (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id text NOT NULL REFERENCES knowledge_intake_batch(batch_id),
    target_hash text NOT NULL CHECK (target_hash ~ '^[a-f0-9]{64}$'),
    target jsonb NOT NULL,
    priority integer NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','captured','no_results','blocked','error')),
    attempts integer NOT NULL DEFAULT 0,
    lease_token uuid,
    lease_expires_at timestamptz,
    available_at timestamptz NOT NULL DEFAULT now(),
    result jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id,target_hash)
);
CREATE INDEX IF NOT EXISTS knowledge_intake_task_claim_idx
    ON knowledge_intake_task(batch_id,status,priority,available_at);
CREATE TABLE IF NOT EXISTS knowledge_intake_attempt (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES knowledge_intake_task(id),
    attempt_number integer NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id,attempt_number)
);
CREATE TABLE IF NOT EXISTS knowledge_intake_candidate (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES knowledge_intake_task(id),
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    payload jsonb NOT NULL,
    decision text NOT NULL DEFAULT 'pending_review'
        CHECK (decision IN ('pending_review','accepted','rejected')),
    review jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz,
    UNIQUE (task_id,content_sha256)
);
CREATE TABLE IF NOT EXISTS knowledge_intake_review_event (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id uuid NOT NULL REFERENCES knowledge_intake_candidate(id),
    content_sha256 text NOT NULL,
    review jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
