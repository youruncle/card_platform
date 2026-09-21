BEGIN;
CREATE TABLE cardhobby_cost_refresh_history (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_id uuid NOT NULL REFERENCES app_user(id),
 collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
 transaction_before jsonb NOT NULL,
 assignment_before jsonb,
 evidence_after jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cardhobby_cost_refresh_item ON cardhobby_cost_refresh_history(owner_id, collection_item_id, created_at);
COMMIT;
