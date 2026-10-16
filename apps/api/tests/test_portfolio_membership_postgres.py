"""Default mobile sorting with real isolated PostgreSQL and HTTP authorization."""
import os
from unittest.mock import patch

import psycopg
import pytest
from test_mobile_postgres import account, client as client
from test_portfolio_sorting_postgres import seed
from card_platform import api

pytestmark = pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="requires disposable PostgreSQL")


def test_membership_order_insert_filter_owner_delete_and_page_projection(client):
    owner, headers = account(client)
    _, other = account(client)
    ids = seed(client, owner, headers, count=35)
    endpoint = "/v1/portfolio/items/page"
    params = {"sort_by": "created_at", "snapshot": "true", "snapshot_mode": "membership", "page_size": 30}
    with patch.object(api.repository, "list_items", wraps=api.repository.list_items) as listing:
        response = client.get(endpoint, headers=headers, params=params)
        assert response.status_code == 200, response.text
        assert len(listing.call_args.kwargs["item_ids"]) == 30
    first = response.json()
    assert [r["id"] for r in first["items"]] == list(reversed(ids))[:30]
    follow = params | {"snapshot_token": first["snapshot_token"], "page": 2}
    # A later insert does not shift pages in the original membership snapshot.
    seed(client, owner, headers, count=1)
    second = client.get(endpoint, headers=headers, params=follow)
    assert second.status_code == 200, second.text
    assert [r["id"] for r in second.json()["items"]] == list(reversed(ids))[30:]
    assert second.json()["total"] == 35
    assert client.get(endpoint, headers=other, params=follow).status_code == 409
    assert client.get(endpoint, headers=headers, params=follow | {"order": "asc"}).status_code == 409
    filtered = client.get(endpoint, headers=headers, params=params | {"search": '{"year_min":2020}', "order": "asc"}).json()
    assert [r["id"] for r in filtered["items"]] == ids[20:]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE collection_item SET deleted_at=now() WHERE id=%s", (ids[0],))
    assert client.get(endpoint, headers=headers, params=follow).status_code == 409


def test_default_without_snapshot_uses_sql_window_and_stable_ties(client):
    owner, headers = account(client)
    ids = seed(client, owner, headers, count=5)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE collection_item SET created_at='2026-01-01' WHERE owner_id=%s", (owner["id"],))
    for order in ["asc", "desc"]:
        with patch.object(api.repository, "list_items", wraps=api.repository.list_items) as listing:
            response = client.get("/v1/portfolio/items/page", headers=headers,
                params={"sort_by": "created_at", "order": order, "page": 2, "page_size": 2})
            assert response.status_code == 200, response.text
            assert [r["id"] for r in response.json()["items"]] == sorted(ids)[2:4]
            assert len(listing.call_args.kwargs["item_ids"]) == 2
