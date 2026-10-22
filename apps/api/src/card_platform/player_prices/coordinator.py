"""Production refresh: short DB phases around bounded external work."""

import asyncio

import httpx
from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.player_prices.admission import (
    source_candidates,
    queue_candidates,
    verified_price_rows,
)
from card_platform.player_prices.catalog import published_catalog
from card_platform.player_prices.discovery import collect_player, card_key
from card_platform.player_prices.image_evidence import _seal
from card_platform.player_prices.image_jobs import ImageJobs
from card_platform.player_prices.image_reader import EvidenceBlobs
from card_platform.player_prices.reconciliation import reconcile_prices, finalize_reconciliation
from card_platform.player_prices.repository import PriceRepository
from card_platform.player_prices.service import (
    existing_samples,
    analyze_observations,
    discovery_targets,
    conflicting_sources,
)
from card_platform.public_source_catalog import store_observations

repo = PriceRepository()
queue = ImageJobs()


async def _load(c, player, report_date, catalog):
    catalog = catalog if catalog is not None else await published_catalog(c)
    catalog = catalog.session()
    observations = await existing_samples(c, player["full_name"], report_date)
    candidates, conflicts, invalid = source_candidates(observations)
    price_ids = await queue.enqueue_candidates(
        c, player["id"], queue_candidates(candidates, player["full_name"])
    )
    source_ids = {"item:" + sale.item_id for _, sale, _ in candidates} | conflicts
    # Keep checking already published prices after their source leaves the
    # report window; new public photo evidence can invalidate an old report.
    subject = await repo.subject(c, player["id"])
    members = subject["member_ids"] if subject else [player["id"]]
    historical_sources = await (
        await c.execute(
            "SELECT source_id FROM player_price_sale WHERE player_id=ANY(%s)",
            (members,),
        )
    ).fetchall()
    all_sources = source_ids | {r["source_id"] for r in historical_sources}
    evidence = list((await queue.source_evidence(c, all_sources, price_job_ids=price_ids)).values())
    for record in evidence:
        record["photo_context_only"] = record["source_id"] not in source_ids
    peer_captures = {
        str(capture["id"]): capture
        for record in evidence
        for peer in record["photo_peers"]
        for capture in (peer, peer["photo_anchor"])
    }
    token = _seal(
        dict(
            # Capture freshness alone does not change sale or image evidence.
            # Keep validity in the token so malformed capture metadata cannot
            # turn a formerly valid observation into a silently reused result.
            observations=sorted(
                [{k: v for k, v in o.items() if k != "captured_at"} for o in observations],
                key=_seal,
            ),
            valid_observation_hashes=sorted(o["raw_row_sha256"] for o, _, _ in candidates),
            conflicting_sources=sorted(conflicts),
            invalid_observations=invalid,
            catalog_version=catalog.version,
            photo_captures=[
                dict(
                    id=key,
                    source=peer["source_id"],
                    observation=_seal(peer["observation"]),
                    receipt=peer["receipt_sha256"],
                    capture_manifest=peer.get("capture_manifest_sha256"),
                    pair=[peer.get("pair_a"), peer.get("pair_b")],
                )
                for key, peer in sorted(peer_captures.items())
            ],
            evidence=sorted(
                [
                    dict(
                        source=r["source_id"],
                        raw=r["observation_sha256"],
                        receipt=r["receipt_sha256"],
                        approved=r["approved_review_sha256s"],
                        price_eligible=r["price_eligible"],
                        capture_manifest=r.get("capture_manifest_sha256"),
                        photo_context_only=r["photo_context_only"],
                    )
                    for r in evidence
                ],
                key=lambda r: r["source"],
            ),
        )
    )
    return observations, catalog, evidence, token


