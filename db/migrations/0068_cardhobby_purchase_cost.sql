BEGIN;
CREATE TABLE cardhobby_purchase_order (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL REFERENCES app_user(id),
 external_order_id text NOT NULL, revision integer NOT NULL DEFAULT 1,
 capture jsonb NOT NULL, evidence_hash text NOT NULL, errors jsonb NOT NULL DEFAULT '[]',
 fetched_at timestamptz NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id, external_order_id), UNIQUE(id, owner_id)
);
CREATE TABLE cardhobby_order_evidence (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), order_id uuid NOT NULL REFERENCES cardhobby_purchase_order(id),
 evidence_hash text NOT NULL, capture jsonb NOT NULL, errors jsonb NOT NULL,
 fetched_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(order_id, evidence_hash)
);
CREATE TABLE cardhobby_purchase_unit (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), order_id uuid NOT NULL,
 owner_id uuid NOT NULL, item_id text NOT NULL, unit_index integer NOT NULL CHECK(unit_index >= 0),
 title text NOT NULL, unit_price numeric(18,2) NOT NULL CHECK(unit_price >= 0),
 shipping numeric(18,2) NOT NULL CHECK(shipping >= 0), active boolean NOT NULL DEFAULT true,
 FOREIGN KEY(order_id, owner_id) REFERENCES cardhobby_purchase_order(id, owner_id),
 UNIQUE(order_id, item_id, unit_index), UNIQUE(id, owner_id)
);
CREATE TABLE cardhobby_cost_assignment (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL REFERENCES app_user(id),
 collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE, unit_id uuid NOT NULL,
 transaction_id uuid NOT NULL REFERENCES card_transaction(id) ON DELETE CASCADE, evidence_hash text NOT NULL,
 card_price numeric(18,2) NOT NULL, shipping numeric(18,2) NOT NULL,
 confidence numeric(5,4) NOT NULL CHECK(confidence BETWEEN 0 AND 1), evidence jsonb NOT NULL,
 confirmation text NOT NULL CHECK(confirmation IN ('user_confirmed','automatic','manual_override')),
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(unit_id, owner_id) REFERENCES cardhobby_purchase_unit(id, owner_id),
 UNIQUE(collection_item_id), UNIQUE(unit_id)
);
CREATE TABLE cardhobby_cost_job (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL REFERENCES app_user(id),
 request_key uuid NOT NULL, target integer NOT NULL CHECK(target BETWEEN 1 AND 500),
 scan_limit integer NOT NULL CHECK(scan_limit BETWEEN 1 AND 2000),
 item_ids uuid[] NOT NULL, scanned integer NOT NULL DEFAULT 0, succeeded integer NOT NULL DEFAULT 0,
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','exhausted','cancelled','failed')),
 results jsonb NOT NULL DEFAULT '[]', attempts integer NOT NULL DEFAULT 0, error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,request_key)
);
CREATE UNIQUE INDEX cardhobby_cost_job_active ON cardhobby_cost_job(owner_id) WHERE status IN ('queued','running');
CREATE INDEX cardhobby_cost_units_owner ON cardhobby_purchase_unit(owner_id, item_id, unit_index);
CREATE INDEX cardhobby_cost_job_queue ON cardhobby_cost_job(created_at) WHERE status IN ('queued','running');
COMMIT;
