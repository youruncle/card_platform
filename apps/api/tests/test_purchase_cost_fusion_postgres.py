"""The real service must use OCR discovery and rebuild, not bypass, legacy scope."""

import asyncio
import copy
import os
from uuid import UUID
import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from card_platform.config import get_settings
from card_platform.purchase_cost import OrderCapture
from card_platform.purchase_cost_archive import PurchaseArchive
from card_platform.purchase_cost_images import PurchaseImageObserver
from card_platform.purchase_cost_service import FIELD_GATE_KEY, PurchaseCostService
from purchase_cost_image_fixtures import paired_observation
from test_purchase_cost_multi_field_postgres import capture
from test_purchase_cost_postgres import client as _postgres_client, register

client = _postgres_client
pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires isolated PostgreSQL"
)


@pytest.mark.parametrize(
    "change",
    [
        "complete",
        "removed",
        "missing_image",
        "observation_failure",
        "occupied",
        "occupied_different",
        "occupied_uncertain",
        "occupied_integrity",
        "text_automatic_uncertain",
    ],
)
def test_legacy_field_rebuild_proves_history_and_still_requires_all_pairs(
    client, monkeypatch, change
):
    owner = UUID(register(client))
    response = client.post(
        "/v1/portfolio/items",
        json=dict(
            owner_id=str(owner),
            card=dict(
                player_name="Example Player",
                brand_name="Topps",
                product_name="Chrome",
                season_start=2025,
                card_number="Fixture-17",
            ),
            serial_number=26,
            serial_total=75,
            is_numbered=True,
        ),
    )
    assert response.status_code == 201, response.text
    item = UUID(response.json()["id"])
    (get_settings().resolved_upload_dir / "fusion.jpg").write_bytes(b"bound-observer-fixture")
    original = capture().model_dump(mode="json")
    if change in ("removed", "missing_image", "occupied", "occupied_different", "occupied_uncertain", "occupied_integrity"):
        other = capture(1).lines[0].model_dump(mode="json")
        original["lines"].append(other)
        original["displayed_total"] = "21.00"
    if change == "text_automatic_uncertain":
        original["lines"][0]["title"] = "2025 Topps Chrome Example Player #Fixture-17 26/75"
    other_item = None
    if change.startswith("occupied"):
        created_other = client.post(
            "/v1/portfolio/items",
            json=dict(
                owner_id=str(owner),
                card=dict(
                    player_name="Other Player",
                    brand_name="Topps",
                    product_name="Chrome",
                    season_start=2025,
                ),
            ),
        )
        assert created_other.status_code == 201
        other_item = UUID(created_other.json()["id"])
    current = copy.deepcopy(original)
    if change == "removed":
        current["lines"].pop()
        current["displayed_total"] = "11.00"
    if change == "missing_image":
        current["lines"][1]["image_urls"] = []

    observed = []

    async def observe(self, card, line, order_hash):
        observed.append(line["item_id"])
        if change == "observation_failure":
            raise TimeoutError("fixture")
        d = paired_observation(card, line, order_hash)
        if change.startswith("occupied") and line["item_id"] == original["lines"][1]["item_id"]:
            if change in ("occupied_different", "occupied_integrity"):
                for f in d["observations"]["candidate_fields"]:
                    if f["kind"] == "card_number":
                        f["text"] = f["value"] = "Other-18"
                d = {**d, "verdict": "different", "reasons": ["printed_card_number_conflict"]}
                if change == "occupied_integrity":
                    d = {**d, "verdict": "uncertain", "reasons": ["repair_dropped_identity_observation"]}
            elif change == "occupied_uncertain":
                d = {**d, "verdict": "uncertain", "reasons": ["independent_finish_unconfirmed"]}
        if change == "text_automatic_uncertain":
            d = {**d, "verdict": "uncertain", "reasons": ["independent_finish_unconfirmed"]}
        return d

    monkeypatch.setattr(PurchaseImageObserver, "observe", observe)
    from card_platform.cardhobby_account_api import account_service

    async def fresh_archive(*args, **kwargs):
        return [current]

    monkeypatch.setattr(type(account_service()), "archive_orders", fresh_archive)
    a = PurchaseArchive.configured(get_settings(), owner)
    a.save_page(dict(account_key="a" * 64, orders=[current], complete=True, next_cursor=None), None)

    async def run():
        async with await psycopg.AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as conn:
            await conn.execute(
                "UPDATE collection_item SET image_front_url=%s WHERE id=%s",
                ("/uploads/fusion.jpg", item),
            )
            service = PurchaseCostService()
            await service.sync(conn, owner, [OrderCapture.model_validate(original)])
            if change.startswith("occupied"):
                unit = await (
                    await conn.execute(
                        "SELECT u.id,o.evidence_hash FROM cardhobby_purchase_unit u JOIN cardhobby_purchase_order o ON o.id=u.order_id WHERE u.owner_id=%s AND u.item_id=%s",
                        (owner, original["lines"][1]["item_id"]),
                    )
                ).fetchone()
                tx = await (
                    await conn.execute(
                        "INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,fee_amount,currency) VALUES(%s,'buy',now(),1,10,0,'CNY') RETURNING id",
                        (other_item,),
                    )
                ).fetchone()
                await conn.execute(
                    "INSERT INTO cardhobby_cost_assignment(owner_id,collection_item_id,unit_id,evidence_hash,confirmation,transaction_id,evidence,card_price,shipping,confidence) VALUES(%s,%s,%s,%s,'automatic',%s,%s,10,0,1)",
                    (owner, other_item, unit["id"], unit["evidence_hash"], tx["id"], Jsonb({})),
                )
            await conn.execute(
                "INSERT INTO cardhobby_cost_visual_inference(owner_id,collection_item_id,fingerprint,decision) VALUES(%s,%s,%s,%s)",
                (
                    owner,
                    item,
                    FIELD_GATE_KEY,
                    Jsonb(
                        dict(
                            source="cost_candidate_field",
                            status="incomplete",
                            pending_order_ids=[original["external_order_id"]],
                        )
                    ),
                ),
            )
            result = await service.resolve_search(
                conn, owner, item, [OrderCapture.model_validate(current)], True
            )
            transactions = await (
                await conn.execute(
                    "SELECT id FROM card_transaction WHERE collection_item_id=%s", (item,)
                )
            ).fetchall()
            gate = await (
                await conn.execute(
                    "SELECT decision FROM cardhobby_cost_visual_inference WHERE owner_id=%s AND collection_item_id=%s AND fingerprint=%s",
                    (owner, item, FIELD_GATE_KEY),
                )
            ).fetchone()
            if change in ("complete", "occupied_different"):
                assert result["status"] == "recorded", result
                assert len(transactions) == 1 and gate is None
            else:
                assert result["status"] == "unmatched", result
                assert transactions == [] and gate
                assert gate["decision"]["pending_item_refs"]
                if change == "occupied":
                    assert (
                        dict(
                            order_id=original["external_order_id"],
                            item_id=original["lines"][1]["item_id"],
                        )
                        in gate["decision"]["pending_item_refs"]
                    )
            if change in ("occupied", "occupied_uncertain", "occupied_integrity", "text_automatic_uncertain"):
                retried = await service.resolve_search(
                    conn, owner, item, [OrderCapture.model_validate(current)], True
                )
                assert retried["status"] == "unmatched", retried
                assert not await (
                    await conn.execute(
                        "SELECT id FROM card_transaction WHERE collection_item_id=%s", (item,)
                    )
                ).fetchall()
            if change.startswith("occupied"):
                assert len(await (await conn.execute(
                    "SELECT id FROM card_transaction WHERE collection_item_id=%s", (other_item,)
                )).fetchall()) == 1
                assert (await (await conn.execute(
                    "SELECT collection_item_id FROM cardhobby_cost_assignment WHERE unit_id=%s", (unit["id"],)
                )).fetchone())["collection_item_id"] == other_item
            if change == "occupied_different":
                assert original["lines"][1]["item_id"] in observed
            await conn.commit()

    asyncio.run(run())
