BEGIN;

ALTER TABLE knowledge_entity DROP CONSTRAINT knowledge_entity_entity_type_check;
ALTER TABLE knowledge_entity ADD CONSTRAINT knowledge_entity_entity_type_check CHECK (
    entity_type IN (
        'manufacturer', 'brand', 'license', 'product', 'insert', 'parallel',
        'attribute', 'player', 'card'
    )
);

ALTER TABLE knowledge_relation DROP CONSTRAINT knowledge_relation_relation_type_check;
ALTER TABLE knowledge_relation ADD CONSTRAINT knowledge_relation_relation_type_check CHECK (
    relation_type IN (
        'conflicts_with', 'excludes', 'requires', 'belongs_to', 'equivalent_to',
        'manufactured_by', 'licensed_by', 'product_family_of', 'available_in'
    )
);

CREATE TABLE knowledge_source (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    source_key text NOT NULL,
    title text NOT NULL,
    url text NOT NULL,
    publisher text NOT NULL,
    source_type text NOT NULL CHECK (
        source_type IN ('official_product', 'official_checklist', 'official_odds',
                        'beckett_checklist', 'cardboard_connection', 'other')
    ),
    retrieved_at timestamptz NOT NULL,
    checksum text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, source_key)
);

CREATE TABLE knowledge_import_batch (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    batch_key text NOT NULL,
    manifest_version integer NOT NULL,
    status text NOT NULL CHECK (
        status IN ('draft', 'validated', 'applied', 'published', 'failed', 'rolled_back')
    ),
    dry_run boolean NOT NULL DEFAULT true,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    conflict_report jsonb NOT NULL DEFAULT '[]'::jsonb,
    replay_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    manifest_checksum text NOT NULL,
    created_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    applied_at timestamptz,
    published_version_id uuid REFERENCES knowledge_version(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, batch_key, manifest_checksum, dry_run)
);

CREATE TABLE knowledge_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    object_type text NOT NULL CHECK (
        object_type IN ('entity', 'alias', 'relation', 'applicability', 'coverage')
    ),
    object_id uuid NOT NULL,
    source_id uuid NOT NULL REFERENCES knowledge_source(id) ON DELETE RESTRICT,
    locator text,
    claim text NOT NULL,
    evidence_level text NOT NULL CHECK (
        evidence_level IN ('primary', 'secondary', 'context_only')
    ),
    verified_at timestamptz,
    verified_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (object_type, object_id, source_id, claim)
);

CREATE TABLE knowledge_applicability (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    product_entity_id uuid REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    season_start integer,
    season_end integer,
    league text NOT NULL DEFAULT 'NBA',
    applicability_status text NOT NULL CHECK (
        applicability_status IN ('verified', 'partial', 'candidate', 'discontinued')
    ),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    review_status text NOT NULL DEFAULT 'draft' CHECK (
        review_status IN ('draft', 'in_review', 'approved', 'rejected')
    ),
    source_id uuid REFERENCES knowledge_source(id) ON DELETE RESTRICT,
    created_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    reviewed_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (season_start IS NULL OR season_end IS NULL OR season_start <= season_end),
    UNIQUE NULLS NOT DISTINCT (
        workspace_id, entity_id, product_entity_id, season_start, season_end, league
    )
);

CREATE TABLE knowledge_coverage (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    manufacturer_entity_id uuid REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    product_entity_id uuid REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    season_start integer,
    season_end integer,
    insert_entity_id uuid REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    parallel_entity_id uuid REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    coverage_status text NOT NULL CHECK (
        coverage_status IN ('covered', 'partial', 'needs_review', 'pending_source', 'not_applicable')
    ),
    source_id uuid REFERENCES knowledge_source(id) ON DELETE RESTRICT,
    notes text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (
        workspace_id, manufacturer_entity_id, product_entity_id, season_start,
        season_end, insert_entity_id, parallel_entity_id
    )
);

CREATE INDEX knowledge_coverage_matrix_idx ON knowledge_coverage (
    workspace_id, coverage_status, manufacturer_entity_id, product_entity_id, season_start
);
CREATE INDEX knowledge_applicability_lookup_idx ON knowledge_applicability (
    workspace_id, product_entity_id, season_start, season_end
);

COMMIT;
