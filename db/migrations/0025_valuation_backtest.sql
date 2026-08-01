BEGIN;

CREATE TABLE valuation_backtest_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version text NOT NULL,
    provider_scope jsonb NOT NULL DEFAULT '[]'::jsonb,
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics jsonb NOT NULL,
    sample_count integer NOT NULL CHECK (sample_count >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX valuation_backtest_run_created_idx
    ON valuation_backtest_run (created_at DESC);

COMMIT;
