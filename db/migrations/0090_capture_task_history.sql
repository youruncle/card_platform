-- Keep observed uncertainty and append-only capture outcomes without changing old cards.
ALTER TABLE card_definition ALTER COLUMN is_rookie DROP NOT NULL,
 ALTER COLUMN is_autograph DROP NOT NULL, ALTER COLUMN is_memorabilia DROP NOT NULL,
 ALTER COLUMN is_patch DROP NOT NULL, ALTER COLUMN has_inscription DROP NOT NULL;
ALTER TABLE collection_item ALTER COLUMN is_numbered DROP NOT NULL;
ALTER TABLE scanner_recognition_job ADD COLUMN manual_retry_limit integer NOT NULL DEFAULT 0;
CREATE TABLE scanner_job_event (
 id bigserial PRIMARY KEY,
 job_id uuid NOT NULL REFERENCES scanner_recognition_job(id) ON DELETE CASCADE,
 owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 attempt_count integer NOT NULL, status text NOT NULL,
 collection_item_id uuid, error_message text, result jsonb,
 source text NOT NULL DEFAULT 'live'
);
CREATE INDEX scanner_job_event_owner_job_idx ON scanner_job_event(owner_id,job_id,id);
INSERT INTO scanner_job_event(job_id,owner_id,occurred_at,attempt_count,status,collection_item_id,error_message,result,source)
 SELECT id,owner_id,updated_at,attempt_count,status,collection_item_id,error_message,result,'legacy_snapshot'
 FROM scanner_recognition_job;
CREATE FUNCTION record_scanner_job_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND (NEW.status,NEW.collection_item_id,NEW.error_message,NEW.result,NEW.attempt_count)
   IS NOT DISTINCT FROM (OLD.status,OLD.collection_item_id,OLD.error_message,OLD.result,OLD.attempt_count) THEN
  RETURN NEW;
 END IF;
 INSERT INTO scanner_job_event(job_id,owner_id,attempt_count,status,collection_item_id,error_message,result,source)
 VALUES(NEW.id,NEW.owner_id,NEW.attempt_count,NEW.status,NEW.collection_item_id,NEW.error_message,NEW.result,COALESCE(NEW.capture_metadata->>'resolution_source','live'));
 RETURN NEW;
END $$;
CREATE TRIGGER scanner_job_event_append AFTER INSERT OR UPDATE OF status,collection_item_id,error_message,result,attempt_count
 ON scanner_recognition_job FOR EACH ROW EXECUTE FUNCTION record_scanner_job_event();
