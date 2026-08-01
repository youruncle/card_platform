BEGIN;

CREATE TABLE recognition_benchmark_batch (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    source_code text NOT NULL,
    status text NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'collecting', 'ready', 'predicting', 'labeling', 'completed', 'failed')
    ),
    sampling_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    target_top1 numeric(5,4) NOT NULL DEFAULT 0.9000,
    target_top3 numeric(5,4) NOT NULL DEFAULT 0.9700,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE recognition_benchmark_sample (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES recognition_benchmark_batch(id) ON DELETE CASCADE,
    source_code text NOT NULL,
    source_item_id text NOT NULL,
    source_url text NOT NULL,
    image_urls jsonb NOT NULL CHECK (
        jsonb_typeof(image_urls) = 'array' AND jsonb_array_length(image_urls) > 0
    ),
    occurred_at timestamptz,
    final_price numeric(18,2) CHECK (final_price > 0),
    currency char(3) NOT NULL DEFAULT 'CNY',
    sealed_reference jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    prediction jsonb,
    prediction_model text,
    prediction_knowledge_version uuid REFERENCES knowledge_version(id) ON DELETE SET NULL,
    prediction_error text,
    predicted_at timestamptz,
    label_status text NOT NULL DEFAULT 'unlabeled' CHECK (
        label_status IN ('unlabeled', 'labeled', 'needs_review')
    ),
    label_result text CHECK (
        label_result IS NULL OR label_result IN (
            'top1_correct', 'top2_correct', 'top3_correct',
            'none_correct', 'insufficient_image', 'source_error'
        )
    ),
    corrected_identity jsonb,
    label_notes text,
    labeled_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (batch_id, source_code, source_item_id)
);

CREATE INDEX recognition_benchmark_sample_queue_idx
    ON recognition_benchmark_sample (batch_id, label_status, created_at);

CREATE INDEX recognition_benchmark_source_item_idx
    ON recognition_benchmark_sample (source_code, source_item_id);

COMMIT;
