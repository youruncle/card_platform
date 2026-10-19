"""Operation-local knowledge reuse must not leak snapshots between cost requests."""
import asyncio
from copy import deepcopy

import pytest

import card_platform.purchase_cost_knowledge as purchase_knowledge


def snapshot(version=1):
    return dict(version=version, entities=[
        dict(id='player', entity_type='player', canonical_name='Example Player',
             aliases=['示例球员']),
        dict(id='brand', entity_type='brand', canonical_name='Topps'),
        dict(id='product', entity_type='product', canonical_name='Chrome'),
        *[dict(id=number, entity_type='card', canonical_name=number,
               attributes=dict(source_url='https://example.com/checklist',
                               brand='Topps', product='Chrome', season_start=2025,
                               season_end=2025, card_number=number,
                               player='Example Player', insert=insert))
          for number, insert in [('A-1', 'First Insert'), ('B-2', 'Second Insert')]],
    ], official_artifacts=[], relations=[])


class Connection:
    """Only replace the database boundary; build actual knowledge resolvers."""
    def __init__(self, version=1):
        self.version = version
        self.reads = 0

    async def execute(self, sql, params):
        self.reads += 1
        await asyncio.sleep(0)
        keep_all, numbers = params
        data = snapshot(self.version)
        if not keep_all:
            data['entities'] = [entity for entity in data['entities']
                                if entity['entity_type'] != 'card'
                                or entity['id'].lower().replace('-', '') in numbers]

        class Cursor:
            async def fetchone(self):
                return {'snapshot': deepcopy(data)}
        return Cursor()


CARD = dict(player_name='Example Player', brand_name='Topps', product_name='Chrome',
            season_start=2025, card_number='A-1')


def scope():
    assert hasattr(purchase_knowledge, 'purchase_cost_knowledge_scope'), \
        'purchase-cost operation scope is missing'
    return purchase_knowledge.purchase_cost_knowledge_scope()


def scoped(function):
    assert hasattr(purchase_knowledge, 'scoped_purchase_cost_knowledge'), \
        'purchase-cost operation decorator is missing'
    return purchase_knowledge.scoped_purchase_cost_knowledge(function)


@pytest.mark.asyncio
async def test_repeated_stage_loads_share_resolver_and_preserve_scoped_official_fact():
    conn = Connection()
    with scope():
        first = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
        second = await purchase_knowledge.load_purchase_cost_knowledge(conn, dict(CARD))
        assert second is first
        assert second.normalize_identity(CARD)[0]['insert_name'] == 'First Insert'
        assert second.aliases_for('player', 'Example Player') == ('Example Player', '示例球员')
    assert conn.reads == 1


@pytest.mark.asyncio
async def test_normalized_projection_scope_reuses_resolver_for_equivalent_number_sets():
    conn = Connection()
    with scope():
        first = await purchase_knowledge.load_purchase_cost_knowledge(
            conn, [CARD, {**CARD, 'card_number': 'B-2'}])
        second = await purchase_knowledge.load_purchase_cost_knowledge(
            conn, [{**CARD, 'card_number': 'b2'}, {**CARD, 'card_number': 'Ａ-１'}, CARD])
        assert second is first
        assert {'A-1', 'B-2'} <= second.entities.keys()
    assert conn.reads == 1


@pytest.mark.asyncio
async def test_different_projection_does_not_lose_required_official_checklist_fact():
    conn = Connection()
    with scope():
        first = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
        other_card = {**CARD, 'card_number': 'B-2'}
        second = await purchase_knowledge.load_purchase_cost_knowledge(conn, other_card)
        assert second is not first
        assert first.normalize_identity(CARD)[0]['insert_name'] == 'First Insert'
        assert second.normalize_identity(other_card)[0]['insert_name'] == 'Second Insert'
        assert 'B-2' not in first.entities
        assert 'A-1' not in second.entities
    assert conn.reads == 2


@pytest.mark.asyncio
async def test_different_connections_never_share_a_resolver_in_one_scope():
    first_conn, second_conn = Connection(1), Connection(2)
    with scope():
        first = await purchase_knowledge.load_purchase_cost_knowledge(first_conn, CARD)
        second = await purchase_knowledge.load_purchase_cost_knowledge(second_conn, CARD)
        assert first is not second
        assert (first.version, second.version) == (1, 2)


@pytest.mark.asyncio
async def test_next_operation_observes_newly_published_version():
    conn = Connection(1)
    with scope():
        first = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
    conn.version = 2
    with scope():
        second = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
    assert first is not second
    assert (first.version, second.version) == (1, 2)
    assert conn.reads == 2


@pytest.mark.asyncio
async def test_nested_decorated_stages_reuse_outer_operation_scope():
    conn = Connection()

    @scoped
    async def inner():
        return await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)

    @scoped
    async def outer():
        first = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
        with scope():
            second = await inner()
        assert first is second
        assert await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD) is first
        return first

    result = await outer()
    assert result.version == 1
    assert conn.reads == 1
    assert outer.__name__ == 'outer'


@pytest.mark.asyncio
async def test_exception_exit_discards_the_failed_operations_snapshot():
    conn = Connection(1)

    @scoped
    async def failing():
        await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
        raise ValueError('synthetic failed lookup')

    with pytest.raises(ValueError, match='synthetic failed lookup'):
        await failing()
    conn.version = 2
    result = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
    assert result.version == 2
    assert conn.reads == 2


@pytest.mark.asyncio
async def test_parallel_operations_on_one_connection_are_task_isolated():
    conn = Connection()

    @scoped
    async def operation():
        first = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
        await asyncio.sleep(0)
        second = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
        assert second is first
        return first

    first, second = await asyncio.gather(operation(), operation())
    assert first is not second
    assert conn.reads == 2


@pytest.mark.asyncio
async def test_child_operation_does_not_inherit_parents_mutable_resolver_cache():
    conn = Connection()

    @scoped
    async def child():
        return await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)

    with scope():
        parent = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
        descendant = await asyncio.create_task(child())
        assert descendant is not parent
        assert await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD) is parent
    assert conn.reads == 2


@pytest.mark.asyncio
async def test_outside_an_operation_loading_does_not_hold_a_process_wide_snapshot():
    conn = Connection(1)
    first = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
    conn.version = 2
    second = await purchase_knowledge.load_purchase_cost_knowledge(conn, CARD)
    assert first is not second
    assert (first.version, second.version) == (1, 2)
