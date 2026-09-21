import os
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
import psycopg
from psycopg.types.json import Jsonb

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

        valuation = client.post(
            f"/v1/portfolio/items/{item_id}/valuations",
            json={
                "currency": "CNY",
                "low_value": 90,
                "fair_value": 100,
                "high_value": 110,
                "confidence": 0.8,
                "evidence": {"source": "pre-confirmation regression fixture"},
            },
        )
        assert valuation.status_code == 201, valuation.text
        valuation_id = valuation.json()["id"]
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            connection.execute(
                """
                INSERT INTO market_identity_cache (
                    identity_fingerprint, normalized_identity
                ) VALUES ('regression:pre-confirmation', %s)
                """,
                (Jsonb({"parallel_name": "prizm"}),),
            )
            connection.execute(
                """
                INSERT INTO market_identity_binding (
                    collection_item_id, identity_fingerprint
                ) VALUES (%s, 'regression:pre-confirmation')
                """,
                (item_id,),
            )
            correction_id = connection.execute(
                """
                INSERT INTO card_identity_correction (
                    collection_item_id, field_name, current_value, suggested_value,
                    confidence, evidence, detector_version
                ) VALUES (%s, 'parallel_name', %s, %s, 0.9, %s, 'regression-fixture')
                RETURNING id
                """,
                (
                    item_id,
                    Jsonb("Prizm"),
                    Jsonb("Silver"),
                    Jsonb([{"source": "stale pre-confirmation suggestion"}]),
                ),
            ).fetchone()[0]

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
        assert confirmed.json()["prior_valuations_marked_incompatible"] == 1

        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
            valuation_state = connection.execute(
                """
                SELECT identity_compatibility = 'incompatible',
                       identity_compatibility_reasons ? 'superseded_by_confirmed_identity'
                FROM valuation_snapshot WHERE id = %s
                """,
                (valuation_id,),
            ).fetchone()
            correction_status = connection.execute(
                "SELECT status = 'superseded' FROM card_identity_correction WHERE id = %s",
                (correction_id,),
            ).fetchone()[0]
        assert valuation_state == (True, True)
        assert correction_status is True
        assert client.get(f"/v1/portfolio/items/{item_id}/identity-corrections").json() == []

        review = client.get(f"/v1/portfolio/identity-review/items/{item_id}")
        assert review.status_code == 200, review.text
        payload = review.json()
        assert payload["latest_version"]["review_status"] == "confirmed"
        assert Decimal(payload["latest_shadow"]["fair_value"]) == Decimal("8.46")
        assert payload["latest_shadow"]["evidence_count"] == 2

        detail = client.get(f"/v1/portfolio/items/{item_id}/detail")
        assert detail.status_code == 200, detail.text
        assert [row["id"] for row in detail.json()["valuations"]] == [valuation_id]
        portfolio = client.get("/v1/portfolio/items")
        assert portfolio.status_code == 200, portfolio.text
        item = next(row for row in portfolio.json() if row["id"] == item_id)
        assert item["fair_value"] is None
