"""Equivalence and concurrent writes against disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from statistics import median
from time import perf_counter

import psycopg

import pytest

from test_mobile_postgres import account, photos
from test_mobile_postgres import client as client
from card_platform import portfolio_search
from card_platform import api

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires disposable PostgreSQL")


@pytest.fixture(autouse=True)
def private_upload_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))


def create(client, owner, headers, index):
    payload = {"owner_id": owner["id"], "card": {
        "player_name": "Kevin Durant" if index % 2 == 0 else "Stephen Curry",
        "brand_name": "Performance Test", "product_name": "Test",
        "season_start": 2020 + index, "season_end": 2021 + index,
        "is_rookie": index % 2 == 0, "is_autograph": index == 2,
        "is_memorabilia": index == 3,
    }}
    if index % 2:
        payload.update(serial_number=4, serial_total=25, grade_company="PSA", grade_value="9")
    if index != 0:
        payload["purchase"] = {"transacted_at": "2026-01-01T00:00:00Z", "price": str(index * 10),
            "currency": "USD" if index == 4 else "CNY", "fx_rate_to_base": "7" if index == 4 else "1"}
    result = client.post("/v1/portfolio/items", headers=headers, json=payload)
    assert result.status_code == 201, result.text
    return result.json()["id"]


def test_sql_pushdown_matches_python_reference_for_structured_filters(client, monkeypatch):
    owner, headers = account(client)
    ids = [create(client, owner, headers, index) for index in range(5)]
    result = client.patch(f"/v1/portfolio/items/{ids[-1]}", headers=headers, json={"holding_status": "sold"})
    assert result.status_code == 204, result.text
    filters = [{}, {"q": "杜兰特"}, {"q": "库里", "graded": True},
        {"holding_status": "held", "rookie": True}, {"holding_status": "sold", "cost_min": 1},
        {"rookie": False}, {"autograph": True}, {"autograph": False},
        {"memorabilia": True}, {"numbered": True}, {"numbered": False},
        {"graded": True}, {"graded": False}, {"has_cost": True}, {"has_cost": False},
        {"cost_min": 100}, {"cost_max": 0}, {"cost_min": 10, "cost_max": 30},
        {"year_min": 2021, "year_max": 2022}, {"year_min": 2025},
        {"serial_number": 4}, {"serial_min": 20, "serial_max": 30}, {"serial_max": 10},
        {"grade_min": 8, "grade_max": 10}, {"grade_max": 8}]
    optimized = portfolio_search.structured_sql_filters
    for conditions in filters:
        params = {"search": json.dumps(conditions, ensure_ascii=False)}
        actual = client.get("/v1/portfolio/items/page", headers=headers, params=params)
        assert actual.status_code == 200, actual.text
        with monkeypatch.context() as context:
            context.setattr(portfolio_search, "structured_sql_filters", lambda filters: ("", ()))
            reference = client.get("/v1/portfolio/items/page", headers=headers, params=params)
        assert reference.status_code == 200, reference.text
        assert actual.json() == reference.json(), conditions
    assert portfolio_search.structured_sql_filters is optimized


def test_concurrent_front_and_back_uploads_preserve_both_images_and_ownership(client):
    owner, headers = account(client)
    item_id = create(client, owner, headers, 0)

    def upload(side):
        return client.post(f"/v1/portfolio/items/{item_id}/images", headers=headers,
            params={"owner_id": owner["id"], "side": side}, files={"image": photos()[side]})

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(upload, ("front", "back")))
    for response in responses:
        assert response.status_code == 201, response.text
    detail = client.get(f"/v1/portfolio/items/{item_id}/detail", headers=headers)
    assert detail.status_code == 200, detail.text
    saved = detail.json()["item"]
    assert saved["image_front_url"] == responses[0].json()["image_url"]
    assert saved["image_back_url"] == responses[1].json()["image_url"]
    _, other = account(client)
    denied = client.get(f"/v1/portfolio/items/{item_id}/detail", headers=other)
    assert denied.status_code == 404


def test_detail_work_scales_with_one_card_instead_of_the_account(client, monkeypatch):
    owner, headers = account(client)
    item_id = create(client, owner, headers, 0)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        connection.execute("""INSERT INTO collection_item (owner_id, card_definition_id)
            SELECT ci.owner_id, ci.card_definition_id FROM collection_item ci
            CROSS JOIN generate_series(1,119) WHERE ci.id=%s""", (item_id,))
    native = api.repository.list_items
    ledger = api.repository._owner_ledger_entries
    sizes = []

    async def observe(connection, owner_id, *, item_ids=None):
        sizes.append(len(item_ids))
        return await ledger(connection, owner_id, item_ids=item_ids)

    async def legacy(connection, owner_id, **kwargs):
        return await native(connection, owner_id)

    monkeypatch.setattr(api.repository, "_owner_ledger_entries", observe)
    times = {"full_account": [], "one_card": []}
    results = {}
    for repetition in range(8):
        for mode, implementation in (("full_account", legacy), ("one_card", native)):
            with monkeypatch.context() as context:
                context.setattr(api.repository, "list_items", implementation)
                started = perf_counter()
                result = client.get(f"/v1/portfolio/items/{item_id}/detail", headers=headers)
                elapsed = perf_counter() - started
            assert result.status_code == 200, result.text
            results[mode] = result.json()
            assert sizes[-1] == (120 if mode == "full_account" else 1)
            if repetition:
                times[mode].append(elapsed * 1000)
    assert results["full_account"] == results["one_card"]
    print("PERFORMANCE " + json.dumps({"cards": 120, "samples_per_mode": 7,
        "median_ms": {mode: round(median(values), 2) for mode, values in times.items()}}))
