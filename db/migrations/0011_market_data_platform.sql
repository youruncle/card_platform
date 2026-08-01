BEGIN;

CREATE TABLE market_identity_cache (
    identity_fingerprint text PRIMARY KEY,
    identity_version integer NOT NULL DEFAULT 1 CHECK (identity_version > 0),
    normalized_identity jsonb NOT NULL,
    freshness text NOT NULL DEFAULT 'insufficient' CHECK (
        freshness IN ('fresh', 'cached', 'stale', 'source_limited', 'insufficient')
    ),
    evidence_count integer NOT NULL DEFAULT 0 CHECK (evidence_count >= 0),
    popularity_score integer NOT NULL DEFAULT 0 CHECK (popularity_score >= 0),
    last_requested_at timestamptz,
    last_refresh_started_at timestamptz,
    last_success_at timestamptz,
    next_refresh_at timestamptz,
    data_as_of timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE market_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_fingerprint text NOT NULL REFERENCES market_identity_cache(identity_fingerprint),
    provider_code text NOT NULL REFERENCES data_source(code),
    external_sale_id text NOT NULL,
    raw_title text NOT NULL,
    sale_price numeric(18, 2) NOT NULL CHECK (sale_price > 0),
    shipping_price numeric(18, 2) NOT NULL DEFAULT 0 CHECK (shipping_price >= 0),
    currency char(3) NOT NULL,
    occurred_at timestamptz NOT NULL,
    source_url text NOT NULL,
    evidence_level text NOT NULL,
    grade_company text,
    grade_value numeric(5, 2),
    match_confidence numeric(5, 4) NOT NULL DEFAULT 0.5 CHECK (
        match_confidence >= 0 AND match_confidence <= 1
    ),
    identity_classification jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_version integer NOT NULL DEFAULT 1 CHECK (evidence_version > 0),
    fetched_at timestamptz NOT NULL DEFAULT now(),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (identity_fingerprint, provider_code, external_sale_id)
);

CREATE INDEX market_evidence_identity_occurred_idx
    ON market_evidence (identity_fingerprint, occurred_at DESC);
CREATE INDEX market_evidence_provider_seen_idx
    ON market_evidence (provider_code, last_seen_at DESC);

CREATE TABLE market_identity_binding (
    collection_item_id uuid PRIMARY KEY REFERENCES collection_item(id) ON DELETE CASCADE,
    identity_fingerprint text NOT NULL REFERENCES market_identity_cache(identity_fingerprint),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX market_identity_binding_fingerprint_idx
    ON market_identity_binding (identity_fingerprint);

CREATE TABLE market_refresh_job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_fingerprint text NOT NULL REFERENCES market_identity_cache(identity_fingerprint),
    requested_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'source_limited', 'insufficient', 'failed')
    ),
    reason text NOT NULL DEFAULT 'stale',
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    provider_code text REFERENCES data_source(code),
    not_before timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    error_message text,
    result_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX market_refresh_job_active_identity_uidx
    ON market_refresh_job (identity_fingerprint)
    WHERE status IN ('queued', 'running');
CREATE INDEX market_refresh_job_claim_idx
    ON market_refresh_job (status, not_before, created_at);

