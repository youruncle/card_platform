#!/usr/bin/env python3
"""Safely rerun currently unvalued cards with the current valuation pipeline.

Jobs are inserted directly in ``identifying`` state so another running worker
cannot claim them with stale configuration. The normal worker transaction and
immutable valuation-snapshot rules still apply.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Sequence
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

# Always exercise the checkout that contains this script.  Audit reruns must
# not silently import an older editable install from another worktree.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = REPOSITORY_ROOT / "apps" / "api" / "src"
if str(API_SOURCE) not in sys.path:
    sys.path.insert(0, str(API_SOURCE))

from card_platform.config import Settings  # noqa: E402
from card_platform.db import Database  # noqa: E402
from card_platform.knowledge_runtime import KnowledgeRuntime  # noqa: E402
from card_platform.market_data.models import MarketIdentity  # noqa: E402
from card_platform.market_data.service import MarketDataService  # noqa: E402
from card_platform.valuation_worker import AutomatedValuationWorker  # noqa: E402


DEFAULT_DATABASE_URL = (
    "postgresql://card_platform:card_platform@127.0.0.1:5432/card_platform"
)
TARGET_SQL = """
SELECT ci.id, ci.display_id
FROM collection_item ci
WHERE ci.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM valuation_snapshot v
    WHERE v.collection_item_id = ci.id
      AND v.identity_compatibility <> 'incompatible'
      AND v.low_value IS NOT NULL AND v.fair_value IS NOT NULL AND v.high_value IS NOT NULL
      AND v.low_value <= v.fair_value AND v.fair_value <= v.high_value
  )
  AND NOT EXISTS (
    SELECT 1 FROM valuation_job j
    WHERE j.collection_item_id = ci.id
      AND j.status IN ('queued', 'identifying', 'searching', 'calculating')
  )
ORDER BY ci.display_id
"""
EXPLICIT_TARGET_SQL = """
SELECT ci.id, ci.display_id
FROM collection_item ci
WHERE ci.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM valuation_job j
    WHERE j.collection_item_id = ci.id
      AND j.status IN ('queued', 'identifying', 'searching', 'calculating')
  )
