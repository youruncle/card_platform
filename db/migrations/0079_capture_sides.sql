-- Additive compatibility: old workers must be drained before single-side uploads.
ALTER TABLE scanner_recognition_job
    ALTER COLUMN front_image_path DROP NOT NULL,
    ALTER COLUMN back_image_path DROP NOT NULL,
    ALTER COLUMN front_original_image_path DROP NOT NULL,
    ALTER COLUMN back_original_image_path DROP NOT NULL,
    ALTER COLUMN front_raw_image_path DROP NOT NULL,
    ALTER COLUMN back_raw_image_path DROP NOT NULL,
    ALTER COLUMN front_content_type DROP NOT NULL,
    ALTER COLUMN back_content_type DROP NOT NULL,
    ADD COLUMN capture_source text NOT NULL DEFAULT 'scanner',
    ADD COLUMN revision integer NOT NULL DEFAULT 1,
    ADD COLUMN target_item_id uuid REFERENCES collection_item(id) ON DELETE SET NULL,
    ADD COLUMN expected_revision timestamptz,
    ADD COLUMN crop_recipe jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN capture_metadata jsonb NOT NULL DEFAULT '{}',
    ADD CONSTRAINT capture_has_side CHECK (front_image_path IS NOT NULL OR back_image_path IS NOT NULL),
    ADD CONSTRAINT capture_front_consistent CHECK ((front_image_path IS NULL) = (front_content_type IS NULL) AND (front_image_path IS NULL) = (front_original_image_path IS NULL) AND (front_image_path IS NULL) = (front_raw_image_path IS NULL)),
    ADD CONSTRAINT capture_back_consistent CHECK ((back_image_path IS NULL) = (back_content_type IS NULL) AND (back_image_path IS NULL) = (back_original_image_path IS NULL) AND (back_image_path IS NULL) = (back_raw_image_path IS NULL)),
    ADD CONSTRAINT capture_auto_import_pair CHECK (require_confirmation OR (front_image_path IS NOT NULL AND back_image_path IS NOT NULL));
DROP INDEX IF EXISTS uq_scanner_recognition_job_collection_item;
CREATE UNIQUE INDEX uq_scanner_recognition_job_collection_item ON scanner_recognition_job(collection_item_id) WHERE collection_item_id IS NOT NULL AND target_item_id IS NULL;
