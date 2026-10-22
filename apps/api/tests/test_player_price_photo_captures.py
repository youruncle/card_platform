"""Free public galleries can disprove independence, never establish a price."""

import asyncio
from datetime import date
import hashlib
import json
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from test_player_price_admission import bound_candidate


def free_capture(record, blobs):
    receipt = record["receipt"]
    manifest = dict(
        contract="price-public-pair-capture-only-v1",
        status="captured_pair",
        source_id=record["source_id"],
        observation=record["observation"],
        captured_at=receipt["captured_at"],
        gallery_html_sha256=receipt["gallery"]["raw_html_sha256"],
        images=receipt["images"],
        new_model_calls=0,
        not_admitted=True,
    )
    content = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
    sha = hashlib.sha256(content).hexdigest()
    blobs[sha] = content
    return sha


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires PostgreSQL")
@pytest.mark.parametrize("current_model", [True, False])
@pytest.mark.parametrize("expired", [True, False])
def test_free_gallery_withdraws_an_old_price_without_a_model_job(current_model, expired):
    async def check():
        from card_platform.player_prices import image_jobs

        assert hasattr(image_jobs.ImageJobs, "record_photo_capture"), (
            "Free capture-only evidence has no persistent negative admission path"
        )
        from card_platform.player_prices.coordinator import _load
        from card_platform.player_prices.admission import verified_price_rows
        from card_platform.player_prices.reconciliation import (
            reconcile_prices,
            finalize_reconciliation,
        )
        from card_platform.public_source_catalog import store_observations

        day = "2026-07-01" if expired else "2026-10-20"
        old = bound_candidate(91301, day="2026-07-01")
        new = bound_candidate(91302, day=day, title_prefix="" if current_model else "lot ")
        blobs = {**old[2], **new[2]}
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                players = []
                for name in ["Stephen Curry", "Unrelated historical owner"]:
                    players.append(
                        await (
                            await c.execute(
                                "INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id,full_name",
                                (name, str(uuid4())),
                            )
                        ).fetchone()
                    )
                queue = image_jobs.ImageJobs()
                # The public model belongs to a different task owner; its old
                # derived price still belongs to the actual player.
                await queue.enqueue(c, players[1]["id"], [old[0]])
                job = await queue.claim(c, daily_call_limit=2)
                assert await queue.finish(
                    c, job["id"], job["lease_token"], old[1]["receipt"], actual_calls=0
                )
                rows, _ = verified_price_rows(
                    [old[0]],
                    "Stephen Curry",
                    old[3],
                    evidence=[old[1]],
                    blob_reader=blobs.__getitem__,
                )
                repaired = await reconcile_prices(c, players[0]["id"], rows, date(2026, 7, 2))
                await finalize_reconciliation(c, repaired["audit_id"])
                await store_observations(c, [old[0], new[0]])
                if current_model:
                    await queue.enqueue(c, players[0]["id"], [new[0]])
                    job = await queue.claim(c, daily_call_limit=4)
                    assert await queue.finish(
                        c, job["id"], job["lease_token"], new[1]["receipt"], actual_calls=0
                    )
                before = await _load(c, players[0], date(2026, 10, 22), old[3])
                sha = free_capture(new[1], blobs)
                result = await queue.record_photo_capture(c, sha, blob_reader=blobs.__getitem__)
                assert result["source_id"] == "item:91302"
                assert (
                    await queue.record_photo_capture(c, sha, blob_reader=blobs.__getitem__)
                ) == result
                after = await _load(c, players[0], date(2026, 10, 22), old[3])
                assert before[3] != after[3]
                rows, coverage = verified_price_rows(
                    after[0],
                    "Stephen Curry",
                    after[1],
                    evidence=after[2],
                    blob_reader=blobs.__getitem__,
                )
                assert rows == []
                assert coverage["photo_reuse_sources"] == ["item:91301", "item:91302"]
                repaired = await reconcile_prices(
                    c,
                    players[0]["id"],
                    rows,
                    date(2026, 10, 22),
                    photo_reuse_sources=coverage["photo_reuse_sources"],
                )
                await finalize_reconciliation(c, repaired["audit_id"])
                stored = await (
                    await c.execute(
                        "SELECT excluded FROM player_price_sale WHERE source_id='item:91301'"
                    )
                ).fetchone()
                assert stored["excluded"] is True
                assert not await (
                    await c.execute("SELECT 1 FROM player_price_source_conflict")
                ).fetchone()
                assert (
                    await (
                        await c.execute("SELECT count(*) AS n FROM player_price_photo_capture")
                    ).fetchone()
                )["n"] == 1
                assert (
                    await (
                        await c.execute("SELECT count(*) AS n FROM player_price_image_job")
                    ).fetchone()
                )["n"] == 1 + int(current_model)
            finally:
                await c.rollback()

    asyncio.run(check())


