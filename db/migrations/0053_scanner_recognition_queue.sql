BEGIN;

CREATE TABLE scanner_recognition_job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    front_image_path text NOT NULL,
    back_image_path text NOT NULL,
    front_content_type text NOT NULL,
    back_content_type text NOT NULL,
    front_crop_confidence numeric(5,4) NOT NULL DEFAULT 0,
    back_crop_confidence numeric(5,4) NOT NULL DEFAULT 0,
    front_transformed boolean NOT NULL DEFAULT false,
    back_transformed boolean NOT NULL DEFAULT false,
    result jsonb,
    error_message text,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX scanner_recognition_job_owner_created_idx
    ON scanner_recognition_job (owner_id, created_at DESC);
CREATE INDEX scanner_recognition_job_queue_idx
    ON scanner_recognition_job (status, created_at)
    WHERE status = 'queued';

COMMIT;
