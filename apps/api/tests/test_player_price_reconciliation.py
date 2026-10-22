"""Audited replacement of derived prices, with no source-fact rewriting."""

import asyncio
import os
from uuid import uuid4
from datetime import date

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from test_player_price_image_evidence import fixture

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires PostgreSQL")


def test_verified_rebuild_repairs_identity_exclusions_and_preserves_audit():
    async def check():
        from card_platform.player_prices.reconciliation import reconcile_prices
        from card_platform.player_prices.admission import verified_price_rows

        obs, receipt, blobs, official = fixture()
        records = [
            dict(
                source_id="item:123",
                observation_sha256=obs["raw_row_sha256"],
                receipt=receipt,
                approved_review_sha256s=[],
            )
        ]
        rows, _ = verified_price_rows(
            [obs], "Stephen Curry", official, evidence=records, blob_reader=blobs.__getitem__
        )
        assert len(rows) == 1
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
                source = rows[0]
                await c.execute(
                    "INSERT INTO player_price_sale(source_id,player_id,card_key,identity,sale_price,occurred_at,source_url,raw_title,excluded) VALUES(%s,%s,'wrong',%s,%s,%s,%s,%s,true)",
                    (
                        source["source_id"],
                        player,
                        Jsonb({"card_number": "XBW10"}),
                        source["sale_price"],
                        source["occurred_at"],
                        source["source_url"],
                        source["raw_title"],
                    ),
                )
                await c.execute(
                    "INSERT INTO player_price_basket(player_id,month,selected_on,cards) VALUES(%s,'2026-10-01','2026-10-01',%s)",
                    (player, Jsonb([{"card_key": "wrong"}] * 3)),
                )
                await c.execute(
                    "INSERT INTO player_price_snapshot(player_id,report_date,result) VALUES(%s,'2026-10-10',%s)",
                    (player, Jsonb({"status": "ready", "change_pct": "99"})),
                )
                result = await reconcile_prices(c, player, rows, date(2026, 10, 21))
                assert result["admitted_count"] == 1 and result["blocked_sources"] == []
                saved = await (
                    await c.execute(
                        "SELECT * FROM player_price_sale WHERE source_id=%s", (source["source_id"],)
                    )
                ).fetchone()
                assert saved["identity"]["card_number"] == "201" and saved["excluded"] is False
                assert (
                    str(saved["sale_price"]) == "100.00"
                    and saved["raw_title"] == source["raw_title"]
                )
                assert (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM player_price_basket WHERE player_id=%s",
                            (player,),
                        )
                    ).fetchone()
                )["n"] == 0
                assert (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM player_price_snapshot WHERE player_id=%s",
                            (player,),
                        )
                    ).fetchone()
                )["n"] == 0
                audit = await (
                    await c.execute(
                        "SELECT * FROM player_price_evidence_audit WHERE player_id=%s", (player,)
                    )
                ).fetchone()
                assert audit["before_state"]["sales"][0]["identity"]["card_number"] == "XBW10"
                assert audit["before_state"]["snapshots"][0]["result"]["change_pct"] == "99"
                await reconcile_prices(c, player, rows, date(2026, 10, 21))
                assert (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM player_price_evidence_audit WHERE player_id=%s",
                            (player,),
                        )
                    ).fetchone()
                )["n"] == 1
                from card_platform.player_prices.reconciliation import rollback_reconciliation

                assert await rollback_reconciliation(c, audit["id"])
                restored = await (
                    await c.execute(
                        "SELECT identity FROM player_price_sale WHERE source_id=%s",
                        (source["source_id"],),
                    )
                ).fetchone()
                assert restored["identity"]["card_number"] == "XBW10"
                assert (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM player_price_basket WHERE player_id=%s",
                            (player,),
                        )
                    ).fetchone()
                )["n"] == 1
                assert not await rollback_reconciliation(c, audit["id"])
                await reconcile_prices(c, player, rows, date(2026, 10, 21))
                await reconcile_prices(c, player, [], date(2026, 10, 21))
                assert (
                    await (
                        await c.execute(
                            "SELECT excluded FROM player_price_sale WHERE source_id=%s",
                            (source["source_id"],),
                        )
                    ).fetchone()
                )["excluded"] is True
            finally:
                await c.rollback()

    asyncio.run(check())


