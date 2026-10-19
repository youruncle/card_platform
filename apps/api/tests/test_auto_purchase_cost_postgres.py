"""Automatic ingestion costs; disposable DB and invented orders only."""
import asyncio
import os
from decimal import Decimal
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from test_purchase_cost_postgres import client, register, item, order

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires migrated PostgreSQL')


def test_synced_batch_uses_complete_history_resolution_instead_of_text_only_preview(client, monkeypatch):
    from card_platform import cardhobby_account_api
    from card_platform.purchase_cost_service import PurchaseCostService
    owner = register(client)
    card = item(client, owner)
    class Account:
        async def archive_orders(self, conn, owner):
            return [order()]
    monkeypatch.setattr(cardhobby_account_api, 'account_service', lambda:Account())
    service = PurchaseCostService()
    service.resolve_search = AsyncMock(return_value={'status':'needs_confirmation','reason':'Two owned purchases'})
    async def run():
        async with await connection() as conn:
            job = await service.create_job(conn, UUID(owner), 1, 10, uuid4(), 'synced')
            await conn.commit()
            assert await service.step(conn)
            row = await (await conn.execute('SELECT results FROM cardhobby_cost_job WHERE id=%s',
                                            (UUID(str(job['id'])),))).fetchone()
            assert row['results'][0]['status'] == 'needs_confirmation'
            assert row['results'][0]['item_id'] == card
    asyncio.run(run())

async def connection():
    return await AsyncConnection.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row)


def test_new_item_enqueues_once_and_owner_isolated(client):
    owner = register(client)
    card = item(client, owner)
    async def check():
        async with await connection() as conn:
            rows = await (await conn.execute('SELECT * FROM automatic_purchase_cost WHERE item_id=%s', (card,))).fetchall()
            assert len(rows) == 1
            assert str(rows[0]['owner_id']) == owner
            assert rows[0]['status'] == 'queued'
    asyncio.run(check())
    response = client.get(f'/v1/purchase-cost/items/{card}/automatic')
    assert response.status_code == 200
    assert response.json()['status'] == 'queued'
    assert response.json()['worker_enabled'] is False
    register(client)
    assert client.get(f'/v1/purchase-cost/items/{card}/automatic').status_code == 404


def test_explicit_purchase_does_not_enqueue(client):
    owner = register(client)
    card = item(client, owner, purchase=dict(price='9', transacted_at='2026-01-01T00:00:00Z'))
    async def check():
        async with await connection() as conn:
            assert await (await conn.execute('SELECT * FROM automatic_purchase_cost WHERE item_id=%s', (card,))).fetchone() is None
    asyncio.run(check())


