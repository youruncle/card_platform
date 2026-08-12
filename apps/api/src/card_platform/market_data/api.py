from fastapi import APIRouter, Depends, Request
from fastapi import HTTPException
from pydantic import BaseModel
from psycopg.types.json import Jsonb

from card_platform.market_data.repository import MarketDataRepository
from card_platform.auth import require_admin
from card_platform.config import get_settings
from card_platform.valuation_backtest import run_temporal_backtest
from card_platform.valuation_worker import deduplicate_market_rows


router = APIRouter(
    prefix="/v1/market-data",
    tags=["market-data"],
    dependencies=[Depends(require_admin)],
)


class IdentityConflictResolution(BaseModel):
    keep_market_comp_id: str | None = None
    reject_all: bool = False


@router.get("/status")
async def provider_status(request: Request) -> dict:
    """Operational counters and circuit state only; credentials are never returned."""
    async with request.app.state.database.connection() as connection:
        providers = await MarketDataRepository().provider_status(connection)
        queue_result = await connection.execute(
            """
            SELECT status, count(*) AS count
            FROM market_refresh_job GROUP BY status ORDER BY status
            """
        )
        queue = {row["status"]: row["count"] for row in await queue_result.fetchall()}
        channel_result = await connection.execute(
            """
            SELECT ds.code, ds.name, ds.source_type, ds.base_url, ds.active, ds.metadata,
                   count(DISTINCT me.id) AS evidence_count,
                   max(me.occurred_at) AS latest_evidence_at,
                   (
                       SELECT count(*) FROM market_comp mc
                       WHERE mc.data_source_id = ds.id
                   ) AS comparable_count,
                   (
                       SELECT max(mc.occurred_at) FROM market_comp mc
                       WHERE mc.data_source_id = ds.id
                   ) AS latest_comparable_at
            FROM data_source ds
            LEFT JOIN market_evidence me ON me.provider_code = ds.code
            WHERE ds.code IN ('cardhobby', 'ebay', '130point', 'weikajia')
            GROUP BY ds.id
            ORDER BY ds.code
            """
        )
        channels = list(await channel_result.fetchall())
        conflict_result = await connection.execute(
            """
            SELECT count(*) AS sale_count
            FROM (
                SELECT data_source_id, external_id
                FROM market_comp
                WHERE external_id IS NOT NULL
                  AND upper(comparable_tier) <> 'REJECTED'
                GROUP BY data_source_id, external_id
                HAVING count(DISTINCT card_definition_id) > 1
            ) conflicts
            """
        )
        identity_conflict_count = (await conflict_result.fetchone())["sale_count"]
    settings = get_settings()
    for channel in channels:
        if channel["code"] == "ebay":
            metadata = dict(channel["metadata"] or {})
            metadata["readiness"] = (
                "active"
                if settings.ebay_client_id and settings.ebay_client_secret
                else "credentials_required"
            )
            channel["metadata"] = metadata
    return {
        "providers": providers,
        "channels": channels,
        "refresh_jobs": queue,
        "quarantined_cross_identity_sale_count": identity_conflict_count,
    }


