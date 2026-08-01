BEGIN;

ALTER TABLE recognition_benchmark_sample
    ADD COLUMN label_pipeline_version text;

UPDATE recognition_benchmark_sample
SET label_pipeline_version = prediction_pipeline_version,
    corrected_identity = COALESCE(
        corrected_identity,
        CASE label_result
            WHEN 'top1_correct' THEN prediction -> 'candidates' -> 0 -> 'identity'
            WHEN 'top2_correct' THEN prediction -> 'candidates' -> 1 -> 'identity'
            WHEN 'top3_correct' THEN prediction -> 'candidates' -> 2 -> 'identity'
            ELSE NULL
        END
    )
WHERE label_status = 'labeled';

COMMIT;
