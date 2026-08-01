CREATE TABLE IF NOT EXISTS card_catalog_snapshot (
    collection_item_id uuid PRIMARY KEY
        REFERENCES collection_item(id) ON DELETE CASCADE,
    result jsonb NOT NULL,
    verified_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS card_catalog_snapshot_verified_at_idx
    ON card_catalog_snapshot (verified_at DESC);