ORDER BY ci.display_id
"""


async def run(args: argparse.Namespace, targets: list[dict]) -> None:
    offline_overrides = (
        {
            "vision_api_key": None,
            "ai_api_key": None,
            "search_index_brave_api_key": None,
            "search_index_serpapi_key": None,
            "valuation_cardhobby_only": True,
        }
        if args.cached_only
        else {"valuation_cardhobby_only": False}
    )
    settings = Settings(
        _env_file=args.env_file,
        database_url=args.database_url,
        valuation_search_timeout_seconds=args.timeout,
        valuation_worker_enabled=False,
        database_pool_min_size=1,
        database_pool_max_size=max(2, args.concurrency + 1),
        **offline_overrides,
    )
    database = Database(settings)
    await database.open()
    runtime = KnowledgeRuntime(database)
    await runtime.refresh()
    worker = AutomatedValuationWorker(database, settings, runtime)
    if args.cached_only:
        # This mode is for deterministic audit backfills after evidence has
        # already been enumerated.  Do not enqueue background refreshes or let
        # optional model/search dependencies delay a real CardHobby estimate.
        worker.market_data.should_refresh = lambda _cached: False  # type: ignore[method-assign]
    cardhobby_providers = [
        provider
        for provider in worker.market_data.providers
        if provider.provider.code == "cardhobby"
    ]
    if args.refresh_first and not cardhobby_providers:
        await database.close()
        raise RuntimeError("--refresh-first requires the CardHobby provider")
    refresh_service = MarketDataService(
        database,
        cardhobby_providers,
        repository=worker.market_data.repository,
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    active_jobs: set[UUID] = set()

    async def claim_refresh_first(identity: dict, valuation_job_id: UUID) -> dict:
        """Atomically reserve a fresh CardHobby refresh for this process."""
        market_identity = MarketIdentity.model_validate(identity)
        deadline = asyncio.get_running_loop().time() + args.timeout
        active_refresh_id: UUID | None = None
        while True:
            if active_refresh_id is not None:
                async with database.connection() as connection:
                    result = await connection.execute(
                        "SELECT status FROM market_refresh_job WHERE id = %s",
                        (active_refresh_id,),
                    )
                    active = await result.fetchone()
                if active is not None and active["status"] in {"queued", "running"}:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError(
                            "timed out waiting for an existing market refresh before "
                            "the forced current-process refresh"
                        )
                    await asyncio.sleep(0.25)
                    continue
                active_refresh_id = None

            async with database.connection() as connection, connection.transaction():
                fingerprint = await refresh_service.repository.ensure_identity(
                    connection,
                    market_identity,
                    identity["collection_item_id"],
                )
                refresh_job_id, _ = await refresh_service.repository.enqueue_refresh(
                    connection,
                    fingerprint,
                    requested_by=identity.get("owner_id"),
                    reason=f"{args.audit_round}:refresh_first",
                )
                claimed = await refresh_service.repository.claim_refresh_by_id(
                    connection, refresh_job_id
                )
                if claimed is not None:
                    await connection.execute(
                        """UPDATE valuation_job
                           SET result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                               updated_at = now()
                           WHERE id = %s""",
                        (
                            psycopg.types.json.Jsonb(
                                {
                                    "refresh_first": True,
                                    "refresh_job_id": str(refresh_job_id),
                                    "refresh_provider": "cardhobby",
                                    "identity_fingerprint": fingerprint,
                                }
                            ),
                            valuation_job_id,
                        ),
                    )
                    return claimed
                active_refresh_id = refresh_job_id

    async def process(target: dict) -> None:
        async with semaphore:
            async with database.connection() as connection, connection.transaction():
                result = await connection.execute(
                    """INSERT INTO valuation_job (
                           collection_item_id, status, attempt_count, started_at, result_summary
                       ) VALUES (%s, 'identifying', 1, now(), %s)
                       ON CONFLICT (collection_item_id)
                       WHERE status IN ('queued', 'identifying', 'searching', 'calculating')
                       DO NOTHING RETURNING id""",
                    (
                        target["id"],
                        psycopg.types.json.Jsonb({"audit_round": args.audit_round}),
                    ),
                )
                row = await result.fetchone()
            if row is None:
                print(f"skip display_id={target['display_id']} active job appeared")
                return
            job_id: UUID = row["id"]
            active_jobs.add(job_id)
            print(f"start display_id={target['display_id']} job={job_id}", flush=True)
            try:
                if args.refresh_first:
                    identity = await worker._load_identity(job_id)
                    claimed_refresh = await claim_refresh_first(identity, job_id)
                    print(
                        f"refresh display_id={target['display_id']} "
                        f"job={claimed_refresh['id']}",
                        flush=True,
                    )
                    await refresh_service.process_claimed_refresh(claimed_refresh)
                await worker._process(job_id)
            except Exception as error:
                await worker._finish(
                    job_id,
                    "failed",
                    {
                        "reason": "refresh_first_failed",
                        "refresh_first": bool(args.refresh_first),
                    },
                    f"{type(error).__name__}: {str(error)}"[:500],
                )
            active_jobs.discard(job_id)
            async with database.connection() as connection:
                result = await connection.execute(
                    "SELECT status, error_message, result_summary FROM valuation_job WHERE id=%s",
                    (job_id,),
                )
                outcome = await result.fetchone()
            print(
                f"finish display_id={target['display_id']} status={outcome['status']} "
                f"tier={(outcome['result_summary'] or {}).get('valuation_tier')} "
                f"error={outcome['error_message'] or ''}",
                flush=True,
            )

    try:
        await asyncio.gather(*(process(target) for target in targets))
    finally:
        # A bounded audit may be stopped deliberately when providers remain
        # unavailable. Never leave those jobs looking active until lease expiry.
        if active_jobs:
            async with database.connection() as connection:
                await connection.execute(
                    """UPDATE valuation_job
                       SET status='insufficient_evidence', error_message=NULL,
                           result_summary=result_summary || jsonb_build_object(
                             'reason', '审计批次被有界停止；等待新市场证据再重试',
                             'audit_batch_stopped', true
                           ), finished_at=now(), updated_at=now()
                       WHERE id = ANY(%s) AND status IN (
                         'identifying', 'searching', 'calculating'
                       )""",
                    (list(active_jobs),),
                )
        await database.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--audit-round", default="unvalued-system-audit-v1")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--display-id",
        type=int,
        action="append",
        dest="display_ids",
        help="Restrict the batch to one or more collection display IDs.",
    )
    parser.add_argument(
        "--include-valued",
        action="store_true",
        help="Allow explicitly selected valued cards to receive a new immutable snapshot.",
    )
    parser.add_argument(
        "--refresh-first",
        action="store_true",
        help=(
            "Force one CardHobby refresh in this process before valuation. "
            "Requires explicit --display-id targets."
        ),
    )
    parser.add_argument(
        "--cached-only",
        action="store_true",
        help=(
            "Skip optional model/search providers and automatic refresh queues. "
            "May be combined with --refresh-first for one explicit CardHobby "
            "refresh followed by deterministic cached valuation."
        ),
    )
    parser.add_argument("--execute", action="store_true")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    if not 1 <= args.concurrency <= 8:
        raise SystemExit("--concurrency must be between 1 and 8")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be greater than zero")
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be greater than zero")
    if args.include_valued and not args.display_ids:
        raise SystemExit("--include-valued requires at least one --display-id")
    if args.refresh_first and not args.display_ids:
        raise SystemExit("--refresh-first requires at least one --display-id")
    return args


def main() -> None:
    args = parse_args()
    target_sql = EXPLICIT_TARGET_SQL if args.include_valued else TARGET_SQL
    with psycopg.connect(args.database_url, row_factory=dict_row) as connection:
        targets = [dict(row) for row in connection.execute(target_sql).fetchall()]
    if args.display_ids:
        requested = set(args.display_ids)
        targets = [row for row in targets if row["display_id"] in requested]
    if args.limit is not None:
        targets = targets[: args.limit]
    print(
        f"targets={len(targets)} display_ids={[row['display_id'] for row in targets]}"
    )
    if not args.execute:
        print("dry run only; pass --execute to create and process jobs")
        return
    asyncio.run(run(args, targets))


if __name__ == "__main__":
    main()
