from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.market_data.models import (
    EvidenceRecord,
    Freshness,
    MarketDataResult,
    MarketIdentity,
)


class MarketDataRepository:
    async def ensure_identity(
        self,
        connection: AsyncConnection,
        identity: MarketIdentity,
        collection_item_id: UUID | None = None,
    ) -> str:
        fingerprint = identity.fingerprint
        await connection.execute(
            """
            INSERT INTO market_identity_cache (
                identity_fingerprint, normalized_identity, last_requested_at
            ) VALUES (%s, %s, now())
            ON CONFLICT (identity_fingerprint) DO UPDATE SET
                normalized_identity = EXCLUDED.normalized_identity,
                popularity_score = market_identity_cache.popularity_score + 1,
                last_requested_at = now(),
                updated_at = now()
            """,
            (fingerprint, Jsonb(identity.normalized())),
        )
        if collection_item_id is not None:
            await connection.execute(
                """
                INSERT INTO market_identity_binding (
                    collection_item_id, identity_fingerprint
                ) VALUES (%s, %s)
                ON CONFLICT (collection_item_id) DO UPDATE SET
                    identity_fingerprint = EXCLUDED.identity_fingerprint,
                    updated_at = now()
                """,
                (collection_item_id, fingerprint),
            )
        return fingerprint

    async def cached(
        self, connection: AsyncConnection, fingerprint: str
    ) -> MarketDataResult:
        cache_result = await connection.execute(
            """
            SELECT freshness, data_as_of, next_refresh_at, evidence_count
            FROM market_identity_cache WHERE identity_fingerprint = %s
            """,
            (fingerprint,),
        )
        cache = await cache_result.fetchone()
        if cache is None:
            return MarketDataResult(
                identity_fingerprint=fingerprint,
                freshness=Freshness.INSUFFICIENT,
            )
        evidence_result = await connection.execute(
            """
            SELECT provider_code, external_sale_id, raw_title, sale_price,
                   shipping_price, currency, occurred_at, source_url, evidence_level,
                   grade_company, grade_value, match_confidence,
                   identity_classification, raw_payload, evidence_version, fetched_at
            FROM market_evidence
            WHERE identity_fingerprint = %s
            ORDER BY occurred_at DESC, first_seen_at DESC
            LIMIT 100
            """,
            (fingerprint,),
        )
        rows = await evidence_result.fetchall()
        evidence = [EvidenceRecord.model_validate(row) for row in rows]
        now = datetime.now(timezone.utc)
        stored = Freshness(cache["freshness"])
        if stored == Freshness.SOURCE_LIMITED:
            freshness = stored
        elif not evidence:
            freshness = Freshness.INSUFFICIENT
        elif cache["next_refresh_at"] and cache["next_refresh_at"] <= now:
            freshness = Freshness.STALE
        elif stored == Freshness.FRESH:
            freshness = Freshness.CACHED
        else:
            freshness = stored
        return MarketDataResult(
            identity_fingerprint=fingerprint,
            freshness=freshness,
            evidence=evidence,
            data_as_of=cache["data_as_of"],
        )

    async def adopt_legacy_evidence(
        self,
        connection: AsyncConnection,
        fingerprint: str,
        card_definition_id: UUID,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO market_evidence (
                identity_fingerprint, provider_code, external_sale_id, raw_title,
                sale_price, shipping_price, currency, occurred_at, source_url,
                evidence_level, grade_company, grade_value, match_confidence,
                identity_classification, raw_payload, evidence_version,
                fetched_at, first_seen_at, last_seen_at
            )
            SELECT
                %s, provider_code, external_sale_id, raw_title,
                sale_price, shipping_price, currency, occurred_at, source_url,
                evidence_level, grade_company, grade_value, match_confidence,
                identity_classification, raw_payload, evidence_version,
                fetched_at, first_seen_at, last_seen_at
            FROM market_evidence
            WHERE identity_fingerprint = 'legacy:' || %s::text
            ON CONFLICT (identity_fingerprint, provider_code, external_sale_id)
            DO NOTHING
            """,
            (fingerprint, card_definition_id),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache c
            SET evidence_count = (
                    SELECT count(*) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                freshness = CASE WHEN EXISTS (
                    SELECT 1 FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ) THEN 'cached' ELSE freshness END,
                data_as_of = (
                    SELECT max(occurred_at) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                next_refresh_at = COALESCE(next_refresh_at, now()),
                updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (fingerprint,),
        )

    async def enqueue_refresh(
        self,
        connection: AsyncConnection,
        fingerprint: str,
        *,
        requested_by: UUID | None = None,
        reason: str,
    ) -> tuple[UUID, bool]:
        result = await connection.execute(
            """
            INSERT INTO market_refresh_job (identity_fingerprint, requested_by, reason)
            VALUES (%s, %s, %s)
            ON CONFLICT (identity_fingerprint)
                WHERE status IN ('queued', 'running')
            DO UPDATE SET updated_at = now()
            RETURNING id, (xmax = 0) AS created
            """,
            (fingerprint, requested_by, reason),
        )
        row = await result.fetchone()
        if not row["created"]:
            await connection.execute(
                """
                UPDATE provider_state
                SET coalesced_job_count = coalesced_job_count + 1, updated_at = now()
                WHERE provider_code = 'cardhobby'
                """
            )
        return row["id"], row["created"]

    async def claim_refresh(self, connection: AsyncConnection) -> dict | None:
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = CASE WHEN attempt_count >= 3 THEN 'failed' ELSE 'queued' END,
                error_message = CASE
                    WHEN attempt_count >= 3
                    THEN COALESCE(error_message, 'refresh worker lease expired')
                    ELSE error_message
                END,
                finished_at = CASE WHEN attempt_count >= 3 THEN now() ELSE NULL END,
                not_before = CASE
                    WHEN attempt_count >= 3 THEN not_before
                    ELSE now() + interval '5 seconds'
                END,
                updated_at = now()
            WHERE status = 'running'
              AND started_at < now() - interval '15 minutes'
            """
        )
        result = await connection.execute(
            """
            SELECT j.id, j.identity_fingerprint, c.normalized_identity, c.data_as_of
            FROM market_refresh_job j
            JOIN market_identity_cache c USING (identity_fingerprint)
            WHERE j.status = 'queued' AND j.not_before <= now()
            ORDER BY j.created_at
            FOR UPDATE OF j SKIP LOCKED
            LIMIT 1
            """
        )
        row = await result.fetchone()
        if row is None:
            return None
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = 'running', attempt_count = attempt_count + 1,
                started_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (row["id"],),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache
            SET last_refresh_started_at = now(), updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (row["identity_fingerprint"],),
        )
        return dict(row)

    async def store_evidence(
        self,
        connection: AsyncConnection,
        fingerprint: str,
        evidence: list[EvidenceRecord],
    ) -> int:
        for item in evidence:
            await connection.execute(
                """
                INSERT INTO market_evidence (
                    identity_fingerprint, provider_code, external_sale_id, raw_title,
                    sale_price, shipping_price, currency, occurred_at, source_url,
                    evidence_level, grade_company, grade_value, match_confidence,
                    identity_classification, raw_payload, evidence_version, fetched_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (identity_fingerprint, provider_code, external_sale_id)
                DO UPDATE SET
                    raw_title = EXCLUDED.raw_title,
                    sale_price = EXCLUDED.sale_price,
                    shipping_price = EXCLUDED.shipping_price,
                    currency = EXCLUDED.currency,
                    occurred_at = EXCLUDED.occurred_at,
                    source_url = EXCLUDED.source_url,
                    evidence_level = EXCLUDED.evidence_level,
                    grade_company = EXCLUDED.grade_company,
                    grade_value = EXCLUDED.grade_value,
                    match_confidence = EXCLUDED.match_confidence,
                    identity_classification = EXCLUDED.identity_classification,
                    raw_payload = EXCLUDED.raw_payload,
                    evidence_version = EXCLUDED.evidence_version,
                    fetched_at = EXCLUDED.fetched_at,
                    last_seen_at = now()
                """,
                (
                    fingerprint,
                    item.provider_code,
                    item.external_sale_id,
                    item.raw_title,
                    item.sale_price,
                    item.shipping_price,
                    item.currency,
                    item.occurred_at,
                    item.source_url,
                    item.evidence_level,
                    item.grade_company,
                    item.grade_value,
                    item.match_confidence,
                    Jsonb(item.identity_classification),
                    Jsonb(item.raw_payload),
                    item.evidence_version,
                    item.fetched_at,
                ),
            )
        return len(evidence)

    async def reconcile_external_sale_identity(
        self,
        connection: AsyncConnection,
        *,
        provider_code: str,
        external_sale_id: str,
        authoritative_fingerprint: str,
        replaceable_evidence_levels: list[str] | None = None,
    ) -> list[str]:
        """Remove the same sale from obsolete identities after authoritative review."""
        level_clause = ""
        params: list[Any] = [
            provider_code,
            external_sale_id,
            authoritative_fingerprint,
        ]
        if replaceable_evidence_levels:
            level_clause = "AND evidence_level = ANY(%s)"
            params.append(replaceable_evidence_levels)
        result = await connection.execute(
            f"""
            DELETE FROM market_evidence
            WHERE provider_code = %s
              AND external_sale_id = %s
              AND identity_fingerprint <> %s
              {level_clause}
            RETURNING identity_fingerprint
            """,
            tuple(params),
        )
        removed = sorted({row["identity_fingerprint"] for row in await result.fetchall()})
        if removed:
            await connection.execute(
                """
                UPDATE market_identity_cache c
                SET evidence_count = (
                        SELECT count(*) FROM market_evidence e
                        WHERE e.identity_fingerprint = c.identity_fingerprint
                    ),
                    data_as_of = (
                        SELECT max(occurred_at) FROM market_evidence e
                        WHERE e.identity_fingerprint = c.identity_fingerprint
                    ),
                    freshness = CASE
                        WHEN EXISTS (
                            SELECT 1 FROM market_evidence e
                            WHERE e.identity_fingerprint = c.identity_fingerprint
                        ) THEN c.freshness
                        ELSE 'insufficient'
                    END,
                    updated_at = now()
                WHERE c.identity_fingerprint = ANY(%s)
                """,
                (removed,),
            )
        return removed

    async def finish_refresh(
        self,
        connection: AsyncConnection,
        job_id: UUID,
        fingerprint: str,
        freshness: Freshness,
        *,
        ttl_seconds: int,
        error: str | None = None,
    ) -> None:
        status = {
            Freshness.SOURCE_LIMITED: "source_limited",
            Freshness.INSUFFICIENT: "insufficient",
        }.get(freshness, "succeeded")
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = %s, error_message = %s, finished_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (status, error, job_id),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache c
            SET freshness = %s,
                evidence_count = (
                    SELECT count(*) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ),
                last_success_at = CASE
                    WHEN %s::text IS NULL THEN now()
                    ELSE last_success_at
                END,
                data_as_of = COALESCE((
                    SELECT max(occurred_at) FROM market_evidence e
                    WHERE e.identity_fingerprint = c.identity_fingerprint
                ), data_as_of),
                next_refresh_at = now() + make_interval(secs => %s),
                last_error = %s,
                updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (freshness.value, error, ttl_seconds, error, fingerprint),
        )
        if freshness != Freshness.SOURCE_LIMITED:
            await connection.execute(
                """
                INSERT INTO valuation_job (collection_item_id, status)
                SELECT b.collection_item_id, 'queued'
                FROM market_identity_binding b
                WHERE b.identity_fingerprint = %s
                  AND EXISTS (
                      SELECT 1 FROM market_evidence evidence
                      WHERE evidence.identity_fingerprint = b.identity_fingerprint
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM valuation_job active
                      WHERE active.collection_item_id = b.collection_item_id
                        AND active.status IN (
                            'queued', 'identifying', 'searching', 'calculating'
                        )
                  )
                  AND COALESCE((
                      SELECT latest.status FROM valuation_job latest
                      WHERE latest.collection_item_id = b.collection_item_id
                      ORDER BY latest.created_at DESC LIMIT 1
                  ), 'insufficient_evidence') = 'insufficient_evidence'
                ON CONFLICT DO NOTHING
                """,
                (fingerprint,),
            )

    async def fail_refresh(
        self,
        connection: AsyncConnection,
        job_id: UUID,
        fingerprint: str,
        error: str,
    ) -> None:
        await connection.execute(
            """
            UPDATE market_refresh_job
            SET status = 'failed', error_message = %s,
                finished_at = now(), updated_at = now()
            WHERE id = %s
            """,
            (error, job_id),
        )
        await connection.execute(
            """
            UPDATE market_identity_cache
            SET last_error = %s, updated_at = now()
            WHERE identity_fingerprint = %s
            """,
            (error, fingerprint),
        )

    async def provider_status(self, connection: AsyncConnection) -> list[dict]:
        result = await connection.execute(
            """
            SELECT provider_code, circuit_state, concurrent_limit, min_interval_ms,
                   error_budget, active_requests, consecutive_failures, request_count,
                   success_count, http_403_count, http_429_count, challenge_count,
                   cache_hit_count, coalesced_job_count, total_duration_ms,
                   last_request_at, last_success_at, circuit_opened_at, circuit_until,
                   retry_after_until, last_error, updated_at
            FROM provider_state ORDER BY provider_code
            """
        )
        return [dict(row) for row in await result.fetchall()]
