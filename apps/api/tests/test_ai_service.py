import asyncio

from card_platform.ai_service import RuleNarrativeProvider


def test_rule_fallback_is_explicit_and_preserves_prices() -> None:
    evidence = {
        "score": {"total_score": "82.50"},
        "recommendation": {
            "rating": "buy",
            "price_assessment": "低于合理区间",
            "currency": "CNY",
            "fair_value_low": "270.00",
            "fair_value_high": "320.00",
            "target_sell_low": "310.00",
            "target_sell_high": "368.00",
            "evidence_count": 0,
            "catalysts": ["年龄曲线与成长空间"],
            "risks": ["年轻球员表现波动"],
        },
    }

    result = asyncio.run(RuleNarrativeProvider().generate(evidence))

    assert "270.00–320.00 CNY" in result.price_reasoning
    assert "规则模型降级解释" in result.disclaimer
    assert any("缺少足够的可比成交" in risk for risk in result.risks)
