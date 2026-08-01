BEGIN;

CREATE TABLE recognition_knowledge_proposal (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES recognition_benchmark_batch(id) ON DELETE CASCADE,
    sample_id uuid NOT NULL UNIQUE REFERENCES recognition_benchmark_sample(id) ON DELETE CASCADE,
    proposed_identity jsonb NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    missing_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'needs_review', 'accepted', 'rejected', 'published')
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX recognition_knowledge_proposal_queue_idx
    ON recognition_knowledge_proposal (status, created_at);

COMMIT;
