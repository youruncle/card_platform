import json
from decimal import Decimal
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


class EvidenceDecision(BaseModel):
    evidence_id: str
    decision: Literal["accept", "reject", "uncertain"]
    confidence: Decimal = Field(ge=0, le=1)
    reason_codes: list[Literal[
        "exact_identity_supported", "insufficient_title_detail", "different_player",
        "different_season", "different_product", "different_card_number",
        "different_parallel", "different_serial_total", "grade_mismatch",
        "multi_card_lot", "digital_card", "reprint_or_custom", "not_completed_sale",
    ]] = Field(default_factory=list)


class EvidenceReview(BaseModel):
    decisions: list[EvidenceDecision] = Field(default_factory=list)


HARD_AI_REJECTIONS = {
    # Identity fields are enforced by the deterministic classifier, which knows
    # catalog and seller aliases.  A language model must not veto an already
    # validated product merely because a seller omitted a catalog suffix.
    "multi_card_lot", "digital_card", "reprint_or_custom", "not_completed_sale",
}


class DeepSeekEvidenceReviewer:
    """Conservative title reviewer; deterministic identity rules remain authoritative."""

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 30) -> None:
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=1
        )

    async def review(self, identity: dict, evidence: list[dict]) -> EvidenceReview:
        schema = json.dumps(EvidenceReview.model_json_schema(), ensure_ascii=False)
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是球星卡成交证据安全复核器，只检查输入身份与成交标题的明确冲突。"
                        "不得凭记忆补全 checklist，不得因为标题省略字段就判冲突；字段不足应"
                        "判 uncertain。只有标题明确显示不同球员、产品、卡号、限编、评级，或"
                        "明确是 lot、数字卡、复刻/自制、非已完成成交时才能 reject。模型只能"
                        "增加拒绝意见，绝不能越过后端 T0、证据等级和去重门禁。"
                        f"只返回符合 JSON Schema 的 JSON：{schema}"
                    ),
                },
                {"role": "user", "content": json.dumps({
                    "confirmed_identity": identity,
                    "evidence": evidence,
                }, ensure_ascii=False, default=str)},
            ],
            response_format={"type": "json_object"},
            max_tokens=1200,
            extra_body={"thinking": {"type": "disabled"}},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("DeepSeek evidence review returned empty content")
        return EvidenceReview.model_validate_json(content)


def enforce_conservative_rejections(review: EvidenceReview) -> dict[str, list[str]]:
    rejected: dict[str, list[str]] = {}
    for decision in review.decisions:
        reasons = sorted(HARD_AI_REJECTIONS.intersection(decision.reason_codes))
        if decision.decision == "reject" and decision.confidence >= Decimal("0.90") and reasons:
            rejected[decision.evidence_id] = [f"deepseek_{reason}" for reason in reasons]
    return rejected
