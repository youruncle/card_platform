import asyncio
import logging
from collections import Counter

import httpx
from psycopg.types.json import Jsonb
from datetime import datetime, timedelta

from card_platform.domain.player_aliases import query_player_aliases, matching_player_aliases
from card_platform.player_prices.discovery import card_key, collect_player, identify_sale, rejection_reason
from card_platform.player_prices.domain import TZ, day_end, select_basket
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


def analyze_observations(observations, name, identities):
    by_source, reasons = {}, Counter()
    invalid = set()
    for observation in observations:
        try:
            sale, _ = validated_sale(observation)
        except (ValueError, KeyError, TypeError):
            # Invalid observations have no trusted commodity ID; count unique payloads.
            import json
            invalid.add(json.dumps(observation, sort_keys=True, default=str))
            continue
        by_source.setdefault("item:" + sale.item_id, []).append(sale)
    if invalid:
        reasons["invalid_observation"] = len(invalid)
    rows = []
    for source, sales in by_source.items():
        if len({(sale.title, sale.sale_price, sale.occurred_at) for sale in sales}) > 1:
            reasons["conflicting_source"] += 1
            continue
        sale = sales[0]
        identity = identify_sale(sale, name, identities)
        if identity is None:
            reasons[rejection_reason(sale)] += 1
            continue
        rows.append(dict(source_id=source, card_key=card_key(identity), identity=identity,
                         sale_price=sale.sale_price, occurred_at=sale.occurred_at,
                         source_url=sale.source_url, raw_title=sale.title))
    return rows, dict(sample_count=len(by_source) + len(invalid), identified_count=len(rows),
                      rejected_count=sum(reasons.values()), rejection_reasons=dict(reasons))


def group_observations(observations, name, identities):
    rows, coverage = analyze_observations(observations, name, identities)
    return rows, coverage["rejected_count"]


def discovery_targets(rows, report_date):
    return select_basket(rows, report_date)


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
        state = await (await c.execute(
            "SELECT coverage FROM player_price_state WHERE player_id=%s", (player_id,),
        )).fetchone()
        previous_coverage = state["coverage"] or {}
        saved = await repo.basket(c, player_id, report_date)
        coverage = {**previous_coverage, "source": "cardhobby", "scope": "local_sample",
                    "errors": [], "pages": 0}
        try:
            observations = await existing_samples(c, name, report_date)
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
            fetched = []
            if fetch:
                prior_rows, _ = await asyncio.to_thread(analyze_observations, observations, name, identities)
                targets = saved["cards"] if saved else discovery_targets(
                    [*await repo.sales(c, player_id, report_date), *prior_rows], report_date
                )
                fetched, coverage = await collector(
                    name, targets, report_date,
                    query_state=previous_coverage.get("history_queries", {}),
                )
                await repo.exclude_sources(c, conflicting_sources(observations + fetched))
                await store_observations(c, fetched)
            observations += fetched
            rows, diagnostics = await asyncio.to_thread(
                analyze_observations, observations, name, identities
            )
            coverage.update(
                **diagnostics,
                local_limit_reached=len(observations) >= 20000,
            )
            await repo.store_sales(c, player_id, rows)
            # Valid exact-card evidence remains useful even if another query failed.
            # Do not publish a new daily index or mark the incomplete run successful.
            if coverage["errors"]:
                raise ValueError("卡淘部分查询失败，保留上次统计；稍后重试")
            if not coverage.get("incremental_complete", True):
                await c.execute(
                    "UPDATE player_price_state SET error=NULL,coverage=%s WHERE player_id=%s",
                    (Jsonb(coverage), player_id),
                )
                return {"status": "collecting"}
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
