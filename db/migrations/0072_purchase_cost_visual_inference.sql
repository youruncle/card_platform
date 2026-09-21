BEGIN;
CREATE TABLE cardhobby_cost_visual_inference (
 owner_id uuid NOT NULL REFERENCES app_user(id),
 collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
 fingerprint text NOT NULL,
 decision jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(owner_id,collection_item_id,fingerprint)
);
COMMIT;
