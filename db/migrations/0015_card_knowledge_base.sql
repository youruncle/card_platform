BEGIN;

CREATE TABLE knowledge_workspace (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE,
    revision bigint NOT NULL DEFAULT 1,
    published_version_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE knowledge_entity (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    entity_type text NOT NULL CHECK (entity_type IN (
        'brand', 'product', 'insert', 'parallel', 'attribute', 'player', 'card'
    )),
    canonical_name text NOT NULL,
    normalized_name text NOT NULL,
    parent_id uuid REFERENCES knowledge_entity(id) ON DELETE SET NULL,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    source jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence numeric(5,4) NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    review_status text NOT NULL DEFAULT 'draft' CHECK (
        review_status IN ('draft', 'in_review', 'approved', 'rejected')
    ),
    active boolean NOT NULL DEFAULT true,
    created_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    reviewed_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, entity_type, normalized_name)
);

CREATE INDEX knowledge_entity_lookup_idx
    ON knowledge_entity (workspace_id, entity_type, normalized_name) WHERE active;

CREATE TABLE knowledge_alias (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    entity_id uuid NOT NULL REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    alias text NOT NULL,
    normalized_alias text NOT NULL,
    language text NOT NULL DEFAULT 'und',
    alias_type text NOT NULL DEFAULT 'synonym' CHECK (
        alias_type IN ('canonical', 'synonym', 'translation', 'misspelling', 'seller_term')
    ),
    source jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence numeric(5,4) NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    review_status text NOT NULL DEFAULT 'draft' CHECK (
        review_status IN ('draft', 'in_review', 'approved', 'rejected')
    ),
    active boolean NOT NULL DEFAULT true,
    created_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    reviewed_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, entity_id, normalized_alias)
);

CREATE INDEX knowledge_alias_lookup_idx
    ON knowledge_alias (workspace_id, normalized_alias) WHERE active;

CREATE TABLE knowledge_relation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    subject_entity_id uuid NOT NULL REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    relation_type text NOT NULL CHECK (
        relation_type IN ('conflicts_with', 'excludes', 'requires', 'belongs_to', 'equivalent_to')
    ),
    object_entity_id uuid NOT NULL REFERENCES knowledge_entity(id) ON DELETE CASCADE,
    reason text,
    source jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence numeric(5,4) NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    review_status text NOT NULL DEFAULT 'draft' CHECK (
        review_status IN ('draft', 'in_review', 'approved', 'rejected')
    ),
    active boolean NOT NULL DEFAULT true,
    created_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    reviewed_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (subject_entity_id <> object_entity_id),
    UNIQUE (workspace_id, subject_entity_id, relation_type, object_entity_id)
);

CREATE TABLE knowledge_observation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    observed_text text NOT NULL,
    normalized_text text NOT NULL,
    context jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurrence_count integer NOT NULL DEFAULT 1 CHECK (occurrence_count > 0),
    proposed_entity_id uuid REFERENCES knowledge_entity(id) ON DELETE SET NULL,
    proposed_alias text,
    proposal_source text CHECK (proposal_source IN ('rule', 'model', 'human')),
    replay_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence numeric(5,4) CHECK (confidence BETWEEN 0 AND 1),
    status text NOT NULL DEFAULT 'observed' CHECK (
        status IN ('observed', 'proposed', 'validated', 'approved', 'rejected', 'promoted')
    ),
    reviewed_by uuid REFERENCES app_user(id) ON DELETE SET NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, normalized_text)
);

CREATE TABLE knowledge_version (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE RESTRICT,
    version_number integer NOT NULL CHECK (version_number > 0),
    status text NOT NULL DEFAULT 'published' CHECK (status IN ('published', 'rolled_back')),
    parent_version_id uuid REFERENCES knowledge_version(id) ON DELETE RESTRICT,
    snapshot jsonb NOT NULL,
    change_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    checksum text NOT NULL,
    release_notes text,
    published_by uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
    published_at timestamptz NOT NULL DEFAULT now(),
    rolled_back_at timestamptz,
    UNIQUE (workspace_id, version_number),
    UNIQUE (workspace_id, checksum)
);

ALTER TABLE knowledge_workspace
    ADD CONSTRAINT knowledge_workspace_published_version_fk
    FOREIGN KEY (published_version_id) REFERENCES knowledge_version(id) ON DELETE RESTRICT;

CREATE TABLE knowledge_audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    actor_id uuid REFERENCES app_user(id) ON DELETE SET NULL,
    action text NOT NULL,
    object_type text NOT NULL,
    object_id uuid,
    request_id text,
    before_value jsonb,
    after_value jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX knowledge_audit_request_uidx
    ON knowledge_audit_log (workspace_id, request_id)
    WHERE request_id IS NOT NULL;

CREATE TABLE knowledge_member (
    workspace_id uuid NOT NULL REFERENCES knowledge_workspace(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    role text NOT NULL CHECK (role IN ('viewer', 'editor', 'reviewer', 'publisher', 'admin')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE knowledge_impact (
    version_id uuid NOT NULL REFERENCES knowledge_version(id) ON DELETE CASCADE,
    object_type text NOT NULL CHECK (object_type IN ('collection_item', 'market_comp')),
    object_id uuid NOT NULL,
    reason jsonb NOT NULL DEFAULT '{}'::jsonb,
    revaluation_status text NOT NULL DEFAULT 'pending' CHECK (
        revaluation_status IN ('pending', 'queued', 'completed', 'failed')
    ),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (version_id, object_type, object_id)
);

INSERT INTO knowledge_workspace (name) VALUES ('Card Platform')
ON CONFLICT (name) DO NOTHING;

COMMIT;
