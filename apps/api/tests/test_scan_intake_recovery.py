import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from card_platform.config import Settings
from card_platform.image_service import PAIR_RECOGNITION_PROMPT, QwenVisionProvider
from card_platform.pair_integrity import derive_pair_validation
from card_platform.scanner_queue import ScannerRecognitionWorker
from card_platform.schemas import CardRecognitionResult


def observation(side, player="Ja Morant"):
    return {"role": side, "player_names": [player], "brand_name": "Panini",
            "product_name": "Donruss Optic", "card_number": "15" if side == "back" else None,
            "evidence": [f"{side}: {player}; OPTIC DONRUSS"]}


def test_provider_null_memorabilia_types_keeps_unknown_without_rejecting_card():
    result = CardRecognitionResult.model_validate({
        "confidence": .94, "player_name": "Ja Morant", "is_memorabilia": None,
        "memorabilia_types": None,
    })
    assert result.memorabilia_types == [] and result.is_memorabilia is None
    assert result.player_name == "Ja Morant"


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["consistent", "conflict", "unknown", "error", "incomplete"])
async def test_provider_completes_omitted_faces_without_inventing_identity(monkeypatch, repair):
    # Captured production shape: a confident verdict with prose, no front/back.
    initial = {"confidence": .91, "player_name": "Ja Morant", "card_number": "15",
               "pair_validation": {"status": "consistent", "evidence": ["两面姓名和产品一致"]}}
    calls = []

    async def completion(create, **kwargs):
        calls.append(kwargs)
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        if len(calls) == 1:
            payload = initial
        else:
            assert len(calls) == 2
            assert len(kwargs["messages"][-1]["content"]) == 3  # current bytes, both faces
            if repair == "error":
                raise TimeoutError("provider timeout")
            payload = {"status": repair if repair in {"consistent", "conflict", "unknown"} else "consistent",
                       "front": observation("front"),
                       "back": observation("back", "Kobe Bryant" if repair == "conflict" else "Ja Morant")}
            if repair == "incomplete":
                payload.pop("back")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))

    monkeypatch.setattr("card_platform.image_service.traced_completion", completion)
    provider = QwenVisionProvider("test", "https://example.invalid/v1", "deepseek-flash")
    result, usage = await provider.recognize_images_with_usage(
        [(b"front", "image/jpeg"), (b"back", "image/jpeg")], PAIR_RECOGNITION_PROMPT)
    assert len(calls) == 2
    assert result.player_name == "Ja Morant" and result.card_number == "15"
    decision = derive_pair_validation(result.model_dump(mode="json"), {"front": "a", "back": "b"})
    expected = repair if repair in {"consistent", "conflict"} else "unknown"
    assert decision["status"] == expected
    assert usage == {"prompt_tokens": 100 if repair == "error" else 200,
                     "completion_tokens": 50 if repair == "error" else 100}
    assert result.recognition_route[-1]["tier"] == "pair_observation_repair"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["complete", "explicit_unknown", "conflict", "observed_conflict", "single"])
async def test_provider_does_not_repeat_a_complete_or_conflicting_observation(monkeypatch, kind):
    calls = []

    async def completion(create, **kwargs):
        calls.append(kwargs)
        pair = {"status": "conflict"} if kind == "conflict" else {
            "status": "unknown" if kind == "explicit_unknown" else "consistent",
            "front": observation("front"), "back": observation("back")}
        if kind == "observed_conflict":
            pair["back"]["player_names"] = ["Kobe Bryant"]
            pair["front"].pop("evidence")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
                {"confidence": .9, "pair_validation": pair})))], usage=None)

    monkeypatch.setattr("card_platform.image_service.traced_completion", completion)
    provider = QwenVisionProvider("test", "https://example.invalid/v1", "deepseek-flash")
    images = [(b"front", "image/jpeg")] * (1 if kind == "single" else 2)
    await provider.recognize_images_with_usage(images, PAIR_RECOGNITION_PROMPT)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_router_preserves_pair_supplement_trace():
    from card_platform.schemas import CardRecognitionResult
    from card_platform.vision_routing import TieredVisionRouter

    trace = {"tier": "pair_observation_repair", "status": "succeeded", "latency_ms": 5}

    async def provider(prior):
        return CardRecognitionResult(confidence=".9", recognition_route=[trace]), {}

    result = await TieredVisionRouter(provider, None, None).recognize()
    assert trace in result.recognition_route


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("player_name", "Kobe Bryant"), ("product_name", "Prizm"), ("card_number", "24"),
])
async def test_pair_supplement_cannot_certify_a_conflicting_draft(monkeypatch, field, value):
    initial = {"confidence": .9, "player_name": "Ja Morant", "product_name": "Donruss Optic",
               "card_number": "15", "pair_validation": {"status": "consistent"}}
    faces = {side: observation(side) for side in ("front", "back")}
    for face in faces.values():
        face["player_names" if field == "player_name" else field] = [value] if field == "player_name" else value
    responses = iter([initial, {"status": "consistent", **faces}])

    async def completion(create, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(next(responses))))], usage=None)

    monkeypatch.setattr("card_platform.image_service.traced_completion", completion)
    provider = QwenVisionProvider("test", "https://example.invalid/v1", "deepseek-flash")
    result, _ = await provider.recognize_images_with_usage(
        [(b"front", "image/jpeg"), (b"back", "image/jpeg")], PAIR_RECOGNITION_PROMPT)
    assert getattr(result, field) == initial[field]
    assert derive_pair_validation(result.model_dump(mode="json"), {})["status"] != "consistent"


@pytest.mark.asyncio
async def test_worker_recovers_jobs_that_become_stale_after_startup(monkeypatch):
    worker = ScannerRecognitionWorker(None, Settings(scanner_worker_poll_seconds=.001))
    recovered_at, ticks = [], [0]

    async def recover():
        recovered_at.append(ticks[0])

    async def claim():
        ticks[0] += 61
        if ticks[0] > 180:
            await worker.stop()
        return None

    monkeypatch.setattr("card_platform.scanner_queue.time", SimpleNamespace(monotonic=lambda: ticks[0]))
    worker._recover_stale, worker._claim = recover, claim
    await asyncio.wait_for(worker.run(), 1)
    assert len(recovered_at) >= 3, recovered_at


@pytest.mark.asyncio
async def test_stale_recovery_never_fails_a_fresh_final_attempt_or_an_imported_job():
    queries = []

    class DB:
        @asynccontextmanager
        async def connection(self):
            yield self

        async def execute(self, sql, params):
            queries.append(sql)

    await ScannerRecognitionWorker(DB(), Settings())._recover_stale()
    assert len(queries) == 2
    for sql in queries:
        assert "updated_at < now() - interval '15 minutes'" in sql
        assert "collection_item_id IS NULL" in sql