def test_rollback_checks_later_changes_and_restores_source_conflict_flags():
    async def check():
        from card_platform.player_prices.reconciliation import (
            reconcile_prices,
            rollback_reconciliation,
        )

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = (
                    await (
                        await c.execute(
                            "INSERT INTO player(full_name,normalized_name) VALUES('Rollback fixture',%s) RETURNING id",
                            (str(uuid4()),),
                        )
                    ).fetchone()
                )["id"]
                await c.execute(
                    "INSERT INTO player_price_sale(source_id,player_id,card_key,identity,sale_price,occurred_at,source_url,raw_title) VALUES('fixture:rollback',%s,'old','{}',100,'2026-10-10','https://example.com','original')",
                    (player,),
                )
                await reconcile_prices(
                    c,
                    player,
                    [
                        dict(
                            source_id="fixture:rollback",
                            card_key="bad",
                            identity={},
                            sale_price="999",
                            occurred_at="2026-10-10T00:00:00+00:00",
                            source_url="https://example.com",
                            raw_title="original",
                        )
                    ],
                    date(2026, 10, 21),
                )
                audit = (
                    await (
                        await c.execute(
                            "SELECT id FROM player_price_evidence_audit WHERE player_id=%s",
                            (player,),
                        )
                    ).fetchone()
                )["id"]
                await c.execute(
                    "UPDATE player_price_sale SET sale_price=101 WHERE source_id='fixture:rollback'"
                )
                with pytest.raises(ValueError, match="changed_since"):
                    await rollback_reconciliation(c, audit)
                await c.execute(
                    "UPDATE player_price_sale SET sale_price=100 WHERE source_id='fixture:rollback'"
                )
                assert await rollback_reconciliation(c, audit)
                row = await (
                    await c.execute(
                        "SELECT sale_price,excluded FROM player_price_sale WHERE source_id='fixture:rollback'"
                    )
                ).fetchone()
                assert str(row["sale_price"]) == "100.00" and row["excluded"] is False
                assert (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM player_price_source_conflict WHERE source_id='fixture:rollback'"
                        )
                    ).fetchone()
                )["n"] == 0
            finally:
                await c.rollback()

    asyncio.run(check())


def test_repair_is_atomic_even_on_an_autocommit_connection():
    async def check():
        from card_platform.player_prices.reconciliation import reconcile_prices

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], autocommit=True, row_factory=dict_row
        ) as c:
            player = (
                await (
                    await c.execute(
                        "INSERT INTO player(full_name,normalized_name) VALUES('Atomic fixture',%s) RETURNING id",
                        (str(uuid4()),),
                    )
                ).fetchone()
            )["id"]
            try:
                await c.execute(
                    "INSERT INTO player_price_sale(source_id,player_id,card_key,identity,sale_price,occurred_at,source_url,raw_title) VALUES('fixture:atomic-old',%s,'old','{}',100,'2026-10-10','https://example.com','original')",
                    (player,),
                )
                incoming = dict(
                    source_id="fixture:atomic-new",
                    card_key="new",
                    identity={
                        "image_evidence": {"receipt_sha256": "a" * 64},
                        "bad_value": object(),
                    },
                    sale_price="100",
                    occurred_at="2026-10-10T00:00:00+00:00",
                    source_url="https://example.com",
                    raw_title="original",
                )
                with pytest.raises(TypeError):
                    await reconcile_prices(c, player, [incoming], date(2026, 10, 21))
                assert (
                    await (
                        await c.execute(
                            "SELECT excluded FROM player_price_sale WHERE source_id='fixture:atomic-old'"
                        )
                    ).fetchone()
                )["excluded"] is False
            finally:
                for table in [
                    "player_price_evidence_audit",
                    "player_price_evidence_revision",
                    "player_price_sale",
                ]:
                    await c.execute(f"DELETE FROM {table} WHERE player_id=%s", (player,))
                await c.execute("DELETE FROM player WHERE id=%s", (player,))

    asyncio.run(check())


def test_rollback_does_not_delete_a_report_computed_after_the_repair():
    async def check():
        from card_platform.player_prices.reconciliation import (
            reconcile_prices,
            rollback_reconciliation,
        )
        from card_platform.player_prices.repository import PriceRepository

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = (
                    await (
                        await c.execute(
                            "INSERT INTO player(full_name,normalized_name) VALUES('Later report fixture',%s) RETURNING id",
                            (str(uuid4()),),
                        )
                    ).fetchone()
                )["id"]
                await reconcile_prices(c, player, [], date(2026, 10, 21))
                audit = (
                    await (
                        await c.execute(
                            "SELECT id FROM player_price_evidence_audit WHERE player_id=%s",
                            (player,),
                        )
                    ).fetchone()
                )["id"]
                await PriceRepository().compute(c, player, date(2026, 10, 22))
                with pytest.raises(ValueError, match="changed_since"):
                    await rollback_reconciliation(c, audit)
                assert (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM player_price_snapshot WHERE player_id=%s",
                            (player,),
                        )
                    ).fetchone()
                )["n"] == 1
            finally:
                await c.rollback()

    asyncio.run(check())


