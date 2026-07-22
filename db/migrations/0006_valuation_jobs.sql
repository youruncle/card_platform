BEGIN;

CREATE TABLE valuation_job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN (
        'queued', 'identifying', 'searching', 'calculating',
        'succeeded', 'insufficient_evidence', 'failed'
    )),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_message text,
    result_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX valuation_job_queue_idx ON valuation_job (status, created_at);
CREATE INDEX valuation_job_item_idx ON valuation_job (collection_item_id, created_at DESC);
CREATE UNIQUE INDEX valuation_job_active_item_uidx ON valuation_job (collection_item_id)
WHERE status IN ('queued', 'identifying', 'searching', 'calculating');

INSERT INTO data_source (code, name, source_type, base_url)
VALUES ('automated_market_search', 'Automated Market Search', 'marketplace_search', NULL)
ON CONFLICT (code) DO NOTHING;

INSERT INTO valuation_job (collection_item_id, status)
SELECT ci.id, 'queued'
FROM collection_item ci
WHERE NOT EXISTS (
    SELECT 1 FROM valuation_snapshot vs WHERE vs.collection_item_id = ci.id
);

COMMIT;
