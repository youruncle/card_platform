"""Sold status round trip on isolated fixtures; all data rolls back."""
import asyncio
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from card_platform.repository import PortfolioRepository, NotFoundError
from card_platform.schemas import CollectionItemCreate, CardIdentityInput, PortfolioItemQuickUpdate
from card_platform.news_alerts import research_context
from card_platform.portfolio_search import SearchFilters, search_item_ids

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires migrated test PostgreSQL')


def test_sold_restore_and_owner_isolation():
    asyncio.run(check())


async def check():
    async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row) as c:
        try:
            owner = (await (await c.execute("INSERT INTO app_user(email,display_name) VALUES(%s,'sold test') RETURNING id", (f'{uuid4()}@example.com',))).fetchone())['id']
            other = (await (await c.execute("INSERT INTO app_user(email,display_name) VALUES(%s,'other') RETURNING id", (f'{uuid4()}@example.com',))).fetchone())['id']
            repo = PortfolioRepository()
            item = await repo.create_item(c, CollectionItemCreate(owner_id=owner, card=CardIdentityInput(player_name=f'Sold test {uuid4()}', brand_name='Test', season_start=2025, product_name='Test')))
            # Cards without a recorded purchase are still held and must be counted.
            assert (await repo.summary(c, owner)).item_count == 1
            await c.execute("INSERT INTO card_transaction(collection_item_id,transaction_type,quantity,unit_price,currency,transacted_at) VALUES(%s,'buy',2,100,'CNY',now()-interval '1 day'),(%s,'sell',1,160,'CNY',now())", (item,item))
            await c.execute("DELETE FROM valuation_job WHERE collection_item_id=%s", (item,))
            await c.execute("INSERT INTO valuation_snapshot(collection_item_id,method,low_value,fair_value,high_value,currency,confidence) VALUES(%s,'manual',140,150,160,'CNY',0.9)", (item,))
            before = await repo.item_detail(c, owner, item)
            original = await repo.summary(c, owner)
            assert original.total_cost == 100 and original.total_market_value == 150
            assert original.realized_profit == 60 and original.unrealized_profit == 50
            with pytest.raises(NotFoundError):
                await repo.quick_update_item(c,item,other,PortfolioItemQuickUpdate(holding_status='sold'))
            for _ in range(2):
                await repo.quick_update_item(c,item,owner,PortfolioItemQuickUpdate(holding_status='sold'))
            sold = await repo.item_detail(c, owner, item)
            assert sold.item.holding_status == 'sold'
            assert sold.item.player_id not in {r['id'] for r in (await research_context(c))['players']}
            assert sold.item.quantity_held == sold.item.remaining_cost == 0
            assert sold.item.unrealized_profit is None
            assert sold.transactions == before.transactions
            assert sold.valuations == before.valuations
            summary = await repo.summary(c, owner)
            assert summary.item_count == summary.valued_item_count == summary.unvalued_item_count == 0
            assert summary.total_cost == summary.total_market_value == summary.unrealized_profit == 0
            assert summary.realized_profit == 60
            assert await search_item_ids(c,owner,SearchFilters(holding_status='sold')) == [item]
            assert await search_item_ids(c,owner,SearchFilters(holding_status='held')) == []
            assert await search_item_ids(c,other,SearchFilters(holding_status='sold')) == []
            await repo.quick_update_item(c,item,owner,PortfolioItemQuickUpdate(holding_status='held'))
            assert (await repo.summary(c, owner)) == original
            assert (await repo.item_detail(c, owner, item)).transactions == before.transactions
            await repo.soft_delete_item(c,item,owner,None)
            with pytest.raises(NotFoundError):
                await repo.quick_update_item(c,item,owner,PortfolioItemQuickUpdate(holding_status='sold'))
        finally:
            await c.rollback()
