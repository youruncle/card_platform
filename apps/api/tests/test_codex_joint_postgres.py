"""Read-time historical recovery, ledger totals, immutable sources and owner isolation."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from card_platform.repository import PortfolioRepository, build_portfolio_summary
from card_platform.schemas import CardIdentityInput, CollectionItemCreate, TransactionCreate
from test_codex_joint_valuation import fixture

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires migrated test PostgreSQL"
)


def test_legacy_history_remains_reference_without_price_or_snapshot_writes(monkeypatch):
    asyncio.run(check(monkeypatch))


async def check(monkeypatch):
    item_data, snapshot, reviews, fx = fixture()
    today = datetime.now(timezone.utc).date().isoformat()
    fx["date"] = today
    for review in reviews.values():
        review.update(checked_at=today, sold_at=today)
    monkeypatch.setattr(
        "card_platform.repository.current_image_hashes",
        lambda _: {"front": "front", "back": "back"},
    )
    monkeypatch.setattr(
        "card_platform.repository.recovery_reviews", lambda: {"sources": reviews, "fx": fx}
    )
    async with await psycopg.AsyncConnection.connect(
        os.environ["TEST_DATABASE_URL"], row_factory=dict_row
    ) as c:
        try:
            owner = (
                await (
                    await c.execute(
                        "INSERT INTO app_user(email,display_name) VALUES(%s,'joint-test') RETURNING id",
                        (f"{uuid4()}@example.com",),
                    )
                ).fetchone()
            )["id"]
            repo = PortfolioRepository()
            item = await repo.create_item(
                c,
                CollectionItemCreate(
                    owner_id=owner,
                    card=CardIdentityInput(
                        player_name="Stitch",
                        brand_name="Topps",
                        season_start=2023,
                        product_name="Topps Chrome",
                        card_number="SC-6",
                        insert_name="Stitch in Costume",
                    ),
                ),
            )
            await c.execute(
                "UPDATE collection_item SET image_front_url=%s,image_back_url=%s WHERE id=%s",
                (item_data["image_front_url"], item_data["image_back_url"], item),
            )
            await c.execute('DELETE FROM valuation_job WHERE collection_item_id=%s', (item,))
            await repo.add_transaction(
                c,
                item,
                owner,
                TransactionCreate(
                    transaction_type="buy",
                    transacted_at=datetime.now(timezone.utc),
                    quantity=2,
                    unit_price=10,
                    currency="CNY",
                ),
            )
            snapshot["raw_result"]["collection_item_id"] = str(item)
            # Newest version has no source; recover the source from compatible older history.
            for version in [1, 2]:
                job = (
                    await (
                        await c.execute(
                            "INSERT INTO card_analysis_job(owner_id,collection_item_id,status) VALUES(%s,%s,'succeeded') RETURNING id",
                            (owner, item),
                        )
                    ).fetchone()
                )["id"]
                await c.execute(
                    """INSERT INTO card_analysis_snapshot(job_id,collection_item_id,analysis_version,source,model_name,identity_summary,identity_confidence,currency,fair_value_low,fair_value,fair_value_high,valuation_confidence,raw_result,evidence_summary,status)
                VALUES(%s,%s,%s,'codex_multimodal','test','legacy',.97,'CNY',40,50,60,.3,%s,%s,'succeeded')""",
                    (
                        job,
                        item,
                        version,
                        Jsonb(snapshot["raw_result"]),
                        Jsonb(snapshot["evidence_summary"] if version == 1 else []),
                    ),
                )
            before = await (
                await c.execute(
                    "SELECT to_jsonb(s) AS data FROM card_analysis_snapshot s WHERE collection_item_id=%s ORDER BY analysis_version",
                    (item,),
                )
            ).fetchall()
            result = (await repo.list_items(c, owner, item_ids=[item]))[0]
            assert result.fair_value is None, result.valuation_synthesis
            assert result.codex_fair_value == Decimal("50")
            assert result.valuation_synthesis["status"] == "review_required"
            assert result.valuation_synthesis["accepted_evidence"] == []
            assert result.quantity_held == 2
            assert result.unrealized_profit is None
            summary = build_portfolio_summary([result], owner, "CNY")
            assert summary.valued_item_count == 0
            assert await repo.list_items(c, uuid4(), item_ids=[item]) == []
            after = await (
                await c.execute(
                    "SELECT to_jsonb(s) AS data FROM card_analysis_snapshot s WHERE collection_item_id=%s ORDER BY analysis_version",
                    (item,),
                )
            ).fetchall()
            assert before == after
            # A prior confirmation must not erase a later manual limited-edition edit.
            await c.execute(
                "INSERT INTO card_identity_version(collection_item_id,version,identity,review_status) VALUES(%s,1,%s,'confirmed')",
                (item, Jsonb({"serial_total": None})),
            )
            await c.execute("UPDATE collection_item SET serial_total=25 WHERE id=%s", (item,))
            changed = (await repo.list_items(c, owner, item_ids=[item]))[0]
            assert changed.serial_total == 25
            assert changed.fair_value is None
            assert changed.valuation_synthesis["status"] == "review_required"
            assert any("人工确认" in reason for reason in changed.valuation_synthesis["reasons"])
        finally:
            await c.rollback()
