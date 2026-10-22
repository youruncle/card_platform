"""Real queue/reader integration; only public HTTP and model endpoints are substituted."""

import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
import os
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row
import pytest
import pytest_asyncio

from card_platform.config import Settings
from card_platform.player_prices.image_jobs import ImageJobs
from card_platform.player_prices.image_reader import EvidenceBlobs, PriceImageReader
from test_player_market import observation
from test_player_price_face_observations import faces
from test_player_price_image_evidence import FRONT, BACK, HTML, photo
from test_player_price_image_jobs import new_player

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires PostgreSQL")


def worker_class():
    assert importlib.util.find_spec("card_platform.player_prices.image_worker"), (
        "bounded image worker missing"
    )
    from card_platform.player_prices.image_worker import PriceImageWorker

    return PriceImageWorker


class TestDatabase:
    __test__ = False

    @asynccontextmanager
    async def connection(self):
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            yield c


@pytest_asyncio.fixture
async def setup_job(tmp_path):
    db = TestDatabase()
    async with db.connection() as c:
        player = await new_player(c)
        from card_platform.public_source_catalog import observe_public_sale

        obs = observe_public_sale({**observation()["raw_row"], "ID": int(uuid4().int % 10**12)})
        ids = await ImageJobs().enqueue(c, player, [obs])
    yield db, player, obs, ids[0], EvidenceBlobs(tmp_path / "blobs")
    async with db.connection() as c:
        await c.execute(
            "DELETE FROM player_price_image_review_approval WHERE job_id=ANY(%s)", (ids,)
        )
        await c.execute("DELETE FROM player_price_image_current WHERE job_id=ANY(%s)", (ids,))
        await c.execute("DELETE FROM player_price_image_attempt WHERE job_id=ANY(%s)", (ids,))
        await c.execute("DELETE FROM player_price_image_job WHERE id=ANY(%s)", (ids,))
        await c.execute(
            "DELETE FROM player_price_photo_capture WHERE source_id=%s",
            ("item:" + obs["sale"]["item_id"],),
        )
        await c.execute("DELETE FROM player_price_state WHERE player_id=%s", (player,))
        await c.execute("DELETE FROM player WHERE id=%s", (player,))


async def budget(db):
    async with db.connection() as c:
        return (
            await (
                await c.execute(
                    "SELECT COALESCE(sum(reserved_calls),0) n FROM player_price_image_attempt WHERE budget_day=(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date"
                )
            ).fetchone()
        )["n"]


