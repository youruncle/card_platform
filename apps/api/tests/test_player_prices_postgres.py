"""Isolated fixtures inside rollback transactions; opt in with TEST_DATABASE_URL."""

import asyncio
import os
from datetime import date, datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from card_platform.player_prices.repository import PriceRepository
from card_platform.player_prices.service import report_day, refresh_player

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires migrated PostgreSQL"
)


def test_price_snapshot_idempotency_fixed_basket_and_account_scope():
    asyncio.run(check())


async def check():
    async with await psycopg.AsyncConnection.connect(
        os.environ["TEST_DATABASE_URL"], row_factory=dict_row
    ) as c:
        try:
            owner = (
                await (
                    await c.execute(
                        "INSERT INTO app_user(email,display_name) VALUES(%s,'price test') RETURNING id",
                        (f"{uuid4()}@example.com",),
                    )
                ).fetchone()
            )["id"]
            player = (
                await (
                    await c.execute(
                        "INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id",
                        (f"Price Test {uuid4()}", str(uuid4())),
                    )
                ).fetchone()
            )["id"]
            brand = (
                await (
                    await c.execute(
                        "INSERT INTO brand(name) VALUES(%s) RETURNING id", (str(uuid4()),)
                    )
                ).fetchone()
            )["id"]
            cs = (
                await (
                    await c.execute(
                        "INSERT INTO card_set(brand_id,season_start,product_name) "
                        "VALUES(%s,2024,'Prizm') RETURNING id",
                        (brand,),
                    )
                ).fetchone()
            )["id"]
            cd = (
                await (
                    await c.execute(
                        "INSERT INTO card_definition(card_set_id,player_id) "
                        "VALUES(%s,%s) RETURNING id",
                        (cs, player),
                    )
                ).fetchone()
            )["id"]
            await c.execute(
                "INSERT INTO collection_item(owner_id,card_definition_id) VALUES(%s,%s)",
                (owner, cd),
            )
            repo = PriceRepository()
            assert len(await repo.roster(c, owner)) == 1
            assert await repo.roster(c, uuid4()) == []
            from test_player_prices import samples, D

            rows = [{**r, "source_id": str(uuid4())} for r in samples()]
            await repo.store_sales(c, player, rows)
            await repo.store_sales(c, player, rows)
            assert len(await repo.sales(c, player, D)) == 18
            first = await repo.compute(c, player, D)
            assert first["change_pct"] == "0.0000"
            assert first["basket_selected_on"] == D.isoformat()
            assert (await repo.compute(c, player, D))["cards"] == first["cards"]
            # A newly popular identity cannot replace the fixed monthly basket.
            extra = [dict(rows[0], source_id=str(uuid4()), card_key="popular") for _ in range(30)]
            await repo.store_sales(c, player, extra)
            again = await repo.compute(c, player, date(2026, 10, 11))
            assert {r["card_key"] for r in again["cards"]} == {"a", "b", "c"}
            new_month = await repo.compute(c, player, date(2026, 11, 1))
            assert "popular" in {r["card_key"] for r in new_month["cards"]}
            assert new_month["basket_month"] == "2026-11-01"

            assert (await repo.board(c, owner, D, "gainers"))["players"][0][
                "change_pct"
            ] == "0.0000"
            assert (await repo.board(c, uuid4(), D, "gainers"))["players"] == []

            # Failure must keep the previous good snapshot.
            async def failure(*args, **kwargs):
                raise ValueError("source offline")

            await refresh_player(c, {"id": player, "full_name": "Test"}, D, collector=failure)
            preserved = (await repo.board(c, owner, D, "gainers"))["players"][0]
            assert preserved["change_pct"] == "0.0000"
            assert preserved["refresh"]["error"]
            stale = (await repo.board(c, owner, date(2026, 10, 12), "gainers"))["players"][0]
            assert stale["status"] == "stale" and stale["change_pct"] is None
            assert stale["report_date"] == "2026-10-11"
            await repo.exclude_sources(c, {rows[0]["source_id"]})
            await repo.store_sales(c, player, [rows[0]])
            assert rows[0]["source_id"] not in {
                r["source_id"] for r in await repo.sales(c, player, D)
            }
            await repo.enqueue(c, owner)
            assert await repo.due_player(c, D) is None  # five-minute request cooldown
            await c.execute(
                "UPDATE player_price_state SET last_attempt_at=now()-interval '6 minutes' WHERE player_id=%s",
                (player,),
            )
            assert await repo.due_player(c, D) is not None
        finally:
            await c.rollback()


def test_schedule_uses_last_completed_day_before_seven():
    assert report_day(datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc)) == date(2026, 10, 11)
    assert report_day(datetime(2026, 10, 12, 23, 0, tzinfo=timezone.utc)) == date(2026, 10, 12)
