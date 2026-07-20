import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from card_platform.config import get_settings
from card_platform.ingestion.providers import EventRecord, MetricRecord
from card_platform.ingestion.service import PlayerSyncService
from card_platform.main import app


class FakeNBAProvider:
    code = "nba_stats"

    async def fetch(self, _: int) -> list[MetricRecord]:
        return [
            MetricRecord(
                season="2025-26",
                scope="season",
                metrics={"pts": 15.2, "reb": 10.1, "gp": 80},
                external_id="fake-season",
            )
        ]


class FakeEventProvider:
    def __init__(self, code: str, event_type: str) -> None:
        self.code = code
        self.event_type = event_type

    async def fetch(self, _: str) -> list[EventRecord]:
        return [
            EventRecord(
                event_type=self.event_type,
                occurred_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
                headline=f"Synced {self.event_type} update",
                summary="Provider fixture",
                source_url=f"https://example.com/{self.event_type}",
                external_id=f"fake-{self.event_type}",
            )
        ]


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ,
    reason="requires a migrated PostgreSQL database",
)


def test_full_investment_workflow(tmp_path) -> None:
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ["UPLOAD_DIR"] = str(tmp_path / "uploads")
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    app.state.player_sync_service = PlayerSyncService(
        get_settings(),
        providers={
            "nba_stats": FakeNBAProvider(),
            "news": FakeEventProvider("news_rss", "news"),
            "injury": FakeEventProvider("injury_rss", "injury"),
        },
    )
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200, ready.text
        user = client.post(
            "/v1/users",
            json={
                "email": f"e2e-{uuid4()}@card-alpha.local",
                "display_name": "E2E Portfolio",
                "base_currency": "CNY",
            },
        )
        assert user.status_code == 201, user.text
        owner_id = user.json()["id"]

        created = client.post(
            "/v1/portfolio/items",
            json={
                "owner_id": owner_id,
                "card": {
                    "player_name": "Jalen Duren",
                    "brand_name": "National Treasures",
                    "brand_tier": 5,
                    "season_start": 2022,
                    "season_end": 2023,
                    "product_name": "National Treasures Basketball",
                    "insert_name": "Rookie Autographs",
                    "is_rookie": True,
                    "is_autograph": True,
                    "print_run": 99,
                },
                "serial_number": 23,
                "serial_total": 99,
                "strategy_tags": ["高端品牌低价球员", "RC潜力股"],
                "purchase": {
                    "transacted_at": "2026-07-01T10:00:00Z",
                    "price": 260,
                    "currency": "CNY",
                    "fx_rate_to_base": 1,
                },
            },
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        assert UUID(item_id)

        items = client.get("/v1/portfolio/items", params={"owner_id": owner_id})
        assert items.status_code == 200, items.text
        item = items.json()[0]
        player_id = item["player_id"]
        assert Decimal(item["remaining_cost"]) == Decimal("260")

        image = client.post(
            f"/v1/portfolio/items/{item_id}/images",
            params={"owner_id": owner_id, "side": "front"},
            files={"image": ("duren.jpg", b"\xff\xd8\xffsafe-test-image", "image/jpeg")},
        )
        assert image.status_code == 201, image.text
        image_url = image.json()["image_url"]
        assert image_url.startswith("/uploads/")
        assert (tmp_path / "uploads" / image_url.removeprefix("/uploads/")).read_bytes() == b"\xff\xd8\xffsafe-test-image"
        item_with_image = client.get(
            "/v1/portfolio/items", params={"owner_id": owner_id}
        ).json()[0]
        assert item_with_image["image_front_url"] == image_url

        recognition = client.post(
            "/v1/cards/recognize",
            files={"image": ("duren.jpg", b"\xff\xd8\xffsafe-test-image", "image/jpeg")},
        )
        assert recognition.status_code == 503
        assert "OPENAI_API_KEY" in recognition.json()["detail"]

        for index, price in enumerate((260, 280, 300), start=1):
            comp = client.post(
                f"/v1/portfolio/items/{item_id}/market-comps",
                json={
                    "owner_id": owner_id,
                    "occurred_at": f"2026-07-{index:02d}T10:00:00Z",
                    "sale_price": price,
                    "shipping_price": 0,
                    "currency": "CNY",
                    "fx_rate_to_base": 1,
                    "listing_title": f"Comparable sale {index}",
                    "match_confidence": 0.9,
                },
            )
            assert comp.status_code == 201, comp.text
        comps = client.get(
            f"/v1/portfolio/items/{item_id}/market-comps",
            params={"owner_id": owner_id},
        )
        assert comps.status_code == 200, comps.text
        assert len(comps.json()) == 3
        valuation = client.post(
            f"/v1/portfolio/items/{item_id}/market-estimate",
            json={"owner_id": owner_id, "currency": "CNY"},
        )
        assert valuation.status_code == 200, valuation.text
        assert Decimal(valuation.json()["fair_value"]) == Decimal("280")
        assert valuation.json()["evidence_count"] == 3

        second_buy = client.post(
            f"/v1/portfolio/items/{item_id}/transactions",
            json={
                "transaction_type": "buy",
                "transacted_at": "2026-07-10T10:00:00Z",
                "quantity": 1,
                "unit_price": 300,
                "fee_amount": 0,
                "currency": "CNY",
                "fx_rate_to_base": 1,
            },
        )
        assert second_buy.status_code == 201, second_buy.text
        sale = client.post(
            f"/v1/portfolio/items/{item_id}/transactions",
            json={
                "transaction_type": "sell",
                "transacted_at": "2026-07-15T10:00:00Z",
                "quantity": 1,
                "unit_price": 350,
                "fee_amount": 10,
                "currency": "CNY",
                "fx_rate_to_base": 1,
            },
        )
        assert sale.status_code == 201, sale.text

        metric = client.put(
            f"/v1/players/{player_id}/metrics",
            json={
                "season": "2025-26",
                "scope": "season",
                "metrics": {"pts": 15.2, "reb": 10.1, "fg_pct": 0.61},
            },
        )
        assert metric.status_code == 204, metric.text
        event = client.post(
            f"/v1/players/{player_id}/events",
            json={
                "event_type": "team_context",
                "occurred_at": "2026-07-16T10:00:00Z",
                "headline": "Role remains stable",
                "summary": "Projected starting center",
                "sentiment": 0.4,
                "impact_score": 65,
            },
        )
        assert event.status_code == 201, event.text
        potential = client.post(
            f"/v1/players/{player_id}/potential",
            json={
                "age": 22,
                "performance_score": 75,
                "performance_trend": 30,
                "availability_score": 82,
                "role_score": 88,
                "team_environment_score": 70,
            },
        )
        assert potential.status_code == 200, potential.text
        assert float(potential.json()["score"]) >= 75

        intelligence = client.get(f"/v1/players/{player_id}/intelligence")
        assert intelligence.status_code == 200, intelligence.text
        assert intelligence.json()["metrics"][0]["metrics"]["reb"] == 10.1
        assert intelligence.json()["events"][0]["event_type"] == "team_context"

        sync = client.post(
            f"/v1/players/{player_id}/sync",
            json={
                "nba_person_id": 1631105,
                "sources": ["nba_stats", "news", "injury"],
            },
        )
        assert sync.status_code == 200, sync.text
        assert sync.json()["status"] == "completed"
        assert sync.json()["source_results"]["nba_stats"]["count"] == 1
        synced_intelligence = client.get(f"/v1/players/{player_id}/intelligence").json()
        assert {event["event_type"] for event in synced_intelligence["events"]} >= {
            "news",
            "injury",
            "team_context",
        }

        automatic_analysis = client.post(
            f"/v1/portfolio/items/{item_id}/analysis/auto",
            json={"owner_id": owner_id, "asking_price": 250, "currency": "CNY"},
        )
        assert automatic_analysis.status_code == 201, automatic_analysis.text
        assert Decimal(automatic_analysis.json()["factor_scores"]["age_potential"]) >= 75
        assert automatic_analysis.json()["fair_value_low"] == "260.00"
        assert automatic_analysis.json()["fair_value_high"] == "300.00"

        analysis = client.post(
            f"/v1/portfolio/items/{item_id}/analysis",
            json={
                "owner_id": owner_id,
                "asking_price": 250,
                "currency": "CNY",
                "fair_value_low": 270,
                "fair_value_high": 320,
                "evidence_count": 4,
                "factors": {
                    "historical_standing": 55,
                    "current_performance": 76,
                    "age_potential": 86,
                    "card_scarcity": 90,
                    "brand_tier": 95,
                    "market_heat": 72,
                },
            },
        )
        assert analysis.status_code == 201, analysis.text
        assert analysis.json()["rating"] in {"strong_buy", "buy"}
        narrative = client.post(f"/v1/analyses/{analysis.json()['analysis_id']}/narrative")
        assert narrative.status_code == 200, narrative.text
        assert narrative.json()["provider"] == "rules"
        assert "规则模型降级解释" in narrative.json()["narrative"]["disclaimer"]

        detail = client.get(f"/v1/portfolio/items/{item_id}/detail", params={"owner_id": owner_id})
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["transactions"]) == 3
        assert len(detail.json()["valuations"]) == 1
        assert Decimal(detail.json()["item"]["remaining_cost"]) == Decimal("280")
        assert Decimal(detail.json()["item"]["realized_profit"]) == Decimal("60")

        summary = client.get("/v1/portfolio/summary", params={"owner_id": owner_id})
        assert summary.status_code == 200, summary.text
        assert summary.json()["item_count"] == 1
        assert Decimal(summary.json()["total_cost"]) == Decimal("280")
        assert Decimal(summary.json()["total_market_value"]) == Decimal("280")
