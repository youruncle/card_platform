from decimal import Decimal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.domain.advisory import build_recommendation
from card_platform.domain.auto_analysis import derive_automatic_factors
from card_platform.domain.scoring import FACTOR_NAMES, calculate_score
from card_platform.repository import NotFoundError
from card_platform.schemas import (
    AutoCardAnalysisCreate,
    CardAnalysisCreate,
    CardAnalysisResult,
    FactorScores,
)


class AnalysisRepository:
    async def automatically_analyze_item(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        payload: AutoCardAnalysisCreate,
    ) -> CardAnalysisResult:
        result = await connection.execute(
            """
            SELECT
                p.hall_of_fame, p.active, b.tier AS brand_tier,
                cd.is_rookie, cd.is_autograph, cd.is_patch, cd.print_run,
                ci.serial_total,
                potential.potential_score,
                metric.metrics,
                valuation.low_value, valuation.high_value,
                COALESCE(comps.market_comp_count, 0) AS market_comp_count,
                events.event_impact
            FROM collection_item ci
            JOIN card_definition cd ON cd.id = ci.card_definition_id
            JOIN player p ON p.id = cd.player_id
            JOIN card_set cs ON cs.id = cd.card_set_id
            JOIN brand b ON b.id = cs.brand_id
            LEFT JOIN LATERAL (
                SELECT potential_score
                FROM player_potential_snapshot
                WHERE player_id = p.id
                ORDER BY assessed_at DESC LIMIT 1
            ) potential ON true
            LEFT JOIN LATERAL (
                SELECT metrics
                FROM player_metric
                WHERE player_id = p.id
                ORDER BY captured_at DESC LIMIT 1
            ) metric ON true
            LEFT JOIN LATERAL (
                SELECT low_value, high_value
                FROM valuation_snapshot
                WHERE collection_item_id = ci.id AND currency = %s
                ORDER BY valued_at DESC LIMIT 1
            ) valuation ON true
            LEFT JOIN LATERAL (
                SELECT count(*)::int AS market_comp_count
                FROM market_comp
                WHERE card_definition_id = cd.id AND match_confidence >= 0.5
            ) comps ON true
            LEFT JOIN LATERAL (
                SELECT avg(COALESCE(impact_score, 50)) AS event_impact
                FROM player_event
                WHERE player_id = p.id AND occurred_at >= now() - interval '180 days'
            ) events ON true
            WHERE ci.id = %s AND ci.owner_id = %s AND ci.deleted_at IS NULL
            """,
            (payload.currency.upper(), item_id, payload.owner_id),
        )
        evidence = await result.fetchone()
        if evidence is None:
            raise NotFoundError("collection item not found")
        derived = derive_automatic_factors(dict(evidence))
        fair_low = evidence["low_value"] or payload.asking_price * Decimal("0.85")
        fair_high = evidence["high_value"] or payload.asking_price * Decimal("1.15")
        analysis = CardAnalysisCreate(
            owner_id=payload.owner_id,
            asking_price=payload.asking_price,
            currency=payload.currency,
            fair_value_low=fair_low.quantize(Decimal("0.01")),
            fair_value_high=fair_high.quantize(Decimal("0.01")),
            evidence_count=evidence["market_comp_count"],
            factors=derived.factors,
            factor_inputs=derived.inputs,
        )
        return await self.analyze_item(connection, item_id, analysis)

    async def analyze_item(
        self,
        connection: AsyncConnection,
        item_id: UUID,
        payload: CardAnalysisCreate,
    ) -> CardAnalysisResult:
        async with connection.transaction():
            item_result = await connection.execute(
                """
                SELECT ci.id
                FROM collection_item ci
                WHERE ci.id = %s AND ci.owner_id = %s AND ci.deleted_at IS NULL
                FOR UPDATE
                """,
                (item_id, payload.owner_id),
            )
            if await item_result.fetchone() is None:
                raise NotFoundError("collection item not found")

            model_result = await connection.execute(
                """
                SELECT id, version, weights
                FROM scoring_model
                WHERE name = 'card_investment_score' AND active
                ORDER BY created_at DESC LIMIT 1
                """
            )
            model = await model_result.fetchone()
            if model is None:
                raise NotFoundError("active scoring model not found")

            factors = {name: Decimal(str(getattr(payload.factors, name))) for name in FACTOR_NAMES}
            weights = {name: Decimal(str(model["weights"][name])) for name in FACTOR_NAMES}
            score = calculate_score(factors, weights)
            recommendation = build_recommendation(
                total_score=score.total,
                asking_price=payload.asking_price,
                fair_value_low=payload.fair_value_low,
                fair_value_high=payload.fair_value_high,
                age_potential=payload.factors.age_potential,
                historical_standing=payload.factors.historical_standing,
                evidence_count=payload.evidence_count,
            )
            factor_inputs = payload.model_dump(mode="json")["factor_inputs"]

            input_snapshot = {
                "asking_price": str(payload.asking_price),
                "currency": payload.currency.upper(),
                "fair_value_low": str(payload.fair_value_low),
                "fair_value_high": str(payload.fair_value_high),
                "evidence_count": payload.evidence_count,
                "factors": {name: str(value) for name, value in factors.items()},
                "factor_inputs": factor_inputs,
            }
            analysis_result = await connection.execute(
                """
                INSERT INTO analysis_request (
                    owner_id, collection_item_id, status, input_payload,
                    evidence_snapshot, completed_at
                ) VALUES (%s, %s, 'completed', %s, %s, now())
                RETURNING id
                """,
                (
                    payload.owner_id,
                    item_id,
                    Jsonb(input_snapshot),
                    Jsonb(
                        {
                            "evidence_count": payload.evidence_count,
                            "scoring_model_version": model["version"],
                        }
                    ),
                ),
            )
            analysis_id = (await analysis_result.fetchone())["id"]

            score_result = await connection.execute(
                """
                INSERT INTO card_score (
                    collection_item_id, scoring_model_id, total_score,
                    factor_scores, factor_inputs, explanation
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    item_id,
                    model["id"],
                    score.total,
                    Jsonb({name: str(value) for name, value in factors.items()}),
                    Jsonb(factor_inputs),
                    Jsonb(
                        {
                            "contributions": {
                                name: str(value) for name, value in score.contributions.items()
                            }
                        }
                    ),
                ),
            )
            score_id = (await score_result.fetchone())["id"]

            thesis = (
                f"综合评分 {score.total}，当前价格{recommendation.price_assessment}；"
                f"建议持有 {recommendation.holding_period_min_months}–"
                f"{recommendation.holding_period_max_months} 个月。"
            )
            await connection.execute(
                """
                INSERT INTO recommendation (
                    analysis_request_id, card_score_id, rating, currency,
                    fair_value_low, fair_value_high, target_sell_low, target_sell_high,
                    holding_period_min_months, holding_period_max_months, confidence,
                    thesis, catalysts, risks, model_provider, model_name, prompt_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, 'rules', 'card-advisor', '1.0.0')
                """,
                (
                    analysis_id,
                    score_id,
                    recommendation.rating.value,
                    payload.currency.upper(),
                    payload.fair_value_low,
                    payload.fair_value_high,
                    recommendation.target_sell_low,
                    recommendation.target_sell_high,
                    recommendation.holding_period_min_months,
                    recommendation.holding_period_max_months,
                    recommendation.confidence,
                    thesis,
                    Jsonb(list(recommendation.catalysts)),
                    Jsonb(list(recommendation.risks)),
                ),
            )

            return CardAnalysisResult(
                analysis_id=analysis_id,
                score_id=score_id,
                total_score=score.total,
                factor_scores=FactorScores(**factors),
                contributions=score.contributions,
                price_assessment=recommendation.price_assessment,
                rating=recommendation.rating.value,
                currency=payload.currency.upper(),
                fair_value_low=payload.fair_value_low,
                fair_value_high=payload.fair_value_high,
                target_sell_low=recommendation.target_sell_low,
                target_sell_high=recommendation.target_sell_high,
                holding_period_min_months=recommendation.holding_period_min_months,
                holding_period_max_months=recommendation.holding_period_max_months,
                confidence=recommendation.confidence,
                catalysts=list(recommendation.catalysts),
                risks=list(recommendation.risks),
            )
