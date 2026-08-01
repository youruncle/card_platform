BEGIN;

ALTER TABLE recognition_benchmark_sample
    ADD COLUMN prediction_pipeline_version text;

UPDATE recognition_benchmark_sample
SET prediction_pipeline_version = 'legacy-mixed-pre-v3'
WHERE prediction IS NOT NULL;

ALTER TABLE recognition_prediction_job
    ADD COLUMN pipeline_version text NOT NULL DEFAULT 'image-v3.0';

CREATE INDEX recognition_benchmark_pipeline_idx
    ON recognition_benchmark_sample (batch_id, prediction_pipeline_version);

COMMIT;
