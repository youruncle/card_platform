import asyncio
import logging

import httpx
from psycopg.types.json import Jsonb
from datetime import datetime, timedelta

from card_platform.domain.player_aliases import query_player_aliases, matching_player_aliases
from card_platform.player_prices.discovery import card_key, collect_player, identify_sale
from card_platform.player_prices.domain import TZ, day_end
from card_platform.player_prices.repository import PriceRepository
from card_platform.public_source_catalog import store_observations, validated_sale

log = logging.getLogger(__name__)
repo = PriceRepository()


def report_day(now=None):
    local = (now or datetime.now(TZ)).astimezone(TZ)
    return local.date() - timedelta(days=1 if local.hour >= 7 else 2)


async def existing_samples(c, player_name, report_date):
    aliases = list(
        dict.fromkeys(
            [
                player_name,
                *query_player_aliases(player_name),
                *matching_player_aliases(player_name.casefold()),
            ]
        )
    )
    patterns = [
        "%" + a.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for a in aliases
    ]
    end = day_end(report_date)
    result = await c.execute(
        """SELECT observation FROM public_source_catalog
        WHERE provider_code='cardhobby'
          AND (observation->'sale'->>'title') ILIKE ANY(%s)
          AND (observation->'sale'->>'occurred_at')::timestamptz >= %s
          AND (observation->'sale'->>'occurred_at')::timestamptz < %s
        ORDER BY (observation->'sale'->>'occurred_at')::timestamptz DESC LIMIT 20000""",
        (patterns, end - timedelta(days=90), end),
    )
    return [row["observation"] for row in await result.fetchall()]


def conflicting_sources(observations):
    facts = {}
    for observation in observations:
        try:
            sale, _ = validated_sale(observation)
        except (ValueError, KeyError, TypeError):
            continue
        facts.setdefault("item:" + sale.item_id, set()).add(
            (sale.title, sale.sale_price, sale.occurred_at)
        )
    return {source for source, values in facts.items() if len(values) > 1}


def group_observations(observations, name, identities):
    rows, rejected = [], 0
    for observation in observations:
        try:
            sale, _ = validated_sale(observation)
            identity = identify_sale(sale, name, identities)
        except (ValueError, KeyError, TypeError):
            identity = None
        if identity is None:
            rejected += 1
            continue
        rows.append(
            dict(
                source_id="item:" + sale.item_id,
                card_key=card_key(identity),
                identity=identity,
                sale_price=sale.sale_price,
                occurred_at=sale.occurred_at,
                source_url=sale.source_url,
                raw_title=sale.title,
            )
        )
    return rows, rejected


async def refresh_player(c, player, report_date, *, collector=collect_player, fetch=True):
    player_id, name = player["id"], player["full_name"]
    async with c.transaction():
        locked = await (
            await c.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,81)) AS locked",
                (str(player_id),),
            )
        ).fetchone()
        if not locked["locked"]:
            return {"status": "busy"}
        await c.execute(
            """INSERT INTO player_price_state(player_id,last_attempt_at)
            VALUES(%s,clock_timestamp()) ON CONFLICT(player_id) DO UPDATE SET
              last_attempt_at=clock_timestamp()""",
            (player_id,),
        )
        saved = await repo.basket(c, player_id, report_date)
        coverage = {"source": "cardhobby", "scope": "local_sample", "errors": [], "pages": 0}
        try:
            observations = await existing_samples(c, name, report_date)
            fetched = []
            if fetch:
                fetched, coverage = await collector(
                    name, saved["cards"] if saved else [], report_date
                )
                # Partial failures cannot produce a falsely successful daily observation.
                if coverage["errors"]:
                    await repo.exclude_sources(c, conflicting_sources(observations + fetched))
                    await store_observations(c, fetched)
                    raise ValueError("卡淘部分查询失败，保留上次统计；稍后重试")
                await repo.exclude_sources(c, conflicting_sources(observations + fetched))
                await store_observations(c, fetched)
            observations += fetched
            known = await (
                await c.execute(
                    """SELECT normalized_identity FROM market_identity_cache
                WHERE lower(normalized_identity->>'player_name')=ANY(%s)""",
                    ([a.casefold() for a in [name, *matching_player_aliases(name.casefold())]],),
                )
            ).fetchall()
            identities = [r["normalized_identity"] for r in known]
            if saved:
                identities += [b["identity"] for b in saved["cards"]]
            rows, rejected = await asyncio.to_thread(
                group_observations, observations, name, identities
            )
            coverage.update(
                sample_count=len(observations),
                identified_count=len(rows),
                rejected_count=rejected,
                local_limit_reached=len(observations) >= 20000,
            )
            await repo.store_sales(c, player_id, rows)
            snapshot = await repo.compute(c, player_id, report_date, coverage=coverage)
            await c.execute(
                """UPDATE player_price_state SET last_success_at=clock_timestamp(),
                last_report_date=%s,error=NULL,coverage=%s WHERE player_id=%s""",
                (report_date, Jsonb(coverage), player_id),
            )
            return snapshot
        except (ValueError, httpx.HTTPError):
            # Use a fixed public error, never expose credential/transport details.
            message = "卡淘采集失败，历史结果保留，稍后重试"
            await c.execute(
                "UPDATE player_price_state SET error=%s,coverage=%s WHERE player_id=%s",
                (message, Jsonb(coverage), player_id),
            )
            return {"status": "source_error", "error": message}


async def run_price_worker(database):
    while True:
        try:
            async with database.connection() as c:
                await repo.seed_states(c)
                player = await repo.due_player(c, report_day())
            if player:
                async with database.connection() as c:
                    requested = player.get("requested_at")
                    attempted = player.get("last_attempt_at")
                    manual = requested is not None and (attempted is None or requested > attempted)
                    target = datetime.now(TZ).date() - timedelta(days=1) if manual else report_day()
                    result = await refresh_player(c, player, target)
                if result["status"] == "busy":
                    await asyncio.sleep(5)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Player price worker failed; will retry")
        await asyncio.sleep(60)
