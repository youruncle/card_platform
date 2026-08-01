BEGIN;

CREATE TABLE IF NOT EXISTS cardhobby_import_batch (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id),
    status text NOT NULL DEFAULT 'preview',
    source_url text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    confirmed_at timestamptz
);

CREATE TABLE IF NOT EXISTS cardhobby_import_item (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES cardhobby_import_batch(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES app_user(id),
    external_order_id text,
    cardhobby_item_id text NOT NULL,
    item_url text NOT NULL,
    title text NOT NULL,
    image_urls jsonb NOT NULL DEFAULT '[]'::jsonb,
    purchased_at timestamptz,
    sale_price numeric(18,2) CHECK (sale_price >= 0),
    shipping_price numeric(18,2) NOT NULL DEFAULT 0 CHECK (shipping_price >= 0),
    currency char(3) NOT NULL DEFAULT 'CNY',
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    proposed_item jsonb,
    recognition_warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL DEFAULT 'preview',
    collection_item_id uuid REFERENCES collection_item(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS cardhobby_import_owner_item_uidx
    ON cardhobby_import_item (owner_id, cardhobby_item_id);

CREATE INDEX IF NOT EXISTS cardhobby_import_batch_idx
    ON cardhobby_import_item (batch_id, status);

COMMIT;
