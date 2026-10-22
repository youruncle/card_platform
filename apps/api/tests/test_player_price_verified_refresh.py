"""Refresh consumes persisted approvals; title-only candidates stay outside prices."""

import asyncio
import os
from uuid import uuid4
from datetime import date

import psycopg
from psycopg.rows import dict_row
import pytest

from test_player_price_image_reviews import reviewed_fixture, archive
from test_player_market import observation

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires PostgreSQL")


@pytest.mark.parametrize("partial", [False, True])
def test_refresh_only_admits_server_reviewed_evidence_and_reuses_it(partial):
    async def check():
        from card_platform.player_prices.service import refresh_player
        from card_platform.player_prices.repository import PriceRepository
        from card_platform.player_prices.image_jobs import ImageJobs
        from card_platform.public_source_catalog import store_observations

        obs, receipt, blobs, official, review = reviewed_fixture()
        sha = archive(blobs, review)
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = (
                    await (
                        await c.execute(
                            "INSERT INTO player(full_name,normalized_name) VALUES('Stephen Curry',%s) RETURNING id",
                            (str(uuid4()),),
                        )
                    ).fetchone()
                )["id"]
                other = observation(124, "2025-26 Topps Chrome Stephen Curry #201 Refractor")
                await store_observations(c, [obs, other])
                jobs = ImageJobs()
                ids = await jobs.enqueue(c, player, [obs])
                claimed = await jobs.claim(c, daily_call_limit=2)
                assert await jobs.finish(c, ids[0], claimed["lease_token"], receipt, actual_calls=0)
                await jobs.approve_review(
                    c, ids[0], sha, blobs[sha], expected_receipt_sha256=receipt["receipt_sha256"]
                )

                async def collect(*args, **kwargs):
                    return [], dict(
                        errors=[{"reason": "source timeout"}] if partial else [],
                        incremental_complete=True,
                        history_complete=False,
                    )

                result = await refresh_player(
                    c,
                    dict(id=player, full_name="Stephen Curry"),
                    date(2026, 10, 21),
                    collector=collect,
                    catalog=official,
                    blob_reader=blobs.__getitem__,
                )
                assert result["status"] == ("source_error" if partial else "insufficient")
                rows = await PriceRepository().sales(c, player, date(2026, 10, 21))
                assert [r["source_id"] for r in rows] == ["item:123"]
                assert rows[0]["identity"]["image_evidence"]["review"]["review_sha256"] == sha
                state = await (
                    await c.execute(
                        "SELECT coverage,last_success_at FROM player_price_state WHERE player_id=%s",
                        (player,),
                    )
                ).fetchone()
                assert state["coverage"]["identified_count"] == 1
                assert state["coverage"]["rejection_reasons"] == {"image_evidence_pending": 1}
                assert (state["last_success_at"] is None) == partial
                await refresh_player(
                    c,
                    dict(id=player, full_name="Stephen Curry"),
                    date(2026, 10, 21),
                    fetch=False,
                    catalog=official,
                    blob_reader=blobs.__getitem__,
                )
                assert len(await PriceRepository().sales(c, player, date(2026, 10, 21))) == 1
                assert (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM player_price_image_attempt WHERE job_id=ANY(%s)",
                            (ids,),
                        )
                    ).fetchone()
                )["n"] == 1
            finally:
                await c.rollback()

    asyncio.run(check())


