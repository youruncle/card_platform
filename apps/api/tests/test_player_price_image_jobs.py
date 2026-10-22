"""Real PostgreSQL queue contracts, with every fixture rolled back."""

import asyncio
from copy import deepcopy
import json
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from test_player_price_image_reviews import reviewed_fixture

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires PostgreSQL")


def test_photo_peer_history_survives_current_switch_and_wrong_public_job_owner():
    async def check():
        from test_player_price_admission import bound_candidate
        from card_platform.player_prices.image_jobs import ImageJobs
        from card_platform.player_prices.admission import verified_price_rows
        from card_platform.public_source_catalog import observe_public_sale

        old = bound_candidate(8811, day="2026-07-24")
        current = bound_candidate(8812, day="2026-10-20")
        fresh = bound_candidate(
            8812, day="2026-10-20", colours=("blue", "black"), title_prefix="更新图库 "
        )
        blobs = {**old[2], **current[2], **fresh[2]}
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                first, second = await new_player(c), await new_player(c)
                jobs = ImageJobs()
                old_ids = await jobs.enqueue(c, first, [old[0]])
                current_ids = await jobs.enqueue(c, second, [current[0]])
                receipts = {
                    old[1]["source_id"]: old[1]["receipt"],
                    current[1]["source_id"]: current[1]["receipt"],
                }
                for _ in range(2):
                    leased = await jobs.claim(c, daily_call_limit=4)
                    assert await jobs.finish(
                        c,
                        leased["id"],
                        leased["lease_token"],
                        receipts[leased["source_id"]],
                        actual_calls=0,
                    )
                changed = observe_public_sale(
                    {**old[0]["raw_row"], "Title": old[0]["sale"]["title"] + " new capture"}
                )
                fresh_ids = await jobs.enqueue(c, second, [fresh[0]])
                leased = await jobs.claim(c, daily_call_limit=6)
                assert leased["id"] == fresh_ids[0]
                assert await jobs.finish(
                    c, leased["id"], leased["lease_token"], fresh[1]["receipt"], actual_calls=0
                )
                await jobs.enqueue(c, first, [changed])
                assert await jobs.evidence(c, old_ids) == {}
                saved = await jobs.evidence(c, fresh_ids, include_photo_peers=True)
                record = saved[fresh_ids[0]]
                assert [peer["id"] for peer in record["photo_peers"]] == old_ids
                assert record["photo_peers"][0]["photo_anchor"]["id"] == current_ids[0]
                assert record["player_id"] == second
                assert record["photo_peers"][0]["player_id"] == first
                rows, coverage = verified_price_rows(
                    [fresh[0]],
                    "Stephen Curry",
                    current[3],
                    evidence=[record],
                    blob_reader=blobs.__getitem__,
                )
                assert rows == [] and coverage["rejection_reasons"] == {"reused_photo_pair": 1}
                newest = observe_public_sale(
                    {**fresh[0]["raw_row"], "Title": fresh[0]["sale"]["title"] + " pending"}
                )
                pending_ids = await jobs.enqueue(c, second, [newest])
                assert await jobs.evidence(c, pending_ids) == {}
                pending = await jobs.evidence(c, pending_ids, include_photo_peers=True)
                assert pending[pending_ids[0]]["receipt"] is None
                rows, coverage = verified_price_rows(
                    [newest],
                    "Stephen Curry",
                    current[3],
                    evidence=list(pending.values()),
                    blob_reader=blobs.__getitem__,
                )
                assert rows == []
                assert coverage["rejection_reasons"] == {"image_evidence_pending": 1}
                assert coverage["photo_reuse_sources"] == ["item:8811", "item:8812"]
            finally:
                await c.rollback()

    asyncio.run(check())


