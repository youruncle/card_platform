BEGIN;

-- v5 evidence audit: remove every model-only object from the publishable set first.
UPDATE knowledge_alias
SET active = false, review_status = 'rejected', updated_at = now()
WHERE active
  AND source->>'kind' = 'llm_curated_bootstrap';

UPDATE knowledge_relation
SET active = false, review_status = 'rejected', updated_at = now()
WHERE active
  AND source->>'kind' = 'llm_curated_bootstrap';

UPDATE knowledge_entity
SET active = false, review_status = 'rejected', updated_at = now()
WHERE active
  AND source->>'kind' = 'llm_curated_bootstrap';

-- Sources used by the corrected audit. Source presence does not imply every
-- seller alias on an entity is verified.
INSERT INTO knowledge_source (
    workspace_id, source_key, title, url, publisher, source_type, retrieved_at, metadata
)
SELECT id, 'fanatics-brands', 'Fanatics Collectibles Brands',
       'https://www.fanaticsinc.com/brands', 'Fanatics Inc', 'official_product',
       '2026-07-28T14:20:00+08:00'::timestamptz,
       '{"claim_scope":["Topps is a brand","Bowman is a brand acquired with Topps"]}'::jsonb
FROM knowledge_workspace WHERE name = 'Card Platform'
ON CONFLICT (workspace_id, source_key) DO UPDATE SET
    title = EXCLUDED.title, url = EXCLUDED.url, publisher = EXCLUDED.publisher,
    source_type = EXCLUDED.source_type, retrieved_at = EXCLUDED.retrieved_at,
    metadata = EXCLUDED.metadata;

INSERT INTO knowledge_source (
    workspace_id, source_key, title, url, publisher, source_type, retrieved_at, metadata
)
SELECT id, 'panini-2019-court-kings-pis', '2019-20 Court Kings Basketball Hobby PIS',
       'https://blog.paniniamerica.net/wp-content/uploads/2020/01/2019-20_Court_Kings_BK_HOBBY.pdf',
       'Panini America', 'official_product', '2026-07-28T14:20:00+08:00'::timestamptz,
       '{"claim_scope":["Court Kings product","First Steps insert","rookie-year sneakers","on-card autographs"]}'::jsonb
FROM knowledge_workspace WHERE name = 'Card Platform'
ON CONFLICT (workspace_id, source_key) DO UPDATE SET
    title = EXCLUDED.title, url = EXCLUDED.url, publisher = EXCLUDED.publisher,
    source_type = EXCLUDED.source_type, retrieved_at = EXCLUDED.retrieved_at,
    metadata = EXCLUDED.metadata;

INSERT INTO knowledge_source (
    workspace_id, source_key, title, url, publisher, source_type, retrieved_at, metadata
)
SELECT id, 'beckett-2025-topps-midnight', '2025-26 Topps Midnight Basketball Checklist',
       'https://www.beckett.com/news/2025-26-topps-midnight-basketball-cards/',
       'Beckett', 'beckett_checklist', '2026-07-28T14:20:00+08:00'::timestamptz,
       '{"claim_scope":["Topps Midnight product","Dreamland insert","Night Owls insert","pack odds"]}'::jsonb
FROM knowledge_workspace WHERE name = 'Card Platform'
ON CONFLICT (workspace_id, source_key) DO UPDATE SET
    title = EXCLUDED.title, url = EXCLUDED.url, publisher = EXCLUDED.publisher,
    source_type = EXCLUDED.source_type, retrieved_at = EXCLUDED.retrieved_at,
    metadata = EXCLUDED.metadata;

INSERT INTO knowledge_source (
    workspace_id, source_key, title, url, publisher, source_type, retrieved_at, metadata
)
SELECT id, source_key, title, url, 'Beckett', 'beckett_checklist',
       '2026-07-28T14:20:00+08:00'::timestamptz, metadata