@pytest.mark.parametrize(
    "source_update",
    ["none", "recapture", "changed_price", "approval_after_check", "source_after_check"],
)
def test_production_refresh_releases_transaction_before_http_and_photo_checks(
    source_update, monkeypatch
):
    async def check():
        import httpx
        from card_platform.config import Settings
        from card_platform.db import Database
        from card_platform.player_prices.coordinator import refresh_player_job
        from card_platform.player_prices.image_jobs import ImageJobs
        from card_platform.public_source_catalog import store_observations, observe_public_sale
        from psycopg.types.json import Jsonb

        obs, receipt, blobs, official, review = reviewed_fixture()
        sha = archive(blobs, review)
        url = os.environ["TEST_DATABASE_URL"]
        app = "price_refresh_" + uuid4().hex
        database = Database(
            Settings(
                _env_file=None,
                database_url=url + " application_name=" + app,
                database_pool_min_size=1,
            )
        )
        async with await psycopg.AsyncConnection.connect(
            url, autocommit=True, row_factory=dict_row
        ) as control:
            player = (
                await (
                    await control.execute(
                        "INSERT INTO player(full_name,normalized_name) VALUES('Stephen Curry',%s) RETURNING id",
                        (str(uuid4()),),
                    )
                ).fetchone()
            )["id"]
            await store_observations(control, [obs])
            queue = ImageJobs()
            ids = await queue.enqueue(control, player, [obs])
            claim = await queue.claim(control, daily_call_limit=2)
            assert await queue.finish(
                control, ids[0], claim["lease_token"], receipt, actual_calls=0
            )
            await queue.approve_review(
                control, ids[0], sha, blobs[sha], expected_receipt_sha256=receipt["receipt_sha256"]
            )
            await control.execute(
                "INSERT INTO player_price_state(player_id,coverage) VALUES(%s,%s)",
                (player, Jsonb({"verification_pending": True})),
            )
            entered = []
            reads = []

            async def assert_idle():
                states = await (
                    await control.execute(
                        "SELECT state FROM pg_stat_activity WHERE application_name=%s", (app,)
                    )
                ).fetchall()
                assert states and all(r["state"] == "idle" for r in states)
                entered.append("http")

            def blob_reader(key):
                with psycopg.connect(url, row_factory=dict_row) as observer:
                    states = observer.execute(
                        "SELECT state FROM pg_stat_activity WHERE application_name=%s", (app,)
                    ).fetchall()
                    assert states and all(r["state"] == "idle" for r in states)
                    if not reads and source_update in {"recapture", "changed_price"}:
                        raw = dict(obs["raw_row"])
                        if source_update == "changed_price":
                            raw["LowestPrice"] = 999
                        new = observe_public_sale(raw)
                        observer.execute(
                            "UPDATE public_source_catalog SET observation=%s,captured_at=%s WHERE external_sale_id='item:123'",
                            (Jsonb(new), new["captured_at"]),
                        )
                reads.append(key)
                entered.append("photo")
                return blobs[key]

            async def handler(request):
                busy = await refresh_player_job(
                    database,
                    dict(id=player, full_name="Stephen Curry"),
                    date(2026, 10, 21),
                    fetch=False,
                    catalog=official,
                )
                assert busy["status"] == "busy"
                await assert_idle()
                return httpx.Response(200, json={"ok": True})

            async def collector(*args, **kwargs):
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    assert (await client.get("https://cardhobby.com.cn/fixture")).status_code == 200
                return [obs], dict(errors=[], incremental_complete=True, history_complete=False)

            mutation = None
            if source_update in {"approval_after_check", "source_after_check"}:
                from copy import deepcopy
                from card_platform.player_prices import coordinator

                real_reconcile = coordinator.reconcile_prices
                changed_review = deepcopy(review)
                changed_review["corrections"][0]["after"]["fixed_colors"] = ["red"]
                changed_sha = archive(blobs, changed_review)

                async def change_after_check():
                    async with await psycopg.AsyncConnection.connect(
                        url + " application_name=" + app + "_mutation", row_factory=dict_row
                    ) as writer:
                        if source_update == "approval_after_check":
                            await queue.approve_review(
                                writer,
                                ids[0],
                                changed_sha,
                                blobs[changed_sha],
                                expected_receipt_sha256=receipt["receipt_sha256"],
                            )
                        else:
                            raw = dict(obs["raw_row"])
                            raw["LowestPrice"] = 999
                            await store_observations(writer, [observe_public_sale(raw)])

                async def reconcile_with_concurrent_update(*args, **kwargs):
                    nonlocal mutation
                    mutation = asyncio.create_task(change_after_check())
                    async with asyncio.timeout(3):
                        while not mutation.done():
                            waiting = await (
                                await control.execute(
                                    "SELECT wait_event_type FROM pg_stat_activity WHERE application_name=%s",
                                    (app + "_mutation",),
                                )
                            ).fetchone()
                            if waiting and waiting["wait_event_type"] == "Lock":
                                break
                            await asyncio.sleep(0.01)
                    assert not mutation.done(), (
                        "Evidence changed after the final check and before the price commit"
                    )
                    return await real_reconcile(*args, **kwargs)

                monkeypatch.setattr(
                    coordinator, "reconcile_prices", reconcile_with_concurrent_update
                )

            await database.open()
            try:
                result = await refresh_player_job(
                    database,
                    dict(id=player, full_name="Stephen Curry"),
                    date(2026, 10, 21),
                    collector=collector,
                    catalog=official,
                    blob_reader=blob_reader,
                )
                assert (
                    result["status"] == "insufficient"
                    and entered[0] == "http"
                    and entered.count("photo") >= 5
                )
                state = await (
                    await control.execute(
                        "SELECT coverage FROM player_price_state WHERE player_id=%s", (player,)
                    )
                ).fetchone()
                assert state["coverage"]["identified_count"] == (
                    0 if source_update == "changed_price" else 1
                )
                assert state["coverage"]["verification_pending"] is False
                if source_update == "changed_price":
                    assert state["coverage"]["rejection_reasons"] == {"image_evidence_pending": 1}
                else:
                    assert state["coverage"]["rejection_reasons"] == {}
                    assert len(reads) == len(set(reads)), (
                        "Re-capturing unchanged facts must not restart photo verification"
                    )
                if mutation is not None:
                    await asyncio.wait_for(mutation, 3)
                    monkeypatch.setattr(coordinator, "reconcile_prices", real_reconcile)
                    await refresh_player_job(
                        database,
                        dict(id=player, full_name="Stephen Curry"),
                        date(2026, 10, 21),
                        fetch=False,
                        catalog=official,
                        blob_reader=blobs.__getitem__,
                    )
                    assert (
                        await (
                            await control.execute(
                                "SELECT count(*) AS n FROM player_price_sale WHERE player_id=%s AND NOT excluded",
                                (player,),
                            )
                        ).fetchone()
                    )["n"] == 0
            finally:
                if mutation is not None and not mutation.done():
                    mutation.cancel()
                    await asyncio.gather(mutation, return_exceptions=True)
                await database.close()
                await control.execute(
                    "DELETE FROM player_price_image_review_approval WHERE job_id IN (SELECT id FROM player_price_image_job WHERE player_id=%s)",
                    (player,),
                )
                await control.execute(
                    "DELETE FROM player_price_image_attempt WHERE job_id IN (SELECT id FROM player_price_image_job WHERE player_id=%s)",
                    (player,),
                )
                await control.execute(
                    "DELETE FROM player_price_image_current WHERE job_id IN (SELECT id FROM player_price_image_job WHERE player_id=%s)",
                    (player,),
                )
                await control.execute(
                    "DELETE FROM player_price_image_job WHERE player_id=%s", (player,)
                )
                for table in [
                    "player_price_snapshot",
                    "player_price_basket",
                    "player_price_state",
                    "player_price_evidence_audit",
                    "player_price_evidence_revision",
                    "player_price_sale",
                ]:
                    await control.execute(f"DELETE FROM {table} WHERE player_id=%s", (player,))
                await control.execute(
                    "DELETE FROM public_source_catalog WHERE external_sale_id='item:123'"
                )
                await control.execute("DELETE FROM player WHERE id=%s", (player,))

    asyncio.run(check())


