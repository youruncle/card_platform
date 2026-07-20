BEGIN;

ALTER TABLE market_comp RENAME COLUMN fx_rate_to_cny TO fx_rate_to_base;

CREATE INDEX market_comp_quality_idx
    ON market_comp (card_definition_id, match_confidence, occurred_at DESC);

COMMIT;
