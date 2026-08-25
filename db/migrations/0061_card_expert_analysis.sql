BEGIN;

CREATE TABLE card_analysis_job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    requested_source text NOT NULL DEFAULT 'user',
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);

CREATE UNIQUE INDEX card_analysis_job_active_uidx
    ON card_analysis_job (owner_id, collection_item_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX card_analysis_job_owner_created_idx
    ON card_analysis_job (owner_id, created_at DESC);

CREATE TABLE card_analysis_snapshot (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL UNIQUE REFERENCES card_analysis_job(id) ON DELETE RESTRICT,
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    analysis_version integer NOT NULL CHECK (analysis_version > 0),
    source text NOT NULL,
    model_name text NOT NULL,
    identity_version_id uuid REFERENCES card_identity_version(id) ON DELETE SET NULL,
    identity_summary text NOT NULL,
    identity_confidence numeric(5,4) NOT NULL CHECK (identity_confidence BETWEEN 0 AND 1),
    currency char(3),
    fair_value_low numeric(18,2),
    fair_value numeric(18,2),
    fair_value_high numeric(18,2),
    valuation_confidence numeric(5,4) CHECK (valuation_confidence BETWEEN 0 AND 1),
    evidence_summary jsonb NOT NULL DEFAULT '[]'::jsonb,
    value_drivers jsonb NOT NULL DEFAULT '[]'::jsonb,
    risks_and_conflicts jsonb NOT NULL DEFAULT '[]'::jsonb,
    investment_conclusion text,
    recommended_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (collection_item_id, analysis_version),
    CHECK (
        status = 'failed'
        OR (currency IS NOT NULL AND fair_value_low <= fair_value AND fair_value <= fair_value_high)
    )
);

CREATE INDEX card_analysis_snapshot_item_created_idx
    ON card_analysis_snapshot (collection_item_id, created_at DESC);

CREATE OR REPLACE FUNCTION reject_card_analysis_snapshot_mutation() RETURNS trigger AS $$
BEGIN
    IF pg_trigger_depth() = 1 THEN
        RAISE EXCEPTION 'card_analysis_snapshot rows are immutable';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER card_analysis_snapshot_immutable
    BEFORE UPDATE OR DELETE ON card_analysis_snapshot
    FOR EACH ROW EXECUTE FUNCTION reject_card_analysis_snapshot_mutation();

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'card_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON card_analysis_job TO card_platform;
        GRANT SELECT, INSERT ON card_analysis_snapshot TO card_platform;
    END IF;
END;
$$;

COMMIT;