CREATE TABLE provider_state (
    provider_code text PRIMARY KEY REFERENCES data_source(code),
    circuit_state text NOT NULL DEFAULT 'closed' CHECK (
        circuit_state IN ('closed', 'open', 'half_open')
    ),
    concurrent_limit integer NOT NULL DEFAULT 1 CHECK (concurrent_limit > 0),
    min_interval_ms integer NOT NULL DEFAULT 1000 CHECK (min_interval_ms >= 0),
    error_budget integer NOT NULL DEFAULT 5 CHECK (error_budget > 0),
    active_requests integer NOT NULL DEFAULT 0 CHECK (active_requests >= 0),
    consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    request_count bigint NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    success_count bigint NOT NULL DEFAULT 0 CHECK (success_count >= 0),
    http_403_count bigint NOT NULL DEFAULT 0 CHECK (http_403_count >= 0),
    http_429_count bigint NOT NULL DEFAULT 0 CHECK (http_429_count >= 0),
    challenge_count bigint NOT NULL DEFAULT 0 CHECK (challenge_count >= 0),
    cache_hit_count bigint NOT NULL DEFAULT 0 CHECK (cache_hit_count >= 0),
    coalesced_job_count bigint NOT NULL DEFAULT 0 CHECK (coalesced_job_count >= 0),
    total_duration_ms bigint NOT NULL DEFAULT 0 CHECK (total_duration_ms >= 0),
    last_request_at timestamptz,
    last_success_at timestamptz,
    circuit_opened_at timestamptz,
    circuit_until timestamptz,
    retry_after_until timestamptz,
    last_error text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO provider_state (
    provider_code, concurrent_limit, min_interval_ms, error_budget
)
SELECT code,
       CASE WHEN code = 'cardhobby' THEN 1 ELSE 2 END,
       CASE WHEN code = 'cardhobby' THEN 1500 ELSE 500 END,
       5
FROM data_source
ON CONFLICT (provider_code) DO NOTHING;

ALTER TABLE market_comp
    ADD COLUMN IF NOT EXISTS market_evidence_id uuid REFERENCES market_evidence(id);

-- Existing comps remain authoritative history. They receive a stable legacy identity
-- and are copied once; later refreshes only insert/upsert evidence and never delete it.
INSERT INTO market_identity_cache (
    identity_fingerprint, normalized_identity, freshness, evidence_count,
    last_success_at, data_as_of, next_refresh_at
)
SELECT
    'legacy:' || mc.card_definition_id::text,
    jsonb_build_object('card_definition_id', mc.card_definition_id, 'migration', 'market_comp'),
    'cached',
    count(*),
    max(mc.created_at),
    max(mc.occurred_at),
    now()
FROM market_comp mc
WHERE mc.sale_price > 0
GROUP BY mc.card_definition_id
ON CONFLICT (identity_fingerprint) DO NOTHING;

INSERT INTO market_evidence (
    identity_fingerprint, provider_code, external_sale_id, raw_title,
    sale_price, shipping_price, currency, occurred_at, source_url,
    evidence_level, grade_company, grade_value, match_confidence,
    identity_classification, raw_payload, fetched_at, first_seen_at, last_seen_at
)
SELECT
    'legacy:' || mc.card_definition_id::text,
    ds.code,
    COALESCE(mc.external_id, 'market_comp:' || mc.id::text),
    COALESCE(mc.listing_title, 'Legacy market comparable'),
    mc.sale_price,
    mc.shipping_price,
    mc.currency,
    mc.occurred_at,
    COALESCE(mc.source_url, 'urn:market-comp:' || mc.id::text),
    CASE WHEN ds.code = 'cardhobby'
         THEN 'legacy_cardhobby_sale'
         ELSE 'legacy_market_comp'
    END,
    mc.grade_company,
    mc.grade_value,
    mc.match_confidence,
    jsonb_build_object(
        'comparable_tier', mc.comparable_tier,
        'similarity', mc.similarity,
        'classification', mc.classification
    ),
    jsonb_build_object('market_comp_id', mc.id),
    mc.created_at,
    mc.created_at,
    mc.created_at
FROM market_comp mc
JOIN data_source ds ON ds.id = mc.data_source_id
WHERE mc.sale_price > 0
ON CONFLICT (identity_fingerprint, provider_code, external_sale_id) DO NOTHING;

UPDATE market_comp mc
SET market_evidence_id = me.id
FROM data_source ds, market_evidence me
WHERE ds.id = mc.data_source_id
  AND me.identity_fingerprint = 'legacy:' || mc.card_definition_id::text
  AND me.provider_code = ds.code
  AND me.external_sale_id = COALESCE(mc.external_id, 'market_comp:' || mc.id::text)
  AND mc.market_evidence_id IS NULL;

COMMIT;
