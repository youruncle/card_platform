import json
import os

import pytest
from test_purchase_cost_postgres import client as cost_client, register

client = cost_client

pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="requires isolated PostgreSQL"
)


def create(client, owner, name="Fixture Player", **card):
    r = client.post(
        "/v1/portfolio/items",
        json={
            "owner_id": owner,
            "card": dict(
                player_name=name,
                brand_name="Topps",
                product_name="Flagship",
                season_start=2025,
                season_end=2026,
                **card,
            ),
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def query(client, filters, **params):
    return client.get(
        "/v1/portfolio/items/page",
        params={"search": json.dumps(filters, ensure_ascii=False), **params},
    )


def test_cross_page_global_search_aliases_exact_id_and_owner_scope(client):
    owner = register(client)
    target = create(client, owner, "KEVIN DURANT", is_autograph=True)
    for n in range(34):
        create(client, owner, f"Other Player {n}")
    initial = client.get("/v1/portfolio/items/page").json()
    assert target not in [i["id"] for i in initial["items"]]
    for name in ["杜兰特", "凯文杜兰特", "KEVIN DURANT", "kevn durant"]:
        result = query(client, {"q": name}).json()
        assert result["total"] == 1, result
        assert result["items"][0]["id"] == target
    display_id = query(client, {"q": "杜兰特"}).json()["items"][0]["display_id"]
    assert query(client, {"q": str(display_id)}).json()["items"][0]["id"] == target
    assert query(client, {"q": "杜兰特", "autograph": False}).json()["total"] == 0
    assert query(client, {"q": "Other Player"}, page=2).json()["total"] == 34
    assert len(query(client, {"q": "Other Player"}, page=2).json()["items"]) == 4
    client.post("/v1/auth/logout")
    register(client)
    assert query(client, {"q": "杜兰特"}, owner_id=owner).json()["total"] == 0


@pytest.mark.parametrize(
    "search", ["{broken", "[]", '{"cost_min":-1}', '{"q":"' + "x" * 161 + '"}']
)
def test_api_rejects_invalid_search_instead_of_ignoring_it(client, search):
    register(client)
    assert client.get("/v1/portfolio/items/page", params={"search": search}).status_code == 422


def test_financial_grade_serial_filters_and_deleted_cards(client):
    owner = register(client)
    missing = create(client, owner)
    ids = []
    for price in ["0", "10"]:
        response = client.post(
            "/v1/portfolio/items",
            json={
                "owner_id": owner,
                "card": {
                    "player_name": "Kevin Durant",
                    "brand_name": "Topps",
                    "product_name": "Flagship",
                    "season_start": 2025,
                },
                "grade_company": "PSA",
                "grade_value": "9",
                "serial_number": 7,
                "serial_total": 99,
                "purchase": {
                    "transacted_at": "2025-01-01T00:00:00Z",
                    "price": price,
                    "currency": "USD",
                    "fx_rate_to_base": "7",
                    "fee_amount": "0",
                },
            },
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])

    def found(filters):
        result = query(client, filters)
        assert result.status_code == 200, result.text
        return {item["id"] for item in result.json()["items"]}

    assert found({"has_cost": False}) == {missing}
    assert found({"cost_max": 0}) == {ids[0]}
    assert found(
        {
            "cost_min": 70,
            "cost_max": 70,
            "graded": True,
            "grade_company": ["psa"],
            "grade_min": 9,
            "grade_max": 9,
            "serial_number": 7,
            "serial_min": 99,
            "serial_max": 99,
        }
    ) == {ids[1]}
    assert found({"value_min": 0}) == set(), "missing formal value is not zero"
    assert (
        client.request(
            "DELETE", f"/v1/portfolio/items/{ids[1]}", json={"owner_id": owner}
        ).status_code
        == 204
    )
    assert found({"q": "杜兰特"}) == {ids[0]}
