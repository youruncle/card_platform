"""Real SQL allocation and occupancy, synthetic orders only."""

import asyncio
import os
from decimal import Decimal

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from card_platform.purchase_cost_service import PurchaseCostService
from card_platform.purchase_cost import OrderCapture
from test_purchase_cost_postgres import client as cost_client, register, item, order  # noqa: F401

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires migrated PostgreSQL"
)


def test_bundle_two_cards_are_allocated_once_and_survive_sync(cost_client):  # noqa: F811
    client = cost_client
    owner = register(client)
    first, second, duplicate = [item(client, owner) for _ in range(3)]
    capture = order(title="Test Player 两张打包(20)", shipping="1.23")
    capture["lines"][0]["unit_price"] = "55.00"
    capture["displayed_total"] = "56.23"
    parsed = OrderCapture.model_validate(capture)
    service = PurchaseCostService()
    decision = dict(
        contract="purchase-bundle-v1",
        source="human_paired_image_review",
        card_count=2,
        matched_index=1,
        complete=True,
        unique_match=True,
        verdict="same",
        confidence=".95",
        design_match="same",
        finish_match="same",
        reasons=["paired image observation"],
    )

    async def run():
        async with await AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as conn:
            await service.sync(conn, owner, [parsed])
            for card_id, index in [(first, 1), (second, 2), (duplicate, 1)]:
                card = await service.card(conn, owner, card_id)
                await service.store_bundle_observation(
                    conn,
                    owner,
                    card,
                    parsed,
                    parsed.lines[0].model_dump(mode="json"),
                    {**decision, "matched_index": index},
                )
            for card_id, price, fee in [(first, "27.5", ".62"), (second, "27.5", ".61")]:
                preview = await service.candidates(conn, owner, card_id)
                candidates = [x for x in preview["candidates"] if x["automatic"]]
                assert len(candidates) == 1
                candidate = candidates[0]
                assert Decimal(candidate["card_price"]) == Decimal(price)
                assert Decimal(candidate["shipping"]) == Decimal(fee)
                await service.confirm(
                    conn, owner, card_id, candidate["unit_id"], candidate["evidence_hash"], True
                )
            assert not (await service.candidates(conn, owner, duplicate))["candidates"]
            await service.sync(conn, owner, [parsed])
            assert (await service.candidates(conn, owner, first, refresh=True))["candidates"][0][
                "automatic"
            ]
            row = await conn.execute(
                "SELECT sum(unit_price) price,sum(fee_amount) fee FROM card_transaction WHERE collection_item_id=ANY(%s::uuid[])",
                ([first, second],),
            )
            assert await row.fetchone() == {"price": Decimal("55"), "fee": Decimal("1.23")}
            changed = parsed.model_copy(update={"paid_total": Decimal("56.24")})
            await service.sync(conn, owner, [changed])
            assert not (await service.candidates(conn, owner, first, refresh=True))["candidates"]
            # Even if a later capture drops the bundle wording, its occupied parent
            # must stay unavailable; an assignment survives evidence invalidation.
            changed = OrderCapture.model_validate({**capture, 'lines': [
                {**capture['lines'][0], 'title': '2025 Test Player'}]})
            await service.sync(conn, owner, [changed])
            parent = await conn.execute("SELECT id,active FROM cardhobby_purchase_unit WHERE owner_id=%s AND bundle_index=0", (owner,))
            parent = await parent.fetchone()
            assert not parent['active']
            # Defensive check also protects against stale/reactivated parent state.
            await conn.execute("UPDATE cardhobby_purchase_unit SET active=true WHERE id=%s", (parent['id'],))
            assert not (await service.candidates(conn, owner, duplicate))['candidates']
            from card_platform.purchase_cost_service import ConflictError
            from card_platform.purchase_cost import evidence_hash
            with pytest.raises(ConflictError):
                await service.confirm(conn, owner, duplicate, parent['id'], evidence_hash(changed), True)


    asyncio.run(run())


def test_live_search_splits_verified_bundle_and_rejects_changed_count(cost_client, monkeypatch):  # noqa: F811
    client = cost_client
    owner = register(client)
    card_id = item(client, owner)
    capture = order(title="2025 Test Player 两张打包(20)", shipping="0")
    capture["lines"][0]["image_urls"] = ["https://images.cardhobby.com/fixture.jpg"]
    decision = dict(
        contract="purchase-bundle-v1",
        source="human_paired_image_review",
        card_count=2,
        matched_index=1,
        complete=True,
        unique_match=True,
        verdict="same",
        confidence=".95",
        design_match="same",
        finish_match="same",
        reasons=["observed complete two-card bundle"],
    )

    async def observe(self, card, line):
        return decision

    # Only the external image/model boundary is replaced; sync/matching/SQL/ledger remain real.
    monkeypatch.setattr(
        "card_platform.purchase_bundle_verifier.PurchaseBundleVerifier.observe", observe
    )
    response = client.post(
        f"/v1/purchase-cost/items/{card_id}/search-result",
        json={"orders": [capture], "complete": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "recorded"
    assert Decimal(response.json()["recorded"]["card_price"]) == Decimal("5")

    async def changed_count():
        async with await AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as conn:
            service = PurchaseCostService()
            card = await service.card(conn, owner, card_id)
            parsed = OrderCapture.model_validate(capture)
            assert not await service.store_bundle_observation(
                conn,
                owner,
                card,
                parsed,
                parsed.lines[0].model_dump(mode="json"),
                {**decision, "card_count": 3},
            )
            # An existing manually enumerated lot cannot be silently remapped by
            # a new observation with a different image coordinate layout.
            assert not await service.store_bundle_observation(
                conn, owner, card, parsed, parsed.lines[0].model_dump(mode='json'),
                {**decision, 'card_boxes': [[0, 0, .4, 1], [.6, 0, 1, 1]],
                 'image_hashes': {'candidate': 'new-layout'}},
            )

    asyncio.run(changed_count())
