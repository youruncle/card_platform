"""Project purchase vocabulary and relevant official card facts before transfer.

normalize_identity also enriches from ScopedCardCatalog. Keep all non-card
entities, metadata-bearing card entities, artifact descriptors, and a superset
of the target's checklist number rows. This preserves official Insert/season
facts while avoiding transfer/indexing of the entire unrelated card catalogue.
"""
import asyncio
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps

from card_platform.knowledge import KnowledgeResolver
from card_platform.knowledge_card_catalog import text_key

_operation_knowledge = ContextVar('purchase_cost_operation_knowledge', default=None)


def purchase_cost_score_cache(knowledge):
    """Derived pure scores share exactly the resolver operation's lifetime."""
    operation = _operation_knowledge.get()
    if knowledge is None or operation is None or operation[0] is not asyncio.current_task():
        return None
    key = ('pure_scores', id(knowledge))
    cached = operation[1].get(key)
    if cached is not None and cached[0] is knowledge:
        return cached[1]
    cache = OrderedDict()
    operation[1][key] = (knowledge, cache)
    return cache


def claim_purchase_observation_attempt(knowledge, inputs):
    """Bound repeated external attempts, without caching a decision or authority."""
    from card_platform.purchase_cost_archive import fingerprint
    operation = _operation_knowledge.get()
    if knowledge is None or operation is None or operation[0] is not asyncio.current_task():
        return True
    key = ('observation_attempts', id(knowledge))
    cached = operation[1].get(key)
    if cached is None or cached[0] is not knowledge:
        cached = (knowledge, OrderedDict())
        operation[1][key] = cached
    attempts = cached[1]
    signature = fingerprint([getattr(knowledge, 'version', None), inputs])
    if signature in attempts:
        attempts.move_to_end(signature)
        return False
    attempts[signature] = None
    if len(attempts) > 4096:
        attempts.popitem(last=False)
    return True


def memoize_purchase_value(knowledge, namespace, inputs, build):
    """Reuse derived pure values within the task, resolver and input revision."""
    from card_platform.purchase_cost_archive import fingerprint
    cache = purchase_cost_score_cache(knowledge)
    if cache is None:
        return build()
    key = (namespace, fingerprint([getattr(knowledge, 'version', None), inputs]))
    if key in cache:
        cache.move_to_end(key)
        return deepcopy(cache[key])
    value = build()
    cache[key] = deepcopy(value)
    if len(cache) > 4096:
        cache.popitem(last=False)
    return value


@contextmanager
def purchase_cost_knowledge_scope():
    """Reuse projected knowledge only within the current async cost operation."""
    task = asyncio.current_task()
    previous = _operation_knowledge.get()
    if previous is not None and previous[0] is task:
        yield
        return
    # ContextVars propagate to child tasks; the task binding prevents those
    # independent operations from inheriting this mutable resolver cache.
    cache = {}
    token = _operation_knowledge.set((task, cache))
    try:
        yield
    finally:
        cache.clear()
        _operation_knowledge.reset(token)


def scoped_purchase_cost_knowledge(operation):
    """Establish a scope around an async service entry point, nesting safely."""
    @wraps(operation)
    async def scoped(*args, **kwargs):
        with purchase_cost_knowledge_scope():
            return await operation(*args, **kwargs)
    return scoped


def _projection_arguments(cards):
    if isinstance(cards, dict):
        cards = [cards]
    numbers = tuple(sorted({text_key(c.get('card_number')) for c in cards or []} - {''}))
    return cards is None or any(not number.isascii() for number in numbers), numbers


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
    keep_all, numbers = _projection_arguments(cards)
    row = await (await conn.execute(PURCHASE_KNOWLEDGE_SQL, (keep_all, list(numbers)))).fetchone()
    return row['snapshot'] if row else None


async def load_purchase_cost_knowledge(conn, cards=None):
    operation = _operation_knowledge.get()
    if operation is None or operation[0] is not asyncio.current_task():
        return KnowledgeResolver(await load_purchase_cost_snapshot(conn, cards))
    # Materialize an iterable once: projection key calculation must not consume
    # a generator before the SQL loader derives the same projection arguments.
    if cards is not None and not isinstance(cards, dict):
        cards = tuple(cards)
    key = (id(conn), *_projection_arguments(cards))
    cache = operation[1]
    cached = cache.get(key)
    if cached is not None and cached[0] is conn:
        return cached[1]
    resolver = KnowledgeResolver(await load_purchase_cost_snapshot(conn, cards))
    # Retain the connection reference as well as its identity until scope exit;
    # a closed/collected connection cannot have its id reused by another one.
    cache[key] = (conn, resolver)
    return resolver
