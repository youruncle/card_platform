"""Exercise the real collector and SDK at their external HTTP boundary."""
import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from card_platform.config import Settings


def test_vision_credentials_do_not_enable_a_search_client():
    settings = Settings(_env_file=None, vision_api_key="visual-key")
    assert settings.resolved_search_api_key is None


@pytest.mark.parametrize("base", [None, "https://api.deepseek.com", "https://api.deepseek.com.attacker.test"])
def test_unpaired_or_wrong_provider_key_cannot_enable_search(base):
    settings = Settings(_env_file=None, vision_api_key="visual-key",
                        search_api_key="old-provider-key", search_base_url=base)
    assert settings.resolved_search_api_key is None


@pytest.mark.asyncio
async def test_search_and_fetch_are_real_requests_and_keep_bounded_evidence():
    from card_platform.deepseek_web import DeepSeekWebResearch

    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/tools/search":
            return httpx.Response(200, json={"results": [
                {"url": "https://www.tcdb.com/card/138#image", "title": "Kobe 138",
                 "content": "Search observed Kobe 138", "summary": "Checklist", "date": None},
                {"url": "https://www.tcdb.com/card/138", "title": "duplicate"},
                {"url": "https://www.tcdb.com.attacker.test/fake", "title": "untrusted"},
                {"url": "https://user:secret@www.tcdb.com/private", "title": "credentials"},
                {"url": "http://127.0.0.1/private", "title": "internal"},
            ]})
        assert request.url.path == "/v1/tools/fetch"
        assert json.loads(request.content) == {"url": "https://www.tcdb.com/card/138"}
        return httpx.Response(200, json={"title": "Kobe checklist", "content": "Kobe 138 " * 1000})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        research = DeepSeekWebResearch("deepseek-key", http_client=http)
        capture = await research.collect(["Kobe 138", "Kobe 138"], allowed_sites=["tcdb.com"])
    assert capture.queries == ["Kobe 138"]
    assert len(capture.pages) == 1
    page = capture.pages[0]
    assert page["url"] == "https://www.tcdb.com/card/138"
    assert page["search_content"] == "Search observed Kobe 138"
    assert page["fetch_status"] == "ok"
    assert len(page["content"]) == 6000
    assert page["content_truncated"] is True
    assert len(page["content_sha256"]) == 64
    assert capture.observed("https://www.tcdb.com/card/138#front")
    assert not capture.observed("https://www.tcdb.com/card/999")
    assert capture.as_dict()["provider"] == "deepseek"
    assert capture.as_dict()["captured_at"].endswith("+00:00")
    assert len(requests) == 2
    assert all(r.headers["Authorization"] == "Bearer deepseek-key" for r in requests)
    assert all(r.url.host == "api.deepseek.com" for r in requests)


@pytest.mark.asyncio
async def test_queries_and_fetches_have_fixed_budgets():
    from card_platform.deepseek_web import DeepSeekWebResearch

    searches, fetches = [], []
    def handler(request):
        payload = json.loads(request.content)
        if request.url.path.endswith("/search"):
            searches.append(payload)
            return httpx.Response(200, json={"results": [
                {"url": f"https://www.tcdb.com/card/{len(searches)}-{i}", "content": "source"}
                for i in range(5)
            ]})
        fetches.append(payload)
        return httpx.Response(200, json={"title": "page", "content": "source"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        capture = await DeepSeekWebResearch("key", http_client=http).collect(
            ["A" * 180, "B", "C", "D", "E"], allowed_sites=["tcdb.com"])
    assert len(searches) == 4
    assert all(1 <= len(q["query"]) <= 128 for q in searches)
    assert all(q["need_content"] is True for q in searches)
    assert len(capture.pages) == 8
    assert {page["query"] for page in capture.pages} == {"A" * 128, "B", "C", "D"}
    assert len(fetches) == 3


@pytest.mark.asyncio
async def test_failed_fetch_keeps_search_body_without_claiming_page_verified():
    from card_platform.deepseek_web import DeepSeekWebResearch

    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"url": "https://www.ebay.com/itm/123",
                "title": "Active listing", "content": "Currently for sale $20"}]})
        return httpx.Response(404, json={"error": {"message": "unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        capture = await DeepSeekWebResearch("key", http_client=http).collect(["card"])
    assert capture.pages[0]["search_content"] == "Currently for sale $20"
    assert capture.pages[0]["fetch_status"] == "http_404"
    assert capture.pages[0]["content"] == ""
    assert any("http_404" in error for error in capture.errors)


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [(401, 1), (402, 1), (403, 1), (429, 2), (504, 2)])
async def test_retries_only_transient_failures_and_never_exposes_error_body(status, expected):
    from card_platform.deepseek_web import DeepSeekWebResearch

    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "secret-should-not-leak"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        capture = await DeepSeekWebResearch("key", http_client=http).collect(["card"])
    assert len(requests) == expected
    assert capture.pages == []
    assert any(f"http_{status}" in error for error in capture.errors)
    assert "secret-should-not-leak" not in json.dumps(capture.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"results": []}, {"results": "malformed"}])
