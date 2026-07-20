import json
from typing import Protocol
from uuid import UUID

from openai import AsyncOpenAI
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.repository import NotFoundError
from card_platform.schemas import AINarrative, AINarrativeResult

SYSTEM_INSTRUCTIONS = """你是篮球球星卡投资研究助手。只解释输入中的结构化证据，不修改规则模型给出的评级、价格区间、目标价或持有期。使用简洁中文，明确区分事实、推断和数据缺口。禁止承诺收益。催化剂与风险各不超过四项。"""


class NarrativeProvider(Protocol):
    name: str
    model: str

    async def generate(self, evidence: dict) -> AINarrative: ...


class OpenAINarrativeProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key)

    async def generate(self, evidence: dict) -> AINarrative:
        response = await self.client.responses.parse(
            model=self.model,
            instructions=SYSTEM_INSTRUCTIONS,
            input=json.dumps(evidence, ensure_ascii=False),
            text_format=AINarrative,
        )
        if response.output_parsed is None:
            raise RuntimeError("AI response did not contain a structured narrative")
        return response.output_parsed


class RuleNarrativeProvider:
    name = "rules"
    model = "deterministic-fallback-1.0"

    async def generate(self, evidence: dict) -> AINarrative:
        recommendation = evidence["recommendation"]
        score = evidence["score"]
        missing = recommendation.get("evidence_count", 0) < 2
        return AINarrative(
            thesis=(
                f"规则模型综合评分为 {score['total_score']}，评级为"
                f"{recommendation['rating']}。当前询价{recommendation['price_assessment']}，"
                "决策应以可比成交和持仓纪律为主。"
            ),
            price_reasoning=(
                f"合理区间为 {recommendation['fair_value_low']}–"
                f"{recommendation['fair_value_high']} {recommendation['currency']}；"
                f"目标区间为 {recommendation['target_sell_low']}–"
                f"{recommendation['target_sell_high']} {recommendation['currency']}。"
            ),
            catalysts=list(recommendation.get("catalysts", [])),
            risks=list(recommendation.get("risks", []))
            + (["缺少足够的可比成交，解释置信度有限"] if missing else []),
            disclaimer="这是规则模型降级解释，不是 AI 生成内容，也不构成投资建议。",
        )


class AIAdvisoryService:
    def __init__(self, settings: Settings, provider: NarrativeProvider | None = None) -> None:
        if provider is not None:
            self.provider = provider
        elif settings.openai_api_key:
            self.provider = OpenAINarrativeProvider(settings.openai_api_key, settings.openai_model)
        else:
            self.provider = RuleNarrativeProvider()

    async def generate_and_save(
        self, connection: AsyncConnection, analysis_id: UUID
    ) -> AINarrativeResult:
        result = await connection.execute(
            """
            SELECT ar.id, ar.input_payload, ar.evidence_snapshot,
                   cs.total_score, cs.factor_scores, cs.factor_inputs,
                   r.rating, r.currency, r.fair_value_low, r.fair_value_high,
                   r.target_sell_low, r.target_sell_high,
                   r.holding_period_min_months, r.holding_period_max_months,
                   r.confidence, r.catalysts, r.risks
            FROM analysis_request ar
            JOIN recommendation r ON r.analysis_request_id = ar.id
            JOIN card_score cs ON cs.id = r.card_score_id
            WHERE ar.id = %s AND ar.status = 'completed'
            """,
            (analysis_id,),
        )
        row = await result.fetchone()
        if row is None:
            raise NotFoundError("completed analysis not found")
        input_payload = row["input_payload"]
        evidence = {
            "input": input_payload,
            "evidence_snapshot": row["evidence_snapshot"],
            "score": {
                "total_score": str(row["total_score"]),
                "factor_scores": row["factor_scores"],
                "factor_inputs": row["factor_inputs"],
            },
            "recommendation": {
                "rating": row["rating"],
                "price_assessment": _price_assessment(
                    input_payload["asking_price"], row["fair_value_low"], row["fair_value_high"]
                ),
                "currency": row["currency"],
                "fair_value_low": str(row["fair_value_low"]),
                "fair_value_high": str(row["fair_value_high"]),
                "target_sell_low": str(row["target_sell_low"]),
                "target_sell_high": str(row["target_sell_high"]),
                "holding_period_months": [
                    row["holding_period_min_months"],
                    row["holding_period_max_months"],
                ],
                "confidence": str(row["confidence"]),
                "evidence_count": input_payload.get("evidence_count", 0),
                "catalysts": row["catalysts"],
                "risks": row["risks"],
            },
        }
        narrative = await self.provider.generate(evidence)
        await connection.execute(
            """
            UPDATE recommendation SET thesis = %s, catalysts = %s, risks = %s,
                model_provider = %s, model_name = %s, prompt_version = '2.0.0'
            WHERE analysis_request_id = %s
            """,
            (
                narrative.thesis,
                Jsonb(narrative.catalysts),
                Jsonb(narrative.risks),
                self.provider.name,
                self.provider.model,
                analysis_id,
            ),
        )
        return AINarrativeResult(
            analysis_id=analysis_id,
            provider=self.provider.name,
            model=self.provider.model,
            narrative=narrative,
        )


def _price_assessment(asking_price: str, low: object, high: object) -> str:
    asking = float(asking_price)
    if asking < float(low):
        return "低于合理区间"
    if asking <= float(high):
        return "处于合理区间"
    return "高于合理区间"
