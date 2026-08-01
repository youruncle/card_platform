BEGIN;

ALTER TABLE collection_item
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz,
    ADD COLUMN IF NOT EXISTS deleted_reason text,
    ADD COLUMN IF NOT EXISTS deleted_by uuid REFERENCES app_user(id);

CREATE INDEX IF NOT EXISTS collection_item_owner_active_idx
    ON collection_item (owner_id, created_at DESC)
    WHERE deleted_at IS NULL;

ALTER TABLE card_transaction
    DROP CONSTRAINT IF EXISTS card_transaction_collection_item_id_fkey,
    ADD CONSTRAINT card_transaction_collection_item_id_fkey
        FOREIGN KEY (collection_item_id) REFERENCES collection_item(id) ON DELETE CASCADE;

ALTER TABLE valuation_snapshot
    DROP CONSTRAINT IF EXISTS valuation_snapshot_collection_item_id_fkey,
    ADD CONSTRAINT valuation_snapshot_collection_item_id_fkey
        FOREIGN KEY (collection_item_id) REFERENCES collection_item(id) ON DELETE CASCADE;

ALTER TABLE card_score
    DROP CONSTRAINT IF EXISTS card_score_collection_item_id_fkey,
    ADD CONSTRAINT card_score_collection_item_id_fkey
        FOREIGN KEY (collection_item_id) REFERENCES collection_item(id) ON DELETE CASCADE;

ALTER TABLE analysis_request
    DROP CONSTRAINT IF EXISTS analysis_request_collection_item_id_fkey,
    ADD CONSTRAINT analysis_request_collection_item_id_fkey
        FOREIGN KEY (collection_item_id) REFERENCES collection_item(id) ON DELETE CASCADE;

ALTER TABLE recommendation
    DROP CONSTRAINT IF EXISTS recommendation_analysis_request_id_fkey,
    ADD CONSTRAINT recommendation_analysis_request_id_fkey
        FOREIGN KEY (analysis_request_id) REFERENCES analysis_request(id) ON DELETE CASCADE;

ALTER TABLE recommendation
    DROP CONSTRAINT IF EXISTS recommendation_card_score_id_fkey,
    ADD CONSTRAINT recommendation_card_score_id_fkey
        FOREIGN KEY (card_score_id) REFERENCES card_score(id) ON DELETE SET NULL;

CREATE OR REPLACE FUNCTION reject_deleted_collection_item_write()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_NARGS > 0
       AND TG_ARGV[0] = 'allow_failed'
       AND to_jsonb(NEW)->>'status' = 'failed' THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1 FROM collection_item
        WHERE id = NEW.collection_item_id AND deleted_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'collection item is deleted' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS card_transaction_active_item_guard ON card_transaction;
CREATE TRIGGER card_transaction_active_item_guard
    BEFORE INSERT OR UPDATE OF collection_item_id ON card_transaction
    FOR EACH ROW EXECUTE FUNCTION reject_deleted_collection_item_write();

DROP TRIGGER IF EXISTS valuation_snapshot_active_item_guard ON valuation_snapshot;
CREATE TRIGGER valuation_snapshot_active_item_guard
    BEFORE INSERT OR UPDATE OF collection_item_id ON valuation_snapshot
    FOR EACH ROW EXECUTE FUNCTION reject_deleted_collection_item_write();

DROP TRIGGER IF EXISTS valuation_job_active_item_guard ON valuation_job;
CREATE TRIGGER valuation_job_active_item_guard
    BEFORE INSERT OR UPDATE ON valuation_job
    FOR EACH ROW EXECUTE FUNCTION reject_deleted_collection_item_write('allow_failed');

COMMIT;
