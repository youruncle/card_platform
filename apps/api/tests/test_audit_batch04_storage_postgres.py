"""Batch storage must survive overlapping writers without losing atomicity.

Connections and locks are real PostgreSQL. The proxy only coordinates execution
at SQL boundaries; no INSERT, exception, commit or rollback is mocked.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
import os
from uuid import uuid4

import psycopg
from psycopg.errors import CheckViolation, DeadlockDetected
from psycopg.rows import dict_row
import pytest

from card_platform.valuation_worker import (
    AutomatedValuationWorker, ComparableSearchResult, DiscoveredComparable,
)

pytestmark = pytest.mark.skipif(
    'TEST_DATABASE_URL' not in os.environ, reason='requires isolated migrated PostgreSQL',
)


def comparable(number, **updates):
    return DiscoveredComparable(
        title=f'2024-25 Panini Mosaic Thunder Road #8 candidate {number}',
        source_url=f'https://cardhobby.com.cn/Market/Item/{number}',
        occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        sale_price=Decimal('33'), currency='CNY', fx_rate_to_base=Decimal('1'),
        match_confidence=Decimal('.9'), source_code='cardhobby',
        **updates,
    )


class ObservedDatabase:
    def __init__(self, hook=None, *, deadlock_timeout='100ms'):
        self.hook = hook
        self.deadlock_timeout = deadlock_timeout
        self.errors = []
        self.connections = []

    @asynccontextmanager
    async def connection(self):
        async with await psycopg.AsyncConnection.connect(
            os.environ['TEST_DATABASE_URL'], row_factory=dict_row,
            options=f'-c deadlock_timeout={self.deadlock_timeout} -c statement_timeout=8000',
        ) as connection:
            self.connections.append(connection.info.backend_pid)
            proxy = ObservedConnection(connection, self)
            yield proxy


class ObservedConnection:
    def __init__(self, connection, database):
        self.connection = connection
        self.database = database
        self.inserts = 0

    def transaction(self):
        return self.connection.transaction()

    async def execute(self, query, params=None):
        insert = 'INSERT INTO market_comp (' in query
        if insert:
            self.inserts += 1
            if self.database.hook:
                await self.database.hook(self, 'before', params)
        try:
            result = await self.connection.execute(query, params)
        except psycopg.Error as error:
            self.database.errors.append(error.sqlstate)
            raise
        if insert and self.database.hook:
            await self.database.hook(self, 'after', params)
        return result


@asynccontextmanager
async def definition():
    """A committed synthetic definition, exclusively in TEST_DATABASE_URL."""
    async with await psycopg.AsyncConnection.connect(
        os.environ['TEST_DATABASE_URL'], row_factory=dict_row,
    ) as c:
        key = str(uuid4())
        brand = (await (await c.execute(
            'INSERT INTO brand(name) VALUES(%s) RETURNING id', (key,),
        )).fetchone())['id']
        player = (await (await c.execute(
            'INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id', (key, key),
        )).fetchone())['id']
        card_set = (await (await c.execute(
            "INSERT INTO card_set(brand_id,season_start,product_name) VALUES(%s,2024,'Mosaic') RETURNING id",
            (brand,),
        )).fetchone())['id']
        card = (await (await c.execute(
            "INSERT INTO card_definition(card_set_id,player_id,card_number) VALUES(%s,%s,'8') RETURNING id",
            (card_set, player),
        )).fetchone())['id']
        await c.commit()
        try:
            yield {'card_definition_id': card}
        finally:
            await c.execute('DELETE FROM market_comp WHERE card_definition_id=%s', (card,))
            await c.execute('DELETE FROM card_definition WHERE id=%s', (card,))
            await c.execute('DELETE FROM card_set WHERE id=%s', (card_set,))
            await c.execute('DELETE FROM player WHERE id=%s', (player,))
            await c.execute('DELETE FROM brand WHERE id=%s', (brand,))
            await c.commit()


def worker(database):
    result = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    result.database = database
    return result


async def stored(identity):
    async with await psycopg.AsyncConnection.connect(
        os.environ['TEST_DATABASE_URL'], row_factory=dict_row,
    ) as c:
        return await (await c.execute(
            'SELECT external_id,listing_title,raw_payload FROM market_comp WHERE card_definition_id=%s ORDER BY external_id',
            (identity['card_definition_id'],),
        )).fetchall()


def test_opposite_search_orders_do_not_deadlock_or_duplicate_sales():
    asyncio.run(opposite_orders())


async def opposite_orders():
    async with definition() as identity:
        ready = asyncio.Event()
        release = asyncio.Event()
        started = set()
        locked = set()

        async def interleave(proxy, stage, _params):
            if proxy.inserts != 1:
                return
            pid = proxy.connection.info.backend_pid
            if stage == 'before':
                started.add(pid)
                if len(started) == 2:
                    ready.set()
                await ready.wait()
            else:
                locked.add(pid)
                await release.wait()

        db = ObservedDatabase(interleave)
        a, b = comparable(138470576), comparable(141133175)
        writes = [asyncio.create_task(worker(db)._store_comparables(
            identity, ComparableSearchResult(comparables=items),
        )) for items in ([a, b], [b, a])]
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL']) as observer:
            async def release_when_contended():
                await ready.wait()
                while len(locked) < 2:
                    blocked = await (await observer.execute(
                        'SELECT count(*) FROM pg_stat_activity WHERE pid=ANY(%s) AND cardinality(pg_blocking_pids(pid))>0',
                        (list(started),),
                    )).fetchone()
                    await observer.rollback()
                    if blocked[0]:
                        break
                    await asyncio.sleep(.01)
                release.set()
            await asyncio.wait_for(release_when_contended(), 5)
        outcomes = await asyncio.wait_for(asyncio.gather(*writes, return_exceptions=True), 10)
        assert not [str(x) for x in outcomes if isinstance(x, BaseException)], outcomes
        assert db.errors == [], 'opposite batch order must not create a deadlock'
        assert [row['external_id'] for row in await stored(identity)] == ['item:138470576', 'item:141133175']


def test_real_deadlock_rolls_back_then_retries_the_complete_batch():
    asyncio.run(real_deadlock())


async def real_deadlock():
    async with definition() as identity:
        search = ComparableSearchResult(comparables=[comparable(138470576), comparable(141133175)])
        await worker(ObservedDatabase())._store_comparables(identity, search)
        a_locked = asyncio.Event()
        b_attempted = asyncio.Event()
        first = True

        async def interleave(proxy, stage, params):
            nonlocal first
            if not first:
                return
            if stage == 'after' and proxy.inserts == 1:
                a_locked.set()
            if stage == 'before' and proxy.inserts == 2:
                first = False
                b_attempted.set()

        db = ObservedDatabase(interleave, deadlock_timeout='50ms')
        async with await psycopg.AsyncConnection.connect(
            os.environ['TEST_DATABASE_URL'], options='-c deadlock_timeout=5s -c statement_timeout=8000',
        ) as peer:
            await peer.execute(
                "SELECT id FROM market_comp WHERE card_definition_id=%s AND external_id='item:141133175' FOR UPDATE",
                (identity['card_definition_id'],),
            )
            task = asyncio.create_task(worker(db)._store_comparables(identity, search))
            await asyncio.wait_for(a_locked.wait(), 3)
            await asyncio.wait_for(b_attempted.wait(), 3)
            await peer.execute(
                "UPDATE market_comp SET listing_title='peer committed' WHERE card_definition_id=%s AND external_id='item:138470576'",
                (identity['card_definition_id'],),
            )
            await peer.commit()
            await asyncio.wait_for(task, 10)
        assert db.errors == ['40P01']
        assert len(db.connections) == 2
        assert len(await stored(identity)) == 2


@pytest.mark.parametrize('sqlstate,error_type,attempts', [
    ('40P01', DeadlockDetected, 3), ('23514', CheckViolation, 1),
])
def test_exhausted_deadlocks_and_nonretryable_errors_leave_no_partial_batch(sqlstate, error_type, attempts):
    asyncio.run(failed_batch(sqlstate, error_type, attempts))


async def failed_batch(sqlstate, error_type, attempts):
    async with definition() as identity:
        async def fail_second(proxy, stage, _params):
            if stage == 'before' and proxy.inserts == 2:
                # PostgreSQL raises the exception and aborts the real transaction.
                await proxy.connection.execute(
                    f"DO $$ BEGIN RAISE EXCEPTION 'audit batch storage sentinel' USING ERRCODE='{sqlstate}'; END $$",
                )
        db = ObservedDatabase(fail_second)
        with pytest.raises(error_type, match='audit batch storage sentinel'):
            await worker(db)._store_comparables(identity, ComparableSearchResult(
                comparables=[comparable(138470576), comparable(141133175)],
            ))
        assert len(db.connections) == attempts
        assert await stored(identity) == []


def test_normal_batch_preserves_payload_and_is_idempotent():
    asyncio.run(normal_batch())


async def normal_batch():
    async with definition() as identity:
        db = ObservedDatabase()
        search = ComparableSearchResult(comparables=[comparable(
            141133175, visual_verdict='uncertain', visual_confidence=Decimal('.4'),
            visual_reasons=['condition_unknown'], evidence_level='ended_auction',
        )])
        await worker(db)._store_comparables(identity, search)
        await worker(db)._store_comparables(identity, search)
        rows = await stored(identity)
        assert len(rows) == 1
        assert rows[0]['raw_payload']['visual_verdict'] == 'uncertain'
        assert rows[0]['raw_payload']['visual_reasons'] == ['condition_unknown']
        assert rows[0]['raw_payload']['evidence_level'] == 'ended_auction'


def test_exhausted_storage_failure_stays_failed_with_original_diagnostic_and_old_reference():
    asyncio.run(failed_job())


async def failed_job():
    from card_platform.repository import PortfolioRepository
    from card_platform.schemas import CardIdentityInput, CollectionItemCreate

    async with await psycopg.AsyncConnection.connect(
        os.environ['TEST_DATABASE_URL'], row_factory=dict_row,
    ) as c:
        try:
            owner = (await (await c.execute(
                "INSERT INTO app_user(email,display_name) VALUES(%s,'batch storage test') RETURNING id",
                (f'{uuid4()}@example.com',),
            )).fetchone())['id']
            fields = dict(player_name=f'Batch Storage {uuid4()}', brand_name='Panini',
                          product_name='Mosaic', season_start=2024, season_end=2025,
                          card_number='8', insert_name='Thunder Road', parallel_name='Green Mosaic')
            repo = PortfolioRepository()
            item = await repo.create_item(c, CollectionItemCreate(owner_id=owner, card=CardIdentityInput(**fields)))
            await c.execute('DELETE FROM valuation_job WHERE collection_item_id=%s', (item,))
            job = (await (await c.execute(
                "INSERT INTO valuation_job(collection_item_id,status) VALUES(%s,'identifying') RETURNING id",
                (item,),
            )).fetchone())['id']
            await c.execute("""INSERT INTO valuation_snapshot(collection_item_id,method,currency,
                low_value,fair_value,high_value,confidence,evidence,model_version,valued_at)
                VALUES(%s,'ai_estimate','CNY',20,50,100,.3,
                '{"pricing_basis":"codex_independent_fallback","source_analyzed_at":"2026-09-21"}',
                'codex-independent-fallback-v1',now()-interval '2 days')""", (item,))
            definition_id = (await (await c.execute(
                'SELECT card_definition_id FROM collection_item WHERE id=%s', (item,),
            )).fetchone())['card_definition_id']

            async def fail_second(proxy, stage, _params):
                if stage == 'before' and proxy.inserts == 2:
                    await proxy.connection.execute(
                        "DO $$ BEGIN RAISE EXCEPTION 'original batch storage deadlock diagnostic' USING ERRCODE='40P01'; END $$",
                    )

            db = ObservedDatabase(fail_second)

            @asynccontextmanager
            async def same_connection():
                db.connections.append(c.info.backend_pid)
                yield ObservedConnection(c, db)

            db.connection = same_connection
            w = worker(db)
            w.knowledge_runtime = None

            async def load(_job):
                return {**fields, 'collection_item_id': item, 'card_definition_id': definition_id}

            async def search(_identity):
                return ComparableSearchResult(comparables=[comparable(138470576), comparable(141133175)])

            w._load_identity = load
            w._search = search
            await w._process(job)
            result = (await repo.list_items(c, owner, item_ids=[item]))[0]
            assert result.valuation_status == 'failed'
            assert result.valuation_error.startswith('original batch storage deadlock diagnostic\nCONTEXT:')
            assert result.valuation_result_summary['reason'] == 'valuation worker failed'
            assert result.valuation_result_summary['historical_reference']['current_job_verified'] is False
            assert result.fair_value == Decimal('50')
            count = (await (await c.execute(
                'SELECT count(*) AS n FROM market_comp WHERE card_definition_id=%s', (definition_id,),
            )).fetchone())['n']
            assert count == 0
        finally:
            await c.rollback()
