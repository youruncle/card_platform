BEGIN;

ALTER TABLE card_identity_version
    ADD COLUMN IF NOT EXISTS identity_fingerprint text,
    ADD COLUMN IF NOT EXISTS confirmed_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS confirmation_reason text,
    ADD COLUMN IF NOT EXISTS supersedes_id uuid REFERENCES card_identity_version(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS card_identity_version_confirmed_idx
    ON card_identity_version (collection_item_id, version DESC)
    WHERE review_status = 'confirmed';

CREATE TABLE card_identity_change_audit (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    identity_version_id uuid NOT NULL REFERENCES card_identity_version(id) ON DELETE RESTRICT,
    actor_id uuid REFERENCES app_user(id) ON DELETE SET NULL,
    reason text NOT NULL,
    before_state jsonb NOT NULL,
    after_state jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE market_evidence_identity_decision (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    identity_version_id uuid NOT NULL REFERENCES card_identity_version(id) ON DELETE RESTRICT,
    market_evidence_id uuid NOT NULL REFERENCES market_evidence(id) ON DELETE CASCADE,
    decision text NOT NULL CHECK (decision IN ('accepted', 'rejected')),
    reason_codes jsonb NOT NULL CHECK (jsonb_typeof(reason_codes) = 'array'),
    classification jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (identity_version_id, market_evidence_id)
);

CREATE INDEX market_evidence_identity_decision_item_idx
    ON market_evidence_identity_decision (collection_item_id, created_at DESC);

CREATE TABLE market_identity_rebind_audit (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    identity_version_id uuid NOT NULL REFERENCES card_identity_version(id) ON DELETE RESTRICT,
    old_fingerprint text,
    new_fingerprint text NOT NULL,
    actor_id uuid REFERENCES app_user(id) ON DELETE SET NULL,
    accepted_count integer NOT NULL DEFAULT 0,
    rejected_count integer NOT NULL DEFAULT 0,
    duplicate_count integer NOT NULL DEFAULT 0,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- A marketplace sale is globally singular. Preserve references on the oldest row,
-- then remove redundant cache copies before installing the global invariant.
WITH ranked AS (
    SELECT id, first_value(id) OVER (
        PARTITION BY provider_code, external_sale_id ORDER BY first_seen_at, id
    ) AS keeper
    FROM market_evidence
), duplicates AS (
    SELECT id, keeper FROM ranked WHERE id <> keeper
)
UPDATE market_comp mc SET market_evidence_id = d.keeper
FROM duplicates d WHERE mc.market_evidence_id = d.id;

WITH ranked AS (
    SELECT id, row_number() OVER (
        PARTITION BY provider_code, external_sale_id ORDER BY first_seen_at, id
    ) AS rank
    FROM market_evidence
)
DELETE FROM market_evidence me USING ranked r WHERE me.id = r.id AND r.rank > 1;

CREATE UNIQUE INDEX market_evidence_provider_sale_uidx
    ON market_evidence (provider_code, external_sale_id);

CREATE OR REPLACE FUNCTION reject_identity_version_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'card_identity_version rows are immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER card_identity_version_immutable
    BEFORE UPDATE OR DELETE ON card_identity_version
    FOR EACH ROW EXECUTE FUNCTION reject_identity_version_mutation();

COMMIT;
