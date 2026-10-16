-- Mobile captures share processing but require explicit human confirmation.
ALTER TABLE scanner_recognition_job
    ADD COLUMN IF NOT EXISTS require_confirmation boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS client_request_id uuid,
    ADD COLUMN IF NOT EXISTS request_digest text;
CREATE UNIQUE INDEX IF NOT EXISTS scanner_mobile_request_unique
    ON scanner_recognition_job(owner_id, client_request_id)
    WHERE client_request_id IS NOT NULL;
