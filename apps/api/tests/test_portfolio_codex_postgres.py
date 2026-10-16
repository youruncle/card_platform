"""Latest successful Codex summaries stay separate from formal valuation."""
import asyncio
import os
from decimal import Decimal
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from card_platform.repository import PortfolioRepository
from card_platform.schemas import CardIdentityInput, CollectionItemCreate

pytestmark = pytest.mark.skipif(
    'TEST_DATABASE_URL' not in os.environ, reason='requires migrated test PostgreSQL'
)


def test_codex_portfolio_summary_selection_and_isolation():
    asyncio.run(check_summary())


async def check_summary():
    async with await psycopg.AsyncConnection.connect(
        os.environ['TEST_DATABASE_URL'], row_factory=dict_row
    ) as connection:
        try:
            owners = []
            for _ in range(2):
                row = await (await connection.execute(
                    "INSERT INTO app_user(email,display_name) VALUES(%s,'test') RETURNING id",
                    (f'{uuid4()}@example.com',),
                )).fetchone()
                owners.append(row['id'])
            repo = PortfolioRepository()
            item = await repo.create_item(connection, CollectionItemCreate(
                owner_id=owners[0], card=CardIdentityInput(
                    player_name=f'Codex test {uuid4()}', brand_name='Test',
                    season_start=2025, product_name='Test',
                ),
            ))

            async def read():
                rows = await repo.list_items(connection, owners[0], item_ids=[item], limit=30)
                assert len(rows) == 1
                assert rows[0].fair_value is None
                assert rows[0].unrealized_profit is None
                return rows[0]

            assert (await read()).codex_analysis_version is None
            for version, source, status, owner, fair in [
                (1, 'codex_multimodal', 'succeeded', owners[0], Decimal('30')),
                (2, 'codex_multimodal', 'succeeded', owners[0], Decimal('0')),
                (3, 'codex_multimodal', 'failed', owners[0], None),
                (4, 'other', 'succeeded', owners[0], Decimal('900')),
                (5, 'codex_multimodal', 'succeeded', owners[1], Decimal('999')),
            ]:
                job = await (await connection.execute(
                    "INSERT INTO card_analysis_job(owner_id,collection_item_id,status) "
                    "VALUES(%s,%s,%s) RETURNING id", (owner,item,status),
                )).fetchone()
                await connection.execute(
                    """INSERT INTO card_analysis_snapshot(
                        job_id,collection_item_id,analysis_version,source,model_name,
                        identity_summary,identity_confidence,currency,
                        fair_value_low,fair_value,fair_value_high,status)
                    VALUES(%s,%s,%s,%s,'test','test',.8,'CNY',%s,%s,%s,%s)""",
                    (job['id'],item,version,source,fair,fair,fair,status),
                )
            result = await read()
            assert result.codex_analysis_version == 2
            assert result.codex_fair_value == Decimal('0')
            assert result.codex_valuation_low == Decimal('0')
            assert result.codex_valuation_high == Decimal('0')
            assert result.codex_valuation_currency == 'CNY'
            assert await repo.list_items(connection, owners[1], item_ids=[item]) == []
        finally:
            await connection.rollback()
