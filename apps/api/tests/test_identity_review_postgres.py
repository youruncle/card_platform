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


def test_confirmation_creates_shadow_and_queues_revaluation_without_formal_overwrite(
    tmp_path,
) -> None:
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["VISION_API_KEY"] = ""
    os.environ["VALUATION_WORKER_ENABLED"] = "false"
    os.environ["RECOGNITION_WORKER_ENABLED"] = "false"
    os.environ["SCANNER_WORKER_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = str(tmp_path / "uploads")
    get_settings.cache_clear()
    email = f"identity-review-{uuid4()}@example.com"
    with TestClient(app) as client:
        registered = client.post(
            "/v1/auth/register",
            json={
                "email": email,
                "password": "A-safe-integration-password-2026!",
                "display_name": "Identity Review Integration",
                "base_currency": "CNY",
            },
        )
        assert registered.status_code == 201, registered.text
        owner_id = registered.json()["id"]
        created = client.post(
            "/v1/portfolio/items",
            json={
                "owner_id": owner_id,
                "card": {
                    "player_name": "BRANDON MILLER",
                    "brand_name": "Panini",
                    "season_start": 2023,
                    "season_end": 2024,
                    "product_name": "Prizm Basketball",
                    "card_number": "152",
                    "insert_name": "Prizm",
                    "parallel_name": "Prizm",
                    "is_rookie": True,
                },
            },
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        research = client.post(
            f"/v1/portfolio/identity-review/items/{item_id}/research",
            json={
                "visual_observations": {
                    "base_card": True,
                    "confidence": "0.94",
                    "reasons": ["front and back match base #152"],
                    "market_evidence": [
                        {
                            "id": "provider:sale-a",
                            "provider_code": "fixture_provider",
                            "external_sale_id": "sale-a",
                            "occurred_at": "2026-07-08T00:00:00Z",
                            "price_base": "8.46",
                            "evidence_level": "aggregated_sale",
                            "tier": "T0",
                        },
                        {
                            "id": "provider:sale-b",
                            "provider_code": "fixture_provider",
                            "external_sale_id": "sale-b",
                            "occurred_at": "2026-07-06T00:00:00Z",
                            "price_base": "6.70",
                            "evidence_level": "aggregated_sale",
                            "tier": "T0",
                        },
                    ],
                }
            },
        )
        assert research.status_code == 202, research.text
        candidate = research.json()["candidates"][0]
        assert candidate["identity"]["insert_name"] is None
        assert candidate["identity"]["parallel_name"] is None

        confirmed = client.post(
            f"/v1/portfolio/identity-review/items/{item_id}/confirm",
            json={
                "identity": candidate["identity"],
                "field_provenance": candidate["field_provenance"],
                "reason": "integration test confirms image-backed base candidate",
            },
        )
        assert confirmed.status_code == 201, confirmed.text
        assert confirmed.json()["valuation_job_id"]
        assert confirmed.json()["published_valuation_unchanged"] is True

        review = client.get(
            f"/v1/portfolio/identity-review/items/{item_id}"
        )
        assert review.status_code == 200, review.text
        payload = review.json()
        assert payload["latest_version"]["review_status"] == "confirmed"
        assert Decimal(payload["latest_shadow"]["fair_value"]) == Decimal("8.46")
        assert payload["latest_shadow"]["evidence_count"] == 2

        detail = client.get(f"/v1/portfolio/items/{item_id}/detail")
        assert detail.status_code == 200, detail.text
        assert detail.json()["valuations"] == []