FROM knowledge_workspace
CROSS JOIN (VALUES
    ('beckett-2023-nt', '2023-24 Panini National Treasures Basketball Checklist',
     'https://www.beckett.com/news/2023-24-panini-national-treasures-basketball-cards/',
     '{"claim_scope":["National Treasures product identity and season"]}'::jsonb),
    ('beckett-2023-one-one', '2023-24 Panini One and One Basketball Checklist',
     'https://www.beckett.com/news/2023-24-panini-one-and-one-basketball-cards/',
     '{"claim_scope":["One and One product identity and season"]}'::jsonb)
) AS source(source_key, title, url, metadata)
WHERE knowledge_workspace.name = 'Card Platform'
ON CONFLICT (workspace_id, source_key) DO UPDATE SET
    title = EXCLUDED.title, url = EXCLUDED.url, publisher = EXCLUDED.publisher,
    source_type = EXCLUDED.source_type, retrieved_at = EXCLUDED.retrieved_at,
    metadata = EXCLUDED.metadata;

-- Correct organization/brand semantics. Bowman was correctly typed as brand,
-- but its old Topps Chrome evidence was unrelated.
UPDATE knowledge_entity e
SET canonical_name = 'Fanatics Collectibles',
    normalized_name = 'fanatics collectibles',
    attributes = '{"organization_type":"manufacturer_and_collectibles_business"}'::jsonb,
    source = jsonb_build_object(
        'source_key', 'fanatics-brands',
        'source_url', 'https://www.fanaticsinc.com/brands',
        'audit', 'v5'
    ),
    confidence = 0.99, review_status = 'approved', active = true, updated_at = now()
WHERE e.workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND e.entity_type = 'manufacturer' AND e.canonical_name = 'Topps / Fanatics';

UPDATE knowledge_entity e
SET attributes = '{"owned_by":"Fanatics Collectibles","semantic_scope":"trading-card brand"}'::jsonb,
    source = jsonb_build_object(
        'source_key', 'fanatics-brands',
        'source_url', 'https://www.fanaticsinc.com/brands',
        'audit', 'v5'
    ),
    confidence = 1, review_status = 'approved', active = true, updated_at = now()
WHERE e.workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND e.entity_type = 'brand' AND e.canonical_name IN ('Topps', 'Bowman');

-- "Bowman Chrome" is too broad for basketball. Official basketball names
-- include Bowman University Chrome; the new Bowman Basketball contains Chrome
-- variants. Keep it out of the runtime until represented as season-scoped products.
UPDATE knowledge_entity
SET active = false, review_status = 'rejected',
    attributes = attributes || '{"audit_reason":"ambiguous basketball product name; do not merge Bowman University Chrome with Bowman Basketball Chrome variants"}'::jsonb,
    updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND entity_type = 'product' AND canonical_name = 'Bowman Chrome';

-- Restore only directly evidenced product/insert identities.
UPDATE knowledge_entity
SET active = true, review_status = 'approved', confidence = 1,
    source = '{"source_key":"panini-2019-court-kings-pis","source_url":"https://blog.paniniamerica.net/wp-content/uploads/2020/01/2019-20_Court_Kings_BK_HOBBY.pdf","audit":"v5"}'::jsonb,
    attributes = '{"manufacturer":"Panini","verified_season_start":2019,"verified_season_end":2020}'::jsonb,
    updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND entity_type = 'product' AND canonical_name = 'Court Kings';

UPDATE knowledge_entity
SET active = true, review_status = 'approved', confidence = 1,
    source = '{"source_key":"panini-2019-court-kings-pis","source_url":"https://blog.paniniamerica.net/wp-content/uploads/2020/01/2019-20_Court_Kings_BK_HOBBY.pdf","audit":"v5"}'::jsonb,
    attributes = '{"semantic_type":"insert","product":"Court Kings","season_start":2019,"season_end":2020,"visual_feature":"rookie-year sneakers"}'::jsonb,
    updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND entity_type = 'insert' AND canonical_name = 'First Steps';

UPDATE knowledge_entity
SET active = true, review_status = 'approved', confidence = 0.98,
    source = '{"source_key":"beckett-2025-topps-midnight","source_url":"https://www.beckett.com/news/2025-26-topps-midnight-basketball-cards/","audit":"v5"}'::jsonb,
    attributes = '{"manufacturer":"Fanatics Collectibles","brand":"Topps","season_start":2025,"season_end":2026}'::jsonb,
    updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND entity_type = 'product' AND canonical_name = 'Topps Midnight';

