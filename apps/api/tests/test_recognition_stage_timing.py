"""Deterministic elapsed-time contracts; external OCR/model/HTTP stay controlled."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from card_platform.config import Settings
from card_platform.schemas import CardRecognitionResult


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def advance(self, milliseconds):
        self.now += milliseconds / 1000


def identity(**updates):
    return CardRecognitionResult(
        player_name="Kobe Bryant", brand_name="Topps", product_name="Chrome",
        season_start=1996, season_end=1997, card_number="138", confidence="0.97",
        recognition_route=[{"tier": "flash", "status": "succeeded", "latency_ms": 50}],
    ).model_copy(update=updates)


def route(result, tier):
    rows = [row for row in result.recognition_route if row["tier"] == tier]
    assert rows, f"missing {tier} elapsed-time trace"
    return rows[0]


@pytest.mark.asyncio
async def test_pair_timing_records_empty_ocr_and_wall_time_of_overlapping_model_serial(monkeypatch):
    import card_platform.image_service as module
    clock = Clock()
    monkeypatch.setattr(module, "time", clock)
    model_started, serial_started, model_finished = (asyncio.Event() for _ in range(3))

    async def ocr(images):
        clock.advance(10)
        await asyncio.sleep(0)
        return []

    class Provider:
        async def recognize_images(self, images, prompt):
            model_started.set()
            await serial_started.wait()
            clock.advance(50)
            model_finished.set()
            return identity()

    async def serial(images):
        serial_started.set()
        await model_started.wait()
        await model_finished.wait()
        clock.advance(30)
        return {"status": "failed", "reason": "local_serial_ocr_timeout", "observations": []}

    async def enrich(result):
        clock.advance(20)
        await asyncio.sleep(0)
        return result

    monkeypatch.setattr(module.local_ocr, "recognize_images", ocr)
    monkeypatch.setattr(module.serial_ocr, "recognize_serial_images", serial)
    service = module.CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    service.search.enrich = enrich
    result = await service.recognize_pair_contents(b"front", "image/jpeg", b"back", "image/jpeg")
    timing = route(result, "paired_pipeline")
    assert timing["latency_ms"] == 110
    assert timing["stages"]["local_ocr"] == {
        "latency_ms": 10, "status": "not_observed", "line_count": 0, "anchor_count": 0,
    }
    parallel = timing["stages"]["identity_and_serial"]
    assert parallel["latency_ms"] == 80
    assert parallel["status"] == "degraded"
    assert parallel["concurrent"] is True
    assert parallel["participating_stages"] == ["identity_model", "local_serial_ocr"]
    assert timing["stages"]["catalog_enrich"]["latency_ms"] == 20
    assert result.card_number == "138"
    assert route(result, "flash")["latency_ms"] == 50
    assert result.recognition_route[-1]["tier"] == "local_serial_ocr"
    assert result.recognition_route[-1]["status"] == "failed"
    assert timing["latency_ms"] < sum(row["latency_ms"] for row in result.recognition_route)


@pytest.mark.asyncio
async def test_pair_timing_marks_serial_skipped_when_ordinary_ocr_has_complete_stamp(monkeypatch):
    import card_platform.image_service as module
    clock = Clock()
    monkeypatch.setattr(module, "time", clock)

    async def ocr(images):
        clock.advance(12)
        return ["No. 138", "1/50"]

    class Provider:
        async def recognize_images(self, images, prompt):
            clock.advance(23)
            await asyncio.sleep(0)
            return identity()

    async def forbidden_serial(images):
        raise AssertionError("complete ordinary OCR anchors should skip serial recovery")

    monkeypatch.setattr(module.local_ocr, "recognize_images", ocr)
    monkeypatch.setattr(module.serial_ocr, "recognize_serial_images", forbidden_serial)
    service = module.CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    result = await service.recognize_pair_contents(b"front", "image/jpeg", b"back", "image/jpeg")
    timing = route(result, "paired_pipeline")
    assert timing["latency_ms"] == 35
    assert timing["stages"]["local_ocr"]["status"] == "succeeded"
    assert timing["stages"]["identity_and_serial"]["participating_stages"] == ["identity_model"]
    assert timing["stages"]["identity_and_serial"]["concurrent"] is False
    assert result.serial_number == 1 and result.serial_total == 50
    assert route(result, "local_ocr")["anchor_evidence"]["serial_evidence"] == "1/50"


def catalog_service(module, create):
    service = module.CardSearchService(Settings(card_search_enabled=False))
    service.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return service


def candidate_content():
    return json.dumps({"candidates": [{
        "title": "Kobe Bryant Topps Chrome 138",
        "identity": {"player_name": "Kobe Bryant", "card_number": "138"},
        "source_urls": ["https://www.tcdb.com/card"],
        "matching_fields": ["player_name", "season_start", "season_end", "brand_name", "product_name", "card_number"],
        "match_confidence": "0.99",
    }]})


@pytest.mark.asyncio
async def test_catalog_timing_measures_llm_pages_images_and_overlapping_reference_query(monkeypatch):
    import card_platform.card_search_service as module
    clock = Clock()
    monkeypatch.setattr(module, "time", clock, raising=False)
    reference_started, image_finished = asyncio.Event(), asyncio.Event()

    async def create(**kwargs):
        await reference_started.wait()
        clock.advance(30)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=candidate_content()))])

    async def reference(self, card):
        reference_started.set()
        await image_finished.wait()
        clock.advance(10)
        return SimpleNamespace(front_url="https://example.test/front.jpg", back_url=None,
                               source_url="https://example.test/item", match_confidence="0.98")

    async def handler(request):
        await asyncio.sleep(0)
        if request.url.path.endswith(".jpg"):
            clock.advance(20)
            image_finished.set()
            return httpx.Response(200, content=b"x" * 600, headers={"content-type": "image/jpeg"})
        clock.advance(40)
        return httpx.Response(200, text='<meta property="og:image" content="https://www.tcdb.com/card.jpg"> Kobe Bryant Topps Chrome 138', headers={"content-type": "text/html"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(module.CardHobbyProvider, "reference_images", reference)
    result = await catalog_service(module, create).enrich(identity())
    assert route(result, "catalog_llm")["latency_ms"] == 30
    assert route(result, "catalog_sources")["latency_ms"] == 40
    # Reference work finishes while the image phase is still unwinding.
    assert route(result, "catalog_images")["latency_ms"] == 30
    assert route(result, "catalog_reference")["latency_ms"] == 100
    assert route(result, "catalog_sources")["concurrent"] is False
    assert route(result, "catalog_images")["concurrent"] is False
    assert all(route(result, tier)["status"] == "succeeded" for tier in (
        "catalog_llm", "catalog_sources", "catalog_images", "catalog_reference"))
    assert route(result, "catalog_llm")["concurrency_group"] == "catalog_and_reference"
    assert route(result, "catalog_reference")["concurrency_group"] == "catalog_and_reference"
    assert result.web_candidates[0].image_url == "https://www.tcdb.com/card.jpg"
    assert result.reference_image_front_url == "https://example.test/front.jpg"
    assert result.recognition_route[-1]["tier"] == "flash"


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status", [(TimeoutError("timeout"), "timeout"), (RuntimeError("unavailable"), "failed")])
async def test_catalog_llm_failure_timing_preserves_visual_and_skips_dependent_stages(monkeypatch, error, status):
    import card_platform.card_search_service as module
    clock = Clock()
    monkeypatch.setattr(module, "time", clock, raising=False)

    async def create(**kwargs):
        clock.advance(25)
        await asyncio.sleep(0)
        raise error

    async def reference(self, card):
        return None

    monkeypatch.setattr(module.CardHobbyProvider, "reference_images", reference)
    result = await catalog_service(module, create).enrich(identity())
    assert route(result, "catalog_llm")["status"] == status
    assert route(result, "catalog_llm")["latency_ms"] == 25
    assert route(result, "catalog_sources")["status"] == "skipped"
    assert route(result, "catalog_images")["status"] == "skipped"
    assert result.card_number == "138"
    assert any("联网目录核验失败" in caveat for caveat in result.caveats)
    assert result.reference_image_front_url is None


@pytest.mark.asyncio
async def test_source_and_reference_timeout_timing_preserves_optional_failure_semantics(monkeypatch):
    import card_platform.card_search_service as module
    clock = Clock()
    monkeypatch.setattr(module, "time", clock, raising=False)

    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=candidate_content()))])

    async def reference(self, card):
        await asyncio.sleep(0)
        raise TimeoutError("private reference context must not enter trace")

    async def handler(request):
        clock.advance(17)
        await asyncio.sleep(0)
        raise httpx.ReadTimeout("private URL context must not enter trace", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(module.CardHobbyProvider, "reference_images", reference)
    result = await catalog_service(module, create).enrich(identity())
    assert route(result, "catalog_sources")["status"] == "timeout"
    assert route(result, "catalog_sources")["latency_ms"] == 17
    assert route(result, "catalog_reference")["status"] == "timeout"
    assert result.web_candidates[0].source_urls == []
    assert result.web_candidates[0].image_url is None
    assert result.reference_image_front_url is None
    assert not result.caveats
    assert "private" not in json.dumps(result.recognition_route)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["timeout", "failed"])
async def test_candidate_image_failure_is_timed_without_adding_unverified_media(monkeypatch, status):
    import card_platform.card_search_service as module
    clock = Clock()
    monkeypatch.setattr(module, "time", clock, raising=False)

    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=candidate_content()))])

    async def reference(self, card):
        return None

    async def handler(request):
        await asyncio.sleep(0)
        if request.url.path.endswith(".jpg"):
            clock.advance(19)
            if status == "timeout":
                raise httpx.ReadTimeout("image timeout", request=request)
            return httpx.Response(503)
        return httpx.Response(200, text='<meta property="og:image" content="https://www.tcdb.com/card.jpg"> Kobe Bryant Topps Chrome 138', headers={"content-type": "text/html"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(module.CardHobbyProvider, "reference_images", reference)
    result = await catalog_service(module, create).enrich(identity())
    assert route(result, "catalog_images")["status"] == status
    assert route(result, "catalog_images")["latency_ms"] == 19
    assert route(result, "catalog_sources")["status"] == "succeeded"
    assert result.web_candidates[0].source_urls == ["https://www.tcdb.com/card"]
    assert result.web_candidates[0].image_url is None
    assert not result.caveats
