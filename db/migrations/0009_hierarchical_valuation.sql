BEGIN;

ALTER TABLE market_comp
    ADD COLUMN IF NOT EXISTS source_reliability numeric(5,4)
        CHECK (source_reliability BETWEEN 0 AND 1),
    ADD COLUMN IF NOT EXISTS similarity numeric(5,4)
        CHECK (similarity BETWEEN 0 AND 1),
    ADD COLUMN IF NOT EXISTS comparable_tier text,
    ADD COLUMN IF NOT EXISTS classification jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS market_comp_tier_time_idx
    ON market_comp (card_definition_id, comparable_tier, occurred_at DESC);

COMMIT;
