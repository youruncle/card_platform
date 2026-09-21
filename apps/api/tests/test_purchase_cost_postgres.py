"""Isolated DB integration; fixtures are invented, never real orders."""

import os
import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from card_platform.config import get_settings
from card_platform.main import app
from card_platform.purchase_cost_service import PurchaseCostService
from card_platform.purchase_cost_visual import load_purchase_visual_reviews

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires migrated PostgreSQL"
)


def order(item_id="900002", external_order_id="900001", quantity=1, shipping="1.00", title=None):
    return dict(
        external_order_id=external_order_id,
        ordered_at="2025-09-01",
        seller="Fixture Shop",
        status="已完成",
        source_url="https://cardhobby.com.cn/Order/detail/900001",
        fetched_at="2025-09-02T00:00:00Z",
        shipping=shipping,
        displayed_total=str(Decimal(10) * quantity + Decimal(shipping)),
        complete=True,
        lines=[
            dict(
                item_id=item_id,
                title=title or "2025 Topps NBA Hoops Test Player Gold Bounce House 卡号 123 7/50编",
                item_url=f"https://cardhobby.com.cn/market/item/{item_id}",
                image_urls=[],
                unit_price="10.00",
                quantity=quantity,
            )
        ],
    )


def register(client):
    result = client.post(
        "/v1/auth/register",
        json=dict(
            email=f"cost-{uuid4()}@example.com",
            password="Fixture-only-test-password-2026!",
            display_name="Cost Fixture",
            base_currency="CNY",
        ),
    )
    assert result.status_code == 201, result.text
    return result.json()["id"]


