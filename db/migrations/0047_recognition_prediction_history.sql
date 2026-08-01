BEGIN;

CREATE TABLE recognition_benchmark_prediction (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sample_id uuid NOT NULL
        REFERENCES recognition_benchmark_sample(id) ON DELETE CASCADE,
    job_id uuid
        REFERENCES recognition_prediction_job(id) ON DELETE SET NULL,
    pipeline_version text NOT NULL,
    knowledge_version uuid
        REFERENCES knowledge_version(id) ON DELETE SET NULL,
    model text,
    prediction jsonb,
    prediction_error text,
    predicted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (
        (prediction IS NOT NULL AND prediction_error IS NULL)
        OR (prediction IS NULL AND prediction_error IS NOT NULL)
    )
);

CREATE UNIQUE INDEX recognition_benchmark_prediction_job_sample_uidx
    ON recognition_benchmark_prediction (job_id, sample_id)
    WHERE job_id IS NOT NULL;

CREATE INDEX recognition_benchmark_prediction_sample_history_idx
    ON recognition_benchmark_prediction (sample_id, predicted_at DESC);

INSERT INTO recognition_benchmark_prediction (
    sample_id, pipeline_version, knowledge_version, model,
    prediction, prediction_error, predicted_at
)
SELECT id,
       COALESCE(prediction_pipeline_version, 'legacy-unknown'),
       prediction_knowledge_version,
       prediction_model,
       prediction,
       prediction_error,
       COALESCE(predicted_at, created_at)
FROM recognition_benchmark_sample
WHERE prediction IS NOT NULL OR prediction_error IS NOT NULL;

ALTER TABLE recognition_prediction_job
    ADD COLUMN replay_all boolean NOT NULL DEFAULT false;

COMMIT;
