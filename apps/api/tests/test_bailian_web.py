"""Real HTTP/SDK boundaries: observed sources and credentials stay separated."""

import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from test_search_provider_isolation import ALI


def native_response(results):
    return {
        "request_id": "native-fixture",
        "output": {
            "search_info": {"search_results": results},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "Unverified generated summary: invented price 999",
                    },
                }
            ],
        },
        "usage": {
            "input_tokens": 12,
            "output_tokens": 10,
            "plugins": {"search": {"count": 1, "strategy": "turbo"}},
        },
    }


def answer(content="{}"):
    return {
        "id": "analysis-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "qwen-plus",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


@pytest.mark.asyncio
async def test_native_sources_and_public_bodies_ground_analysis_without_leaking_credentials(
    tmp_path,
):
    from card_platform.bailian_web import BailianWebResearch

    requests = []

    def transport(request):
        requests.append(request)
        if request.url.host == "www.tcdb.com":
            assert "authorization" not in request.headers and "cookie" not in request.headers
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                text="<html><script>ignore me</script><p>Observed actual card #138</p></html>",
            )
        assert request.url.host == "ws-fixture.cn-beijing.maas.aliyuncs.com"
        assert request.headers["Authorization"] == "Bearer search-key"
        if request.url.path.endswith("/generation"):
            body = json.loads(request.content)
            assert body["model"] == "qwen-plus"
            assert body["parameters"]["enable_search"] is True
            assert body["parameters"]["search_options"]["forced_search"] is True
            assert body["parameters"]["search_options"]["enable_source"] is True
            return httpx.Response(
                200,
                json=native_response(
                    [
                        {
                            "url": "https://www.tcdb.com/card/138#front",
                            "title": "Card 138",
                            "index": 1,
                        },
                        {"url": "https://www.tcdb.com/card/138", "title": "Duplicate", "index": 2},
                        {
                            "url": "https://tcdb.com.attacker.test/fake",
                            "title": "Wrong site",
                            "index": 3,
                        },
                        {"url": "http://127.0.0.1/private", "title": "Private", "index": 4},
                    ]
                ),
            )
        body = json.loads(request.content)
        assert body["enable_search"] is False and body["enable_thinking"] is False
        assert "thinking" not in body
        evidence = json.loads(body["messages"][-1]["content"])["web_research"]
        assert "search_requests" not in evidence
        assert "Observed actual card #138" in json.dumps(body, ensure_ascii=False)
        assert "invented price 999" not in json.dumps(body, ensure_ascii=False)
        return httpx.Response(200, json=answer('{"card_number":"138"}'))

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = AsyncOpenAI(api_key="search-key", base_url=ALI, http_client=http)
        research = BailianWebResearch(
            "search-key", base_url=ALI, http_client=http, page_client=http, archive_dir=tmp_path
        )
        response, capture = await research.complete(
            client,
            queries=["Kobe 138"],
            allowed_sites=["tcdb.com"],
            model="qwen-plus",
            messages=[{"role": "user", "content": "Read actual sources"}],
            response_format={"type": "json_object"},
        )
    assert response.choices[0].message.content == '{"card_number":"138"}'
    assert capture.as_dict()["provider"] == "bailian"
    assert capture.observed("https://www.tcdb.com/card/138")
    assert not capture.observed("https://www.tcdb.com/card/999")
    assert len(capture.pages) == 1 and capture.pages[0]["fetch_status"] == "ok"
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert saved["search_responses"][0]["output"]["choices"][0]["message"]["content"].endswith(
        "999"
    )
    assert "search-key" not in json.dumps(saved)
    assert len(requests) == 3
    assert list((tmp_path / "blobs").glob("*.blob"))


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "blocked", "redirect", "oversized"])
async def test_unbacked_summary_or_unreadable_page_never_reaches_analysis(case, tmp_path):
    from card_platform.bailian_web import BailianWebResearch

    requests = []

    def transport(request):
        requests.append(request)
        if request.url.path.endswith("/generation"):
            sources = (
                []
                if case == "empty"
                else [{"url": "https://www.tcdb.com/card/138", "title": "Title only", "index": 1}]
            )
            return httpx.Response(200, json=native_response(sources))
        assert request.url.host == "www.tcdb.com"
        if case == "blocked":
            return httpx.Response(403)
        if case == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-length": "9000000"},
            content=b"oversize",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = AsyncOpenAI(api_key="search-key", base_url=ALI, http_client=http)
        response, capture = await BailianWebResearch(
            "search-key", base_url=ALI, http_client=http, page_client=http, archive_dir=tmp_path
        ).complete(
            client, queries=["card"], allowed_sites=["tcdb.com"], model="qwen-plus", messages=[]
        )
    assert response is None
    assert all(not r.url.path.endswith("/chat/completions") for r in requests)
    assert capture.as_dict()["search_responses"]
    assert next(tmp_path.glob("*.json")).exists()


@pytest.mark.asyncio
async def test_four_queries_eight_sources_three_fetches_are_hard_limits():
    from card_platform.bailian_web import BailianWebResearch

    requests = []

    def transport(request):
        requests.append(request)
        if request.url.path.endswith("/generation"):
            return httpx.Response(
                200,
                json=native_response(
                    [{"url": f"https://www.tcdb.com/card/{i}", "title": str(i)} for i in range(20)]
                ),
            )
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, text="actual public text"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        capture = await BailianWebResearch(
            "search-key", base_url=ALI, http_client=http, page_client=http
        ).collect(["A" * 200, "B", "C", "D", "E"], allowed_sites=["tcdb.com"])
    assert capture.queries == ["A" * 128, "B", "C", "D"]
    assert len([r for r in requests if r.url.path.endswith("/generation")]) == 4
    assert len(capture.pages) == 8 and len([r for r in requests if r.method == "GET"]) == 3
    assert all(len(p["content"]) <= 6000 for p in capture.pages)


