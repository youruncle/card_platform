BEGIN;

ALTER TABLE flatbed_scan_sheet
    ADD COLUMN detector_version text NOT NULL DEFAULT 'legacy-contours-v1',
    ADD COLUMN layout_hypothesis jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN front_to_back_prior jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN requires_review boolean NOT NULL DEFAULT true,
    ADD COLUMN derived_version integer NOT NULL DEFAULT 1;

CREATE TABLE flatbed_layout_override (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sheet_id uuid NOT NULL REFERENCES flatbed_scan_sheet(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    regions jsonb NOT NULL,
    confirmed boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(sheet_id, revision)
);
CREATE INDEX flatbed_layout_override_sheet_idx ON flatbed_layout_override(sheet_id, revision DESC);

-- Source bytes remain immutable. Detection metadata may only be changed by
-- creating a new sheet/override revision, never by rewriting the original.
CREATE OR REPLACE FUNCTION preserve_flatbed_sheet_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.session_id,NEW.owner_id,NEW.side,NEW.revision,NEW.original_path,NEW.content_type,NEW.sha256)
       IS DISTINCT FROM
       ROW(OLD.session_id,OLD.owner_id,OLD.side,OLD.revision,OLD.original_path,OLD.content_type,OLD.sha256) THEN
        RAISE EXCEPTION 'flatbed source sheets are immutable';
    END IF;
    RETURN NEW;
END $$;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'card_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON flatbed_layout_override TO card_platform;
    END IF;
END $$;

COMMIT;
