-- New cards only: transactional outbox; no historical collection backfill.
CREATE TABLE automatic_purchase_cost (
    item_id uuid PRIMARY KEY REFERENCES collection_item(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'completed', 'failed')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
    result jsonb,
    run_after timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX automatic_purchase_cost_pending ON automatic_purchase_cost(run_after, created_at)
    WHERE status = 'queued';
CREATE INDEX automatic_purchase_cost_owner ON automatic_purchase_cost(owner_id);
