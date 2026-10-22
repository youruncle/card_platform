BEGIN;
ALTER TABLE player_price_state ADD COLUMN photo_recheck_requested_at timestamptz;
-- Immutable public-photo history, independent of model jobs, owners and budgets.
-- These rows can exclude a price but can never supply one.
CREATE TABLE player_price_photo_capture (
    capture_manifest_sha256 text PRIMARY KEY CHECK (capture_manifest_sha256 ~ '^[a-f0-9]{64}$'),
    source_id text NOT NULL,
    observation_sha256 text NOT NULL CHECK (observation_sha256 ~ '^[a-f0-9]{64}$'),
    observation jsonb NOT NULL,
    captured_at timestamptz NOT NULL,
    pair_a text NOT NULL CHECK (pair_a ~ '^[a-f0-9]{64}$'),
    pair_b text NOT NULL CHECK (pair_b ~ '^[a-f0-9]{64}$'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (pair_a < pair_b)
);
CREATE INDEX player_price_photo_capture_source_idx ON player_price_photo_capture(source_id);
CREATE INDEX player_price_photo_capture_pair_idx ON player_price_photo_capture(pair_a,pair_b);
CREATE INDEX player_price_image_job_pair_idx ON player_price_image_job(
    LEAST(receipt->'images'->0->>'sha256',receipt->'images'->1->>'sha256'),
    GREATEST(receipt->'images'->0->>'sha256',receipt->'images'->1->>'sha256')
) WHERE status='captured';
COMMIT;
