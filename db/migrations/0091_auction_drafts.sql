-- Private preparation only: this schema has no published/queued auction state.
CREATE TABLE auction_draft (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    status text NOT NULL DEFAULT 'draft' CHECK (status = 'draft'),
    title text NOT NULL DEFAULT '' CHECK (length(title) <= 200),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 8000),
    condition_notes text NOT NULL DEFAULT '' CHECK (length(condition_notes) <= 2000),
    selected_photo_ids jsonb NOT NULL DEFAULT '[]',
    photos jsonb NOT NULL DEFAULT '[]',
    identity jsonb NOT NULL,
    generation_basis jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (owner_id, item_id),
    CHECK (jsonb_typeof(photos) = 'array' AND jsonb_array_length(photos) <= 12),
    CHECK (jsonb_typeof(selected_photo_ids) = 'array' AND jsonb_array_length(selected_photo_ids) <= 12)
);
