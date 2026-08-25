BEGIN;

CREATE TABLE flatbed_scan_session (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    device_serial text NOT NULL, status text NOT NULL DEFAULT 'created', dpi integer NOT NULL CHECK (dpi IN (300,600)), color_mode text NOT NULL DEFAULT 'color',
    pairing_transform text, pairing_requires_review boolean NOT NULL DEFAULT true, submitted_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE flatbed_scan_sheet (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), session_id uuid NOT NULL REFERENCES flatbed_scan_session(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE, side text NOT NULL CHECK (side IN ('front','back')), revision integer NOT NULL DEFAULT 1,
    original_path text NOT NULL, content_type text NOT NULL, sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(session_id,side,revision)
);
CREATE TABLE flatbed_scan_region (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), sheet_id uuid NOT NULL REFERENCES flatbed_scan_sheet(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE, ordinal integer NOT NULL, bounds jsonb NOT NULL, confidence double precision NOT NULL,
    failure_reason text, crop_path text, signals jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(sheet_id,ordinal)
);
CREATE TABLE flatbed_pair_proposal (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), session_id uuid NOT NULL REFERENCES flatbed_scan_session(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE, front_region_id uuid REFERENCES flatbed_scan_region(id), back_region_id uuid REFERENCES flatbed_scan_region(id),
    score double precision NOT NULL, confidence_level text NOT NULL, transform text NOT NULL, signals jsonb NOT NULL, conflicts jsonb NOT NULL DEFAULT '[]'::jsonb,
    selected boolean NOT NULL DEFAULT false, manual_override boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX flatbed_session_owner_idx ON flatbed_scan_session(owner_id,created_at DESC);
CREATE INDEX flatbed_sheet_session_idx ON flatbed_scan_sheet(session_id,side);
CREATE INDEX flatbed_region_sheet_idx ON flatbed_scan_region(sheet_id,ordinal);
CREATE INDEX flatbed_pair_session_idx ON flatbed_pair_proposal(session_id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'card_platform') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON flatbed_scan_session TO card_platform;
        GRANT SELECT, INSERT, UPDATE, DELETE ON flatbed_scan_sheet TO card_platform;
        GRANT SELECT, INSERT, UPDATE, DELETE ON flatbed_scan_region TO card_platform;
        GRANT SELECT, INSERT, UPDATE, DELETE ON flatbed_pair_proposal TO card_platform;
    END IF;
END $$;

COMMIT;
