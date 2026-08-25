BEGIN;

CREATE TABLE IF NOT EXISTS ebay_account_deletion_notification (
    notification_id text PRIMARY KEY,
    event_date timestamptz,
    publish_date timestamptz,
    publish_attempt_count integer NOT NULL DEFAULT 1 CHECK (publish_attempt_count > 0),
    payload_sha256 char(64) NOT NULL,
    subject_hashes jsonb NOT NULL DEFAULT '[]'::jsonb,
    signature_key_id text NOT NULL,
    signature_valid boolean NOT NULL,
    processing_status text NOT NULL CHECK (
        processing_status IN ('processed', 'failed')
    ),
    deleted_market_evidence_count integer NOT NULL DEFAULT 0 CHECK (
        deleted_market_evidence_count >= 0
    ),
    deleted_market_comp_count integer NOT NULL DEFAULT 0 CHECK (
        deleted_market_comp_count >= 0
    ),
    first_received_at timestamptz NOT NULL DEFAULT now(),
    last_received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);

CREATE INDEX IF NOT EXISTS ebay_account_deletion_received_idx
    ON ebay_account_deletion_notification (last_received_at DESC);

COMMIT;
