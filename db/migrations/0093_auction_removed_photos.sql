-- Retain immutable private media so draft removals are reversible.
ALTER TABLE auction_draft ADD COLUMN IF NOT EXISTS removed_photos jsonb NOT NULL DEFAULT '[]'
    CHECK (jsonb_typeof(removed_photos) = 'array');
