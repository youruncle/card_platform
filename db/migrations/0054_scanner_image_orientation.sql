BEGIN;

ALTER TABLE scanner_recognition_job
    ADD COLUMN front_original_image_path text,
    ADD COLUMN back_original_image_path text,
    ADD COLUMN front_rotation_degrees integer NOT NULL DEFAULT 0 CHECK (
        front_rotation_degrees IN (0, 90, 180, 270)
    ),
    ADD COLUMN back_rotation_degrees integer NOT NULL DEFAULT 0 CHECK (
        back_rotation_degrees IN (0, 90, 180, 270)
    ),
    ADD COLUMN front_orientation_confidence numeric(5,4) NOT NULL DEFAULT 0,
    ADD COLUMN back_orientation_confidence numeric(5,4) NOT NULL DEFAULT 0,
    ADD COLUMN front_orientation_method text NOT NULL DEFAULT 'pending' CHECK (
        front_orientation_method IN ('pending', 'apple_vision', 'vision_model', 'manual', 'unresolved')
    ),
    ADD COLUMN back_orientation_method text NOT NULL DEFAULT 'pending' CHECK (
        back_orientation_method IN ('pending', 'apple_vision', 'vision_model', 'manual', 'unresolved')
    ),
    ADD COLUMN front_orientation_corrected_at timestamptz,
    ADD COLUMN back_orientation_corrected_at timestamptz;

UPDATE scanner_recognition_job
SET front_original_image_path = front_image_path,
    back_original_image_path = back_image_path,
    front_orientation_method = 'unresolved',
    back_orientation_method = 'unresolved';

ALTER TABLE scanner_recognition_job
    ALTER COLUMN front_original_image_path SET NOT NULL,
    ALTER COLUMN back_original_image_path SET NOT NULL;

CREATE TABLE card_image_orientation_correction (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_item_id uuid NOT NULL REFERENCES collection_item(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    side text NOT NULL CHECK (side IN ('front', 'back')),
    rotation_degrees integer NOT NULL CHECK (rotation_degrees IN (90, 180, 270)),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX card_image_orientation_correction_item_idx
    ON card_image_orientation_correction (collection_item_id, created_at DESC);

COMMIT;