@pytest.mark.parametrize(
    "corruption", ["manifest", "html", "image", "index_source", "index_pair", "index_observation"]
)
def test_free_capture_revalidates_original_bytes_and_index(corruption):
    from card_platform.player_prices.photo_captures import read_capture, read_bound_capture

    _, record, blobs, _ = bound_candidate(91401)
    sha = free_capture(record, blobs)
    capture = read_capture(sha, blobs.__getitem__)
    if corruption == "manifest":
        blobs[sha] = b"{}"
    elif corruption == "html":
        blobs[record["receipt"]["gallery"]["raw_html_sha256"]] = b"foreign item HTML"
    elif corruption == "image":
        blobs[record["receipt"]["images"][0]["sha256"]] = b"changed photo bytes"
    elif corruption == "index_source":
        capture["source_id"] = "item:999"
    elif corruption == "index_pair":
        capture["pair_a"] = "0" * 64
    else:
        capture["observation"] = {
            **capture["observation"],
            "captured_at": "2000-01-01T00:00:00+00:00",
        }
    with pytest.raises(ValueError):
        read_bound_capture(capture, blobs.__getitem__)


def test_free_photo_context_cannot_create_a_price_or_double_count_its_own_captures():
    from card_platform.player_prices.photo_captures import read_capture
    from card_platform.player_prices.admission import verified_price_rows

    obs, record, blobs, official = bound_candidate(91402)
    capture = read_capture(free_capture(record, blobs), blobs.__getitem__)
    capture["photo_peers"] = [{**capture, "photo_anchor": capture}]
    rows, coverage = verified_price_rows(
        [obs], "Stephen Curry", official, evidence=[capture], blob_reader=blobs.__getitem__
    )
    assert rows == [] and coverage["sample_count"] == 1
    assert coverage["rejection_reasons"] == {"image_evidence_pending": 1}
    assert coverage["photo_reuse_sources"] == []


