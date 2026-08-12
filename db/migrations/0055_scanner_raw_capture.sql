BEGIN;

ALTER TABLE scanner_recognition_job
    ADD COLUMN front_raw_image_path text,
    ADD COLUMN back_raw_image_path text;

UPDATE scanner_recognition_job
SET front_raw_image_path = front_original_image_path,
    back_raw_image_path = back_original_image_path;

ALTER TABLE scanner_recognition_job
    ALTER COLUMN front_raw_image_path SET NOT NULL,
    ALTER COLUMN back_raw_image_path SET NOT NULL;

COMMIT;