async def _lock_inputs(c, observations, *, current_catalog):
    # All queue receipts/current pointers and trusted approvals use this same
    # short transaction lock. Source and publication writers use row locks.
    await c.execute("SELECT pg_advisory_xact_lock(8184001)")
    if current_catalog:
        await c.execute("SELECT id FROM knowledge_workspace WHERE name='Card Platform' FOR SHARE")
    candidates, conflicts, _ = source_candidates(observations)
    sources = sorted({"item:" + sale.item_id for _, sale, _ in candidates} | conflicts)
    await c.execute(
        """SELECT external_sale_id FROM public_source_catalog
        WHERE provider_code='cardhobby' AND external_sale_id=ANY(%s)
        ORDER BY external_sale_id FOR SHARE""",
        (sources,),
    )


async def _save_state(c, player_id, coverage, error=None):
    await c.execute(
        "UPDATE player_price_state SET coverage=%s,error=%s WHERE player_id=%s",
        (Jsonb(coverage), error, player_id),
    )


async def refresh_player_job(
    database,
    player,
    report_date,
    *,
    collector=collect_player,
    fetch=True,
    catalog=None,
    blob_reader=None,
):
    async with database.connection() as c:
        player = await repo.subject(c, player["id"]) or player
        player_id, name = player["id"], player["full_name"]
        locked = (
            await (
                await c.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s,81)) AS locked",
                    (str(player_id),),
                )
            ).fetchone()
        )["locked"]
        await c.commit()
        if not locked:
            return dict(status="busy")
        try:
            await c.execute(
                """INSERT INTO player_price_state(player_id,last_attempt_at) VALUES(%s,clock_timestamp())
                ON CONFLICT(player_id) DO UPDATE SET last_attempt_at=clock_timestamp()""",
                (player_id,),
            )
            state = await (
                await c.execute(
                    "SELECT coverage FROM player_price_state WHERE player_id=%s", (player_id,)
                )
            ).fetchone()
            prior = state["coverage"] or {}
            coverage = {
                **prior,
                "source": "cardhobby",
                "scope": "local_sample",
                "errors": [],
                "pages": 0,
                "verification_pending": False,
            }
            observations, official, _, _ = await _load(c, player, report_date, catalog)
            coverage.update(
                catalog_version=official.version, catalog_available=official.version is not None
            )
            await c.commit()
            if official.version is None:
                coverage["errors"] = [{"reason": "official_catalog_unavailable"}]
                message = "官方卡款资料暂不可用，历史结果保留，稍后重试"
                await _save_state(c, player_id, coverage, message)
                await c.commit()
                return dict(status="source_error", error=message)
            fetched = []
            if fetch:
                # These identities are discovery-only; they never enter price rows.
                candidate_rows, _ = await asyncio.to_thread(
                    analyze_observations, observations, name, [], catalog=official
                )
                targets = discovery_targets(candidate_rows, report_date)
                seeds = await asyncio.to_thread(
                    official.discovery_targets, name, [o.get("sale", {}) for o in observations]
                )
                seen = {card_key(target["identity"]) for target in targets}
                targets = (
                    targets + [seed for seed in seeds if card_key(seed["identity"]) not in seen]
                )[:3]
                if official.errors:
                    coverage["errors"] = [{"reason": "official_artifact_unavailable"}]
                    message = "官方卡款资料暂不可用，历史结果保留，稍后重试"
                    await _save_state(c, player_id, coverage, message)
                    await c.commit()
                    return dict(status="source_error", error=message)
                try:
                    fetched, collected = await collector(
                        name, targets, report_date, query_state=prior.get("history_queries", {})
                    )
                    coverage.update(collected)
                except (ValueError, httpx.HTTPError):
                    coverage["errors"] = [{"reason": "source_unavailable"}]
            source_conflicts = conflicting_sources(observations + fetched)
            await store_observations(c, fetched)
            await c.commit()
            # Recheck source/approval/publication snapshots after reading blobs.
            # A changing snapshot gets one bounded retry without re-fetching.
            for _ in range(2):
                observations, official, evidence, token = await _load(
                    c, player, report_date, catalog
                )
                await c.commit()
                reader = blob_reader
                if evidence and reader is None:
                    reader = EvidenceBlobs(
                        f"{get_settings().scanner_job_dir}/.player-price-evidence/blobs"
                    ).read
                rows, diagnostics = await asyncio.to_thread(
                    verified_price_rows,
                    observations,
                    name,
                    official,
                    evidence=evidence,
                    blob_reader=reader,
                )
                await _lock_inputs(c, observations, current_catalog=catalog is None)
                latest = await _load(c, player, report_date, catalog)
                if token != latest[3]:
                    await c.commit()
                    continue
                coverage.update(diagnostics, local_limit_reached=len(observations) >= 20000)
                if official.errors or coverage["local_limit_reached"]:
                    coverage["errors"].append(
                        {
                            "reason": "official_artifact_unavailable"
                            if official.errors
                            else "local_observation_limit"
                        }
                    )
                    message = "行情资料暂不完整，历史结果保留，稍后重试"
                    await _save_state(c, player_id, coverage, message)
                    await c.commit()
                    return dict(status="source_error", error=message)
                reconciled = await reconcile_prices(
                    c,
                    player_id,
                    rows,
                    report_date,
                    conflicted_sources=source_conflicts,
                    photo_reuse_sources=diagnostics["photo_reuse_sources"],
                    photo_pending_sources=diagnostics["photo_pending_sources"],
                )
                if reconciled["blocked_sources"]:
                    count = sum(r["source_id"] in reconciled["blocked_sources"] for r in rows)
                    coverage["identified_count"] -= count
                    coverage["rejected_count"] += count
                    coverage["rejection_reasons"]["conflicting_stored_source"] = count
                photo_only = set(reconciled["withdrawn_sources"]) - set(
                    reconciled["blocked_sources"]
                )
                withdrew = sum(r["source_id"] in photo_only for r in rows)
                if withdrew:
                    coverage["identified_count"] -= withdrew
                    coverage["rejected_count"] += withdrew
                    coverage["rejection_reasons"]["reused_photo_pair"] = (
                        coverage["rejection_reasons"].get("reused_photo_pair", 0) + withdrew
                    )
                await finalize_reconciliation(c, reconciled["audit_id"])
                if diagnostics["photo_pending_sources"]:
                    coverage["verification_pending"] = True
                    message = "历史图片证据暂不可读，相关价格暂退，等待独立性复核"
                    await _save_state(c, player_id, coverage, message)
                    await c.commit()
                    return dict(status="collecting", error=message)
                if coverage["errors"]:
                    message = "卡淘部分采集失败，已核验成交保留，稍后重试"
                    await _save_state(c, player_id, coverage, message)
                    await c.commit()
                    return dict(status="source_error", error=message)
                if not coverage.get("incremental_complete", True):
                    await _save_state(c, player_id, coverage)
                    await c.commit()
                    return dict(status="collecting")
                result = await repo.compute(c, player_id, report_date, coverage=coverage)
                await c.execute(
                    """UPDATE player_price_state SET last_success_at=clock_timestamp(),last_report_date=%s,
                    error=NULL,coverage=%s WHERE player_id=%s""",
                    (report_date, Jsonb(coverage), player_id),
                )
                await finalize_reconciliation(c, reconciled["audit_id"])
                await c.commit()
                return result
            # Do not leave now-known obsolete rows active while inputs churn.
            await reconcile_prices(
                c, player_id, [], report_date, conflicted_sources=source_conflicts
            )
            coverage["verification_pending"] = True
            await _save_state(c, player_id, coverage)
            await c.commit()
            return dict(status="collecting")
        except BaseException:
            await c.rollback()
            raise
        finally:
            await c.execute("SELECT pg_advisory_unlock(hashtextextended(%s,81))", (str(player_id),))
            await c.commit()
