-- Confirmation belongs to exact sheet revisions and image bytes, not a session flag.
ALTER TABLE flatbed_scan_session ADD COLUMN IF NOT EXISTS confirmed_sources jsonb;
ALTER TABLE flatbed_pair_proposal ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'unknown';
ALTER TABLE flatbed_pair_proposal ADD COLUMN IF NOT EXISTS identity_reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb;
