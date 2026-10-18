"""Project purchase vocabulary and relevant official card facts before transfer.

normalize_identity also enriches from ScopedCardCatalog. Keep all non-card
entities, metadata-bearing card entities, artifact descriptors, and a superset
of the target's checklist number rows. This preserves official Insert/season
facts while avoiding transfer/indexing of the entire unrelated card catalogue.
"""
from card_platform.knowledge import KnowledgeResolver
from card_platform.knowledge_card_catalog import text_key

PURCHASE_KNOWLEDGE_SQL = '''
    SELECT jsonb_build_object(
        'version', kv.snapshot->'version',
        'entities', COALESCE((
            SELECT jsonb_agg(e ORDER BY ordinal)
            FROM jsonb_array_elements(kv.snapshot->'entities') WITH ORDINALITY AS entries(e, ordinal)
            WHERE e->>'entity_type' <> 'card'
               OR %s::boolean
               OR regexp_replace(lower(normalize(COALESCE(e->'attributes'->>'card_number',''), NFKD)),
                                 '[^a-z0-9]', '', 'g') = ANY(%s::text[])
               OR COALESCE(e->'attributes'->>'card_number','') ~ '[^ -~]'
               OR (e->'attributes') ?| ARRAY['catalog_mappings','parallel_options']
        ), '[]'::jsonb),
        'official_artifacts', COALESCE(kv.snapshot->'official_artifacts', '[]'::jsonb),
        'relations', COALESCE(kv.snapshot->'relations', '[]'::jsonb)
    ) AS snapshot
    FROM knowledge_workspace kw
    LEFT JOIN knowledge_version kv ON kv.id=kw.published_version_id
    WHERE kw.name='Card Platform'
'''


async def load_purchase_cost_snapshot(conn, cards=None):
    # Catalog lookup never changes card_number. For ASCII text keys the SQL
    # normalizer handles printable ASCII; unusual source keys are also retained
    # (Python casefold has expansions such as ß -> ss that SQL lower lacks).
    # Unusual non-ASCII keys retain all rows instead of silently losing facts.
    if isinstance(cards, dict):
        cards = [cards]
    numbers = sorted({text_key(c.get('card_number')) for c in cards or []} - {''})
    keep_all = cards is None or any(not number.isascii() for number in numbers)
    row = await (await conn.execute(PURCHASE_KNOWLEDGE_SQL, (keep_all, numbers))).fetchone()
    return row['snapshot'] if row else None


async def load_purchase_cost_knowledge(conn, cards=None):
    return KnowledgeResolver(await load_purchase_cost_snapshot(conn, cards))
