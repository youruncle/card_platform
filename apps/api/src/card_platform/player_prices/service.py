import asyncio
import logging
from collections import Counter

import httpx
from psycopg.types.json import Jsonb
from datetime import datetime, timedelta

from card_platform.domain.player_aliases import matching_player_aliases
from card_platform.player_prices.collection import collection_player_aliases
from card_platform.player_prices.discovery import card_key, collect_player, identify_sale, rejection_reason
from card_platform.player_prices.domain import TZ, day_end, select_basket
from card_platform.player_prices.repository import PriceRepository
from card_platform.player_prices.catalog import published_catalog
from card_platform.public_source_catalog import store_observations, validated_sale
from card_platform.player_prices.admission import source_candidates, queue_candidates, verified_price_rows
from card_platform.player_prices.image_jobs import ImageJobs
from card_platform.player_prices.reconciliation import reconcile_prices, finalize_reconciliation

log = logging.getLogger(__name__)
repo = PriceRepository()


class CatalogUnavailableError(ValueError):
    pass


def report_day(now=None):
    local = (now or datetime.now(TZ)).astimezone(TZ)
    return local.date() - timedelta(days=1 if local.hour >= 7 else 2)


async def existing_samples(c, player_name, report_date):
    aliases = list(
        dict.fromkeys(
            [
                player_name,
                *collection_player_aliases(player_name),
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
        ORDER BY (observation->'sale'->>'occurred_at')::timestamptz DESC,external_sale_id LIMIT 20000""",
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


def analyze_observations(observations, name, identities, *, catalog=None):
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
        identity = identify_sale(sale, name, identities, catalog=catalog)
        if identity is None:
            reason = ("catalog_unconfirmed" if catalog is not None
                      and identify_sale(sale, name, identities) is not None else rejection_reason(sale))
            reasons[reason] += 1
            continue
        rows.append(dict(source_id=source, card_key=card_key(identity), identity=identity,
                         sale_price=sale.sale_price, occurred_at=sale.occurred_at,
                         source_url=sale.source_url, raw_title=sale.title))
    coverage = dict(sample_count=len(by_source) + len(invalid), identified_count=len(rows),
                    rejected_count=sum(reasons.values()), rejection_reasons=dict(reasons))
    if catalog is not None:
        coverage.update(catalog_version=catalog.version, catalog_errors=len(catalog.errors),
                        catalog_available=catalog.version is not None)
    return rows, coverage


def group_observations(observations, name, identities):
    rows, coverage = analyze_observations(observations, name, identities)
    return rows, coverage["rejected_count"]


def discovery_targets(rows, report_date):
    return select_basket(rows, report_date)


async def refresh_player(c, player, report_date, *, collector=collect_player, fetch=True, catalog=None,
                         blob_reader=None):
    # Transaction-bound replay helper. Production must use the coordinator so
    # real HTTP and photo validation never run in an open DB transaction.
    if fetch and collector is collect_player:
        raise ValueError("Use refresh_player_job for network collection")
    player = await repo.subject(c, player["id"]) or player
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
            catalog = catalog if catalog is not None else await published_catalog(c)
            catalog = catalog.session()
            coverage.update(catalog_version=catalog.version,
                            catalog_available=catalog.version is not None)
            if catalog.version is None:
                coverage["errors"].append({"reason": "official_catalog_unavailable"})
                raise CatalogUnavailableError()
            observations = await existing_samples(c, name, report_date)
            local_limit_reached = len(observations) >= 20000
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
                prior_rows, _ = await asyncio.to_thread(
                    analyze_observations, observations, name, identities, catalog=catalog,
                )
                # Legacy baskets/identity caches are not authority for query
                # targets. Re-verify source titles under the current publication.
                targets = discovery_targets(prior_rows, report_date)
                seeds = await asyncio.to_thread(catalog.discovery_targets, name,
                                               [o.get("sale", {}) for o in observations])
                seen = {card_key(target["identity"]) for target in targets}
                targets += [seed for seed in seeds if card_key(seed["identity"]) not in seen]
                targets = targets[:3]
                fetched, coverage = await collector(
                    name, targets, report_date,
                    query_state=previous_coverage.get("history_queries", {}),
                )
                await store_observations(c, fetched)
            observations += fetched
            candidates, photo_conflicts, _ = source_candidates(observations)
            jobs = ImageJobs()
            price_ids = await jobs.enqueue_candidates(c, player_id, queue_candidates(candidates, name))
            source_ids = {"item:" + sale.item_id for _, sale, _ in candidates} | photo_conflicts
            evidence = list((await jobs.source_evidence(c, source_ids, price_job_ids=price_ids)).values())
            if evidence and blob_reader is None:
                from card_platform.config import get_settings
                from card_platform.player_prices.image_reader import EvidenceBlobs
                blob_reader = EvidenceBlobs(
                    f"{get_settings().scanner_job_dir}/.player-price-evidence/blobs"
                ).read
            rows, diagnostics = await asyncio.to_thread(
                verified_price_rows, observations, name, catalog,
                evidence=evidence, blob_reader=blob_reader,
            )
            coverage.update(
                **diagnostics,
                local_limit_reached=local_limit_reached,
            )
            if catalog.errors:
                coverage["errors"].append({"reason": "official_artifact_unavailable"})
                raise CatalogUnavailableError()
            if local_limit_reached:
                coverage["errors"].append({"reason": "local_observation_limit"})
                raise ValueError("观察范围不完整，保留上次统计")
            reconciled = await reconcile_prices(c, player_id, rows, report_date,
                                                conflicted_sources=conflicting_sources(observations),
                                                photo_reuse_sources=diagnostics['photo_reuse_sources'])
            if reconciled["blocked_sources"]:
                blocked = sum(r["source_id"] in reconciled["blocked_sources"] for r in rows)
                coverage["identified_count"] -= blocked
                coverage["rejected_count"] += blocked
                coverage["rejection_reasons"]["conflicting_stored_source"] = blocked
            photo_only = set(reconciled['withdrawn_sources']) - set(reconciled['blocked_sources'])
            withdrew = sum(r['source_id'] in photo_only for r in rows)
            if withdrew:
                coverage['identified_count'] -= withdrew
                coverage['rejected_count'] += withdrew
                coverage['rejection_reasons']['reused_photo_pair'] = (
                    coverage['rejection_reasons'].get('reused_photo_pair', 0) + withdrew)
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
            await finalize_reconciliation(c, reconciled["audit_id"])
            return snapshot
        except (ValueError, httpx.HTTPError) as error:
            # Use a fixed public error, never expose credential/transport details.
            message = ("官方卡款资料暂不可用，历史结果保留，稍后重试"
                       if isinstance(error, CatalogUnavailableError)
                       else "卡淘采集失败，历史结果保留，稍后重试")
            await c.execute(
                "UPDATE player_price_state SET error=%s,coverage=%s WHERE player_id=%s",
                (message, Jsonb(coverage), player_id),
            )
            return {"status": "source_error", "error": message}


async def run_price_worker(database):
    from card_platform.player_prices.coordinator import refresh_player_job

    while True:
        try:
            async with database.connection() as c:
                await repo.seed_states(c)
                player = await repo.due_player(c, report_day())
            if player:
                requested = player.get("requested_at")
                attempted = player.get("last_attempt_at")
                manual = requested is not None and (attempted is None or requested > attempted)
                target = datetime.now(TZ).date() - timedelta(days=1) if manual else report_day()
                result = await refresh_player_job(database, player, target)
                if result["status"] == "busy":
                    await asyncio.sleep(5)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Player price worker failed; will retry")
        await asyncio.sleep(60)
