CREATE TABLE IF NOT EXISTS chatgpt_market_run (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_id uuid NOT NULL REFERENCES app_user(id),
 connection_id uuid NOT NULL REFERENCES chatgpt_connection(id),
 idempotency_key uuid NOT NULL,
 request_hash text NOT NULL,
 identity jsonb NOT NULL,
 target_notes jsonb NOT NULL DEFAULT '[]',
 target_images jsonb NOT NULL DEFAULT '[]',
 queries jsonb NOT NULL DEFAULT '[]',
 candidates jsonb NOT NULL DEFAULT '[]',
 reviews jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS chatgpt_market_run_owner ON chatgpt_market_run(owner_id,created_at DESC);