@pytest.mark.asyncio
async def test_default_zero_budget_does_not_touch_public_http_or_model(setup_job):
    db, _, _, job_id, store = setup_job

    def forbidden(request):
        raise AssertionError("zero budget must perform no HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        worker = worker_class()(db, Settings(), store=store, public_client=client)
        assert not await worker.run_once()
        await worker.close()
    async with db.connection() as c:
        assert (
            await (
                await c.execute("SELECT status FROM player_price_image_job WHERE id=%s", (job_id,))
            ).fetchone()
        )["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,want_calls,want_status",
    [
        ("ok", 2, "captured"),
        ("boundary", 2, "captured"),
        ("invalid", 1, "rejected"),
        ("envelope", 1, "rejected"),
        ("low", 1, "rejected"),
        ("missing", 0, "rejected"),
        ("transport", 0, "retry"),
        ("paidtransport", 1, "retry"),
        ("expired", 1, "leased"),
        ("day", 1, "leased"),
        ("current", 1, "leased"),
    ],
)
async def test_bounded_capture_receipt_and_lease_guard(setup_job, mode, want_calls, want_status):
    db, player, obs, job_id, store = setup_job
    calls = []
    source_calls = []
    item = obs["sale"]["item_id"]
    html = HTML.replace("123", str(item))

    async def no_open_transactions():
        async with db.connection() as c:
            rows = await (
                await c.execute(
                    "SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND state='idle in transaction'"
                )
            ).fetchall()
            assert rows == [], "network must run after releasing every DB transaction"

    async def source(request):
        await no_open_transactions()
        source_calls.append(str(request.url))
        if mode == "transport":
            raise httpx.ConnectError("offline", request=request)
        if "/market/item/" in request.url.path.lower():
            return httpx.Response(
                200, text=html.replace(f'<img src="{BACK}">', "") if mode == "missing" else html
            )
        assert str(request.url) in {FRONT, BACK}
        return httpx.Response(200, content=photo("white" if str(request.url) == FRONT else "gray"))

    async def model(request):
        await no_open_transactions()
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "disabled"}
        assert body["model"] == "deepseek-flash"
        calls.append(body)
        if mode == "envelope":
            return httpx.Response(200, json={"id": "broken-fixture", "choices": []})
        if mode == "paidtransport":
            return httpx.Response(503, json={"error": {"message": "fixture unavailable"}})
        raw = faces()[len(calls) - 1]
        if mode == "boundary":
            raw["confidence"] = 0.88
        if mode == "low":
            raw["confidence"] = 0.5
        if mode in {"expired", "day", "current"}:
            async with db.connection() as c:
                if mode == "expired":
                    await c.execute(
                        "UPDATE player_price_image_job SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
                        (job_id,),
                    )
                elif mode == "day":
                    await c.execute(
                        "UPDATE player_price_image_attempt SET budget_day=budget_day-1 WHERE job_id=%s",
                        (job_id,),
                    )
                else:
                    await c.execute(
                        "DELETE FROM player_price_image_current WHERE job_id=%s", (job_id,)
                    )
        return httpx.Response(
            200,
            json=dict(
                id="fixture",
                object="chat.completion",
                created=1,
                model="deepseek-flash",
                choices=[
                    dict(
                        index=0,
                        finish_reason="stop",
                        message=dict(
                            role="assistant", content="{}" if mode == "invalid" else json.dumps(raw)
                        ),
                    )
                ],
                usage=dict(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            ),
        )

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(source)) as public,
        httpx.AsyncClient(transport=httpx.MockTransport(model)) as model_client,
    ):
        settings = Settings(
            vision_api_key="fixture",
            vision_model="deepseek-flash",
            vision_base_url="https://api.deepseek.com",
            player_price_image_daily_call_limit=int(await budget(db)) + 2,
        )
        reader = PriceImageReader(settings, http_client=model_client)
        worker = worker_class()(db, settings, store=store, reader=reader, public_client=public)
        assert await worker.run_once()
        await worker.close()
    assert len(calls) == want_calls
    async with db.connection() as c:
        row = await (
            await c.execute("SELECT * FROM player_price_image_job WHERE id=%s", (job_id,))
        ).fetchone()
        attempt = await (
            await c.execute("SELECT * FROM player_price_image_attempt WHERE job_id=%s", (job_id,))
        ).fetchone()
        assert row["status"] == want_status
        assert attempt["actual_calls"] == want_calls
        if want_calls:
            trace = json.loads(store.read(attempt["trace_sha256"]))
            assert trace["model_calls"] == want_calls
            if mode == "envelope":
                assert trace["observations"][0]["raw_response"]["id"] == "broken-fixture"
            elif mode == "paidtransport":
                assert trace["observations"][0]["raw_response"] == {
                    "error": {"message": "fixture unavailable"}
                }
            else:
                assert trace["observations"][0]["raw_response"]
        if mode == "ok":
            receipt = row["receipt"]
            assert receipt["usage"]["model_calls"] == 2
            for image in receipt["images"]:
                assert store.read(image["sha256"]) in {photo("white"), photo("gray")}
            assert (
                await (
                    await c.execute(
                        "SELECT count(*) n FROM player_price_photo_capture WHERE source_id=%s",
                        (row["source_id"],),
                    )
                ).fetchone()
            )["n"] == 1
            assert (
                await (
                    await c.execute(
                        "SELECT count(*) n FROM player_price_image_review_approval WHERE job_id=%s",
                        (job_id,),
                    )
                ).fetchone()
            )["n"] == 0
            assert (
                await (
                    await c.execute(
                        "SELECT requested_at FROM player_price_state WHERE player_id=%s", (player,)
                    )
                ).fetchone()
            )["requested_at"] is not None
        if mode == "transport":
            assert row["next_attempt_at"] > row["last_attempt_at"]
    if mode == "transport":
        assert len(source_calls) == 1