@pytest.mark.parametrize('outcome', ['recorded', 'not_found', 'needs_confirmation'])
def test_worker_reuses_live_search_and_never_refreshes(client, outcome):
    from card_platform.auto_purchase_cost import process_automatic_cost
    from card_platform.purchase_cost import OrderCapture
    from card_platform.purchase_cost_service import PurchaseCostService
    owner = register(client)
    card = item(client, owner)
    service = PurchaseCostService()
    service.resolve_search = AsyncMock(return_value={'status':outcome, 'reason':'fixture'})
    account = AsyncMock()
    account.search.return_value = {'orders':[order()], 'complete':True}
    async def check():
        async with await connection() as conn:
            # Other tests' pending work is irrelevant to this worker fixture.
            await conn.execute("UPDATE automatic_purchase_cost SET status='completed' WHERE item_id<>%s", (card,))
            await conn.commit()
            assert await process_automatic_cost(conn, service, lambda:account)
            account.search.assert_awaited_once()
            args = service.resolve_search.await_args
            assert args.args[1:3] == (UUID(owner), UUID(card))
            assert isinstance(args.args[3][0], OrderCapture)
            assert args.kwargs == {'refresh':False}
            row = await (await conn.execute('SELECT * FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone()
            assert row['result']['status'] == outcome
            assert row['status'] == 'completed'
            assert not await process_automatic_cost(conn, service, lambda:account)
    asyncio.run(check())


def test_real_matching_writes_cost_once_and_keeps_evidence(client):
    from card_platform.auto_purchase_cost import process_automatic_cost
    from card_platform.purchase_cost_service import PurchaseCostService
    owner = register(client)
    card = item(client, owner)
    account = AsyncMock()
    account.search.return_value = {'orders':[order()], 'complete':True}
    async def check():
        async with await connection() as conn:
            await conn.execute("UPDATE automatic_purchase_cost SET status='completed' WHERE item_id<>%s", (card,))
            await conn.commit()
            assert await process_automatic_cost(conn, PurchaseCostService(), lambda:account)
            row = await (await conn.execute('SELECT result FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone()
            assert row['result']['status'] == 'recorded', row
            buy = await (await conn.execute('SELECT unit_price,fee_amount FROM card_transaction WHERE collection_item_id=%s',(card,))).fetchall()
            assert len(buy) == 1
            assert buy[0]['unit_price'] + buy[0]['fee_amount'] == Decimal('11')
            # Force redelivery to prove the cost itself is idempotent.
            await conn.execute("UPDATE automatic_purchase_cost SET status='queued',run_after=now() WHERE item_id=%s",(card,))
            await conn.commit()
            assert await process_automatic_cost(conn, PurchaseCostService(), lambda:account)
            assert account.search.await_count == 1
            rows = await (await conn.execute('SELECT id FROM card_transaction WHERE collection_item_id=%s',(card,))).fetchall()
            assert len(rows) == 1
    asyncio.run(check())
    assert client.get(f'/v1/purchase-cost/items/{card}/audit').json()['evidence']


@pytest.mark.parametrize('mode', ['not_found','ambiguous','incomplete','manual','verification','network'])
def test_guarded_results_preserve_unknown_or_manual_cost(client, mode):
    from card_platform.auto_purchase_cost import process_automatic_cost
    from card_platform.cardhobby_account import AccountError
    from card_platform.purchase_cost_service import PurchaseCostService
    owner = register(client)
    card = item(client, owner)
    account = AsyncMock()
    orders = [] if mode == 'not_found' else [order()]
    if mode == 'ambiguous':
        orders.append(order(item_id='900003', external_order_id='900004'))
    account.search.return_value = {'orders':orders, 'complete':mode!='incomplete'}
    if mode == 'verification':
        account.search.side_effect = AccountError('VERIFICATION_REQUIRED','fixture')
    if mode == 'network':
        account.search.side_effect = RuntimeError('private data must not be logged')
    async def check():
        async with await connection() as conn:
            await conn.execute("UPDATE automatic_purchase_cost SET status='completed' WHERE item_id<>%s", (card,))
            if mode == 'manual':
                await conn.execute("INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,currency,fx_rate_to_base) VALUES(%s,'buy',now(),1,99,'CNY',1)",(card,))
            await conn.commit()
            for _ in range(3 if mode=='network' else 1):
                assert await process_automatic_cost(conn, PurchaseCostService(), lambda:account)
                await conn.execute('UPDATE automatic_purchase_cost SET run_after=now() WHERE item_id=%s',(card,))
                await conn.commit()
            row = await (await conn.execute('SELECT * FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone()
            assert row['status'] == ('failed' if mode=='network' else 'completed')
            if mode=='network':
                assert row['attempts'] == 3
            if mode=='verification':
                assert row['result']['status'] == 'account_required'
            transactions = await (await conn.execute('SELECT unit_price FROM card_transaction WHERE collection_item_id=%s',(card,))).fetchall()
            assert len(transactions) == (1 if mode=='manual' else 0)
            if mode=='manual':
                assert transactions[0]['unit_price'] == 99
                account.search.assert_not_awaited()
            assert not await process_automatic_cost(conn, PurchaseCostService(), lambda:account)
    asyncio.run(check())


def test_ingestion_rollback_removes_card_and_outbox(client):
    from card_platform.repository import PortfolioRepository
    from card_platform.schemas import CollectionItemCreate
    owner = register(client)
    async def check():
        async with await connection() as conn:
            card = None
            with pytest.raises(RuntimeError):
                async with conn.transaction():
                    payload = CollectionItemCreate(owner_id=owner, card=dict(player_name='Rollback Fixture',brand_name='Topps',product_name='Test',season_start=2025))
                    card = await PortfolioRepository().create_item(conn,payload)
                    raise RuntimeError('abort ingestion')
            assert card
            assert await (await conn.execute('SELECT item_id FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone() is None
            assert await (await conn.execute('SELECT id FROM collection_item WHERE id=%s',(card,))).fetchone() is None
    asyncio.run(check())


def test_network_failure_preserves_account_cooldown(client):
    from card_platform.auto_purchase_cost import process_automatic_cost
    from card_platform.cardhobby_account import AccountError
    from card_platform.purchase_cost_service import PurchaseCostService
    owner = register(client)
    card = item(client, owner)
    account = AsyncMock()
    async def search(conn, owner, keywords):
        await conn.execute("INSERT INTO cardhobby_account(owner_id,retry_after) VALUES(%s,now()+interval '60 seconds') ON CONFLICT(owner_id) DO UPDATE SET retry_after=EXCLUDED.retry_after",(owner,))
        await asyncio.sleep(1)  # Backoff must start after the remote attempt finishes.
        raise AccountError('NETWORK_ERROR','fixture')
    account.search.side_effect = search
    async def check():
        async with await connection() as conn:
            await conn.execute("UPDATE automatic_purchase_cost SET status='completed' WHERE item_id<>%s", (card,))
            await conn.commit()
            assert await process_automatic_cost(conn, PurchaseCostService(), lambda:account)
            row = await (await conn.execute('SELECT retry_after>now() AS cooling FROM cardhobby_account WHERE owner_id=%s',(owner,))).fetchone()
            assert row and row['cooling']
            job = await (await conn.execute('SELECT status,attempts FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone()
            assert job == {'status':'queued','attempts':1}
            delay = await (await conn.execute('SELECT extract(epoch FROM (run_after-clock_timestamp())) AS seconds FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone()
            assert delay['seconds'] > Decimal('59.5')
    asyncio.run(check())


def test_parallel_worker_skips_locked_job_and_manual_cost_wins(client):
    from card_platform.auto_purchase_cost import process_automatic_cost
    from card_platform.purchase_cost_service import PurchaseCostService
    owner = register(client)
    card = item(client, owner)
    async def check():
        started, release = asyncio.Event(), asyncio.Event()
        account = AsyncMock()
        async def search(*args):
            started.set()
            await release.wait()
            return {'orders':[order()], 'complete':True}
        account.search.side_effect = search
        async with await connection() as first, await connection() as second:
            await first.execute("UPDATE automatic_purchase_cost SET status='completed' WHERE item_id<>%s", (card,))
            await first.commit()
            running = asyncio.create_task(process_automatic_cost(first, PurchaseCostService(), lambda:account))
            try:
                await asyncio.wait_for(started.wait(), 5)
                assert not await process_automatic_cost(second, PurchaseCostService(), lambda:account)
                await second.execute("INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,currency,fx_rate_to_base) VALUES(%s,'buy',now(),1,99,'CNY',1)",(card,))
                await second.commit()
            finally:
                release.set()
            assert await running
            transactions = await (await first.execute('SELECT unit_price FROM card_transaction WHERE collection_item_id=%s',(card,))).fetchall()
            assert transactions == [{'unit_price':Decimal('99')}]
            result = await (await first.execute('SELECT result FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone()
            assert result['result']['status']=='skipped'
    asyncio.run(check())


def test_cancelled_worker_leaves_durable_job_for_restart(client):
    from card_platform.auto_purchase_cost import process_automatic_cost
    from card_platform.purchase_cost_service import PurchaseCostService
    owner = register(client)
    card = item(client, owner)
    async def check():
        started = asyncio.Event()
        account = AsyncMock()
        async def stalled(*args):
            started.set()
            await asyncio.Event().wait()
        account.search.side_effect = stalled
        async with await connection() as conn:
            await conn.execute("UPDATE automatic_purchase_cost SET status='completed' WHERE item_id<>%s", (card,))
            await conn.commit()
            running = asyncio.create_task(process_automatic_cost(conn, PurchaseCostService(), lambda:account))
            await asyncio.wait_for(started.wait(),5)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        async with await connection() as conn:
            job = await (await conn.execute('SELECT status,attempts FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone()
            assert job == {'status':'queued','attempts':0}
            await conn.commit()
            account.search.side_effect = None
            account.search.return_value = {'orders':[], 'complete':True}
            assert await process_automatic_cost(conn, PurchaseCostService(), lambda:account)
            job = await (await conn.execute('SELECT result FROM automatic_purchase_cost WHERE item_id=%s',(card,))).fetchone()
            assert job['result']['status']=='not_found'
    asyncio.run(check())
