BEGIN;

ALTER TABLE scanner_recognition_job
    DROP CONSTRAINT scanner_recognition_job_front_orientation_method_check,
    DROP CONSTRAINT scanner_recognition_job_back_orientation_method_check;

ALTER TABLE scanner_recognition_job
    ADD CONSTRAINT scanner_recognition_job_front_orientation_method_check CHECK (
        front_orientation_method IN (
            'pending',
            'apple_vision',
            'apple_vision_text',
            'apple_vision_face_text',
            'apple_vision_face_override',
            'vision_model',
            'manual',
            'unresolved'
        )
    ),
    ADD CONSTRAINT scanner_recognition_job_back_orientation_method_check CHECK (
        back_orientation_method IN (
            'pending',
            'apple_vision',
            'apple_vision_text',
            'apple_vision_face_text',
            'apple_vision_face_override',
            'vision_model',
            'manual',
            'unresolved'
        )
    );

COMMIT;
