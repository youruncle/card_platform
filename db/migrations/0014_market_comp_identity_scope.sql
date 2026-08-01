BEGIN;

ALTER TABLE market_comp
    DROP CONSTRAINT IF EXISTS market_comp_data_source_id_external_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS market_comp_identity_source_external_uidx
    ON market_comp (card_definition_id, data_source_id, external_id);

COMMIT;
