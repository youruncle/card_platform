CREATE TABLE IF NOT EXISTS chatgpt_purchase_lookup (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_id uuid NOT NULL REFERENCES app_user(id),
 connection_id uuid NOT NULL REFERENCES chatgpt_connection(id),
 item_id uuid REFERENCES collection_item(id),
 expected_revision text,
 keywords jsonb NOT NULL DEFAULT '[]',
 idempotency_key uuid NOT NULL,
 request_hash text NOT NULL,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','account_required','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0,
 lease_id uuid,
 lease_until timestamptz,
 result jsonb NOT NULL DEFAULT '{}',
 error_code text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS chatgpt_purchase_lookup_queue ON chatgpt_purchase_lookup(status,created_at);
