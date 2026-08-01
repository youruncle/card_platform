BEGIN;

ALTER TABLE recognition_prediction_job
    ALTER COLUMN pipeline_version SET DEFAULT 'image-v3.1';

COMMIT;