def item(client, owner, player="Test Player", source="900002", purchase=None):
    payload = dict(
        owner_id=owner,
        card=dict(
            player_name=player,
            brand_name="Topps",
            product_name="NBA Hoops",
            season_start=2025,
            insert_name="Bounce House",
            parallel_name="Gold",
            card_number="123",
            print_run=50,
        ),
        serial_number=7,
        serial_total=50,
        notes=f"https://cardhobby.com.cn/market/item/{source}",
    )
    if purchase is not None:
        payload["purchase"] = purchase
    response = client.post("/v1/portfolio/items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def preview(client, item_id):
    response = client.get(f"/v1/purchase-cost/items/{item_id}/candidates")
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, item_id, candidate):
    return client.post(
        f"/v1/purchase-cost/items/{item_id}/confirm",
        json={key: candidate[key] for key in ["unit_id", "evidence_hash"]},
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    for name, value in {
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "VISION_API_KEY": "",
        "VALUATION_WORKER_ENABLED": "false",
        "RECOGNITION_WORKER_ENABLED": "false",
        "SCANNER_WORKER_ENABLED": "false",
        "PURCHASE_COST_WORKER_ENABLED": "false",
        "UPLOAD_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def test_sync_idempotency_conservation_confirmation_and_audit(client):
    owner = register(client)
    card = item(client, owner)
    for index in range(2):
        response = client.post(
            "/v1/purchase-cost/sync", json={"orders": [order(quantity=3, shipping=".10")]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["orders"][0]["status"] == ("synced" if index == 0 else "unchanged")
    candidates = preview(client, card)["candidates"]
    assert len(candidates) == 3
    assert [c["shipping"] for c in candidates] == ["0.04", "0.03", "0.03"]
    assert confirm(client, card, candidates[0]).status_code == 200
    assert confirm(client, card, candidates[0]).json()["status"] == "already_recorded"
    detail = client.get(f"/v1/portfolio/items/{card}/detail").json()
    assert len(detail["transactions"]) == 1
    assert Decimal(detail["item"]["purchase_total_cost"]) == Decimal("10.04")
    assert Decimal(detail["item"]["remaining_cost"]) == Decimal("10.04")
    audit = client.get(f"/v1/purchase-cost/items/{card}/audit").json()
    assert audit["card_price"] == "10.00"
    assert audit["shipping"] == "0.04"
    assert not audit["source_changed"]
    client.patch(f"/v1/portfolio/items/{card}", json={"purchase_price": "12.00"})
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json()["manual_override"]


@pytest.mark.parametrize('old_price', ['0', '25'])
def test_explicit_refresh_replaces_manual_cost_without_duplicate_buy(client, old_price):
    owner = register(client)
    card = item(client, owner, purchase=dict(transacted_at='2025-01-01T00:00:00Z', price=old_price, currency='CNY'))
    original = client.get(f'/v1/portfolio/items/{card}/detail').json()['transactions'][0]
    assert client.get(f'/v1/purchase-cost/items/{card}/search-plan').json()['status'] == 'ready'
    for _ in range(2):
        result = client.post(f'/v1/purchase-cost/items/{card}/search-result', json={'orders':[order()], 'complete':True, 'refresh':True})
        assert result.status_code == 200 and result.json()['status'] == 'recorded', result.text
    transactions = client.get(f'/v1/portfolio/items/{card}/detail').json()['transactions']
    assert len(transactions) == 1 and transactions[0]['id'] == original['id']
    assert Decimal(transactions[0]['unit_price']) == Decimal('10')
    audit = client.get(f'/v1/purchase-cost/items/{card}/audit').json()
    assert audit['card_price'] == '10.00' and audit['shipping'] == '1.00'
    assert Decimal(audit['refresh_history'][-1]['transaction_before']['unit_price']) == Decimal(old_price)
    assert not audit['manual_override']


def test_explicit_refresh_can_update_its_own_assigned_order_but_not_take_another_cards_unit(client):
    owner = register(client)
    card = item(client, owner)
    path = f'/v1/purchase-cost/items/{card}/search-result'
    assert client.post(path, json={'orders':[order()], 'complete':True, 'refresh':True}).json()['status'] == 'recorded'
    changed = order()
    changed['lines'][0]['unit_price'] = '20.00'
    changed['displayed_total'] = '21.00'
    assert client.post(path, json={'orders':[changed], 'complete':True, 'refresh':True}).json()['status'] == 'recorded'
    audit = client.get(f'/v1/purchase-cost/items/{card}/audit').json()
    assert audit['card_price'] == '20.00'
    assert audit['refresh_history'][0]['assignment_before']['card_price'] == 10
    other = item(client, owner)
    result = client.post(f'/v1/purchase-cost/items/{other}/search-result', json={'orders':[changed], 'complete':True, 'refresh':True})
    assert result.json()['status'] == 'unmatched'
    assert not client.get(f'/v1/portfolio/items/{other}/detail').json()['transactions']


@pytest.mark.parametrize('mode', ['empty', 'incomplete', 'invalid', 'duplicate'])
def test_refresh_never_clears_existing_cost_without_verified_unique_match(client, mode):
    owner = register(client)
    card = item(client, owner, purchase=dict(transacted_at='2025-01-01T00:00:00Z', price='25', currency='CNY'))
    before = client.get(f'/v1/portfolio/items/{card}/detail').json()['transactions']
    orders = [] if mode == 'empty' else [order()]
    if mode == 'invalid':
        orders[0]['shipping'] = None
    if mode == 'duplicate':
        orders.append(order(external_order_id='900003'))
    result = client.post(f'/v1/purchase-cost/items/{card}/search-result', json={'orders':orders, 'complete':mode != 'incomplete', 'refresh':True})
    assert result.json()['status'] == {'empty':'not_found','incomplete':'incomplete','invalid':'unmatched','duplicate':'needs_confirmation'}[mode], result.text
    assert client.get(f'/v1/portfolio/items/{card}/detail').json()['transactions'] == before
    if mode == 'duplicate':
        selected = result.json()['candidates'][1]
        confirmed = client.post(f'/v1/purchase-cost/items/{card}/confirm', json={'unit_id':selected['unit_id'],'evidence_hash':selected['evidence_hash'],'refresh':True})
        assert confirmed.status_code == 200, confirmed.text
        assert len(client.get(f'/v1/portfolio/items/{card}/detail').json()['transactions']) == 1


def test_manual_cost_zero_protected_and_owner_isolation(client):
    owner = register(client)
    card = item(
        client,
        owner,
        purchase=dict(transacted_at="2025-01-01T00:00:00Z", price="0", currency="CNY"),
    )
    assert client.post("/v1/purchase-cost/sync", json={"orders": [order()]}).status_code == 200
    candidate = preview(client, card)["candidates"][0]
    assert confirm(client, card, candidate).status_code == 409
    client.cookies.clear()
    other = register(client)
    assert client.get(f"/v1/purchase-cost/items/{card}/candidates").status_code == 404
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").status_code == 404
    assert confirm(client, card, candidate).status_code == 404
    other_card = item(client, other)
    assert preview(client, other_card)["candidates"] == []
    assert confirm(client, other_card, candidate).status_code == 409
    assert (
        client.post(
            "/v1/purchase-cost/sync", json={"owner_id": owner, "orders": [order()]}
        ).status_code
        == 422
    )


def test_stale_candidate_and_updated_order_dont_change_existing_cost(client):
    owner = register(client)
    card = item(client, owner)
    client.post("/v1/purchase-cost/sync", json={"orders": [order()]})
    candidate = preview(client, card)["candidates"][0]
    changed = order(shipping="2")
    changed["fetched_at"] = "2025-09-03T00:00:00Z"
    assert client.post("/v1/purchase-cost/sync", json={"orders": [changed]}).status_code == 200
    assert confirm(client, card, candidate).status_code == 409
    current = preview(client, card)["candidates"][0]
    assert confirm(client, card, current).status_code == 200
    refund = {**changed, "fetched_at": "2025-09-04T00:00:00Z", "refund": "1.00"}
    client.post("/v1/purchase-cost/sync", json={"orders": [refund]})
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json()["source_changed"]
    assert Decimal(
        client.get(f"/v1/portfolio/items/{card}/detail").json()["item"]["purchase_total_cost"]
    ) == Decimal("12.00")
    assert (
        client.post("/v1/purchase-cost/sync", json={"orders": [order()]}).json()["orders"][0][
            "status"
        ]
        == "stale"
    )


def test_multiple_candidates_and_hard_conflict(client):
    owner = register(client)
    card = item(client, owner)
    orders = [
        order(),
        order("900003", "900004"),
        order(
            "900005",
            "900006",
            title="2024 Topps NBA Hoops Test Player Gold Bounce House 卡号 123 7/50编",
        ),
    ]
    assert client.post("/v1/purchase-cost/sync", json={"orders": orders}).status_code == 200
    result = preview(client, card)
    assert len(result["candidates"]) == 2
    assert len(result["rejected"]) == 1
    assert any("年份" in reason for reason in result["rejected"][0]["reasons"])


async def process_steps(count=10):
    async with await AsyncConnection.connect(
        os.environ["TEST_DATABASE_URL"], row_factory=dict_row
    ) as conn:
        for _ in range(count):
            await PurchaseCostService().step(conn)


def test_batch_counts_success_not_scanned_cancel_retry_idempotency(client):
    owner = register(client)
    item(client, owner, player="No Matching Player", source="900008")
    matched = item(client, owner)
    client.post("/v1/purchase-cost/sync", json={"orders": [order()]})
    payload = dict(target=1, scan_limit=100, request_key=str(uuid4()))
    response = client.post("/v1/purchase-cost/jobs", json=payload)
    assert response.status_code == 200, response.text
    job = response.json()
    job_id = job["id"]
    assert client.post("/v1/purchase-cost/jobs", json=payload).json()["id"] == job_id
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    asyncio.run(process_steps())
    assert client.get(f"/v1/purchase-cost/jobs/{job_id}").json()["scanned"] == 0
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/retry").status_code == 200
    asyncio.run(process_steps())
    job = client.get(f"/v1/purchase-cost/jobs/{job_id}").json()
    assert job["status"] == "completed", job
    assert job["scanned"] == 2
    assert job["succeeded"] == 1
    assert Decimal(
        client.get(f"/v1/portfolio/items/{matched}/detail").json()["item"]["purchase_total_cost"]
    ) == Decimal("11.00")
    client.cookies.clear()
    register(client)
    assert client.get(f"/v1/purchase-cost/jobs/{job_id}").status_code == 404
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/retry").status_code == 404


def test_exhausted_scan_limit_and_no_cross_owner_jobs(client):
    owner = register(client)
    for index in range(3):
        item(client, owner, player=f"No Candidate {index}")
    job = client.post(
        "/v1/purchase-cost/jobs", json=dict(target=2, scan_limit=1, request_key=str(uuid4()))
    ).json()
    asyncio.run(process_steps())
    result = client.get("/v1/purchase-cost/jobs/" + job["id"]).json()
    assert result["status"] == "exhausted"
    assert result["scanned"] == 1 and result["succeeded"] == 0


def test_one_purchase_for_identical_inventory_can_fill_once_without_manual_choice(client):
    owner = register(client)
    one = item(client, owner)
    item(client, owner)
    client.post("/v1/purchase-cost/sync", json={"orders": [order()]})
    candidate = preview(client, one)["candidates"][0]
    assert candidate["automatic"]
    job = client.post(
        "/v1/purchase-cost/jobs", json=dict(target=1, scan_limit=100, request_key=str(uuid4()))
    ).json()
    asyncio.run(process_steps())
    result = client.get("/v1/purchase-cost/jobs/" + job["id"]).json()
    assert result["succeeded"] == 1 and result["status"] == "completed"
    assert client.get(f"/v1/purchase-cost/items/{one}/audit").json()["confirmation"] == "automatic"


def test_concurrent_confirm_only_one_inventory_can_claim_unit(client):
    from uuid import UUID
    from card_platform.repository import ConflictError

    owner = register(client)
    one = item(client, owner)
    two = item(client, owner)
    client.post("/v1/purchase-cost/sync", json={"orders": [order()]})
    candidate = preview(client, one)["candidates"][0]

    async def attempt(item_id):
        async with await AsyncConnection.connect(
            os.environ["TEST_DATABASE_URL"], row_factory=dict_row
        ) as conn:
            try:
                await PurchaseCostService().confirm(
                    conn,
                    UUID(owner),
                    UUID(item_id),
                    UUID(candidate["unit_id"]),
                    candidate["evidence_hash"],
                )
                return "recorded"
            except ConflictError:
                return "conflict"

    async def race():
        return await asyncio.gather(attempt(one), attempt(two))

    assert sorted(asyncio.run(race())) == ["conflict", "recorded"]


def test_live_search_plan_and_automatic_fill_without_file_sync(client):
    owner = register(client)
    card = item(client, owner)
    plan = client.get(f"/v1/purchase-cost/items/{card}/search-plan").json()
    assert plan["keywords"] == ["Test Player"]
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={"orders": [order()], "complete": True})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "recorded"
    assert response.json()["recorded"]["total_cost"] == "11.00"
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json()["confirmation"] == "automatic"
    assert client.get(f"/v1/purchase-cost/items/{card}/search-plan").json()["status"] == "ready"
    # Retrying the user click cannot double-write or overwrite an existing cost.
    assert client.post(f"/v1/purchase-cost/items/{card}/search-result", json={"orders": [order()], "complete": True}).json()["status"] == "skipped"
    assert len(client.get(f"/v1/portfolio/items/{card}/detail").json()["transactions"]) == 1


def test_live_truncated_and_empty_results_cannot_auto_confirm_cached_orders(client):
    owner = register(client)
    card = item(client, owner)
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={"orders": [order()], "complete": False})
    assert response.json()["status"] == "incomplete"
    assert response.json()["candidates"] == []
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json() is None
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={"orders": [], "complete": True})
    assert response.json()["status"] == "not_found"
    assert response.json()["candidates"] == []
    client.cookies.clear()
    register(client)
    assert client.get(f"/v1/purchase-cost/items/{card}/search-plan").status_code == 404
    assert client.post(f"/v1/purchase-cost/items/{card}/search-result", json={"orders": [order()], "complete": True}).status_code == 404


def test_live_batch_cursor_cancel_retry_and_duplicate_step(client):
    owner = register(client)
    card1 = item(client, owner, source="900002")
    card2 = item(client, owner, player="Another Player", source="900012")
    payload = dict(target=2, scan_limit=100, request_key=str(uuid4()), source_mode="browser_search")
    job = client.post("/v1/purchase-cost/jobs", json=payload).json()
    assert job["source_mode"] == "browser_search"
    job_id = job["id"]
    wrong = client.post(f"/v1/purchase-cost/jobs/{job_id}/search-step", json=dict(item_id=card2, orders=[], complete=True))
    assert wrong.status_code == 409
    first = dict(item_id=card1, orders=[order()], complete=True)
    response = client.post(f"/v1/purchase-cost/jobs/{job_id}/search-step", json=first)
    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == 1
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/search-step", json=first).json()["scanned"] == 1
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    second = dict(item_id=card2, orders=[order(item_id="900012", external_order_id="900011", title="2025 Topps NBA Hoops Another Player Gold Bounce House 卡号 123 7/50编")], complete=True)
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/search-step", json=second).status_code == 409
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/retry").json()["scanned"] == 1
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/search-failed").json()["status"] == "failed"
    assert client.post(f"/v1/purchase-cost/jobs/{job_id}/retry").json()["status"] == "queued"
    response = client.post(f"/v1/purchase-cost/jobs/{job_id}/search-step", json=second)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["succeeded"] == 2


def base_item(client, owner):
    response = client.post("/v1/portfolio/items", json={
        "owner_id": owner,
        "card": {"player_name": "Test Player", "brand_name": "Topps", "product_name": "NBA Hoops", "season_start": 2025},
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_live_ordinary_card_auto_fills_without_card_number_source_link_or_manual_input(client):
    owner = register(client)
    card = base_item(client, owner)
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={
        "orders": [order(title="2025 Topps NBA Hoops Test Player Base")], "complete": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "recorded", response.json()
    assert response.json()["candidates"] == []
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json()["confirmation"] == "automatic"


def test_definition_print_run_is_used_when_inventory_serial_total_is_unknown(client):
    owner = register(client)
    created = client.post("/v1/portfolio/items", json={
        "owner_id": owner,
        # Definitions are shared across owners: use a distinct player so this
        # numbered definition cannot change later tests' unnumbered Base card.
        "card": {"player_name": "Print Run Fixture", "brand_name": "Topps",
                 "product_name": "NBA Hoops", "season_start": 2025, "print_run": 50},
    })
    assert created.status_code == 201, created.text
    card = created.json()["id"]
    # The checklist knows the print run even though this physical copy's serial
    # fields and parallel name have not yet been filled in.
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={
        "orders": [order(title="2025 Topps NBA Hoops Print Run Fixture Base")], "complete": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "unmatched", response.json()
    assert response.json()["candidates"] == []
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json() is None
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={
        "orders": [order(title="2025 Topps NBA Hoops Print Run Fixture /50")], "complete": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "recorded", response.json()
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json()["confirmation"] == "automatic"


def test_live_only_duplicate_same_card_purchases_require_selection(client):
    owner = register(client)
    card = base_item(client, owner)
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={
        "orders": [
            order(title="2025 Topps NBA Hoops Test Player Base"),
            order(item_id="900012", external_order_id="900011", title="2025 Topps NBA Hoops Test Player Base"),
            order(item_id="900022", external_order_id="900021", title="2024 Topps NBA Hoops Test Player Base"),
        ], "complete": True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "needs_confirmation"
    assert len(result["candidates"]) == 2
    assert {c["item_id"] for c in result["candidates"]} == {"900002", "900012"}
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json() is None
    assert confirm(client, card, result["candidates"][1]).status_code == 200


def test_live_weak_or_conflicting_single_result_does_not_request_manual_confirmation(client):
    owner = register(client)
    card = base_item(client, owner)
    for title in ["Test Player", "2024 Topps NBA Hoops Test Player Base", "2025 Topps NBA Hoops Test Player Gold /50"]:
        response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={
            "orders": [order(title=title)], "complete": True,
        })
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "unmatched", response.json()
        assert response.json()["candidates"] == []
        assert client.get(f"/v1/purchase-cost/items/{card}/audit").json() is None


def test_live_auto_match_filters_nonmatching_same_player_results_before_uniqueness(client):
    owner = register(client)
    card = base_item(client, owner)
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={
        "orders": [
            order(title="2025 Topps NBA Hoops Test Player Base"),
            order(item_id="900012", external_order_id="900011", title="Test Player"),
        ], "complete": True,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "recorded", response.json()


def test_duplicate_confirmation_rechecks_current_card_identity(client):
    owner = register(client)
    card = base_item(client, owner)
    response = client.post(f"/v1/purchase-cost/items/{card}/search-result", json={
        "orders": [order(title="2025 Topps NBA Hoops Test Player Base"),
                   order(item_id="900012", external_order_id="900011", title="2025 Topps NBA Hoops Test Player Base")],
        "complete": True,
    })
    result = response.json()
    assert result["status"] == "needs_confirmation"
    candidate = result["candidates"][0]

    async def change_fixture_identity():
        async with await AsyncConnection.connect(os.environ["TEST_DATABASE_URL"]) as conn:
            await conn.execute("UPDATE collection_item SET serial_total=50 WHERE id=%s", (card,))
    asyncio.run(change_fixture_identity())
    # The order hash is unchanged, but it no longer proves the card's parallel.
    assert confirm(client, card, candidate).status_code == 409
    assert client.get(f"/v1/purchase-cost/items/{card}/audit").json() is None


@pytest.mark.parametrize("linked", [True, False])
def test_sketch_cost_refresh_requires_reviewed_original_source_and_retains_evidence(client, linked):
    owner = register(client)
    created = client.post("/v1/portfolio/items", json={
        "owner_id":owner,
        "card":{"player_name":"Art Subject Fixture", "brand_name":"Upper Deck",
                "product_name":"Marvel Spider-Man", "season_start":2022,
                "insert_name":"Artist Sketch Card", "print_run":1},
        "serial_number":1, "serial_total":1,
        "notes":"reviewed original https://cardhobby.com.cn/market/item/900002" if linked else None,
        "purchase":{"transacted_at":"2025-09-01T00:00:00Z", "price":"0"},
    })
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    captured = order(title="2022 Upper Deck Marvel (MCU) Spider-Man Art Subject Fixture 手绘卡 1/1编 画师亲笔签字卡签")
    response = client.post(f"/v1/purchase-cost/items/{item_id}/search-result", json={
        "orders":[captured], "complete":True, "refresh":True,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    detail = client.get(f"/v1/portfolio/items/{item_id}/detail").json()
    assert len(detail["transactions"]) == 1
    if linked:
        assert data["status"] == "recorded", data
        assert Decimal(data["recorded"]["card_price"]) == 10
        assert Decimal(data["recorded"]["total_cost"]) == 11
        assert data["recorded"]["item_url"].endswith("/900002")
        audit = client.get(f"/v1/purchase-cost/items/{item_id}/audit").json()
        assert audit["evidence"]["item_url"] == data["recorded"]["item_url"]
    else:
        assert data["status"] == "unmatched"
        assert "手绘原作需核对实物" in data["reason"]
        assert Decimal(detail["transactions"][0]["unit_price"]) == 0


@pytest.mark.parametrize('state', ['accepted','rejected','revoked','new_version','changed_image','changed_identity'])
def test_cost_reuses_only_current_owner_version_and_assets_not_public_price(client,state):
    owner=register(client)
    identity=dict(player_name='Visual Fixture '+uuid4().hex, brand_name='Topps', product_name='Flagship',
                  season_start=2025, season_end=2026, card_number='157', parallel_name='Black and White',
                  is_rookie=False,is_autograph=False,is_memorabilia=False)
    created=client.post('/v1/portfolio/items',json={'owner_id':owner,'card':identity})
    assert created.status_code == 201,created.text
    item_id=created.json()['id']
    image='https://images.cardhobby.com/fixture.jpg'
    source='https://cardhobby.com.cn/Market/Item/900002'
    capture=order(title=f"2025 Topps Flagship {identity['player_name']} 黑白折 底片折")
    capture['lines'][0]['image_urls']=[image]
    async def seed():
        async with await AsyncConnection.connect(os.environ['TEST_DATABASE_URL'],row_factory=dict_row) as conn:
            await conn.execute('UPDATE collection_item SET image_front_url=%s,image_back_url=%s WHERE id=%s',('/uploads/front.jpg','/uploads/back.jpg',item_id))
            v=await conn.execute("INSERT INTO card_identity_version(collection_item_id,version,identity,review_status) VALUES(%s,1,%s,'confirmed') RETURNING id",(item_id,Jsonb(identity)))
            version=(await v.fetchone())['id']
            fp='fixture:'+uuid4().hex
            await conn.execute('INSERT INTO market_identity_cache(identity_fingerprint,normalized_identity) VALUES(%s,%s)',(fp,Jsonb(identity)))
            r=await conn.execute("INSERT INTO market_evidence(identity_fingerprint,provider_code,external_sale_id,raw_title,sale_price,currency,occurred_at,source_url,evidence_level) VALUES(%s,'cardhobby',%s,%s,999,'CNY',now(),%s,'ended_auction') RETURNING id",(fp,uuid4().hex,capture['lines'][0]['title'],source))
            evidence_id=(await r.fetchone())['id']
            review=dict(source='human_paired_image_review',verdict='different' if state=='rejected' else 'same',confidence='.96',
                        target_front_url='/uploads/front.jpg',target_back_url='/uploads/back.jpg',candidate_source_url=source,
                        candidate_image_url=image,reasons=['paired fixture images checked'])
            await conn.execute("INSERT INTO market_evidence_identity_decision(collection_item_id,identity_version_id,market_evidence_id,decision,reason_codes,classification) VALUES(%s,%s,%s,%s,'[]',%s)",(item_id,version,evidence_id,'rejected' if state=='rejected' else 'accepted',Jsonb({'visual_review':review})))
            card=await PurchaseCostService().card(conn,owner,item_id)
            assert await load_purchase_visual_reviews(conn,uuid4(),item_id,card) == []
            if state in {'revoked','new_version'}:
                await conn.execute('INSERT INTO card_identity_version(collection_item_id,version,identity,review_status,supersedes_id) VALUES(%s,2,%s,%s,%s)',(item_id,Jsonb(identity),'rejected' if state=='revoked' else 'confirmed',version))
            elif state=='changed_image':
                await conn.execute("UPDATE collection_item SET image_front_url='/uploads/changed.jpg' WHERE id=%s",(item_id,))
            elif state=='changed_identity':
                await conn.execute("UPDATE card_definition SET card_number='158' WHERE id=(SELECT card_definition_id FROM collection_item WHERE id=%s)",(item_id,))
    asyncio.run(seed())
    response=client.post(f'/v1/purchase-cost/items/{item_id}/search-result',json={'orders':[capture],'complete':True,'refresh':True})
    assert response.status_code==200,response.text
    result=response.json()
    if state=='accepted':
        assert result['status']=='recorded',result
        assert Decimal(result['recorded']['card_price'])==10  # not public market evidence's 999
        assert result['recorded']['visual_review']['identity_version_id']
        audit=client.get(f'/v1/purchase-cost/items/{item_id}/audit').json()
        assert audit['evidence']['visual_review']['candidate_image_url']==image
    else:
        assert result['status']=='unmatched',result
        assert client.get(f'/v1/purchase-cost/items/{item_id}/audit').json() is None


@pytest.mark.parametrize('count,complete',[(1,True),(2,True),(1,False)])
def test_soft_mismatch_visual_inference_and_duplicate_snapshot(client,monkeypatch,count,complete):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from card_platform.comparable_image_verifier import VisualDecision
    import card_platform.purchase_cost_service as module
    owner=register(client)
    created=client.post('/v1/portfolio/items',json={'owner_id':owner,'card':{
        'player_name':'Soft Match '+uuid4().hex,'brand_name':'Topps','product_name':'NBA Hoops',
        'season_start':2025,'season_end':2026,'insert_name':'Oasis'}})
    item_id=created.json()['id']
    async def images():
        async with await AsyncConnection.connect(os.environ['TEST_DATABASE_URL']) as c:
            await c.execute("UPDATE collection_item SET image_front_url='/uploads/fixture.jpg' WHERE id=%s",(item_id,))
    asyncio.run(images())
    async def verify(target,candidates,**kwargs):
        return {row.id:VisualDecision(candidate_id=row.id,verdict='same',confidence='.92',reasons=['same core printing']) for row in candidates}
    mock=AsyncMock(side_effect=verify)
    monkeypatch.setattr(module,'ComparableImageVerifier',lambda _:SimpleNamespace(verify=mock))
    player=client.get(f'/v1/portfolio/items/{item_id}/detail').json()['item']['player_name']
    orders=[]
    for index in range(count):
        captured=order(item_id=str(900002+index),external_order_id=str(900001+index),title=f'2026 Topps NBA Hoops {player} Oasis 折射 切割瑕疵图三')
        captured['lines'][0]['image_urls']=['https://images.cardhobby.com/fixture.jpg']
        orders.append(captured)
    response=client.post(f'/v1/purchase-cost/items/{item_id}/search-result',json={'orders':orders,'complete':complete,'refresh':True})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['status']==('incomplete' if not complete else 'recorded' if count==1 else 'needs_confirmation'),result
    if not complete:
        assert mock.call_count==0
    else:
        assert mock.call_count==1
        candidate=result.get('recorded') or result['candidates'][0]
        assert candidate['match_method']=='inferred'
        assert candidate['visual_inference']['candidate_image_url']
        if count==1:
            audit=client.get(f'/v1/purchase-cost/items/{item_id}/audit').json()
            assert audit['evidence']['title']==orders[0]['lines'][0]['title']
            assert audit['evidence']['match_method']=='inferred'
