"""Duplicate public subjects must not damage market evidence or account scope."""
import asyncio
import os
from datetime import date
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from card_platform.player_prices.repository import PriceRepository
from test_player_prices import sale

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ,
                               reason='requires isolated migrated PostgreSQL')


async def add_owner(c):
    return (await (await c.execute(
        "INSERT INTO app_user(email,display_name) VALUES(%s,'subject test') RETURNING id",
        (f'{uuid4()}@example.com',),
    )).fetchone())['id']


async def add_player(c, name, age=0):
    return (await (await c.execute(
        "INSERT INTO player(full_name,normalized_name,created_at) "
        "VALUES(%s,%s,now()-(%s * interval '1 day')) RETURNING id",
        (name, str(uuid4()), age),
    )).fetchone())['id']


async def hold(c, owner, player):
    brand = (await (await c.execute('INSERT INTO brand(name) VALUES(%s) RETURNING id',
                                    (str(uuid4()),))).fetchone())['id']
    cs = (await (await c.execute("INSERT INTO card_set(brand_id,season_start,product_name) "
                                "VALUES(%s,2024,'Prizm') RETURNING id", (brand,))).fetchone())['id']
    cd = (await (await c.execute('INSERT INTO card_definition(card_set_id,player_id) '
                                'VALUES(%s,%s) RETURNING id', (cs,player))).fetchone())['id']
    return (await (await c.execute('INSERT INTO collection_item(owner_id,card_definition_id) '
                                  'VALUES(%s,%s) RETURNING id', (owner,cd))).fetchone())['id']


def test_alias_uuid_sales_are_shared_once_and_only_owned_subjects_are_authorized():
    async def run():
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row) as c:
            try:
                owner, other = await add_owner(c), await add_owner(c)
                original = await add_player(c,'VICTOR WEMBANYAMA',2)
                alias = await add_player(c,'Victor Wembanyama',1)
                foreign = await add_player(c,'Stephen Curry')
                await hold(c,owner,alias)
                await hold(c,owner,original)
                await hold(c,other,original)
                await hold(c,other,foreign)
                holdings = await (await c.execute('SELECT id,owner_id,card_definition_id FROM collection_item ORDER BY id')).fetchall()
                repo = PriceRepository()
                roster = await repo.roster(c,owner)
                assert len(roster) == 1 and roster[0]['id'] == original
                rows = [sale(source_id=str(uuid4()))]
                await repo.store_sales(c,original,rows)
                await repo.store_sales(c,alias,rows)
                assert len(await repo.sales(c,alias,date(2026,10,10))) == 1
                assert (await (await c.execute('SELECT excluded FROM player_price_sale WHERE source_id=%s',
                                              (rows[0]['source_id'],))).fetchone())['excluded'] is False
                await repo.compute(c,alias,date(2026,10,10))
                board = await repo.board(c,owner,date(2026,10,10),'gainers')
                assert len(board['players']) == 1
                assert board['players'][0]['player_name'] == 'Victor Wembanyama'
                assert board['players'][0]['computed_at'] is not None
                assert board['players'][0]['market']['sale_count'] == 1
                assert await repo.enqueue(c,owner,player_id=alias) == 1
                assert await repo.enqueue(c,owner,player_id=foreign) == 0
                assert await repo.enqueue(c,uuid4(),player_id=original) == 0
                assert (await repo.board(c,uuid4(),date(2026,10,10),'gainers'))['players'] == []
                assert await (await c.execute('SELECT id,owner_id,card_definition_id FROM collection_item ORDER BY id')).fetchall() == holdings
                # A real amount conflict remains excluded after normalization.
                await repo.store_sales(c,alias,[dict(rows[0],sale_price='999')])
                assert await repo.sales(c,original,date(2026,10,10)) == []
            finally:
                await c.rollback()
    asyncio.run(run())


def test_only_canonical_subject_is_scheduled_even_when_old_alias_state_exists():
    async def run():
        async with await psycopg.AsyncConnection.connect(os.environ['TEST_DATABASE_URL'], row_factory=dict_row) as c:
            try:
                owner = await add_owner(c)
                original = await add_player(c,'LUKA DONČIĆ',2)
                alias = await add_player(c,'Luka Doncic',1)
                await hold(c,owner,alias)  # Canonical UUID itself need not be held.
                await c.execute('INSERT INTO player_price_state(player_id,requested_at) VALUES(%s,now())',(alias,))
                repo = PriceRepository()
                await repo.seed_states(c)
                due = await repo.due_player(c,date(2026,10,21))
                assert due and due['id'] == original
            finally:
                await c.rollback()
    asyncio.run(run())
