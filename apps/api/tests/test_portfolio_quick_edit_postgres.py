import os
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from card_platform.config import get_settings
from card_platform.main import app


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="requires a migrated PostgreSQL database",
)


def test_list_quick_edit_initializes_purchase_and_updates_attributes(tmp_path) -> None:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["VISION_API_KEY"] = ""
    os.environ["VALUATION_WORKER_ENABLED"] = "false"
    os.environ["RECOGNITION_WORKER_ENABLED"] = "false"
    os.environ["SCANNER_WORKER_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = str(tmp_path / "uploads")
    get_settings.cache_clear()
    with TestClient(app) as client:
        registered = client.post(
            "/v1/auth/register",
            json={
                "email": f"quick-edit-{uuid4()}@example.com",
                "password": "A-safe-integration-password-2026!",
                "display_name": "Quick Edit Integration",
                "base_currency": "CNY",
            },
        )
        assert registered.status_code == 201, registered.text
        created = client.post(
            "/v1/portfolio/items",
            json={
                "owner_id": registered.json()["id"],
                "card": {
                    "player_name": "Scoot Henderson",
                    "brand_name": "Panini",
                    "season_start": 2023,
                    "product_name": "NBA Hoops",
                    "card_number": "272",
                    "print_run": 99,
                },
                "serial_number": 12,
                "serial_total": 99,
            },
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        second = client.post(
            "/v1/portfolio/items",
            json={
                "owner_id": registered.json()["id"],
                "card": {
                    "player_name": "Amen Thompson",
                    "brand_name": "Panini",
                    "season_start": 2023,
                    "product_name": "NBA Hoops",
                    "card_number": "273",
                },
            },
        )
        assert second.status_code == 201, second.text

        updated = client.patch(
            f"/v1/portfolio/items/{item_id}",
            json={
                "purchase_price": 23,
                "purchase_currency": "CNY",
                "is_rookie": True,
                "is_numbered": True,
                "is_autograph": True,
                "is_memorabilia": True,
            },
        )
        assert updated.status_code == 204, updated.text

        items = client.get("/v1/portfolio/items").json()
        item = next(value for value in items if value["id"] == item_id)
        second_item = next(value for value in items if value["id"] == second.json()["id"])
        assert item["display_id"] == 0
        assert second_item["display_id"] == 1
        assert item["purchase_price"] == "23.00"
        assert Decimal(item["remaining_cost"]) == Decimal("23")
        assert item["is_rookie"] is True
        assert item["is_numbered"] is True
        assert item["is_autograph"] is True
        assert item["is_memorabilia"] is True

        renumbered = client.patch(
            f"/v1/portfolio/items/{item_id}",
            json={"serial_number": 21, "serial_total": 99},
        )
        assert renumbered.status_code == 204, renumbered.text
        item = next(
            value for value in client.get("/v1/portfolio/items").json()
            if value["id"] == item_id
        )
        assert item["serial_number"] == 21
        assert item["serial_total"] == 99
        assert item["is_numbered"] is True

        unnumbered = client.patch(
            f"/v1/portfolio/items/{item_id}", json={"is_numbered": False}
        )
        assert unnumbered.status_code == 204, unnumbered.text
        item = next(
            value for value in client.get("/v1/portfolio/items").json()
            if value["id"] == item_id
        )
        assert item["is_numbered"] is False

        repriced = client.patch(
            f"/v1/portfolio/items/{item_id}", json={"purchase_price": 25}
        )
        assert repriced.status_code == 204, repriced.text
        detail = client.get(f"/v1/portfolio/items/{item_id}/detail").json()
        buys = [row for row in detail["transactions"] if row["transaction_type"] == "buy"]
        assert len(buys) == 1
        assert buys[0]["unit_price"] == "25.00"
