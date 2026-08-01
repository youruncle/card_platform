BEGIN;

ALTER TABLE recognition_benchmark_sample
    ALTER COLUMN prediction_pipeline_version SET DEFAULT 'image-v4.1';

COMMIT;