def test_changed_transaction_facts_remain_excluded_and_are_never_overwritten():
    async def check():
        from card_platform.player_prices.reconciliation import reconcile_prices

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = (
                    await (
                        await c.execute(
                            "INSERT INTO player(full_name,normalized_name) VALUES('Price repair fixture',%s) RETURNING id",
                            (str(uuid4()),),
                        )
                    ).fetchone()
                )["id"]
                await c.execute(
                    "INSERT INTO player_price_sale(source_id,player_id,card_key,identity,sale_price,occurred_at,source_url,raw_title) VALUES('fixture:conflict',%s,'old','{}',100,'2026-10-10','https://example.com','original')",
                    (player,),
                )
                incoming = dict(
                    source_id="fixture:conflict",
                    card_key="new",
                    identity={},
                    sale_price="999",
                    occurred_at="2026-10-10T00:00:00+00:00",
                    source_url="https://example.com",
                    raw_title="original",
                )
                result = await reconcile_prices(c, player, [incoming], date(2026, 10, 21))
                assert result["admitted_count"] == 0 and result["blocked_sources"] == [
                    "fixture:conflict"
                ]
                saved = await (
                    await c.execute(
                        "SELECT sale_price,excluded FROM player_price_sale WHERE source_id='fixture:conflict'"
                    )
                ).fetchone()
                assert str(saved["sale_price"]) == "100.00" and saved["excluded"] is True
                assert (
                    await (
                        await c.execute(
                            "SELECT count(*) AS n FROM player_price_source_conflict WHERE source_id='fixture:conflict'"
                        )
                    ).fetchone()
                )["n"] == 1
            finally:
                await c.rollback()

    asyncio.run(check())


def test_new_sample_preserves_existing_reports_but_invalidated_source_clears_affected_reports():
    async def check():
        from card_platform.player_prices.reconciliation import reconcile_prices

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                player = (
                    await (
                        await c.execute(
                            "INSERT INTO player(full_name,normalized_name) VALUES('Snapshot preservation fixture',%s) RETURNING id",
                            (str(uuid4()),),
                        )
                    ).fetchone()
                )["id"]
                await reconcile_prices(c, player, [], date(2026, 10, 21))
                await c.execute(
                    "INSERT INTO player_price_snapshot(player_id,report_date,result) VALUES(%s,'2026-10-21',%s)",
                    (player, Jsonb({"status": "ready", "change_pct": "5"})),
                )
                incoming = dict(
                    source_id="fixture:preserve",
                    card_key="verified",
                    identity={"image_evidence": {"receipt_sha256": "a" * 64}},
                    sale_price="100",
                    occurred_at="2026-10-20T00:00:00+00:00",
                    source_url="https://example.com",
                    raw_title="original",
                )
                await reconcile_prices(c, player, [incoming], date(2026, 10, 21))
                saved = await (
                    await c.execute(
                        "SELECT result FROM player_price_snapshot WHERE player_id=%s", (player,)
                    )
                ).fetchall()
                assert saved == [{"result": {"status": "ready", "change_pct": "5"}}]
                # The same previously valid sale now fails proof: stale ready
                # reports in its two overlapping seven-day windows must go.
                await reconcile_prices(c, player, [], date(2026, 10, 21))
                assert not await (
                    await c.execute(
                        "SELECT result FROM player_price_snapshot WHERE player_id=%s", (player,)
                    )
                ).fetchall()
            finally:
                await c.rollback()

    asyncio.run(check())