UPDATE knowledge_entity
SET active = true, review_status = 'approved', confidence = 0.98,
    source = '{"source_key":"beckett-2025-topps-midnight","source_url":"https://www.beckett.com/news/2025-26-topps-midnight-basketball-cards/","audit":"v5"}'::jsonb,
    attributes = jsonb_build_object(
        'semantic_type', 'insert', 'product', 'Topps Midnight',
        'season_start', 2025, 'season_end', 2026
    ),
    updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND entity_type = 'insert' AND canonical_name IN ('Dreamland', 'Night Owls');

UPDATE knowledge_entity
SET source = jsonb_build_object(
        'source_key', CASE canonical_name
            WHEN 'National Treasures' THEN 'beckett-2023-nt'
            ELSE 'beckett-2023-one-one' END,
        'source_url', CASE canonical_name
            WHEN 'National Treasures' THEN 'https://www.beckett.com/news/2023-24-panini-national-treasures-basketball-cards/'
            ELSE 'https://www.beckett.com/news/2023-24-panini-one-and-one-basketball-cards/' END,
        'audit', 'v5'
    ),
    attributes = attributes || '{"verified_season_start":2023,"verified_season_end":2024}'::jsonb,
    confidence = 0.98, review_status = 'approved', active = true, updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND entity_type = 'product' AND canonical_name IN ('National Treasures', 'One and One');

-- Remove claims whose cited pages do not establish the claimed semantics.
UPDATE knowledge_entity
SET active = false, review_status = 'rejected',
    attributes = attributes || '{"audit_reason":"cited source does not directly support this generic taxonomy claim"}'::jsonb,
    updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND entity_type = 'attribute'
  AND canonical_name IN (
      'Case Hit', 'Large Material Window', 'Sticker Autograph', 'Three Material Windows'
  );

-- Chinese seller terms and misspellings require corpus evidence, not a product
-- checklist. Keep canonical identities, but remove unsupported aliases.
UPDATE knowledge_alias a
SET active = false, review_status = 'rejected', updated_at = now()
FROM knowledge_entity e
WHERE a.entity_id = e.id
  AND e.workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND a.active
  AND a.alias_type IN ('seller_term', 'misspelling')
  AND NOT (
      e.canonical_name IN ('First Steps', 'Dreamland', 'Night Owls')
      AND a.alias IN ('鞋灯', '梦幻之地', '夜莺', '猫头鹰')
  );

-- Even the four retained Chinese regression aliases are title-corpus mappings,
-- not manufacturer claims; mark them explicitly lower confidence.
UPDATE knowledge_alias a
SET source = '{"kind":"historical_title_replay","audit":"v5","requires_human_corpus_review":true}'::jsonb,
    confidence = 0.80, review_status = 'approved', active = true, updated_at = now()
FROM knowledge_entity e
WHERE a.entity_id = e.id
  AND e.workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND (
      (e.canonical_name = 'First Steps' AND a.alias = '鞋灯')
      OR (e.canonical_name = 'Dreamland' AND a.alias = '梦幻之地')
      OR (e.canonical_name = 'Night Owls' AND a.alias IN ('夜莺', '猫头鹰'))
  );

INSERT INTO knowledge_audit_log (
    workspace_id, actor_id, action, object_type, object_id, before_value, after_value
)
SELECT kw.id, km.user_id, 'evidence_audit_v5', 'workspace', kw.id,
       '{"published_version":4}'::jsonb,
       jsonb_build_object(
           'policy', 'direct evidence or inactive',
           'bowman_classification', 'brand',
           'bowman_chrome_status', 'rejected_as_ambiguous',
           'model_only_objects', 'rejected'
       )
FROM knowledge_workspace kw
JOIN LATERAL (
    SELECT user_id FROM knowledge_member
    WHERE workspace_id = kw.id AND role = 'admin' LIMIT 1
) km ON true
WHERE kw.name = 'Card Platform';

UPDATE knowledge_workspace
SET revision = revision + 1, updated_at = now()
WHERE name = 'Card Platform';

COMMIT;