@pytest.mark.parametrize("swapped", [False, True])
@pytest.mark.parametrize("missing_blob", [False, True])
def test_free_peer_excludes_bound_model_price_or_holds_when_unreadable(swapped, missing_blob):
    from card_platform.player_prices.photo_captures import read_capture
    from card_platform.player_prices.admission import verified_price_rows

    obs, record, blobs, official = bound_candidate(91403)
    _, peer, extra, _ = bound_candidate(
        91404, colours=("gray", "white") if swapped else ("white", "gray")
    )
    blobs.update(extra)
    sha = free_capture(peer, blobs)
    capture = read_capture(sha, blobs.__getitem__)
    if missing_blob:
        del blobs[sha]
    record["photo_peers"] = [{**capture, "photo_anchor": record}]
    rows, coverage = verified_price_rows(
        [obs], "Stephen Curry", official, evidence=[record], blob_reader=blobs.__getitem__
    )
    assert rows == []
    reason = "photo_independence_pending" if missing_blob else "reused_photo_pair"
    assert coverage["rejection_reasons"] == {reason: 1}
    assert coverage["photo_reuse_sources"] == ([] if missing_blob else ["item:91403", "item:91404"])


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires PostgreSQL")
def test_free_capture_wakes_a_price_owner_outside_the_current_holdings_roster(monkeypatch):
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs
        from card_platform.player_prices.repository import PriceRepository
        from card_platform.player_prices.reconciliation import (
            reconcile_prices,
            finalize_reconciliation,
        )
        from card_platform.player_prices.admission import verified_price_rows

        old = bound_candidate(91501, day="2026-07-01")
        peer = bound_candidate(91502, day="2026-07-02")
        blobs = {**old[2], **peer[2]}
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = await (
                    await c.execute(
                        "INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id",
                        ("Historical photo owner " + str(uuid4()), str(uuid4())),
                    )
                ).fetchone()
                jobs, repo = ImageJobs(), PriceRepository()
                await jobs.enqueue(c, player["id"], [old[0]])
                job = await jobs.claim(c, daily_call_limit=2)
                assert await jobs.finish(
                    c, job["id"], job["lease_token"], old[1]["receipt"], actual_calls=0
                )
                rows, _ = verified_price_rows(
                    [old[0]],
                    "Stephen Curry",
                    old[3],
                    evidence=[old[1]],
                    blob_reader=blobs.__getitem__,
                )
                audit = await reconcile_prices(c, player["id"], rows, date(2026, 7, 2))
                await finalize_reconciliation(c, audit["audit_id"])
                assert player["id"] not in {
                    member for subject in await repo.roster(c) for member in subject["member_ids"]
                }

                # Other test suites may have existing held subjects. Isolate
                # their scheduling, without deleting their records or holding
                # rows; this owner still has real prices and real DB state.
                async def empty_roster(_c):
                    return []

                monkeypatch.setattr(repo, "roster", empty_roster)
                assert await repo.due_player(c, date(2026, 10, 21)) is None
                await jobs.record_photo_capture(
                    c, free_capture(peer[1], blobs), blob_reader=blobs.__getitem__
                )
                due = await repo.due_player(c, date(2026, 10, 21))
                assert due is not None and due["id"] == player["id"], (
                    "An old non-roster price owner must receive the photo withdrawal refresh"
                )
                await c.execute(
                    "UPDATE player_price_state SET last_success_at=clock_timestamp(),last_report_date=%s,last_attempt_at=clock_timestamp() WHERE player_id=%s",
                    (date(2026, 10, 21), player["id"]),
                )
                assert await repo.due_player(c, date(2026, 10, 21)) is None
            finally:
                await c.rollback()

    asyncio.run(check())


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires PostgreSQL")
def test_unreadable_expired_free_peer_temporarily_withdraws_old_price():
    async def check():
        from card_platform.player_prices.image_jobs import ImageJobs
        from card_platform.player_prices.coordinator import _load
        from card_platform.player_prices.admission import verified_price_rows
        from card_platform.player_prices.reconciliation import (
            reconcile_prices,
            finalize_reconciliation,
        )

        old, peer = (
            bound_candidate(91601, day="2026-07-01"),
            bound_candidate(91602, day="2026-07-02"),
        )
        blobs = {**old[2], **peer[2]}
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = await (
                    await c.execute(
                        "INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id,full_name",
                        ("Expired photo scope " + str(uuid4()), str(uuid4())),
                    )
                ).fetchone()
                jobs = ImageJobs()
                await jobs.enqueue(c, player["id"], [old[0]])
                claimed = await jobs.claim(c, daily_call_limit=2)
                assert await jobs.finish(
                    c, claimed["id"], claimed["lease_token"], old[1]["receipt"], actual_calls=0
                )
                rows, _ = verified_price_rows(
                    [old[0]],
                    "Stephen Curry",
                    old[3],
                    evidence=[old[1]],
                    blob_reader=blobs.__getitem__,
                )
                audit = await reconcile_prices(c, player["id"], rows, date(2026, 7, 2))
                await finalize_reconciliation(c, audit["audit_id"])
                sha = free_capture(peer[1], blobs)
                await jobs.record_photo_capture(c, sha, blob_reader=blobs.__getitem__)
                del blobs[sha]
                loaded = await _load(c, player, date(2026, 10, 22), old[3])
                rows, coverage = verified_price_rows(
                    loaded[0],
                    player["full_name"],
                    loaded[1],
                    evidence=loaded[2],
                    blob_reader=blobs.__getitem__,
                )
                assert coverage.get("photo_pending_sources") == ["item:91601", "item:91602"], (
                    "Unavailable historical peer must not silently leave old prices verified"
                )
                assert coverage["photo_reuse_sources"] == [] and coverage["sample_count"] == 0
                audit = await reconcile_prices(
                    c,
                    player["id"],
                    rows,
                    date(2026, 10, 22),
                    photo_pending_sources=coverage["photo_pending_sources"],
                )
                await finalize_reconciliation(c, audit["audit_id"])
                saved = await (
                    await c.execute(
                        "SELECT excluded FROM player_price_sale WHERE source_id='item:91601'"
                    )
                ).fetchone()
                assert saved["excluded"] is True
                record = await (
                    await c.execute(
                        "SELECT before_state FROM player_price_evidence_audit WHERE id=%s",
                        (audit["audit_id"],),
                    )
                ).fetchone()
                assert record["before_state"]["photo_independence_pending_withdrawals"] == [
                    "item:91601",
                    "item:91602",
                ]
                assert "photo_reuse_withdrawals" not in record["before_state"]
                # Exclusion is temporary and does not erase the pending
                # input after both sales have left the report window.
                again = await _load(c, player, date(2026, 10, 22), old[3])
                _, pending = verified_price_rows(
                    again[0],
                    player["full_name"],
                    again[1],
                    evidence=again[2],
                    blob_reader=blobs.__getitem__,
                )
                assert pending["photo_pending_sources"] == ["item:91601", "item:91602"]
                free_capture(peer[1], blobs)  # Restore exactly the archived bytes.
                _, restored = verified_price_rows(
                    again[0],
                    player["full_name"],
                    again[1],
                    evidence=again[2],
                    blob_reader=blobs.__getitem__,
                )
                assert restored["photo_pending_sources"] == []
                assert restored["photo_reuse_sources"] == ["item:91601", "item:91602"]
            finally:
                await c.rollback()

    asyncio.run(check())
