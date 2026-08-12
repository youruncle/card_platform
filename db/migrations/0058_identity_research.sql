BEGIN;

CREATE TABLE identity_research_case (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN (
        'identity_research_required', 'candidate_identity_ready',
        'awaiting_confirmation', 'confirmed_but_no_exact_sales', 'shadow_estimated'
    )),
    trigger_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_version text NOT NULL,
    attempt_count integer NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX identity_research_case_active_uidx
    ON identity_research_case (collection_item_id)
    WHERE status IN ('identity_research_required', 'candidate_identity_ready', 'awaiting_confirmation');

CREATE TABLE identity_research_candidate (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    research_case_id uuid NOT NULL REFERENCES identity_research_case(id) ON DELETE CASCADE,
    rank integer NOT NULL CHECK (rank > 0),
    identity jsonb NOT NULL,
    field_provenance jsonb NOT NULL,
    visual_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    checklist_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    market_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    validation_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    is_valid boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (research_case_id, rank)
);

ALTER TABLE valuation_snapshot
    ADD COLUMN IF NOT EXISTS identity_compatibility text NOT NULL DEFAULT 'unknown'
        CHECK (identity_compatibility IN ('unknown', 'compatible', 'incompatible')),
    ADD COLUMN IF NOT EXISTS identity_compatibility_reasons jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE OR REPLACE VIEW latest_item_valuation AS
SELECT DISTINCT ON (collection_item_id)
    collection_item_id, valued_at, method, currency, low_value, fair_value,
    high_value, confidence
FROM valuation_snapshot
WHERE identity_compatibility <> 'incompatible'
ORDER BY collection_item_id, valued_at DESC, created_at DESC;

COMMIT;
