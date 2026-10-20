import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from openai import AsyncOpenAI

from card_platform.config import Settings
from card_platform.deepseek_web import DeepSeekWebResearch


def research_transport(model_result, *, source="https://www.tcdb.com/card/138", empty=False):
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [] if empty else [{
                "url": source, "title": "Observed source", "content": "Observed card 138"}]})
        if request.url.path.endswith("/fetch"):
            return httpx.Response(200, json={"title": "Observed source", "content": "Observed card 138"})
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(model_result)}}]})
    return httpx.MockTransport(handler), requests


@pytest.mark.asyncio
async def test_catalog_captures_queries_and_rejects_model_invented_sources(monkeypatch):
    from card_platform.card_search_service import CardSearchService
    from card_platform.schemas import CardRecognitionResult

    transport, requests = research_transport({"candidates": [{
        "title": "Invented source", "source_urls": ["https://www.tcdb.com/card/999"],
        "matching_fields": ["player_name", "card_number"], "match_confidence": "0.99"}],
        "resolved_fields": {"parallel_name": "Gold"}, "field_evidence": {"parallel_name": ["unsupported"]}})
    async with httpx.AsyncClient(transport=transport) as http:
        service = CardSearchService(Settings(_env_file=None, vision_api_key="key"))
        await service.client.close()
        service.client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http)
        service.research = DeepSeekWebResearch("key", http_client=http)
        # Existing downstream verification is held permissive to isolate the
        # new observed-source gate; no real website is contacted in this test.
        async def verified(candidate, pages):
            return candidate.source_urls
        monkeypatch.setattr(service, "_verified_source_urls", verified)
        monkeypatch.setattr(service, "_candidate_image", AsyncMock(return_value=None))
        result = await service._enrich_catalog(CardRecognitionResult(
            player_name="Kobe Bryant", card_number="138", confidence="0.9"))
    assert requests[0].url.path == "/v1/tools/search"
    assert result.web_candidates[0].source_urls == []
    assert result.parallel_name is None
    assert result.web_research["pages"][0]["url"] == "https://www.tcdb.com/card/138"
    assert result.search_queries == result.web_research["queries"]
    body = json.loads(requests[-1].content)
    assert body["thinking"] == {"type": "disabled"}
    assert "enable_search" not in body


@pytest.mark.asyncio
async def test_catalog_empty_search_preserves_unknown_fields_and_skips_model():
    from card_platform.card_search_service import CardSearchService
    from card_platform.schemas import CardRecognitionResult

    transport, requests = research_transport({}, empty=True)
    async with httpx.AsyncClient(transport=transport) as http:
        service = CardSearchService(Settings(_env_file=None, vision_api_key="key"))
        await service.client.close()
        service.client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http)
        service.research = DeepSeekWebResearch("key", http_client=http)
        result = await service._enrich_catalog(CardRecognitionResult(player_name="Kobe Bryant", confidence="0.9"))
    assert all(r.url.path == "/v1/tools/search" for r in requests)
    assert result.product_name is None
    assert result.web_research["pages"] == []
    assert any("来源" in caveat for caveat in result.caveats)


@pytest.mark.asyncio
async def test_cardhobby_discovery_drops_unobserved_sale_and_keeps_actual_queries():
    from card_platform.ingestion.cardhobby import CardHobbyProvider
    from test_cardhobby_provider import _raw_identity, _sale

    transport, requests = research_transport({"sales": [_sale().model_dump(mode="json")],
        "search_queries": ["model never executed this"]}, source="https://cardhobby.com.cn/market/item/123")
    async with httpx.AsyncClient(transport=transport) as http:
        client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http)
        provider = CardHobbyProvider(client, "deepseek-flash")
        provider.research = DeepSeekWebResearch("key", http_client=http)
        result = await provider._search_model(_raw_identity())
    assert requests[0].url.path == "/v1/tools/search"
    assert result.sales == []
    assert "model never executed this" not in result.search_queries
    assert result.web_research["queries"] == result.search_queries
    assert json.loads(requests[-1].content)["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_market_discards_unsupported_price_and_keeps_capture():
    from card_platform.valuation_worker import AutomatedValuationWorker

    transport, requests = research_transport({"search_queries": ["made up"],
        "fallback_valuation": {"low_value": 10, "fair_value": 20, "high_value": 30,
            "confidence": 0.4, "methodology": "unsupported", "references": [{
                "title": "Invented sale", "source_url": "https://www.ebay.com/itm/999",
                "observed_price_base": 20, "evidence_type": "public_completed_sale",
                "match_confidence": "0.9", "adjustment": "none",
                "player_name": "Kobe Bryant", "condition_type": "raw"}]}},
        source="https://www.ebay.com/itm/123")
    async with httpx.AsyncClient(transport=transport) as http:
        worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
        worker.settings = Settings(_env_file=None, valuation_cardhobby_only=False,
                                   valuation_verify_sources=False)
        worker.client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http)
        worker.research = DeepSeekWebResearch("key", http_client=http)
        worker.cardhobby = None
        worker.search_index = SimpleNamespace(enabled=False)
        result = await worker._search_external_legacy({"player_name": "Kobe Bryant",
            "product_name": "Topps", "base_currency": "CNY"}, include_cardhobby=False)
    assert requests[0].url.path == "/v1/tools/search"
    assert result.fallback_valuation is None
    assert "made up" not in result.search_queries
    assert result.web_research["pages"][0]["url"] == "https://www.ebay.com/itm/123"


@pytest.mark.asyncio
async def test_order_title_parser_explicitly_disables_deepseek_default_thinking():
    from card_platform.cardhobby_import import CardHobbyOrderParser
    from test_cardhobby_import import capture

    transport, requests = research_transport({})
    async with httpx.AsyncClient(transport=transport) as http:
        parser = CardHobbyOrderParser(Settings(_env_file=None, vision_api_key="key"))
        await parser.client.close()
        parser.client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http)
        assert await parser._parse_with_model(uuid4(), capture()) is None
    assert json.loads(requests[0].content)["thinking"] == {"type": "disabled"}
