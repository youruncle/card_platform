BEGIN;

-- A brand is not an alias of its owner/manufacturer. Remove inherited Topps
-- aliases from Fanatics Collectibles and all remaining aliases whose evidence
-- was only another product's checklist.
UPDATE knowledge_alias a
SET active = false, review_status = 'rejected', updated_at = now()
FROM knowledge_entity e
WHERE a.entity_id = e.id
  AND e.workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND a.active
  AND NOT (
      (e.canonical_name = 'First Steps' AND a.alias = '鞋灯')
      OR (e.canonical_name = 'Dreamland' AND a.alias = '梦幻之地')
      OR (e.canonical_name = 'Night Owls' AND a.alias IN ('夜莺', '猫头鹰'))
      OR (e.canonical_name = 'RC' AND a.alias = 'Rookie')
      OR (e.canonical_name = 'RPA' AND a.alias = 'Rookie Patch Autograph')
      OR (e.canonical_name = 'SSP' AND a.alias = 'Super Short Print')
      OR (e.canonical_name = 'Topps Chrome' AND a.alias = 'Topps Chrome Basketball')
  );

UPDATE knowledge_entity
SET attributes = jsonb_set(attributes, '{manufacturer}', '"Fanatics Collectibles"'::jsonb),
    updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND entity_type = 'product' AND canonical_name = 'Topps Chrome';

UPDATE knowledge_relation
SET source = '{"source_key":"beckett-2025-topps-midnight","audit":"v5"}'::jsonb,
    reason = '同一产品与赛季内的两个不同 insert 身份，单卡 identity 不可合并。',
    updated_at = now()
WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND subject_entity_id = (
      SELECT id FROM knowledge_entity
      WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
        AND entity_type = 'insert' AND canonical_name = 'Dreamland'
  )
  AND object_entity_id = (
      SELECT id FROM knowledge_entity
      WHERE workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
        AND entity_type = 'insert' AND canonical_name = 'Night Owls'
  );

-- Replace stale planning-only coverage for now-verified products.
UPDATE knowledge_coverage c
SET coverage_status = 'covered',
    season_start = 2023, season_end = 2024,
    source_id = s.id,
    notes = '产品身份与该赛季 checklist 已验证；insert/parallel 仍需单独矩阵行。',
    updated_at = now()
FROM knowledge_source s, knowledge_entity p
WHERE c.workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND p.id = c.product_entity_id
  AND p.canonical_name = 'National Treasures'
  AND s.workspace_id = c.workspace_id AND s.source_key = 'beckett-2023-nt';

UPDATE knowledge_coverage c
SET coverage_status = 'covered',
    season_start = 2023, season_end = 2024,
    source_id = s.id,
    notes = '产品身份与该赛季 checklist 已验证；insert/parallel 仍需单独矩阵行。',
    updated_at = now()
FROM knowledge_source s, knowledge_entity p
WHERE c.workspace_id = (SELECT id FROM knowledge_workspace WHERE name = 'Card Platform')
  AND p.id = c.product_entity_id
  AND p.canonical_name = 'One and One'
  AND s.workspace_id = c.workspace_id AND s.source_key = 'beckett-2023-one-one';

INSERT INTO knowledge_coverage (
    workspace_id, manufacturer_entity_id, product_entity_id, season_start, season_end,
    insert_entity_id, coverage_status, source_id, notes
)
SELECT kw.id, manufacturer.id, product.id, 2019, 2020, insert_entity.id,
       'covered', source.id,
       'Panini 官方 PIS 直接确认 First Steps 属于 2019-20 Court Kings。'
FROM knowledge_workspace kw
JOIN knowledge_entity manufacturer ON manufacturer.workspace_id = kw.id
    AND manufacturer.entity_type = 'manufacturer' AND manufacturer.canonical_name = 'Panini'
JOIN knowledge_entity product ON product.workspace_id = kw.id
    AND product.entity_type = 'product' AND product.canonical_name = 'Court Kings'
JOIN knowledge_entity insert_entity ON insert_entity.workspace_id = kw.id
    AND insert_entity.entity_type = 'insert' AND insert_entity.canonical_name = 'First Steps'
JOIN knowledge_source source ON source.workspace_id = kw.id
    AND source.source_key = 'panini-2019-court-kings-pis'
WHERE kw.name = 'Card Platform'
ON CONFLICT (
    workspace_id, manufacturer_entity_id, product_entity_id, season_start,
    season_end, insert_entity_id, parallel_entity_id
) DO UPDATE SET coverage_status = EXCLUDED.coverage_status,
                source_id = EXCLUDED.source_id, notes = EXCLUDED.notes,
                updated_at = now();

INSERT INTO knowledge_coverage (
    workspace_id, manufacturer_entity_id, product_entity_id, season_start, season_end,
    insert_entity_id, coverage_status, source_id, notes
)
SELECT kw.id, manufacturer.id, product.id, 2025, 2026, insert_entity.id,
       'covered', source.id,
       'Beckett checklist 直接列出该 2025-26 Topps Midnight insert 与赔率。'
FROM knowledge_workspace kw
JOIN knowledge_entity manufacturer ON manufacturer.workspace_id = kw.id
    AND manufacturer.entity_type = 'manufacturer'
    AND manufacturer.canonical_name = 'Fanatics Collectibles'
JOIN knowledge_entity product ON product.workspace_id = kw.id
    AND product.entity_type = 'product' AND product.canonical_name = 'Topps Midnight'
JOIN knowledge_entity insert_entity ON insert_entity.workspace_id = kw.id
    AND insert_entity.entity_type = 'insert'
    AND insert_entity.canonical_name IN ('Dreamland', 'Night Owls')
JOIN knowledge_source source ON source.workspace_id = kw.id
    AND source.source_key = 'beckett-2025-topps-midnight'
WHERE kw.name = 'Card Platform'
ON CONFLICT (
    workspace_id, manufacturer_entity_id, product_entity_id, season_start,
    season_end, insert_entity_id, parallel_entity_id
) DO UPDATE SET coverage_status = EXCLUDED.coverage_status,
                source_id = EXCLUDED.source_id, notes = EXCLUDED.notes,
                updated_at = now();

UPDATE knowledge_workspace
SET revision = revision + 1, updated_at = now()
WHERE name = 'Card Platform';

COMMIT;
