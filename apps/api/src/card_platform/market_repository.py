import hashlib
import re
from urllib.parse import urlparse
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.domain.valuation import ComparableSale, estimate_from_comps
from card_platform.repository import NotFoundError
from card_platform.schemas import (
    AutomatedValuationDetail,
    AutomatedValuationSnapshot,
    MarketCompCreate,
    MarketCompView,
    MarketEstimateCreate,
    MarketEstimateResult,
    ShadowValuationSnapshot,
)


class MarketRepository:
    SOURCE_HOSTS = {
        "cardhobby": {"cardhobby.com.cn"},
        "ebay": {"ebay.com"},
        "130point": {"130point.com"},
    }

    @staticmethod
    def _external_id_from_url(source_code: str, source_url: str | None) -> str | None:
        path = urlparse(source_url or "").path.rstrip("/")
        patterns = {
            "ebay": r"/itm/(?:[^/]+/)?(\d+)$",
            "cardhobby": r"/market/item/(\d+)$",
        }
        pattern = patterns.get(source_code)
        if not pattern:
            return None
        match = re.search(pattern, path, re.IGNORECASE)
        return match.group(1) if match else None

    @classmethod
    def _validate_source(cls, payload: MarketCompCreate) -> str:
        expected_hosts = cls.SOURCE_HOSTS.get(payload.source_code)
        if expected_hosts:
            hostname = (
                (urlparse(payload.source_url or "").hostname or "")
                .lower()
                .removeprefix("www.")
            )
            if not any(
                hostname == host or hostname.endswith(f".{host}")
                for host in expected_hosts
            ):
                raise ValueError(
                    f"{payload.source_code} evidence requires a matching source URL"
                )
        if payload.source_code == "weikajia" and not (
            payload.external_id or payload.source_url
        ):
            raise ValueError("weikajia evidence requires an external_id or source_url")
        if (
            payload.source_code == "130point"
            and payload.origin_source_code
            and payload.origin_source_code != "130point"
            and not payload.external_id
        ):
            raise ValueError(
                "130point aggregated evidence requires the underlying marketplace "
                "external_id for cross-channel deduplication"
            )
        resolved_external_id = payload.external_id or cls._external_id_from_url(
            payload.source_code,
            payload.source_url,
        )
        fingerprint = "|".join(
            (
                payload.source_code,
                resolved_external_id or "",
                payload.source_url or "",
                payload.occurred_at.isoformat(),
                str(payload.sale_price),
                payload.listing_title or "",
            )
        )
        return resolved_external_id or hashlib.sha256(fingerprint.encode()).hexdigest()

    async def _definition_id(
        self, connection: AsyncConnection, item_id: UUID, owner_id: UUID | None = None
    ) -> UUID:
        query = (
            "SELECT card_definition_id FROM collection_item "
            "WHERE id = %s AND deleted_at IS NULL"
        )
        params: tuple = (item_id,)
        if owner_id:
            query += " AND owner_id = %s"
            params = (item_id, owner_id)
        result = await connection.execute(query, params)
        row = await result.fetchone()
        if row is None:
            raise NotFoundError("collection item not found")
        return row["card_definition_id"]

    async def create_comp(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        payload: MarketCompCreate,
    ) -> UUID:
        definition_id = await self._definition_id(connection, item_id, payload.owner_id)
        external_id = self._validate_source(payload)
        source = await connection.execute(
            "SELECT id FROM data_source WHERE code = %s AND active",
            (payload.source_code,),
        )
        source_row = await source.fetchone()
        if source_row is None:
            raise ValueError("market source is not registered or active")
        source_id = source_row["id"]
        existing_identity = await connection.execute(
            """
            SELECT card_definition_id
            FROM market_comp
            WHERE data_source_id = %s AND external_id = %s
            LIMIT 1
            """,
            (source_id, external_id),
        )
        existing_identity_row = await existing_identity.fetchone()
        if (
            existing_identity_row is not None
            and existing_identity_row["card_definition_id"] != definition_id
        ):
            raise ValueError(
                "this underlying sale is already bound to a different card identity; "
                "review the card identity before importing it again"
            )
        result = await connection.execute(
            """
            INSERT INTO market_comp (
                card_definition_id, data_source_id, occurred_at, sale_price,
                shipping_price, currency, fx_rate_to_base, grade_company,
                grade_value, listing_title, source_url, external_id,
                source_reliability, match_confidence, classification
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                jsonb_build_object(
                    'source_code', %s::text,
                    'origin_source_code', %s::text,
                    'ingestion', 'authorized_import'
                )
            )
            ON CONFLICT (card_definition_id, data_source_id, external_id)
            DO UPDATE SET
                occurred_at = EXCLUDED.occurred_at,
                sale_price = EXCLUDED.sale_price,
                shipping_price = EXCLUDED.shipping_price,
                currency = EXCLUDED.currency,
                fx_rate_to_base = EXCLUDED.fx_rate_to_base,
                grade_company = EXCLUDED.grade_company,
                grade_value = EXCLUDED.grade_value,
                listing_title = EXCLUDED.listing_title,
                source_url = EXCLUDED.source_url,
                source_reliability = EXCLUDED.source_reliability,
                match_confidence = EXCLUDED.match_confidence,
                classification = market_comp.classification || EXCLUDED.classification
            RETURNING id
            """,
            (
                definition_id,
                source_id,
                payload.occurred_at,
                payload.sale_price,
                payload.shipping_price,
                payload.currency.upper(),
                payload.fx_rate_to_base,
                payload.grade_company,
                payload.grade_value,
                payload.listing_title,
                payload.source_url,
                external_id,
                payload.source_reliability,
                payload.match_confidence,
                payload.source_code,
                payload.origin_source_code or payload.source_code,
            ),
        )
        return (await result.fetchone())["id"]

    async def list_comps(
        self, connection: AsyncConnection, item_id: UUID, owner_id: UUID
    ) -> list[MarketCompView]:
        definition_id = await self._definition_id(connection, item_id, owner_id)
        result = await connection.execute(
            """
            SELECT id, occurred_at, sale_price, shipping_price, currency,
                   fx_rate_to_base, grade_company, grade_value, listing_title,
                   source_url, match_confidence, source_reliability, similarity,
                   comparable_tier, classification
            FROM market_comp WHERE card_definition_id = %s
            ORDER BY occurred_at DESC, created_at DESC LIMIT 100
            """,
            (definition_id,),
        )
        return [MarketCompView(**row) for row in await result.fetchall()]

    async def estimate(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        payload: MarketEstimateCreate,
    ) -> MarketEstimateResult:
        definition_id = await self._definition_id(connection, item_id, payload.owner_id)
        result = await connection.execute(
            """
            SELECT id,
                   sale_price * COALESCE(fx_rate_to_base, 1) AS value_base,
                   GREATEST(0, EXTRACT(day FROM now() - occurred_at))::int AS age_days,
                   COALESCE(match_confidence, 0.5) AS match_confidence
            FROM market_comp
            WHERE card_definition_id = %s
              AND occurred_at >= now() - interval '730 days'
              AND COALESCE(match_confidence, 0) >= 0.5
            ORDER BY occurred_at DESC
            """,
            (definition_id,),
        )
        rows = await result.fetchall()
        try:
            estimate = estimate_from_comps(
                [
                    ComparableSale(
                        value_base=row["value_base"],
                        age_days=row["age_days"],
                        match_confidence=row["match_confidence"],
                    )
                    for row in rows
                ]
            )
        except ValueError as error:
            raise NotFoundError(str(error)) from error
        valuation_result = await connection.execute(
            """
            INSERT INTO valuation_snapshot (
                collection_item_id, method, currency, low_value, fair_value,
                high_value, confidence, evidence, model_version
            ) VALUES (%s, 'market_comp', %s, %s, %s, %s, %s, %s, 'market-comp-1.0')
            RETURNING id
            """,
            (
                item_id,
                payload.currency.upper(),
                estimate.low,
                estimate.fair,
                estimate.high,
                estimate.confidence,
                Jsonb(
                    {
                        "market_comp_ids": [str(row["id"]) for row in rows],
                        "price_basis": "sale_price_excluding_shipping",
                    }
                ),
            ),
        )
        valuation_id = (await valuation_result.fetchone())["id"]
        return MarketEstimateResult(
            valuation_id=valuation_id,
            currency=payload.currency.upper(),
            low_value=estimate.low,
            fair_value=estimate.fair,
            high_value=estimate.high,
            confidence=estimate.confidence,
            evidence_count=estimate.evidence_count,
        )

    async def automated_detail(
        self, connection: AsyncConnection, item_id: UUID, owner_id: UUID
    ) -> AutomatedValuationDetail:
        await self._definition_id(connection, item_id, owner_id)
        job_result = await connection.execute(
            """
            SELECT id AS job_id, status, error_message, result_summary,
                   created_at, started_at, finished_at
            FROM valuation_job WHERE collection_item_id = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (item_id,),
        )
        job = await job_result.fetchone()
        valuation_result = await connection.execute(
            """
            SELECT id, valued_at, currency, low_value, fair_value, high_value,
                   confidence, evidence, model_version
            FROM valuation_snapshot v
            WHERE collection_item_id = %s AND method = 'ai_estimate'
              AND identity_compatibility <> 'incompatible'
              AND (%s::timestamptz IS NULL OR valued_at >= %s OR (
                  model_version IN ('codex-independent-fallback-v1','codex-researched-fallback-v2') AND id = (
                      SELECT current_value.id FROM valuation_snapshot current_value
                      WHERE current_value.collection_item_id = v.collection_item_id
                        AND current_value.identity_compatibility <> 'incompatible'
                      ORDER BY current_value.valued_at DESC, current_value.created_at DESC LIMIT 1
                  )
              ))
            ORDER BY valued_at DESC, created_at DESC LIMIT 1
            """,
            (
                item_id,
                job["created_at"] if job else None,
                job["created_at"] if job else None,
            ),
        )
        valuation = await valuation_result.fetchone()
        shadow_result = await connection.execute(
            """
            SELECT id, created_at, status, currency, low_value, fair_value,
                   high_value, confidence, evidence_count, review_reasons,
                   evidence, model_version
            FROM valuation_shadow_result
            WHERE collection_item_id = %s
            ORDER BY created_at DESC LIMIT 1
            """,
            (item_id,),
        )
        shadow = await shadow_result.fetchone()
        comps = await self.list_comps(connection, item_id, owner_id)
        return AutomatedValuationDetail(
            **(job or {}),
            valuation=AutomatedValuationSnapshot(**valuation) if valuation else None,
            shadow_valuation=ShadowValuationSnapshot(**shadow) if shadow else None,
            comparables=comps,
        )

    async def enqueue_valuation(
        self, connection: AsyncConnection, item_id: UUID, owner_id: UUID
    ) -> UUID:
        await self._definition_id(connection, item_id, owner_id)
        async with connection.transaction():
            await connection.execute(
                """
                UPDATE market_identity_cache c
                SET freshness = 'stale', next_refresh_at = now(), updated_at = now()
                FROM market_identity_binding b
                WHERE b.collection_item_id = %s
                  AND b.identity_fingerprint = c.identity_fingerprint
                """,
                (item_id,),
            )
            await connection.execute(
                """
                INSERT INTO market_refresh_job (
                    identity_fingerprint, requested_by, reason
                )
                SELECT b.identity_fingerprint, %s, 'user_retry'
                FROM market_identity_binding b
                WHERE b.collection_item_id = %s
                ON CONFLICT (identity_fingerprint)
                    WHERE status IN ('queued', 'running')
                DO UPDATE SET updated_at = now()
                """,
                (owner_id, item_id),
            )
            await connection.execute(
                """
                UPDATE valuation_job SET status = 'failed',
                    error_message = '由用户重新发起估值', finished_at = now(), updated_at = now()
                WHERE collection_item_id = %s
                  AND status IN ('queued', 'identifying', 'searching', 'calculating')
                """,
                (item_id,),
            )
            result = await connection.execute(
                """
                INSERT INTO valuation_job (collection_item_id, status)
                VALUES (%s, 'queued') RETURNING id
                """,
                (item_id,),
            )
            return (await result.fetchone())["id"]
