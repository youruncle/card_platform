import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from card_platform.config import get_settings
from card_platform.main import app


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="requires a migrated PostgreSQL database",
)


def test_suggestion_badge_api_and_one_click_apply(tmp_path) -> None:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["VISION_API_KEY"] = ""
    os.environ["UPLOAD_DIR"] = str(tmp_path / "uploads")
    get_settings.cache_clear()
    with TestClient(app) as client:
        registered = client.post(
            "/v1/auth/register",
            json={
                "email": f"identity-fix-{uuid4()}@example.com",
                "display_name": "Identity Fix",
                "base_currency": "CNY",
                "password": "A-safe-integration-password-2026!",
            },
        )
        assert registered.status_code == 201, registered.text
        owner_id = registered.json()["id"]
        wrong_card_number = f"WRONG-{uuid4().hex[:8]}"
        source_url = (
            "https://www.cardhobby.com.cn/market/item/122450195"
            f"?test={uuid4().hex}"
        )
        item_id = client.post(
            "/v1/portfolio/items",
            json={
                "owner_id": owner_id,
                "card": {
                    "player_name": "LeBron James",
                    "brand_name": "Panini",
                    "season_start": 2021,
                    "season_end": 2022,
                    "product_name": "Origins",
                    "insert_name": "GU Jersey",
                    "parallel_name": "Cur",
                    "card_number": wrong_card_number,
                    "is_patch": True,
                },
            },
        ).json()["id"]
        comp = client.post(
            f"/v1/portfolio/items/{item_id}/market-comps",
            json={
                "owner_id": owner_id,
                "occurred_at": "2026-07-18T20:01:00+08:00",
                "sale_price": 476,
                "shipping_price": 18,
                "currency": "CNY",
                "fx_rate_to_base": 1,
                "listing_title": (
                    "2021-22 Panini Origins LeBron James GU亲穿 球衣 【Cur】 01"
                ),
                "source_url": source_url,
                "match_confidence": 0.9,
            },
        )
        assert comp.status_code == 201, comp.text
        original_comp_id = comp.json()["id"]
        scan = client.post(
            "/v1/portfolio/identity-corrections/scan",
            params={"owner_id": owner_id},
        )
        assert scan.status_code == 200, scan.text
        counts = client.get(
            "/v1/portfolio/identity-corrections", params={"owner_id": owner_id}
        ).json()
        assert counts[item_id] == 1
        suggestions = client.get(
            f"/v1/portfolio/items/{item_id}/identity-corrections",
            params={"owner_id": owner_id},
        ).json()
        assert suggestions[0]["field_name"] == "card_number"
        assert suggestions[0]["suggested_value"] == "01"
        applied = client.post(
            f"/v1/portfolio/items/{item_id}/identity-corrections/apply",
            json={"owner_id": owner_id, "suggestion_ids": [suggestions[0]["id"]]},
        )
        assert applied.status_code == 200, applied.text
        detail = client.get(
            f"/v1/portfolio/items/{item_id}/detail",
            params={"owner_id": owner_id},
        ).json()
        assert detail["item"]["card_number"] == "01"
        corrected_comps = client.get(
            f"/v1/portfolio/items/{item_id}/market-comps",
            params={"owner_id": owner_id},
        )
        assert corrected_comps.status_code == 200, corrected_comps.text
        assert original_comp_id not in {
            row["id"] for row in corrected_comps.json()
        }
        assert (
            client.get(
                "/v1/portfolio/identity-corrections",
                params={"owner_id": owner_id},
            ).json()
            == {}
        )
