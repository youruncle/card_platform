"""Opt-in integration tests; every fixture and change rolls back."""
import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from card_platform.news_alerts import NewsAlertInput, import_alerts
from card_platform.repository import PortfolioRepository
from card_platform.schemas import CardIdentityInput, CollectionItemCreate

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ,
                                reason='requires migrated PostgreSQL')


def test_news_dedup_expiry_portfolio_and_isolation():
    asyncio.run(check())


async def check():
    async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],
                                                     row_factory=dict_row) as c:
        try:
            owner = (await (await c.execute(
                "INSERT INTO app_user(email,display_name) VALUES(%s,'news test') RETURNING id",
                (f'{uuid4()}@example.com',))).fetchone())['id']
            repo = PortfolioRepository()
            item_id = await repo.create_item(c, CollectionItemCreate(owner_id=owner,
                card=CardIdentityInput(player_name=f'News Test {uuid4()}',
                                       brand_name='Test', season_start=2025, product_name='Test')))
            items = await repo.list_items(c, owner)
            player_id = items[0].player_id
            assert items[0].news_alerts == []
            data = dict(player_id=player_id, event_key='injury-test', direction='negative',
                        headline='测试新闻', facts='已确认事实', attribution='短期需求可能下降，价格待核实',
                        confidence='medium', source_url='https://www.nba.com/news/test',
                        published_at=datetime.now(timezone.utc))
            assert (await import_alerts(c, [NewsAlertInput(**data)]))['inserted'] == 1
            first = (await repo.list_items(c, owner))[0].news_alerts[0]
            assert (first.expires_at-first.created_at).total_seconds() == 72*3600
            assert (await import_alerts(c, [NewsAlertInput(**data)]))['duplicates'] == 1
            # A syndicated URL with the same event key also cannot renew it.
            data['source_url'] = 'https://www.nba.com/news/test-copy'
            assert (await import_alerts(c, [NewsAlertInput(**data)]))['duplicates'] == 1
            again = (await repo.list_items(c, owner))[0].news_alerts[0]
            assert again.expires_at == first.expires_at
            assert await repo.list_items(c, uuid4(), item_ids=[item_id]) == []
            data.update(event_key='return-test', direction='positive',
                        source_url='https://www.nba.com/news/test')
            assert (await import_alerts(c, [NewsAlertInput(**data)]))['inserted'] == 1
            assert len((await repo.list_items(c, owner))[0].news_alerts) == 2
            await c.execute("UPDATE player_news_alert SET created_at=now()-interval '73 hours', "
                            "expires_at=now()-interval '1 hour' WHERE player_id=%s", (player_id,))
            assert (await repo.list_items(c, owner))[0].news_alerts == []
            # Expired events must not revive after a rerun.
            assert (await import_alerts(c, [NewsAlertInput(**data)]))['duplicates'] == 1
            assert (await repo.list_items(c, owner))[0].news_alerts == []
            # An invalid player aborts the entire batch.
            data.update(event_key='batch', source_url='https://www.nba.com/news/batch')
            valid = NewsAlertInput(**data)
            invalid = valid.model_copy(update={'player_id': uuid4()})
            with pytest.raises(ValueError):
                await import_alerts(c, [valid, invalid])
            count = await (await c.execute("SELECT count(*) AS n FROM player_news_alert "
                                          "WHERE player_id=%s", (player_id,))).fetchone()
            assert count['n'] == 2
        finally:
            await c.rollback()