def test_multi_subject_listing_cannot_occupy_either_players_image_queue():
    async def check():
        from card_platform.player_prices.coordinator import _load
        from card_platform.public_source_catalog import store_observations

        _, _, _, official, _ = reviewed_fixture()
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                await store_observations(
                    c,
                    [
                        observation(123, "2025-26 Topps Chrome Stephen Curry #201 Refractor"),
                        observation(
                            124, "2025-26 Topps Chrome Stephen Curry LeBron James #201 Refractor"
                        ),
                    ],
                )
                for name, want in [("Stephen Curry", 1), ("LeBron James", 0)]:
                    player = await (
                        await c.execute(
                            "INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id,full_name",
                            (name, str(uuid4())),
                        )
                    ).fetchone()
                    await _load(c, player, date(2026, 10, 21), official)
                    jobs = await (
                        await c.execute(
                            "SELECT source_id FROM player_price_image_job WHERE player_id=%s",
                            (player["id"],),
                        )
                    ).fetchall()
                    assert len(jobs) == want
                    if jobs:
                        assert jobs[0]["source_id"] == "item:123"
            finally:
                await c.rollback()

    asyncio.run(check())


def test_local_replay_does_not_depend_on_query_discovery_artifacts(monkeypatch):
    async def check():
        from card_platform.config import Settings
        from card_platform.db import Database
        from card_platform.player_prices.coordinator import refresh_player_job
        from card_platform.player_prices.catalog import PriceCardCatalog

        _, _, _, official, _ = reviewed_fixture()

        def unavailable_query_artifact(*args, **kwargs):
            raise FileNotFoundError("query-only artifact unavailable")

        monkeypatch.setattr(PriceCardCatalog, "discovery_targets", unavailable_query_artifact)
        url = os.environ["TEST_DATABASE_URL"]
        database = Database(Settings(_env_file=None, database_url=url, database_pool_min_size=1))
        async with await psycopg.AsyncConnection.connect(
            url, autocommit=True, row_factory=dict_row
        ) as control:
            player = await (
                await control.execute(
                    "INSERT INTO player(full_name,normalized_name) VALUES('Unlisted Replay Player',%s) RETURNING id,full_name",
                    (str(uuid4()),),
                )
            ).fetchone()
            await database.open()
            try:
                result = await refresh_player_job(
                    database, player, date(2026, 10, 21), fetch=False, catalog=official
                )
                assert result["status"] == "insufficient"
            finally:
                await database.close()
                for table in [
                    "player_price_state",
                    "player_price_snapshot",
                    "player_price_evidence_audit",
                    "player_price_evidence_revision",
                ]:
                    await control.execute(
                        f"DELETE FROM {table} WHERE player_id=%s", (player["id"],)
                    )
                await control.execute("DELETE FROM player WHERE id=%s", (player["id"],))

    asyncio.run(check())