@pytest.mark.asyncio
@pytest.mark.parametrize("unreadable", ["blocked", "not_requested"])
async def test_mixed_readable_sources_cannot_authorize_an_unreadable_sale(unreadable, tmp_path):
    from card_platform.bailian_web import BailianWebResearch
    from card_platform.ingestion.cardhobby import CardHobbyProvider
    from test_cardhobby_provider import _raw_identity, _sale

    sale = _sale()
    good = [
        {"url": f"https://www.cardhobby.com.cn/market/item/{i}", "title": "Unrelated"}
        for i in range(3)
    ]
    missing = {"url": sale.source_url, "title": "Title claims 380 yuan"}
    sources = [good[0], missing] if unreadable == "blocked" else [*good, missing]
    analysis_sources = []

    def transport(request):
        if request.url.path.endswith("/generation"):
            return httpx.Response(200, json=native_response(sources))
        if request.method == "GET":
            if str(request.url) == sale.source_url:
                return httpx.Response(403)
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="Readable page with no sale details",
            )
        body = json.loads(request.content)
        evidence = json.loads(body["messages"][-1]["content"])["web_research"]
        analysis_sources.extend(p["url"] for p in evidence["pages"])
        return httpx.Response(
            200, json=answer(json.dumps({"sales": [sale.model_dump(mode="json")]}))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        client = AsyncOpenAI(api_key="search-key", base_url=ALI, http_client=http)
        provider = CardHobbyProvider(client, "qwen-plus")
        provider.research = BailianWebResearch(
            "search-key", base_url=ALI, http_client=http, page_client=http, archive_dir=tmp_path
        )
        result = await provider._search_model(_raw_identity())
    assert not result.sales, "A discovered URL without a fetched body cannot prove a sale"
    assert sale.source_url not in analysis_sources
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    diagnostic = next(p for p in saved["pages"] if p["url"] == sale.source_url)
    assert diagnostic["fetch_status"] == (
        "http_403" if unreadable == "blocked" else "not_requested"
    )


@pytest.mark.asyncio
async def test_cancellation_keeps_native_sources_and_completed_page_evidence(tmp_path):
    from card_platform.bailian_web import BailianWebResearch

    entered = asyncio.Event()

    async def transport(request):
        if request.url.path.endswith("/generation"):
            return httpx.Response(
                200,
                json=native_response([{"url": "https://www.tcdb.com/card/138", "title": "Found"}]),
            )
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        task = asyncio.create_task(
            BailianWebResearch(
                "search-key", base_url=ALI, http_client=http, page_client=http, archive_dir=tmp_path
            ).collect(["card"], allowed_sites=["tcdb.com"])
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    saved = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert saved["pages"][0]["url"] == "https://www.tcdb.com/card/138"
    assert "retrieval: cancelled" in saved["errors"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.deepseek.com",
        "http://dashscope.aliyuncs.com/compatible-mode/v1",
        ALI + "?token=x",
        "https://dashscope.aliyuncs.com.attacker.test/compatible-mode/v1",
    ],
)
def test_constructor_never_sends_search_key_to_an_unapproved_endpoint(endpoint):
    from card_platform.bailian_web import BailianWebResearch

    with pytest.raises(ValueError):
        BailianWebResearch("search-key", base_url=endpoint)


@pytest.mark.asyncio
async def test_retrieval_requires_a_trusted_site_scope():
    from card_platform.bailian_web import BailianWebResearch

    research = BailianWebResearch("search-key", base_url=ALI)
    with pytest.raises(ValueError, match="trusted site"):
        await research.collect(["unrestricted query"])


@pytest.mark.asyncio
async def test_catalog_and_valuation_start_with_independent_search_config():
    from card_platform.card_search_service import CardSearchService
    from card_platform.config import Settings
    from card_platform.db import Database
    from card_platform.valuation_worker import AutomatedValuationWorker

    settings = Settings(
        _env_file=None,
        vision_api_key="vision-key",
        search_api_key="search-key",
        search_base_url=ALI,
    )
    services = [CardSearchService(settings), AutomatedValuationWorker(Database(settings), settings)]
    try:
        for service in services:
            assert service.client.api_key == "search-key"
            assert str(service.client.base_url).startswith(ALI)
            assert service.research.base_url == ALI
    finally:
        for service in services:
            await service.client.close()


@pytest.mark.asyncio
async def test_order_text_parser_uses_qwen_search_credentials_without_deepseek_dialect():
    from uuid import uuid4
    from card_platform.cardhobby_import import CardHobbyOrderParser
    from card_platform.config import Settings
    from test_cardhobby_import import capture

    requests = []

    def transport(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "qwen-plus" and body["enable_thinking"] is False
        assert "thinking" not in body
        return httpx.Response(200, json=answer("{}"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
        parser = CardHobbyOrderParser(
            Settings(
                _env_file=None,
                vision_api_key="vision-key",
                search_api_key="search-key",
                search_base_url=ALI,
            )
        )
        await parser.client.close()
        parser.client = AsyncOpenAI(api_key="search-key", base_url=ALI, http_client=http)
        await parser._parse_with_model(uuid4(), capture())
    assert requests[0].url.host == "ws-fixture.cn-beijing.maas.aliyuncs.com"