@pytest.mark.asyncio
async def test_cross_source_complete_photo_pair_is_free_negative_evidence(setup_job):
    from test_player_price_admission import bound_candidate
    from test_player_price_photo_captures import free_capture

    db, _, obs, job_id, store = setup_job
    _, peer, blobs, _ = bound_candidate(int(uuid4().int % 10**12))
    manifest = free_capture(peer, blobs)
    for data in blobs.values():
        store.put(data)
    async with db.connection() as c:
        await ImageJobs().record_photo_capture(c, manifest, blob_reader=store.read)
    try:

        def source(request):
            if "/market/item/" in request.url.path.lower():
                return httpx.Response(200, text=HTML.replace("123", obs["sale"]["item_id"]))
            assert str(request.url) in {FRONT, BACK}
            return httpx.Response(
                200, content=photo("white" if str(request.url) == FRONT else "gray")
            )

        def no_model(request):
            raise AssertionError("complete reused images must never spend model budget")

        settings = Settings(
            vision_api_key="fixture",
            vision_model="deepseek-flash",
            vision_base_url="https://api.deepseek.com",
            player_price_image_daily_call_limit=int(await budget(db)) + 2,
        )
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(source)) as public,
            httpx.AsyncClient(transport=httpx.MockTransport(no_model)) as model,
        ):
            worker = worker_class()(
                db,
                settings,
                store=store,
                reader=PriceImageReader(settings, http_client=model),
                public_client=public,
            )
            assert await worker.run_once()
            await worker.close()
        async with db.connection() as c:
            row = await (
                await c.execute(
                    "SELECT * FROM player_price_image_attempt WHERE job_id=%s", (job_id,)
                )
            ).fetchone()
            assert row["actual_calls"] == 0 and row["status"] == "failed"
            assert (
                await (
                    await c.execute(
                        "SELECT count(*) n FROM player_price_photo_capture WHERE source_id=%s",
                        ("item:" + obs["sale"]["item_id"],),
                    )
                ).fetchone()
            )["n"] == 1
    finally:
        async with db.connection() as c:
            await c.execute(
                "DELETE FROM player_price_photo_capture WHERE capture_manifest_sha256=%s",
                (manifest,),
            )


@pytest.mark.asyncio
async def test_cancel_closes_clients_and_restart_keeps_lease_debit_and_trace(setup_job):
    db, _, obs, job_id, store = setup_job
    started = asyncio.Event()

    async def source(request):
        if "/market/item/" in request.url.path.lower():
            return httpx.Response(200, text=HTML.replace("123", obs["sale"]["item_id"]))
        return httpx.Response(200, content=photo("white" if str(request.url) == FRONT else "gray"))

    async def model(request):
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancel must interrupt this request")

    settings = Settings(
        vision_api_key="fixture",
        vision_model="deepseek-flash",
        vision_base_url="https://api.deepseek.com",
        player_price_image_daily_call_limit=int(await budget(db)) + 2,
    )
    public = httpx.AsyncClient(transport=httpx.MockTransport(source))
    model_client = httpx.AsyncClient(transport=httpx.MockTransport(model))
    worker = worker_class()(
        db,
        settings,
        store=store,
        reader=PriceImageReader(settings, http_client=model_client),
        public_client=public,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert public.is_closed and model_client.is_closed
    async with db.connection() as c:
        attempt = await (
            await c.execute("SELECT * FROM player_price_image_attempt WHERE job_id=%s", (job_id,))
        ).fetchone()
        assert (
            attempt["actual_calls"] == 1
            and attempt["reserved_calls"] == 2
            and attempt["status"] == "leased"
        )
        assert (
            json.loads(store.read(attempt["trace_sha256"]))["observations"][0]["error_type"]
            == "CancelledError"
        )
        token = attempt["lease_token"]
    restarted = worker_class()(db, settings, store=store)
    assert not await restarted.run_once()
    async with db.connection() as c:
        await c.execute(
            "UPDATE player_price_image_job SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (job_id,),
        )
    assert not await restarted.run_once(), "expired reservation is not refunded"
    async with db.connection() as c:
        jobs = ImageJobs()
        new = await jobs.claim(c, daily_call_limit=settings.player_price_image_daily_call_limit + 2)
        assert new["id"] == job_id and new["lease_token"] != token
        assert not await jobs.authorize_call(c, job_id, token, 1)
        assert await jobs.authorize_call(c, job_id, new["lease_token"], 0)
        assert not await jobs.authorize_call(c, job_id, new["lease_token"], 0), (
            "same request cannot debit twice"
        )
        assert (
            await (
                await c.execute(
                    "SELECT status,actual_calls FROM player_price_image_attempt WHERE lease_token=%s",
                    (token,),
                )
            ).fetchone()
        ) == {"status": "expired", "actual_calls": 1}
    await restarted.close()