@router.post("/valuation-backtests", status_code=201)
async def create_valuation_backtest(request: Request) -> dict:
    async with request.app.state.database.connection() as connection:
        result = await connection.execute(
            """
            WITH identity_conflicts AS (
                SELECT provider_code, external_sale_id
                FROM market_evidence
                GROUP BY provider_code, external_sale_id
                HAVING count(DISTINCT identity_fingerprint) > 1
            )
            SELECT me.id, me.identity_fingerprint AS card_definition_id,
                   me.occurred_at, me.sale_price AS value_base,
                   me.shipping_price AS shipping_base,
                   me.match_confidence, me.match_confidence AS similarity,
                   CASE me.evidence_level
                       WHEN 'human_verified_exact_identity' THEN 1.0
                       WHEN 'codex_assisted_exact_identity' THEN 0.85
                       WHEN 'confirmed_transaction' THEN 0.85
                       WHEN 'ended_auction' THEN 0.75
                       ELSE 0.65
                   END AS source_reliability,
                   me.external_sale_id AS external_id,
                   me.identity_classification AS classification,
                   me.provider_code AS source_code
            FROM market_evidence me
            JOIN market_identity_cache mic
              ON mic.identity_fingerprint = me.identity_fingerprint
            WHERE me.sale_price > 0
              AND me.occurred_at IS NOT NULL
              AND me.currency = 'CNY'
              AND me.match_confidence >= 0.5
              AND me.evidence_level IN (
                  'human_verified_exact_identity',
                  'codex_assisted_exact_identity',
                  'confirmed_transaction',
                  'legacy_cardhobby_sale'
              )
              AND NULLIF(mic.normalized_identity->>'player_name', '') IS NOT NULL
              AND mic.normalized_identity->>'season_start' IS NOT NULL
              AND NULLIF(mic.normalized_identity->>'product_name', '') IS NOT NULL
              AND (
                  NULLIF(mic.normalized_identity->>'card_number', '') IS NOT NULL
                  OR NULLIF(mic.normalized_identity->>'insert_name', '') IS NOT NULL
                  OR NULLIF(mic.normalized_identity->>'set_name', '') IS NOT NULL
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM identity_conflicts conflict
                  WHERE conflict.provider_code = me.provider_code
                    AND conflict.external_sale_id = me.external_sale_id
              )
            ORDER BY me.identity_fingerprint, me.occurred_at, me.id
            """
        )
        raw_rows = list(await result.fetchall())
        rows, duplicate_sales = deduplicate_market_rows(raw_rows)
        metrics = run_temporal_backtest(rows, max_prior_sales=3)
        sales_per_identity: dict[str, int] = {}
        for row in rows:
            key = str(row["card_definition_id"])
            sales_per_identity[key] = sales_per_identity.get(key, 0) + 1
        metrics["eligible_sale_count"] = len(rows)
        metrics["eligible_identity_count"] = len(sales_per_identity)
        metrics["identities_with_2plus_sales"] = sum(
            count >= 2 for count in sales_per_identity.values()
        )
        metrics["identities_with_3plus_sales"] = sum(
            count >= 3 for count in sales_per_identity.values()
        )
        metrics["raw_sale_count"] = len(raw_rows)
        metrics["deduplicated_sale_count"] = len(rows)
        metrics["duplicate_sale_count"] = len(duplicate_sales)
        stored = await connection.execute(
            """
            INSERT INTO valuation_backtest_run (
                model_version, provider_scope, config, metrics, sample_count
            ) VALUES (%s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (
                "hierarchical-evidence-3.0-temporal-conflict-quarantined",
                Jsonb(sorted({row["source_code"] for row in rows})),
                Jsonb(
                    {
                        "evidence_store": "market_evidence",
                        "base_currency": "CNY",
                        "minimum_match_confidence": 0.5,
                        "allowed_evidence_levels": [
                            "human_verified_exact_identity",
                            "codex_assisted_exact_identity",
                            "confirmed_transaction",
                            "legacy_cardhobby_sale",
                        ],
                        "exact_identity_specificity_required": True,
                        "min_prior_sales": 2,
                        "max_prior_sales": 3,
                        "cross_channel_deduplication": True,
                        "cross_identity_conflict_quarantine": True,
                    }
                ),
                Jsonb(metrics),
                metrics["sample_count"],
            ),
        )
        run = await stored.fetchone()
    return {**run, "metrics": metrics}


@router.get("/valuation-backtests")
async def list_valuation_backtests(request: Request) -> list[dict]:
    async with request.app.state.database.connection() as connection:
        result = await connection.execute(
            """
            SELECT id, model_version, provider_scope, config, metrics,
                   sample_count, created_at
            FROM valuation_backtest_run
            ORDER BY created_at DESC
            LIMIT 50
            """
        )
        return list(await result.fetchall())


@router.get("/identity-conflicts")
async def list_identity_conflicts(request: Request) -> list[dict]:
    """List unresolved underlying sales bound to multiple exact-card identities."""
    async with request.app.state.database.connection() as connection:
        result = await connection.execute(
            """
            WITH conflicts AS (
                SELECT data_source_id, external_id
                FROM market_comp
                WHERE external_id IS NOT NULL
                  AND upper(comparable_tier) <> 'REJECTED'
                GROUP BY data_source_id, external_id
                HAVING count(DISTINCT card_definition_id) > 1
            )
            SELECT ds.code AS source_code, mc.external_id, mc.id AS market_comp_id,
                   mc.card_definition_id, mc.listing_title, mc.source_url,
                   mc.occurred_at, mc.sale_price, mc.shipping_price, mc.currency,
                   mc.comparable_tier, mc.match_confidence, mc.source_reliability,
                   p.full_name AS player_name, cs.season_start, cs.season_end,
                   cs.product_name, cd.card_number, cd.insert_name,
                   cd.parallel_name, cd.print_run, cd.is_rookie,
                   cd.is_autograph, cd.is_memorabilia, cd.is_patch
            FROM market_comp mc
            JOIN conflicts c USING (data_source_id, external_id)
            JOIN data_source ds ON ds.id = mc.data_source_id
            JOIN card_definition cd ON cd.id = mc.card_definition_id
            JOIN player p ON p.id = cd.player_id
            JOIN card_set cs ON cs.id = cd.card_set_id
            WHERE upper(mc.comparable_tier) <> 'REJECTED'
            ORDER BY ds.code, mc.external_id, mc.id
            """
        )
        rows = list(await result.fetchall())
    groups: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["source_code"], row["external_id"])
        group = groups.setdefault(
            key,
            {
                "source_code": row["source_code"],
                "external_id": row["external_id"],
                "listing_title": row["listing_title"],
                "source_url": row["source_url"],
                "occurred_at": row["occurred_at"],
                "sale_price": row["sale_price"],
                "shipping_price": row["shipping_price"],
                "currency": row["currency"],
                "bindings": [],
            },
        )
        group["bindings"].append(
            {
                "market_comp_id": row["market_comp_id"],
                "card_definition_id": row["card_definition_id"],
                "identity": {
                    "player_name": row["player_name"],
                    "season_start": row["season_start"],
                    "season_end": row["season_end"],
                    "product_name": row["product_name"],
                    "card_number": row["card_number"],
                    "insert_name": row["insert_name"],
                    "parallel_name": row["parallel_name"],
                    "print_run": row["print_run"],
                    "is_rookie": row["is_rookie"],
                    "is_autograph": row["is_autograph"],
                    "is_memorabilia": row["is_memorabilia"],
                    "is_patch": row["is_patch"],
                },
                "comparable_tier": row["comparable_tier"],
                "match_confidence": row["match_confidence"],
                "source_reliability": row["source_reliability"],
            }
        )
    return list(groups.values())


@router.post("/identity-conflicts/{source_code}/{external_id}/resolve")
async def resolve_identity_conflict(
    source_code: str,
    external_id: str,
    payload: IdentityConflictResolution,
    request: Request,
) -> dict:
    if payload.reject_all == (payload.keep_market_comp_id is not None):
        raise HTTPException(
            status_code=422,
            detail="choose exactly one of keep_market_comp_id or reject_all",
        )
    async with request.app.state.database.connection() as connection, connection.transaction():
        result = await connection.execute(
            """
            SELECT mc.id
            FROM market_comp mc
            JOIN data_source ds ON ds.id = mc.data_source_id
            WHERE ds.code = %s AND mc.external_id = %s
              AND upper(mc.comparable_tier) <> 'REJECTED'
            FOR UPDATE OF mc
            """,
            (source_code, external_id),
        )
        ids = {str(row["id"]) for row in await result.fetchall()}
        if len(ids) < 2:
            raise HTTPException(status_code=404, detail="unresolved identity conflict not found")
        if payload.keep_market_comp_id is not None and payload.keep_market_comp_id not in ids:
            raise HTTPException(status_code=422, detail="keep_market_comp_id is not in this conflict")
        rejected_ids = (
            ids
            if payload.reject_all
            else ids - {str(payload.keep_market_comp_id)}
        )
        await connection.execute(
            """
            UPDATE market_comp
            SET comparable_tier = 'rejected',
                classification = COALESCE(classification, '{}'::jsonb) || %s
            WHERE id = ANY(%s::uuid[])
            """,
            (
                Jsonb(
                    {
                        "identity_conflict_resolution": "rejected",
                        "identity_conflict_source_code": source_code,
                        "identity_conflict_external_id": external_id,
                    }
                ),
                list(rejected_ids),
            ),
        )
        if payload.keep_market_comp_id is not None:
            await connection.execute(
                """
                UPDATE market_comp
                SET classification = COALESCE(classification, '{}'::jsonb) || %s
                WHERE id = %s
                """,
                (
                    Jsonb(
                        {
                            "identity_conflict_resolution": "kept",
                            "identity_conflict_source_code": source_code,
                            "identity_conflict_external_id": external_id,
                        }
                    ),
                    payload.keep_market_comp_id,
                ),
            )
    return {
        "source_code": source_code,
        "external_id": external_id,
        "kept_market_comp_id": payload.keep_market_comp_id,
        "rejected_count": len(rejected_ids),
    }
