"""Private anonymous intake is excluded from the public player-price roster."""
import asyncio
import os

import psycopg
from psycopg.rows import dict_row
import pytest

from card_platform.player_prices.repository import PriceRepository
from test_player_price_subjects_postgres import add_owner, add_player, hold


pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ,
                               reason="requires isolated migrated PostgreSQL")


def test_anonymous_holdings_never_enqueue_a_public_player_price_subject():
    async def run():
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row,
        ) as connection:
            try:
                owner = await add_owner(connection)
                anonymous = [await add_player(connection, None), await add_player(connection, None)]
                named = await add_player(connection, "Stephen Curry")
                for player in [*anonymous, named]:
                    await hold(connection, owner, player)
                repository = PriceRepository()
                roster = await repository.roster(connection, owner)
                assert len(roster) == 1 and roster[0]["full_name"] == "Stephen Curry"
                assert [await repository.subject(connection, player) for player in anonymous] == [None, None]
                assert await repository.enqueue(connection, owner) == 1
                states = await (await connection.execute(
                    "SELECT player_id FROM player_price_state WHERE player_id=ANY(%s)",
                    (anonymous,),
                )).fetchall()
                assert states == []
            finally:
                await connection.rollback()
    asyncio.run(run())
