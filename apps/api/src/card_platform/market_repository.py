from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.domain.valuation import ComparableSale, estimate_from_comps
from card_platform.repository import NotFoundError
from card_platform.schemas import (
    MarketCompCreate,
    MarketCompView,
    MarketEstimateCreate,
    MarketEstimateResult,
)


class MarketRepository:
    async def _definition_id(
        self, connection: AsyncConnection, item_id: UUID, owner_id: UUID | None = None
    ) -> UUID:
        query = "SELECT card_definition_id FROM collection_item WHERE id = %s"
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
        source = await connection.execute("SELECT id FROM data_source WHERE code = 'market_import'")
        source_id = (await source.fetchone())["id"]
        result = await connection.execute(
            """
            INSERT INTO market_comp (
                card_definition_id, data_source_id, occurred_at, sale_price,
                shipping_price, currency, fx_rate_to_base, grade_company,
                grade_value, listing_title, source_url, match_confidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                payload.match_confidence,
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
                   source_url, match_confidence
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
                   (sale_price + shipping_price) * COALESCE(fx_rate_to_base, 1) AS value_base,
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
                Jsonb({"market_comp_ids": [str(row["id"]) for row in rows]}),
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
