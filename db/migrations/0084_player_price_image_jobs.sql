BEGIN;
-- Public candidate work, never private inventory or valuation acceptance.
CREATE TABLE player_price_image_job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    player_id uuid NOT NULL REFERENCES player(id),
    source_id text NOT NULL,
    observation_sha256 text NOT NULL CHECK (observation_sha256 ~ '^[a-f0-9]{64}$'),
    observation jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    priority integer NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','leased','retry','captured','rejected')),
    lease_token uuid,
    lease_until timestamptz,
    last_attempt_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    receipt jsonb,
    receipt_sha256 text CHECK (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source_id, observation_sha256),
    CHECK ((status='leased') = (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK ((status='captured') = (receipt IS NOT NULL AND receipt_sha256 IS NOT NULL))
);
CREATE INDEX player_price_image_job_due_idx ON player_price_image_job(next_attempt_at,player_id)
    WHERE status IN ('pending','retry','leased');
CREATE INDEX player_price_image_job_player_idx ON player_price_image_job(player_id,last_attempt_at);

-- Preserve old evidence, but only the latest observed source facts can run.
CREATE TABLE player_price_image_current (
    source_id text PRIMARY KEY,
    job_id uuid NOT NULL UNIQUE REFERENCES player_price_image_job(id),
    last_seen_at timestamptz NOT NULL
);

-- Reservation survives expiry/failure; do not refund uncertain paid requests.
CREATE TABLE player_price_image_attempt (
    lease_token uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES player_price_image_job(id),
    budget_day date NOT NULL,
    reserved_calls integer NOT NULL CHECK (reserved_calls=2),
    actual_calls integer CHECK (actual_calls BETWEEN 0 AND reserved_calls),
    trace_sha256 text CHECK (trace_sha256 ~ '^[a-f0-9]{64}$'),
    status text NOT NULL DEFAULT 'leased' CHECK(status IN ('leased','expired','captured','failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX player_price_image_attempt_day_idx ON player_price_image_attempt(budget_day);

-- Only a trusted server workflow writes approvals; there is no public endpoint.
CREATE TABLE player_price_image_review_approval (
    job_id uuid NOT NULL REFERENCES player_price_image_job(id),
    review_sha256 text NOT NULL CHECK (review_sha256 ~ '^[a-f0-9]{64}$'),
    receipt_sha256 text NOT NULL CHECK (receipt_sha256 ~ '^[a-f0-9]{64}$'),
    reviewer_kind text NOT NULL CHECK (reviewer_kind='codex_visual_review'),
    approved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(job_id,review_sha256)
);
COMMIT;
