-- Match owner/active-card pagination including its deterministic ID tie-breaker.
CREATE INDEX IF NOT EXISTS collection_item_owner_active_order_idx
    ON collection_item (owner_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;

-- First purchase is used on every portfolio row; ignore sale/adjustment history.
CREATE INDEX IF NOT EXISTS card_transaction_first_purchase_idx
    ON card_transaction (collection_item_id, transacted_at, created_at, id)
    WHERE transaction_type = 'buy';

-- Used both by latest_item_valuation and the detailed projection's lateral lookup.
CREATE INDEX IF NOT EXISTS valuation_snapshot_compatible_latest_idx
    ON valuation_snapshot (collection_item_id, valued_at DESC, created_at DESC, id DESC)
    WHERE identity_compatibility <> 'incompatible';

-- A confirmed identity must check whether a rejected version revoked it.
CREATE INDEX IF NOT EXISTS card_identity_version_rejected_parent_idx
    ON card_identity_version (supersedes_id)
    WHERE review_status = 'rejected';

-- The review panel reads the latest case regardless of its active/completed state.
CREATE INDEX IF NOT EXISTS identity_research_case_item_latest_idx
    ON identity_research_case (collection_item_id, created_at DESC);
