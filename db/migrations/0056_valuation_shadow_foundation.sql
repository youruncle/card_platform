BEGIN;

CREATE TABLE card_identity_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    identity jsonb NOT NULL,
    field_provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    conflicts jsonb NOT NULL DEFAULT '[]'::jsonb,
    review_status text NOT NULL DEFAULT 'pending' CHECK (
        review_status IN ('pending', 'confirmed', 'rejected')
    ),
    source text NOT NULL DEFAULT 'valuation_worker',
    confirmed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (collection_item_id, version)
);

CREATE INDEX card_identity_version_item_created_idx
    ON card_identity_version (collection_item_id, created_at DESC);

CREATE TABLE valuation_shadow_result (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    valuation_job_id uuid REFERENCES valuation_job(id) ON DELETE SET NULL,
    identity_version_id uuid REFERENCES card_identity_version(id) ON DELETE SET NULL,
    model_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('estimated', 'manual_review', 'insufficient')),
    currency char(3) NOT NULL,
    low_value numeric(18,2),
    fair_value numeric(18,2),
    high_value numeric(18,2),
    confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_count integer NOT NULL DEFAULT 0 CHECK (evidence_count >= 0),
    review_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (low_value IS NULL AND fair_value IS NULL AND high_value IS NULL)
        OR (low_value <= fair_value AND fair_value <= high_value)
    )
);

CREATE INDEX valuation_shadow_item_created_idx
    ON valuation_shadow_result (collection_item_id, created_at DESC);

COMMIT;