async def test_empty_or_malformed_search_never_asks_model_to_invent_evidence(payload):
    from card_platform.deepseek_web import DeepSeekWebResearch

    paths = []
    def handler(request):
        paths.append(request.url.path)
        assert request.url.path == "/v1/tools/search"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http)
        response, capture = await DeepSeekWebResearch("key", http_client=http).complete(
            client, queries=["card"], model="deepseek-flash", messages=[{"role": "user", "content": "card"}])
    assert response is None
    assert capture.pages == []
    assert paths == ["/v1/tools/search"]


@pytest.mark.asyncio
async def test_analysis_receives_actual_capture_and_explicit_non_thinking_request():
    from card_platform.deepseek_web import DeepSeekWebResearch

    analysis = []
    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"url": "https://www.tcdb.com/card/138",
                "title": "Kobe Bryant", "content": "Checklist card 138"}]})
        if request.url.path.endswith("/fetch"):
            return httpx.Response(200, json={"title": "Kobe Bryant", "content": "Kobe Bryant #138"})
        body = json.loads(request.content)
        analysis.append(body)
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "{}"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http)
        response, capture = await DeepSeekWebResearch("key", http_client=http).complete(
            client, queries=["Kobe 138"], allowed_sites=["tcdb.com"], model="deepseek-flash",
            messages=[{"role": "user", "content": "Identify card"}], response_format={"type": "json_object"})
    assert response.choices[0].message.content == "{}"
    assert analysis[0]["thinking"] == {"type": "disabled"}
    assert "enable_search" not in analysis[0]
    context = json.loads(analysis[0]["messages"][-1]["content"])["web_research"]
    assert context["pages"][0]["content"] == "Kobe Bryant #138"
    assert context["queries"] == capture.queries == ["Kobe 138"]


@pytest.mark.asyncio
async def test_model_failure_preserves_collected_evidence_and_private_archive(tmp_path):
    from card_platform.deepseek_web import DeepSeekWebResearch

    def handler(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"url": "https://www.tcdb.com/card/138",
                "content": "Observed card 138"}]})
        if request.url.path.endswith("/fetch"):
            return httpx.Response(200, json={"content": "Observed checklist"})
        return httpx.Response(500, json={"error": {"message": "secret-error-text"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http,
                             max_retries=0)
        response, capture = await DeepSeekWebResearch("key", http_client=http, archive_dir=tmp_path).complete(
            client, queries=["card"], model="deepseek-flash", messages=[{"role": "user", "content": "card"}])
    assert response is None
    assert capture.pages[0]["content"] == "Observed checklist"
    assert "analysis: http_500" in capture.errors
    archived = list(tmp_path.glob("*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text()) == capture.as_dict()
    assert archived[0].stat().st_mode & 0o777 == 0o600
    assert "secret-error-text" not in archived[0].read_text()


@pytest.mark.asyncio
async def test_url_only_search_and_failed_fetch_skip_analysis_but_keep_capture():
    from card_platform.deepseek_web import DeepSeekWebResearch

    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, json={"results": [{"url": "https://www.tcdb.com/card/138"}]})
        if request.url.path.endswith("/fetch"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1,
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": "{}"}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AsyncOpenAI(api_key="key", base_url="https://api.deepseek.com", http_client=http)
        response, capture = await DeepSeekWebResearch("key", http_client=http).complete(
            client, queries=["card"], model="deepseek-flash", messages=[{"role": "user", "content": "card"}])
    assert response is None
    assert paths == ["/v1/tools/search", "/v1/tools/fetch"]
    assert capture.pages[0]["fetch_status"] == "http_404"


@pytest.mark.asyncio
async def test_cancellation_archives_completed_searches_while_other_query_is_pending(tmp_path):
    from card_platform.deepseek_web import DeepSeekWebResearch

    pending = asyncio.Event()
    async def handler(request):
        if json.loads(request.content)["query"] == "done":
            return httpx.Response(200, json={"results": [{"url": "https://www.tcdb.com/card/138",
                "content": "Already captured"}]})
        pending.set()
        await asyncio.Event().wait()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        task = asyncio.create_task(DeepSeekWebResearch("key", http_client=http,
            archive_dir=tmp_path).collect(["done", "pending"]))
        await pending.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    capture = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert capture["pages"][0]["search_content"] == "Already captured"
