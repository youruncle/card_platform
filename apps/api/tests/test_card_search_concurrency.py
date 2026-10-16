import asyncio
from types import SimpleNamespace

import httpx
import pytest

from card_platform.card_search_service import CardSearchService
from card_platform.config import Settings
from card_platform.schemas import CardRecognitionResult, CardWebCandidate


@pytest.mark.asyncio
async def test_catalog_and_reference_queries_overlap(monkeypatch):
    catalog_started = asyncio.Event()
    reference_started = asyncio.Event()

    async def create(**kwargs):
        catalog_started.set()
        await asyncio.wait_for(reference_started.wait(), .1)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"candidates":[]}'))])

    async def reference(self, identity):
        reference_started.set()
        await catalog_started.wait()
        return SimpleNamespace(front_url="https://example.test/front.jpg", back_url=None,
                               source_url="https://example.test/item", match_confidence="0.98")

    monkeypatch.setattr("card_platform.card_search_service.CardHobbyProvider.reference_images", reference)
    service = CardSearchService(Settings(card_search_enabled=False))
    service.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = await service.enrich(CardRecognitionResult(player_name="Kobe Bryant", confidence="0.97"))
    assert not any("联网目录核验失败" in c for c in result.caveats)
    assert result.reference_image_front_url == "https://example.test/front.jpg"


@pytest.mark.asyncio
async def test_source_pages_are_fetched_concurrently_and_reused_for_images(monkeypatch):
    import card_platform.card_search_service as module
    started = []
    both_started = asyncio.Event()
    real_client = httpx.AsyncClient

    async def handler(request):
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://www.tcdb.com/a"})
        if request.url.path.endswith(".jpg"):
            return httpx.Response(200, content=b"x" * 600, headers={"content-type": "image/jpeg"})
        started.append(str(request.url))
        if len(set(started)) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), .1)
        return httpx.Response(200, text='<meta property="og:image" content="https://www.tcdb.com/card.jpg"> Kobe Bryant Topps Chrome 138', headers={"content-type": "text/html"})

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs
    ))
    # Literal fixture: both independent sources support the exact same card.
    candidate = CardWebCandidate(title="Kobe Bryant Chrome 138", source_urls=[
        "https://www.tcdb.com/old", "https://www.tcdb.com/b"
    ], matching_fields=["player_name", "card_number"], match_confidence="0.95")
    monkeypatch.setattr(module, "source_page_supports_candidate", lambda text, card: "Kobe Bryant" in text)
    service = CardSearchService(Settings(card_search_enabled=False))
    pages = module._CatalogPages()
    urls = await service._verified_source_urls(candidate, pages)
    image = await service._candidate_image(urls, None, True, pages)
    assert image == "https://www.tcdb.com/card.jpg"
    assert started == ["https://www.tcdb.com/a", "https://www.tcdb.com/b"]
