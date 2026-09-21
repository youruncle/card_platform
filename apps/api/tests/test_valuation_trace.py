"""Trace must never invent billing or lose failed calls."""

import importlib
from decimal import Decimal
from types import SimpleNamespace
import pytest


def trace_module():
    assert importlib.util.find_spec("card_platform.valuation_trace"), "valuation trace missing"
    return importlib.import_module("card_platform.valuation_trace")


@pytest.mark.asyncio
async def test_usage_and_cached_tokens_are_priced_with_version():
    m = trace_module()

    async def call(**kwargs):
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                prompt_tokens_details=SimpleNamespace(cached_tokens=40),
            )
        )

    events = []
    rate = m.PriceRate("CNY", "fixture-v1", Decimal("2"), Decimal("1"), Decimal("4"))
    with m.trace_run(events.append, prices={"test-model": rate}, card_id="test"):
        await m.traced_completion(call, stage="same_card", model="test-model")
    e = events[0]
    assert e["input_tokens"] == 100 and e["cached_tokens"] == 40
    assert Decimal(e["cost_amount"]) == Decimal(".00024")
    assert e["price_version"] == "fixture-v1" and e["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_failure_and_missing_usage_are_not_free_calls():
    m = trace_module()
    events = []

    async def missing(**kwargs):
        return SimpleNamespace(usage=None)

    async def failed(**kwargs):
        raise TimeoutError("private payload must not be logged")

    with m.trace_run(events.append):
        await m.traced_completion(missing, stage="same_card", model="m")
        with pytest.raises(TimeoutError):
            await m.traced_completion(failed, stage="same_card", model="m")
    assert [e["status"] for e in events] == ["succeeded", "failed"]
    assert all(e["cost_amount"] is None and e["usage_missing_reason"] for e in events)
    assert "private payload" not in str(events)


@pytest.mark.asyncio
async def test_no_trace_does_not_change_completion_or_require_prices():
    m = trace_module()
    response = object()

    async def call(**kwargs):
        return response

    assert await m.traced_completion(call, stage="same_card", model="m") is response


@pytest.mark.asyncio
async def test_model_budget_stops_before_an_additional_call():
    m = trace_module()
    events = []
    calls = []

    async def call(**kwargs):
        calls.append(1)
        return SimpleNamespace(usage=None)

    with m.trace_run(events.append, max_model_calls=1):
        await m.traced_completion(call, stage="same_card", model="m")
        with pytest.raises(m.AuditBudgetExceeded):
            await m.traced_completion(call, stage="same_card", model="m")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_cache_usage_preserves_known_input_and_output_counts():
    m = trace_module()
    events = []

    async def call(**kwargs):
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                prompt_tokens_details=SimpleNamespace(cached_tokens=None),
            )
        )

    with m.trace_run(events.append):
        await m.traced_completion(call, stage="same_card", model="fixture")
    assert events[0]["input_tokens"] == 100
    assert events[0]["output_tokens"] == 20
    assert events[0]["cached_tokens"] is None
    assert events[0]["cost_amount"] is None
    assert events[0]["usage_missing_reason"] == "provider_cache_usage_unavailable"