@pytest.mark.parametrize("photo_reuse", [False, True])
def test_conflicting_or_reused_source_repairs_and_audits_its_previous_owner(photo_reuse):
    async def check():
        from card_platform.player_prices.reconciliation import (
            reconcile_prices,
            rollback_reconciliation,
        )

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as c:
            try:
                players = []
                for name in ["Previous owner fixture", "New owner fixture"]:
                    players.append(
                        (
                            await (
                                await c.execute(
                                    "INSERT INTO player(full_name,normalized_name) VALUES(%s,%s) RETURNING id",
                                    (name, str(uuid4())),
                                )
                            ).fetchone()
                        )["id"]
                    )
                a, b = players
                source_day = date(2026, 7, 24) if photo_reuse else date(2026, 10, 20)
                snapshot_day = date(2026, 7, 25) if photo_reuse else date(2026, 10, 21)
                report_day = date(2026, 10, 22) if photo_reuse else date(2026, 10, 21)
                await reconcile_prices(c, a, [], date(2026, 10, 21))
                await reconcile_prices(c, b, [], date(2026, 10, 21))
                await c.execute(
                    "INSERT INTO player_price_sale(source_id,player_id,card_key,identity,sale_price,occurred_at,source_url,raw_title) VALUES('fixture:old-owner',%s,'old','{}',100,%s,'https://example.com','original')",
                    (a, source_day),
                )
                await c.execute(
                    "INSERT INTO player_price_snapshot(player_id,report_date,result) VALUES(%s,%s,%s)",
                    (a, snapshot_day, Jsonb({"status": "ready", "change_pct": "5"})),
                )
                incoming = dict(
                    source_id="fixture:old-owner",
                    card_key="bad",
                    identity={},
                    sale_price="999",
                    occurred_at="2026-10-20T00:00:00+00:00",
                    source_url="https://example.com",
                    raw_title="original",
                )
                result = await reconcile_prices(
                    c,
                    b,
                    [] if photo_reuse else [incoming],
                    report_day,
                    photo_reuse_sources=["fixture:old-owner"] if photo_reuse else [],
                )
                assert result["blocked_sources"] == ([] if photo_reuse else ["fixture:old-owner"])
                assert not await (
                    await c.execute(
                        "SELECT result FROM player_price_snapshot WHERE player_id=%s", (a,)
                    )
                ).fetchall()
                audit = await (
                    await c.execute(
                        "SELECT * FROM player_price_evidence_audit WHERE player_id=%s ORDER BY id DESC LIMIT 1",
                        (b,),
                    )
                ).fetchone()
                assert str(a) in audit["before_state"]["affected_player_ids"]
                assert audit["before_state"]["sales"][0]["excluded"] is False
                if photo_reuse:
                    assert audit["before_state"]["photo_reuse_withdrawals"] == ["fixture:old-owner"]
                    assert not await (
                        await c.execute(
                            "SELECT * FROM player_price_source_conflict WHERE source_id='fixture:old-owner'"
                        )
                    ).fetchall()
                    stored = await (
                        await c.execute(
                            "SELECT * FROM player_price_sale WHERE source_id='fixture:old-owner'"
                        )
                    ).fetchone()
                    assert stored["excluded"] and stored["sale_price"] == 100
                    assert stored["raw_title"] == "original"
                assert await rollback_reconciliation(c, audit["id"])
                assert (
                    await (
                        await c.execute(
                            "SELECT result FROM player_price_snapshot WHERE player_id=%s", (a,)
                        )
                    ).fetchone()
                )["result"]["status"] == "ready"
            finally:
                await c.rollback()

    asyncio.run(check())


@pytest.mark.parametrize("seal_with_compute", [False, True])
def test_repair_seal_cannot_be_rewritten_by_a_later_transaction(seal_with_compute):
    async def check():
        from card_platform.player_prices.reconciliation import (
            reconcile_prices,
            finalize_reconciliation,
            rollback_reconciliation,
        )
        from card_platform.player_prices.repository import PriceRepository

        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], autocommit=True, row_factory=dict_row
        ) as c:
            player = (
                await (
                    await c.execute(
                        "INSERT INTO player(full_name,normalized_name) VALUES('Seal transaction fixture',%s) RETURNING id",
                        (str(uuid4()),),
                    )
                ).fetchone()
            )["id"]
            try:
                async with c.transaction():
                    repaired = await reconcile_prices(c, player, [], date(2026, 10, 21))
                    await PriceRepository().compute(c, player, date(2026, 10, 21))
                    if seal_with_compute:
                        await finalize_reconciliation(c, repaired["audit_id"])
                # A later caller must not bless newly created reports by
                # reusing an old repair audit to bypass rollback protection.
                with pytest.raises(ValueError, match="finalize_transaction"):
                    async with c.transaction():
                        await finalize_reconciliation(c, repaired["audit_id"])
                if seal_with_compute:
                    assert await rollback_reconciliation(c, repaired["audit_id"])
                    assert not await (
                        await c.execute(
                            "SELECT * FROM player_price_snapshot WHERE player_id=%s", (player,)
                        )
                    ).fetchall()
                else:
                    with pytest.raises(ValueError, match="changed_since"):
                        await rollback_reconciliation(c, repaired["audit_id"])
            finally:
                for table in [
                    "player_price_evidence_audit",
                    "player_price_evidence_revision",
                    "player_price_snapshot",
                    "player_price_basket",
                    "player_price_sale",
                ]:
                    await c.execute(f"DELETE FROM {table} WHERE player_id=%s", (player,))
                await c.execute("DELETE FROM player WHERE id=%s", (player,))

    asyncio.run(check())
