"""PYTHONPATH=apps/api/src .venv/bin/python scripts/player_price_index.py --local-only --limit 5."""

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta

from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.player_prices.domain import TZ
from card_platform.player_prices.repository import PriceRepository
from card_platform.player_prices.service import refresh_player


async def run(args):
    day = args.date or (datetime.now(TZ).date() - timedelta(days=1))
    if day >= datetime.now(TZ).date():
        raise ValueError("report date must be a completed Shanghai day")
    database = Database(get_settings())
    await database.open()
    try:
        async with database.connection() as c:
            roster = await PriceRepository().roster(c)
        if args.player:
            roster = [
                p for p in roster if p["full_name"].casefold() == args.player.casefold()
            ]
        if args.limit:
            roster = roster[: args.limit]
        for player in roster:
            async with database.connection() as c:
                result = await refresh_player(c, player, day, fetch=not args.local_only)
                state = await (
                    await c.execute(
                        "SELECT coverage,error FROM player_price_state "
                        "WHERE player_id=%s",
                        (player["id"],),
                    )
                ).fetchone()
            print(
                json.dumps(
                    {
                        "player": player["full_name"],
                        "report_date": str(day),
                        "status": result["status"],
                        "change_pct": result.get("change_pct"),
                        "cards": len(result.get("cards", [])),
                        **state,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                flush=True,
            )
    finally:
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="球员每日卡价采集与统计（独立表）")
    parser.add_argument(
        "--local-only", action="store_true", help="只复用已有公开样本，不联网"
    )
    parser.add_argument("--player", help="只处理指定球员全名")
    parser.add_argument(
        "--limit", type=int, default=0, help="最多处理球员数，0表示全部"
    )
    parser.add_argument("--date", type=date.fromisoformat)
    asyncio.run(run(parser.parse_args()))
