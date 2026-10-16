"""Exercise actual SQL selection, synthesis, summary and account isolation."""

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from types import SimpleNamespace
from card_platform.valuation_worker import AutomatedValuationWorker

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from card_platform.repository import PortfolioRepository, build_portfolio_summary
from card_platform.schemas import CardIdentityInput, CollectionItemCreate

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires migrated test PostgreSQL"
)


def test_model_quotes_without_joint_identity_do_not_publish_and_snapshots_are_preserved():
    asyncio.run(check())


async def check():
    async with await psycopg.AsyncConnection.connect(
        os.environ["TEST_DATABASE_URL"], row_factory=dict_row
    ) as c:
        try:
            owner = (
                await (
                    await c.execute(
                        "INSERT INTO app_user(email,display_name) VALUES(%s,'test') RETURNING id",
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
                        player_name=f"Test {uuid4()}",
                        brand_name="Test",
                        season_start=2025,
                        product_name="Test",
                    ),
                ),
            )
            identity = (
                await (
                    await c.execute(
                        "INSERT INTO card_identity_version(collection_item_id,version,identity,review_status) "
                        "VALUES(%s,1,'{}','confirmed') RETURNING id",
                        (item,),
                    )
                ).fetchone()
            )["id"]
            await c.execute(
                "INSERT INTO valuation_job(collection_item_id,status) VALUES(%s,'insufficient_evidence')",
                (item,),
            )
            job = (
                await (
                    await c.execute(
                        "INSERT INTO card_analysis_job(owner_id,collection_item_id,status) VALUES(%s,%s,'succeeded') RETURNING id",
                        (owner, item),
                    )
                ).fetchone()
            )["id"]
            await c.execute(
                """INSERT INTO card_analysis_snapshot(job_id,collection_item_id,
                analysis_version,source,model_name,identity_summary,identity_confidence,
                identity_version_id,currency,fair_value_low,fair_value,fair_value_high,
                valuation_confidence,evidence_summary,status)
                VALUES(%s,%s,1,'codex_multimodal','test','test',.95,%s,'CNY',100,120,140,.8,%s,'succeeded')""",
                (
                    job,
                    item,
                    identity,
                    Jsonb(
                        [
                            dict(
                                type="completed_sale",
                                verified=True,
                                url="https://market.test/2",
                                price=120,
                                currency="CNY",
                                sold_at=datetime.now(timezone.utc).date().isoformat(),
                                match_type="exact",
                            )
                        ]
                    ),
                ),
            )
            result = (await repo.list_items(c, owner, item_ids=[item]))[0]
            assert result.fair_value is None
            assert result.valuation_status != "succeeded"
            assert result.valuation_synthesis["status"] == "review_required"
            assert build_portfolio_summary([result], owner, "CNY").valued_item_count == 0
            assert await repo.list_items(c, uuid4(), item_ids=[item]) == []
            await AutomatedValuationWorker._publish_relative_valuation(
                c,
                uuid4(),
                dict(
                    collection_item_id=item,
                    base_currency="CNY",
                    confirmed_identity_version_id=identity,
                ),
                SimpleNamespace(
                    low=80,
                    fair=100,
                    high=120,
                    confidence=Decimal(".8"),
                    used_ids=["comp-1"],
                    rejected=[],
                    review_reasons=[],
                ),
                SimpleNamespace(search_queries=[], notes=[]),
                {"comp-1": {"source_url": "https://market.test/1"}},
            )
            result = (await repo.list_items(c, owner, item_ids=[item]))[0]
            assert result.fair_value == Decimal("100")
            assert result.valuation_synthesis["status"] == "review_required"
            original = await (
                await c.execute(
                    "SELECT fair_value FROM valuation_snapshot WHERE collection_item_id=%s", (item,)
                )
            ).fetchone()
            assert original["fair_value"] == Decimal("100")
            # New identity invalidates the old Codex record immediately.
            await c.execute(
                "INSERT INTO card_identity_version(collection_item_id,version,identity,review_status) VALUES(%s,2,'{}','confirmed')",
                (item,),
            )
            result = (await repo.list_items(c, owner, item_ids=[item]))[0]
            assert result.fair_value == Decimal("100")
            assert result.valuation_synthesis["status"] == "review_required"
        finally:
            await c.rollback()