def test_queue_reuses_immutable_observations_and_never_resets_existing_work():
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = await new_player(c)
                obs, receipt, _, _, review = reviewed_fixture()
                jobs = ImageJobs()
                first = await jobs.enqueue(c, player, [obs])
                again = await jobs.enqueue(c, player, [deepcopy(obs)])
                assert first == again and len(first) == 1
                assert await jobs.claim(c, daily_call_limit=0) is None
                job = await jobs.claim(c, daily_call_limit=2)
                assert job["id"] == first[0] and job["reserved_calls"] == 2
                assert await jobs.enqueue(c, player, [obs]) == first
                assert await jobs.claim(c, daily_call_limit=2) is None
                bad = deepcopy(receipt)
                bad["observation_sha256"] = "f" * 64
                with pytest.raises(ValueError, match="receipt_binding"):
                    await jobs.finish(c, job["id"], job["lease_token"], bad, actual_calls=2)
                assert not await jobs.finish(c, job["id"], uuid4(), receipt, actual_calls=2)
                assert await jobs.finish(c, job["id"], job["lease_token"], receipt, actual_calls=2)
                state = await jobs.evidence(c, first)
                assert state[first[0]]["receipt"] == receipt
                assert state[first[0]]["approved_review_sha256s"] == []
                assert await jobs.claim(c, daily_call_limit=200) is None
                # A self-declared client review never becomes approved on storage.
                assert "approved_review_sha256s" not in state[first[0]]["receipt"]
                with pytest.raises(ValueError, match="review_binding"):
                    await jobs.approve_review(
                        c,
                        first[0],
                        "a" * 64,
                        json.dumps(review).encode(),
                        expected_receipt_sha256="f" * 64,
                    )
                from card_platform.player_prices.image_evidence import _seal

                sha = _seal(review)
                review_bytes = json.dumps(
                    review, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
                await jobs.approve_review(
                    c,
                    first[0],
                    sha,
                    review_bytes,
                    expected_receipt_sha256=receipt["receipt_sha256"],
                )
                assert (await jobs.evidence(c, first))[first[0]]["approved_review_sha256s"] == [sha]
                assert await jobs.enqueue(c, player, [obs]) == first
                altered = deepcopy(obs)
                altered["sale"]["sale_price"] = "999"
                with pytest.raises(ValueError):
                    await jobs.enqueue(c, player, [altered])
            finally:
                await c.rollback()

    asyncio.run(check())


def test_expired_leases_keep_budget_and_old_workers_cannot_finish():
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = await new_player(c)
                obs, receipt, *_ = reviewed_fixture()
                jobs = ImageJobs()
                ids = await jobs.enqueue(c, player, [obs])
                old = await jobs.claim(c, daily_call_limit=2)
                await c.execute(
                    "UPDATE player_price_image_job SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
                    (ids[0],),
                )
                assert await jobs.claim(c, daily_call_limit=2) is None
                new = await jobs.claim(c, daily_call_limit=4)
                assert new["lease_token"] != old["lease_token"]
                assert not await jobs.finish(
                    c, old["id"], old["lease_token"], receipt, actual_calls=2
                )
                assert await jobs.finish(c, new["id"], new["lease_token"], receipt, actual_calls=2)
                budget = await (
                    await c.execute(
                        "SELECT sum(reserved_calls) AS n FROM player_price_image_attempt"
                    )
                ).fetchone()
                assert budget["n"] == 4
            finally:
                await c.rollback()

    asyncio.run(check())


async def new_player(c):
    return (
        await (
            await c.execute(
                "INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id",
                ("Image queue fixture " + uuid4().hex, str(uuid4())),
            )
        ).fetchone()
    )["id"]


def test_failures_have_bounded_retry_and_cannot_hide_paid_attempts():
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = await new_player(c)
                obs, *_ = reviewed_fixture()
                jobs = ImageJobs()
                ids = await jobs.enqueue(c, player, [obs])
                job = await jobs.claim(c, daily_call_limit=2)
                with pytest.raises(ValueError, match="error_code"):
                    await jobs.fail(
                        c,
                        job["id"],
                        job["lease_token"],
                        error_code="secret transport body",
                        actual_calls=1,
                    )
                assert not await jobs.fail(
                    c,
                    job["id"],
                    uuid4(),
                    error_code="transport",
                    actual_calls=1,
                    trace_sha256="a" * 64,
                )
                with pytest.raises(ValueError, match="failure_trace"):
                    await jobs.fail(
                        c, job["id"], job["lease_token"], error_code="transport", actual_calls=1
                    )
                assert await jobs.fail(
                    c,
                    job["id"],
                    job["lease_token"],
                    error_code="transport",
                    actual_calls=1,
                    trace_sha256="a" * 64,
                )
                row = await (
                    await c.execute("SELECT * FROM player_price_image_job WHERE id=%s", (ids[0],))
                ).fetchone()
                assert row["status"] == "retry" and row["next_attempt_at"] > row["last_attempt_at"]
                assert await jobs.claim(c, daily_call_limit=10) is None
                await c.execute(
                    "UPDATE player_price_image_job SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                    (ids[0],),
                )
                assert await jobs.claim(c, daily_call_limit=2) is None
                second = await jobs.claim(c, daily_call_limit=4)
                assert await jobs.fail(
                    c,
                    second["id"],
                    second["lease_token"],
                    error_code="insufficient_images",
                    actual_calls=0,
                )
                assert await jobs.claim(c, daily_call_limit=20) is None
                records = await (
                    await c.execute(
                        "SELECT status,actual_calls,trace_sha256 FROM player_price_image_attempt WHERE job_id=%s ORDER BY created_at",
                        (ids[0],),
                    )
                ).fetchall()
                assert [r["status"] for r in records] == ["failed", "failed"]
                assert sorted(r["actual_calls"] for r in records) == [0, 1]
                assert (
                    next(r for r in records if r["actual_calls"] == 1)["trace_sha256"] == "a" * 64
                )
            finally:
                await c.rollback()

    asyncio.run(check())


def test_concurrent_workers_obey_one_global_call_budget():
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs
        from card_platform.public_source_catalog import observe_public_sale

        jobs = ImageJobs()
        url = os.environ["TEST_DATABASE_URL"]
        async with await psycopg.AsyncConnection.connect(
            url, autocommit=True, row_factory=dict_row
        ) as c:
            # Dedicated test DB only; no network or production data involved.
            player = await new_player(c)
            obs, *_ = reviewed_fixture()
            observations = []
            offset = int(uuid4().int % 100000000)
            for i in range(3):
                row = {**obs["raw_row"], "ID": offset + i}
                observations.append(observe_public_sale(row))
            ids = await jobs.enqueue(c, player, observations)
            before = await (
                await c.execute(
                    "SELECT COALESCE(sum(reserved_calls),0) AS n FROM player_price_image_attempt WHERE budget_day=(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date"
                )
            ).fetchone()
            try:

                async def claim():
                    async with await psycopg.AsyncConnection.connect(
                        url, autocommit=True, row_factory=dict_row
                    ) as other:
                        return await jobs.claim(other, daily_call_limit=int(before["n"]) + 2)

                result = await asyncio.gather(claim(), claim(), claim())
                accepted = [r for r in result if r is not None]
                assert len(accepted) == 1 and accepted[0]["id"] in ids
                assert (
                    len(
                        await (
                            await c.execute(
                                "SELECT lease_token FROM player_price_image_attempt WHERE job_id=ANY(%s)",
                                (ids,),
                            )
                        ).fetchall()
                    )
                    == 1
                )
            finally:
                await c.execute(
                    "DELETE FROM player_price_image_current WHERE job_id=ANY(%s)", (ids,)
                )
                await c.execute(
                    "DELETE FROM player_price_image_attempt WHERE job_id=ANY(%s)", (ids,)
                )
                await c.execute("DELETE FROM player_price_image_job WHERE id=ANY(%s)", (ids,))
                await c.execute("DELETE FROM player WHERE id=%s", (player,))

    asyncio.run(check())


def test_changed_source_facts_get_new_jobs_and_scheduling_rotates_players():
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs
        from card_platform.public_source_catalog import observe_public_sale

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                first_player, second_player = await new_player(c), await new_player(c)
                original, *_ = reviewed_fixture()
                jobs = ImageJobs()
                changed = observe_public_sale({**original["raw_row"], "LowestPrice": 999})
                other = observe_public_sale({**original["raw_row"], "ID": 987654321})
                first_ids = await jobs.enqueue(c, first_player, [original, changed])
                second_ids = await jobs.enqueue(c, second_player, [other])
                assert len(first_ids) == 2
                first = await jobs.claim(c, daily_call_limit=4)
                second = await jobs.claim(c, daily_call_limit=4)
                assert first["player_id"] != second["player_id"]
                assert await jobs.evidence(c, [*first_ids, *second_ids]) == {}
                # New facts have no receipt or approval inherited from the old capture.
                row = await (
                    await c.execute(
                        "SELECT observation,receipt FROM player_price_image_job WHERE id=%s",
                        (first_ids[1],),
                    )
                ).fetchone()
                assert row["observation"] == changed and row["receipt"] is None
                assert original["raw_row_sha256"] != changed["raw_row_sha256"]
            finally:
                await c.rollback()

    asyncio.run(check())


@pytest.mark.parametrize("old_state", ["pending", "leased", "captured"])
def test_new_source_capture_invalidates_old_pending_and_captured_work(old_state):
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs
        from card_platform.public_source_catalog import observe_public_sale

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = await new_player(c)
                obs, receipt, *_ = reviewed_fixture()
                jobs = ImageJobs()
                old_id = (await jobs.enqueue(c, player, [obs]))[0]
                old = await jobs.claim(c, daily_call_limit=2) if old_state != "pending" else None
                if old_state == "captured":
                    assert await jobs.finish(c, old_id, old["lease_token"], receipt, actual_calls=2)
                changed = observe_public_sale(
                    {**obs["raw_row"], "Title": obs["raw_row"]["Title"] + " corrected title"}
                )
                new_id = (await jobs.enqueue(c, player, [changed]))[0]
                if old:
                    assert not await jobs.finish(
                        c, old_id, old["lease_token"], receipt, actual_calls=2
                    )
                assert await jobs.evidence(c, [old_id]) == {}
                # Seeing the archived old capture again cannot overwrite newer facts.
                assert (await jobs.enqueue(c, player, [obs])) == [old_id]
                claimed = await jobs.claim(c, daily_call_limit=2 if old_state == "pending" else 4)
                assert claimed["id"] == new_id and claimed["observation"] == changed
                # Preserve old observations for audit, without spending on them.
                assert (
                    await (
                        await c.execute(
                            "SELECT observation FROM player_price_image_job WHERE id=%s", (old_id,)
                        )
                    ).fetchone()
                )["observation"] == obs
            finally:
                await c.rollback()

    asyncio.run(check())


def test_alias_subjects_share_jobs_without_rebinding_a_different_person():
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                name = "Same queue subject " + uuid4().hex
                ids = []
                for created in ["2020-01-01", "2020-01-02"]:
                    ids.append(
                        (
                            await (
                                await c.execute(
                                    "INSERT INTO player(full_name,normalized_name,created_at) VALUES(%s,%s,%s) RETURNING id",
                                    (name, str(uuid4()), created),
                                )
                            ).fetchone()
                        )["id"]
                    )
                obs, *_ = reviewed_fixture()
                queue = ImageJobs()
                first = await queue.enqueue(c, ids[1], [obs])
                again = await queue.enqueue(c, ids[0], [obs])
                assert first == again
                assert (
                    await (
                        await c.execute(
                            "SELECT player_id FROM player_price_image_job WHERE id=%s", (first[0],)
                        )
                    ).fetchone()
                )["player_id"] == ids[0]
                other = await new_player(c)
                with pytest.raises(ValueError, match="subject_conflict"):
                    await queue.enqueue(c, other, [obs])
            finally:
                await c.rollback()

    asyncio.run(check())
