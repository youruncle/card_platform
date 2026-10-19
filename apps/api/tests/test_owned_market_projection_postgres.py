"""The SQL read path withholds owned evidence without rewriting snapshots."""
import asyncio
import os
from decimal import Decimal
from uuid import uuid4
import pytest
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from card_platform.repository import PortfolioRepository, build_portfolio_summary
from card_platform.schemas import CardIdentityInput, CollectionItemCreate

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires migrated isolated PostgreSQL")


@pytest.mark.parametrize("price", ["4.00", "64.32", "7.99", "70.00"])
def test_current_owned_purchase_source_cannot_be_market_price_or_shadow(price):
    asyncio.run(check_case(price, owned=True, want=None))


def test_persisted_owned_boolean_without_current_binding_is_not_ownership():
    asyncio.run(check_case("12.00", owned=False, want=Decimal("12.00")))


def test_explicit_manual_price_remains_user_authority():
    asyncio.run(check_case("25.00", owned=True, want=Decimal("25.00"), method="manual"))


def test_codex_historical_reference_is_not_reclassified_as_new_market_output():
    asyncio.run(check_case("18.00", owned=True, want=Decimal("18.00"), model="codex-researched-fallback-v2"))


@pytest.mark.parametrize("status", ["queued", "failed"])
def test_withholding_old_snapshot_preserves_current_job_status_and_error(status):
    asyncio.run(check_case("14.00", owned=True, want=None, job_status=status))


@pytest.mark.parametrize("evidence_key", ["used_comparables", "comparables"])
def test_market_center_contract_checks_both_published_snapshot_shapes(evidence_key):
    asyncio.run(check_case("12.00", owned=True, want=None,
                           evidence_key=evidence_key, center=True))


@pytest.mark.parametrize("evidence_key", ["used_comparables", "comparables"])
def test_unused_owned_context_does_not_withhold_independent_market_center(evidence_key):
    asyncio.run(check_case("12.00", owned=True, want=Decimal("12.00"),
                           evidence_key=evidence_key, center=False))


async def check_case(price, *, owned, want, method="ai_estimate", model="relative-multimodal-comps-1.0", job_status="succeeded", evidence_key="used_comparables", center=None):
    async with await psycopg.AsyncConnection.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as c:
        try:
            owner = (await (await c.execute("INSERT INTO app_user(email,display_name) VALUES(%s,'projection test') RETURNING id", (f"{uuid4()}@example.test",))).fetchone())["id"]
            repo = PortfolioRepository()
            item = await repo.create_item(c, CollectionItemCreate(owner_id=owner, card=CardIdentityInput(player_name=f"Projection {uuid4()}", brand_name="Test", product_name="Test", season_start=2025)))
            identity_version = (await (await c.execute(
                "INSERT INTO card_identity_version(collection_item_id,version,identity,review_status) "
                "VALUES(%s,1,'{}','confirmed') RETURNING id", (item,))).fetchone())["id"]
            await c.execute("DELETE FROM valuation_job WHERE collection_item_id=%s", (item,))
            job = (await (await c.execute("INSERT INTO valuation_job(collection_item_id,status,created_at) VALUES(%s,'succeeded',now()-interval '1 second') RETURNING id", (item,))).fetchone())["id"]
            source = "https://cardhobby.com.cn/Market/Item/123456"
            evidence = {"automatic_publication": True, "used_comparables": [{"source_url": source, "tier": "T0", "classification": {"field_matches": {"owned_public_auction_history": True, "formal_cardhobby_contract": "same-card-source-revalidated-v22"}}}]}
            if center is not None:
                entries = evidence.pop("used_comparables")
                entries[0].update(used_for_center=center, final_weight="1" if center else "0")
                if not center:
                    entries.append({"source_url": "https://cardhobby.com.cn/Market/Item/789789",
                                    "used_for_center": True, "final_weight": "1"})
                evidence[evidence_key] = entries
            snapshot = (await (await c.execute("""INSERT INTO valuation_snapshot(collection_item_id,method,currency,low_value,fair_value,high_value,confidence,model_version,evidence) VALUES(%s,%s,'CNY',%s,%s,%s,.45,%s,%s) RETURNING id""", (item,method,price,price,price,model,Jsonb(evidence)))).fetchone())["id"]
            await c.execute("""INSERT INTO valuation_shadow_result(valuation_job_id,collection_item_id,identity_version_id,model_version,status,currency,low_value,fair_value,high_value,confidence,evidence) VALUES(%s,%s,%s,'robust-shadow-1.1','estimated','CNY',%s,%s,%s,.45,'{}')""", (job,item,identity_version,price,price,price))
            if owned:
                tx = (await (await c.execute("INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,fee_amount,currency,fx_rate_to_base) VALUES(%s,'buy',now(),1,10,0,'CNY',1) RETURNING id", (item,))).fetchone())["id"]
                order = (await (await c.execute("INSERT INTO cardhobby_purchase_order(owner_id,external_order_id,capture,evidence_hash,fetched_at) VALUES(%s,%s,'{}',%s,now()) RETURNING id", (owner,str(uuid4()),'a'*64))).fetchone())["id"]
                unit = (await (await c.execute("INSERT INTO cardhobby_purchase_unit(order_id,owner_id,item_id,unit_index,title,unit_price,shipping) VALUES(%s,%s,'internal-unit',0,'test',10,0) RETURNING id", (order,owner))).fetchone())["id"]
                bound = {"item_url": source, "item_id": "internal-unit", "evidence_hash": 'a'*64}
                await c.execute("""INSERT INTO cardhobby_cost_assignment(owner_id,collection_item_id,unit_id,transaction_id,evidence_hash,card_price,shipping,confidence,evidence,confirmation) VALUES(%s,%s,%s,%s,%s,10,0,1,%s,'automatic')""", (owner,item,unit,tx,'a'*64,Jsonb(bound)))
            if job_status != "succeeded":
                await c.execute("UPDATE valuation_snapshot SET valued_at=now()-interval '2 seconds' WHERE id=%s", (snapshot,))
                await c.execute("UPDATE valuation_job SET status=%s,created_at=now()-interval '1 second',error_message='retained upstream failure' WHERE id=%s", (job_status,job))
            before = (await (await c.execute("SELECT to_jsonb(v) AS value FROM valuation_snapshot v WHERE id=%s", (snapshot,))).fetchone())["value"]
            projection = (await repo.list_items(c, owner, item_ids=[item]))[0]
            assert projection.fair_value == want
            if want is None:
                assert projection.valuation_currency is None
                assert projection.shadow_fair_value is None
                assert projection.valuation_status == ("insufficient_evidence" if job_status == "succeeded" else job_status)
                if job_status != "succeeded":
                    assert projection.valuation_error == "retained upstream failure"
                assert projection.valuation_result_summary["publication_validation"]["snapshot_id"] == str(snapshot)
                assert build_portfolio_summary([projection], owner, "CNY").valued_item_count == 0
            else:
                # The current confirmed identity must really join the shadow;
                # otherwise a null-shadow assertion on owned cases is vacuous.
                assert projection.shadow_fair_value == Decimal(price)
            after = (await (await c.execute("SELECT to_jsonb(v) AS value FROM valuation_snapshot v WHERE id=%s", (snapshot,))).fetchone())["value"]
            assert before == after
            assert after["fair_value"] == float(price)
            assert await repo.list_items(c, uuid4(), item_ids=[item]) == []
        finally:
            await c.rollback()
