ALTER TABLE collection_item
    ADD COLUMN holding_status text NOT NULL DEFAULT 'held'
    CHECK (holding_status IN ('held', 'sold'));
