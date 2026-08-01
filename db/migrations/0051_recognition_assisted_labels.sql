BEGIN;

ALTER TABLE recognition_benchmark_sample
    ADD COLUMN IF NOT EXISTS assisted_label_result text,
    ADD COLUMN IF NOT EXISTS assisted_identity jsonb,
    ADD COLUMN IF NOT EXISTS assisted_confidence numeric(5,4),
    ADD COLUMN IF NOT EXISTS assisted_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS assisted_labeler text,
    ADD COLUMN IF NOT EXISTS assisted_review_status text,
    ADD COLUMN IF NOT EXISTS assisted_at timestamptz;

ALTER TABLE recognition_benchmark_sample
    DROP CONSTRAINT IF EXISTS recognition_assisted_label_result_check,
    ADD CONSTRAINT recognition_assisted_label_result_check
        CHECK (
            assisted_label_result IS NULL OR assisted_label_result IN (
                'top1_correct', 'top2_correct', 'top3_correct',
                'none_correct', 'insufficient_image', 'source_error'
            )
        ),
    DROP CONSTRAINT IF EXISTS recognition_assisted_review_status_check,
    ADD CONSTRAINT recognition_assisted_review_status_check
        CHECK (
            assisted_review_status IS NULL OR assisted_review_status IN (
                'pending', 'accepted', 'overridden'
            )
        ),
    DROP CONSTRAINT IF EXISTS recognition_assisted_confidence_check,
    ADD CONSTRAINT recognition_assisted_confidence_check
        CHECK (
            assisted_confidence IS NULL OR
            (assisted_confidence >= 0 AND assisted_confidence <= 1)
        );

COMMIT;
