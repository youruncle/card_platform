BEGIN;

ALTER TABLE flatbed_pair_proposal
    ADD COLUMN scanner_job_id uuid REFERENCES scanner_recognition_job(id) ON DELETE SET NULL;

CREATE FUNCTION preserve_flatbed_sheet_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.session_id,NEW.owner_id,NEW.side,NEW.revision,NEW.original_path,NEW.content_type,NEW.sha256)
       IS DISTINCT FROM
       ROW(OLD.session_id,OLD.owner_id,OLD.side,OLD.revision,OLD.original_path,OLD.content_type,OLD.sha256) THEN
        RAISE EXCEPTION 'flatbed source sheets are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER flatbed_sheet_source_immutable BEFORE UPDATE ON flatbed_scan_sheet
FOR EACH ROW EXECUTE FUNCTION preserve_flatbed_sheet_source();

CREATE FUNCTION preserve_flatbed_region_detection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.sheet_id,NEW.owner_id,NEW.ordinal,NEW.bounds,NEW.confidence,NEW.failure_reason)
       IS DISTINCT FROM
       ROW(OLD.sheet_id,OLD.owner_id,OLD.ordinal,OLD.bounds,OLD.confidence,OLD.failure_reason) THEN
        RAISE EXCEPTION 'flatbed source detections are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER flatbed_region_detection_immutable BEFORE UPDATE ON flatbed_scan_region
FOR EACH ROW EXECUTE FUNCTION preserve_flatbed_region_detection();

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'card_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON flatbed_pair_proposal TO card_platform;
    END IF;
END $$;

COMMIT;
