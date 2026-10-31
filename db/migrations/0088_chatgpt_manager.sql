BEGIN;

CREATE TABLE IF NOT EXISTS chatgpt_oauth_client (
    client_id text PRIMARY KEY,
    metadata jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chatgpt_connection (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id),
    client_id text NOT NULL REFERENCES chatgpt_oauth_client(client_id),
    scopes text[] NOT NULL CHECK (cardinality(scopes) > 0 AND scopes <@
        ARRAY['cards:read','cards:write','transactions:write','research:run']::text[]),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '30 days',
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(id,owner_id)
);

CREATE TABLE IF NOT EXISTS chatgpt_oauth_request (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id text NOT NULL REFERENCES chatgpt_oauth_client(client_id),
    params jsonb NOT NULL,
    owner_id uuid REFERENCES app_user(id),
    consent_session_id uuid,
    csrf_hash char(64),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '5 minutes',
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chatgpt_oauth_code (
    code_hash char(64) PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES chatgpt_connection(id),
    params jsonb NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '5 minutes',
    consumed_at timestamptz
);

CREATE TABLE IF NOT EXISTS chatgpt_oauth_token (
    token_hash char(64) PRIMARY KEY,
    connection_id uuid NOT NULL REFERENCES chatgpt_connection(id),
    kind text NOT NULL CHECK (kind IN ('access','refresh')),
    issuer text NOT NULL,
    resource text NOT NULL,
    scopes text[] NOT NULL CHECK (cardinality(scopes) > 0 AND scopes <@
        ARRAY['cards:read','cards:write','transactions:write','research:run']::text[]),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chatgpt_token_connection_idx ON chatgpt_oauth_token(connection_id);

CREATE TABLE IF NOT EXISTS chatgpt_operation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id),
    connection_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('import','update','transaction','trash','restore','publish_research')),
    idempotency_key uuid NOT NULL,
    payload jsonb NOT NULL,
    payload_hash char(64) NOT NULL,
    expected_revision text NOT NULL,
    status text NOT NULL DEFAULT 'prepared' CHECK (status IN
        ('prepared','approved','executing','succeeded','failed','expired','cancelled')),
    approved_payload_hash char(64),
    approved_session_id uuid,
    approved_at timestamptz,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '15 minutes',
    result_ref jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    UNIQUE(owner_id,kind,idempotency_key),
    FOREIGN KEY(connection_id,owner_id) REFERENCES chatgpt_connection(id,owner_id)
);

CREATE TABLE IF NOT EXISTS chatgpt_batch (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id),
    connection_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('capture','research')),
    idempotency_key uuid NOT NULL,
    payload_hash char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,idempotency_key),
    UNIQUE(id,owner_id),
    FOREIGN KEY(connection_id,owner_id) REFERENCES chatgpt_connection(id,owner_id)
);
CREATE TABLE IF NOT EXISTS chatgpt_batch_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    item_ref jsonb NOT NULL,
    ordinal integer NOT NULL DEFAULT 0 CHECK (ordinal BETWEEN 0 AND 19),
    job_ref text,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN
        ('queued','running','review_required','succeeded','failed','blocked','cancelled')),
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 3),
    retryable boolean NOT NULL DEFAULT false,
    result_ref jsonb,
    error_code text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(batch_id,owner_id) REFERENCES chatgpt_batch(id,owner_id)
);
CREATE INDEX IF NOT EXISTS chatgpt_batch_owner_time_idx ON chatgpt_batch(owner_id,created_at DESC,id);

CREATE TABLE IF NOT EXISTS chatgpt_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES app_user(id),
    connection_id uuid NOT NULL,
    event_type text NOT NULL,
    target_id uuid,
    payload_hash char(64),
    result_code text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(connection_id,owner_id) REFERENCES chatgpt_connection(id,owner_id)
);

CREATE TABLE IF NOT EXISTS chatgpt_capture_intake (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES app_user(id),
    connection_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    request_hash char(64) NOT NULL,
    content_hash char(64) NOT NULL,
    manifest jsonb NOT NULL,
    analysis_results jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'queued' CHECK (status IN
        ('queued','running','succeeded','review_required','failed','blocked','cancelled')),
    lease_id uuid,
    started_at timestamptz,
    finished_at timestamptz,
    error_code text,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,idempotency_key),
    UNIQUE(id,owner_id),
    FOREIGN KEY(connection_id,owner_id) REFERENCES chatgpt_connection(id,owner_id)
);
CREATE TABLE IF NOT EXISTS chatgpt_capture_job (
    job_id uuid PRIMARY KEY REFERENCES scanner_recognition_job(id),
    intake_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 19),
    draft_metadata jsonb NOT NULL DEFAULT '{}',
    UNIQUE(intake_id,ordinal),
    FOREIGN KEY(intake_id,owner_id) REFERENCES chatgpt_capture_intake(id,owner_id)
);
CREATE INDEX IF NOT EXISTS chatgpt_capture_intake_claim_idx
    ON chatgpt_capture_intake(created_at) WHERE status IN ('queued','running');
ALTER TABLE chatgpt_capture_intake ADD COLUMN IF NOT EXISTS analysis_results jsonb NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS chatgpt_research_run (
    id uuid PRIMARY KEY,
    owner_id uuid NOT NULL REFERENCES app_user(id),
    connection_id uuid NOT NULL,
    item_id uuid NOT NULL REFERENCES collection_item(id),
    idempotency_key uuid NOT NULL,
    request_hash char(64) NOT NULL,
    target_binding char(64) NOT NULL,
    packet jsonb NOT NULL,
    assessment jsonb NOT NULL DEFAULT '{}',
    capture_manifest jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'queued' CHECK (status IN
      ('queued','running','review_required','completed','insufficient_evidence','blocked','failed')),
    lease_id uuid,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 3),
    started_at timestamptz,
    finished_at timestamptz,
    reviewed_at timestamptz,
    reviewed_session_id uuid,
    reviewed_digest char(64),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(owner_id,idempotency_key),
    UNIQUE(id,owner_id),
    FOREIGN KEY(connection_id,owner_id) REFERENCES chatgpt_connection(id,owner_id)
);
CREATE INDEX IF NOT EXISTS chatgpt_research_claim_idx ON chatgpt_research_run(created_at)
    WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS chatgpt_job_control (
    owner_id uuid NOT NULL REFERENCES app_user(id),
    job_id uuid NOT NULL,
    retry_count integer NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 3),
    PRIMARY KEY(owner_id,job_id)
);
CREATE TABLE IF NOT EXISTS chatgpt_job_retry (
    owner_id uuid NOT NULL REFERENCES app_user(id),
    idempotency_key uuid NOT NULL,
    job_id uuid NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(owner_id,idempotency_key)
);
ALTER TABLE chatgpt_batch_item ADD COLUMN IF NOT EXISTS ordinal integer NOT NULL DEFAULT 0 CHECK (ordinal BETWEEN 0 AND 19);
ALTER TABLE chatgpt_research_run ADD COLUMN IF NOT EXISTS pending_packet jsonb;
COMMIT;
