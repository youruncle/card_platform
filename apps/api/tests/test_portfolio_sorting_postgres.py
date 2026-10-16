"""Disposable PostgreSQL exercises real final projections, snapshots and HTTP auth."""

import os

import psycopg
import pytest

from test_mobile_postgres import account
from test_mobile_postgres import client as client
from card_platform.portfolio_sort import snapshots

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires disposable PostgreSQL"
)


def seed(client, user, headers, count=41):
    ids = []
    for i in range(count):
        response = client.post(
            "/v1/portfolio/items",
            headers=headers,
            json={
                "owner_id": user["id"],
                "card": {
                    "player_name": f"Sort Player {i:03}",
                    "brand_name": "Sort Test",
                    "season_start": 2000 + i,
                    "product_name": "Sort",
                },
                "purchase": {
                    "transacted_at": "2026-01-01T00:00:00Z",
                    "price": str(i),
                    "currency": "USD" if i == count - 1 else "CNY",
                    "fx_rate_to_base": "7" if i == count - 1 else "1",
                },
            },
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
        valuation = client.post(
            f"/v1/portfolio/items/{ids[-1]}/valuations",
            headers=headers,
            json={
                "currency": "USD" if i == count - 1 else "CNY",
                "low_value": str(i),
                "fair_value": str(i),
                "high_value": str(i),
            },
        )
        assert valuation.status_code == 201, valuation.text
    return ids


def test_profit_order_combined_summary_and_filtered_snapshot(client):
    owner, headers = account(client)
    ids = seed(client, owner, headers, count=5)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        for item_id, value in [(ids[0], 30), (ids[1], 0), (ids[3], 10)]:
            db.execute("UPDATE valuation_snapshot SET low_value=%s,fair_value=%s,high_value=%s WHERE collection_item_id=%s", (value, value, value, item_id))
    endpoint = "/v1/portfolio/items/page"
    params = {"sort_by": "unrealized_profit", "include_summary": "true"}
    for direction, expected in [("desc", [0, 3, 2, 1, 4]), ("asc", [1, 2, 3, 0, 4])]:
        result = client.get(endpoint, headers=headers, params=params | {"order": direction})
        assert result.status_code == 200, result.text
        assert [row["id"] for row in result.json()["items"]] == [ids[i] for i in expected]
        assert result.json()["summary"] == client.get("/v1/portfolio/summary", headers=headers).json()
    filtered = client.get(endpoint, headers=headers, params=params | {
        "search": '{"year_min":2001,"year_max":2001}', "snapshot": "true",
    }).json()
    assert [row["id"] for row in filtered["items"]] == [ids[1]]
    assert filtered["summary"]["item_count"] == 5
    continued = client.get(endpoint, headers=headers, params=params | {
        "search": '{"year_min":2001,"year_max":2001}', "snapshot_token": filtered["snapshot_token"],
    })
    assert continued.status_code == 200, continued.text
    assert continued.json()["summary"] == filtered["summary"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE collection_item SET holding_status='sold' WHERE id=%s", (ids[0],))
    sold = client.get(endpoint, headers=headers, params=params).json()["items"]
    assert next(row for row in sold if row["id"] == ids[0])["unrealized_profit"] is None
    assert {row["id"] for row in sold[-2:]} == {ids[0], ids[4]}


def test_full_sort_snapshots_frozen_pages_filter_binding_and_session(client):
    owner, headers = account(client)
    _, other = account(client)
    ids = seed(client, owner, headers)
    params = {"sort_by": "fair_value", "order": "desc", "page_size": 10, "snapshot": "true"}
    endpoint = "/v1/portfolio/items/page"
    first = client.get(endpoint, headers=headers, params=params)
    assert first.status_code == 200, first.text
    first = first.json()
    assert [row["id"] for row in first["items"]] == list(reversed(ids[30:40]))
    for field in ["created_at", "player_name", "season_start", "remaining_cost", "fair_value"]:
        for direction in ["asc", "desc"]:
            ordered = client.get(
                endpoint,
                headers=headers,
                params={
                    "sort_by": field,
                    "order": direction,
                    "page_size": 100,
                },
            ).json()["items"]
            comparable = ids[:40] if field == "fair_value" else ids
            expected = comparable if direction == "asc" else list(reversed(comparable))
            if field == "fair_value":
                expected = expected + [ids[40]]
            assert [row["id"] for row in ordered] == expected, (field, direction)
    token = first["snapshot_token"]
    assert first["snapshot_at"]
    # Changing a formal value cannot move rows between an existing snapshot's pages.
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE valuation_snapshot SET low_value=900,fair_value=900,high_value=900 WHERE collection_item_id=%s",
            (ids[0],),
        )
    follow = {k: v for k, v in params.items() if k != "snapshot"} | {"snapshot_token": token}
    gathered = [row["id"] for row in first["items"]]
    for page in range(2, 6):
        result = client.get(endpoint, headers=headers, params=follow | {"page": page})
        assert result.status_code == 200, result.text
        gathered.extend(row["id"] for row in result.json()["items"])
    assert gathered == list(reversed(ids[:40])) + [ids[40]]
    assert len(set(gathered)) == 41
    for changes in [{"order": "asc"}, {"sort_by": "player_name"}, {"search": '{"q":"Player"}'}]:
        assert client.get(endpoint, headers=headers, params=follow | changes).status_code == 409
    assert client.get(endpoint, headers=other, params=follow).status_code == 409
    fresh = client.get(endpoint, headers=headers, params=params).json()
    assert fresh["items"][0]["id"] == ids[0]
    # Base currency is part of the binding even if it changes in the database.
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE app_user SET base_currency='USD' WHERE id=%s", (owner["id"],))
    assert client.get(endpoint, headers=headers, params=follow).status_code == 409
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE app_user SET base_currency='CNY' WHERE id=%s", (owner["id"],))
        db.execute("UPDATE collection_item SET deleted_at=now() WHERE id=%s", (ids[-1],))
    invalid = client.get(endpoint, headers=headers, params=follow)
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["code"] == "snapshot_expired"
    assert client.post("/v1/auth/logout", headers=headers).status_code == 204
    assert client.get(endpoint, headers=headers, params=follow).status_code == 401


def test_cost_uses_base_currency_sold_zero_and_unknown_last(client):
    owner, headers = account(client)
    ids = seed(client, owner, headers, 4)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE collection_item SET holding_status='sold' WHERE id=%s", (ids[1],))
        db.execute("DELETE FROM card_transaction WHERE collection_item_id=%s", (ids[2],))
    result = client.get(
        "/v1/portfolio/items/page",
        headers=headers,
        params={"sort_by": "remaining_cost", "order": "desc"},
    ).json()
    assert result["items"][0]["id"] == ids[3]
    from decimal import Decimal

    assert Decimal(result["items"][0]["remaining_cost"]) == Decimal("21")
    assert {row["id"] for row in result["items"][1:3]} == set(ids[:2])
    assert result["items"][-1]["id"] == ids[2]


def test_snapshot_expiration_eviction_and_ownership_transfer(client, monkeypatch):
    import card_platform.portfolio_sort as module

    owner, headers = account(client)
    other, _ = account(client)
    ids = seed(client, owner, headers, 1)
    endpoint = "/v1/portfolio/items/page"
    params = {"snapshot": "true", "sort_by": "player_name"}
    tokens = [
        client.get(endpoint, headers=headers, params=params).json()["snapshot_token"]
        for _ in range(4)
    ]
    follow = {"snapshot_token": tokens[0], "sort_by": "player_name"}
    assert client.get(endpoint, headers=headers, params=follow).status_code == 409
    follow["snapshot_token"] = tokens[-1]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE collection_item SET owner_id=%s WHERE id=%s", (other["id"], ids[0]))
    assert client.get(endpoint, headers=headers, params=follow).status_code == 409
    fresh = client.get(endpoint, headers=headers, params=params).json()["snapshot_token"]
    now = module.monotonic()
    monkeypatch.setattr(module, "monotonic", lambda: now + 61)
    assert (
        client.get(
            endpoint, headers=headers, params={"snapshot_token": fresh, "sort_by": "player_name"}
        ).status_code
        == 409
    )


@pytest.mark.skipif(
    os.environ.get("PORTFOLIO_SORT_BENCHMARK") != "1",
    reason="opt-in synthetic performance baseline",
)
@pytest.mark.parametrize("count", [100, 1000, 5000])
def test_sort_snapshot_performance_baseline(client, count):
    import json
    import resource
    import time

    owner, headers = account(client)
    ids = seed(client, owner, headers, 1)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            """INSERT INTO collection_item(owner_id,card_definition_id)
            SELECT owner_id,card_definition_id FROM collection_item CROSS JOIN generate_series(1,%s)
            WHERE id=%s""",
            (count - 1, ids[0]),
        )
        db.execute(
            """INSERT INTO card_transaction(collection_item_id,transaction_type,transacted_at,quantity,unit_price,currency,fx_rate_to_base)
            SELECT id,'buy',now(),1,display_id,'CNY',1 FROM collection_item
            WHERE owner_id=%s AND id<>%s""",
            (owner["id"], ids[0]),
        )
        db.execute(
            """INSERT INTO valuation_snapshot(collection_item_id,method,currency,low_value,fair_value,high_value)
            SELECT id,'manual','CNY',display_id,display_id,display_id FROM collection_item
            WHERE owner_id=%s AND id<>%s""",
            (owner["id"], ids[0]),
        )
        db.execute("ANALYZE collection_item")
        db.execute("ANALYZE card_transaction")
        db.execute("ANALYZE valuation_snapshot")
    endpoint = "/v1/portfolio/items/page"
    params = {"snapshot": "true", "sort_by": "fair_value", "page_size": 30}
    start = time.perf_counter()
    first = client.get(endpoint, headers=headers, params=params)
    cold = (time.perf_counter() - start) * 1000
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["total"] == count
    token = payload["snapshot_token"]
    durations = []
    for _ in range(20):
        start = time.perf_counter()
        result = client.get(
            endpoint,
            headers=headers,
            params={"sort_by": "fair_value", "snapshot_token": token, "page_size": 30},
        )
        durations.append((time.perf_counter() - start) * 1000)
        assert result.status_code == 200, result.text
    print(
        "SORT_BENCHMARK "
        + json.dumps(
            {
                "count": count,
                "cold_ms": round(cold, 2),
                "hot_p95_ms": round(sorted(durations)[18], 2),
                "cache_accounted_bytes": snapshots.entries[token].size,
                "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            }
        )
    )


def test_sort_uses_joint_recalculation_when_database_order_is_reversed(client, monkeypatch):
    from datetime import datetime, timezone
    from decimal import Decimal
    from psycopg.types.json import Jsonb
    from test_codex_joint_valuation import fixture

    data, snapshot, reviews, fx = fixture()
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
    owner, headers = account(client)
    card = {
        key: data[key]
        for key in [
            "player_name",
            "season_start",
            "product_name",
            "brand_name",
            "card_number",
            "insert_name",
        ]
    }
    recovered = client.post(
        "/v1/portfolio/items", headers=headers, json={"owner_id": owner["id"], "card": card}
    ).json()["id"]
    ordinary = seed(client, owner, headers, 1)[0]
    snapshot["raw_result"]["collection_item_id"] = recovered
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute(
            "UPDATE collection_item SET image_front_url=%s,image_back_url=%s WHERE id=%s",
            (data["image_front_url"], data["image_back_url"], recovered),
        )
        db.execute(
            "UPDATE valuation_snapshot SET currency='CNY',low_value=100,fair_value=100,high_value=100 WHERE collection_item_id=%s",
            (ordinary,),
        )
        db.execute(
            "INSERT INTO valuation_snapshot(collection_item_id,method,currency,low_value,fair_value,high_value) VALUES(%s,'market_comp','CNY',200,200,200)",
            (recovered,),
        )
        job = db.execute(
            "INSERT INTO card_analysis_job(owner_id,collection_item_id,status) VALUES(%s,%s,'succeeded') RETURNING id",
            (owner["id"], recovered),
        ).fetchone()[0]
        db.execute(
            """INSERT INTO card_analysis_snapshot(job_id,collection_item_id,analysis_version,source,model_name,
            identity_summary,identity_confidence,currency,fair_value_low,fair_value,fair_value_high,valuation_confidence,
            raw_result,evidence_summary,status) VALUES(%s,%s,1,'codex_multimodal','test','legacy',.97,'CNY',40,50,60,.3,%s,%s,'succeeded')""",
            (job, recovered, Jsonb(snapshot["raw_result"]), Jsonb(snapshot["evidence_summary"])),
        )
    response = client.get(
        "/v1/portfolio/items/page",
        headers=headers,
        params={"sort_by": "fair_value", "order": "asc"},
    )
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [row["id"] for row in rows] == [recovered, ordinary], [
        (row["fair_value"], row["valuation_synthesis"]) for row in rows
    ]
    assert Decimal(rows[0]["fair_value"]) == Decimal("61.98")
    assert rows[0]["valuation_synthesis"]["status"] == "joint_recalculated"
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        assert db.execute(
            "SELECT fair_value FROM valuation_snapshot WHERE collection_item_id=%s", (recovered,)
        ).fetchone()[0] == Decimal("200")
