BEGIN;

CREATE TABLE recognition_prediction_job (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES recognition_benchmark_batch(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'partial', 'failed')
    ),
    requested_limit integer NOT NULL CHECK (requested_limit BETWEEN 1 AND 50),
    retry_errors boolean NOT NULL DEFAULT false,
    knowledge_version uuid REFERENCES knowledge_version(id) ON DELETE SET NULL,
    requested_count integer NOT NULL DEFAULT 0,
    predicted_count integer NOT NULL DEFAULT 0,
    failed_count integer NOT NULL DEFAULT 0,
    error_message text,
    attempt_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX recognition_prediction_job_one_active_per_batch
    ON recognition_prediction_job (batch_id)
    WHERE status IN ('queued', 'running');

CREATE INDEX recognition_prediction_job_queue_idx
    ON recognition_prediction_job (status, created_at);

COMMIT;