def test_input_order_is_not_a_sale_or_approval_change(monkeypatch):
    async def check():
        from card_platform.player_prices import coordinator

        _, _, _, official, _ = reviewed_fixture()
        inputs = [
            observation(123, "2025-26 Topps Chrome Stephen Curry #201 Refractor"),
            observation(124, "2025-26 Topps Chrome Stephen Curry #201 Refractor"),
        ]
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = await (
                    await c.execute(
                        "INSERT INTO player(full_name,normalized_name) VALUES('Stephen Curry',%s) RETURNING id,full_name",
                        (str(uuid4()),),
                    )
                ).fetchone()

                async def source(*args):
                    return list(inputs)

                monkeypatch.setattr(coordinator, "existing_samples", source)
                first = await coordinator._load(c, player, date(2026, 10, 21), official)
                inputs.reverse()
                second = await coordinator._load(c, player, date(2026, 10, 21), official)
                assert first[3] == second[3], (
                    "A query may reorder tied sale dates without changing evidence"
                )
            finally:
                await c.rollback()

    asyncio.run(check())


def test_shared_query_only_hit_does_not_block_other_subjects_valid_candidates():
    async def check():
        from card_platform.player_prices.coordinator import _load
        from card_platform.public_source_catalog import store_observations

        _, _, _, official, _ = reviewed_fixture()
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                await store_observations(
                    c,
                    [
                        observation(123, "2025-26 Topps Chrome 弗拉格 詹姆斯 #251 银折"),
                        observation(124, "2024-25 Panini Prizm LeBron James #130 Base"),
                    ],
                )
                for name in ["Cooper Flagg", "LeBron James"]:
                    player = await (
                        await c.execute(
                            "INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id,full_name",
                            (name, str(uuid4())),
                        )
                    ).fetchone()
                    await _load(c, player, date(2026, 10, 21), official)
                    if name == "LeBron James":
                        rows = await (
                            await c.execute(
                                "SELECT source_id FROM player_price_image_job WHERE player_id=%s",
                                (player["id"],),
                            )
                        ).fetchall()
                        assert rows == [{"source_id": "item:124"}]
            finally:
                await c.rollback()

    asyncio.run(check())
