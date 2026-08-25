from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from card_platform.ingestion.cardhobby import CardHobbySale, CardHobbySearchResult
from card_platform.market_data.cardhobby_provider import CardHobbyMarketDataProvider
from card_platform.market_data.models import MarketIdentity


class FakeCardHobbyClient:
    async def search(self, _identity: dict) -> CardHobbySearchResult:
        return CardHobbySearchResult(
            sales=[
                CardHobbySale(
                    item_id="123456",
                    title="2025-26 Topps Chrome Luka Doncic #2 红白蓝",
                    source_url="https://cardhobby.com.cn/Market/Item/123456",
                    occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                    sale_price=Decimal("12"),
                    shipping_price=Decimal("18"),
                    bid_count=3,
                    sale_type="auction",
                    evidence_level="ended_auction",
                    sold_status_evidence="Status=-2",
                    matching_fields=["cardhobby_tier:exact_or_near_exact"],
                    match_confidence=Decimal("0.80"),
                )
            ]
        )


@pytest.mark.asyncio
async def test_snapshot_refresh_reingests_older_sale_after_matcher_upgrade() -> None:
    provider = CardHobbyMarketDataProvider(FakeCardHobbyClient())
    identity = MarketIdentity(
        player_name="Luka Doncic",
        season_start=2025,
        season_end=2026,
        product_name="Topps Chrome Basketball",
        card_number="2",
        parallel_name="Red, White, and Blue Refractor",
    )

    result = await provider.fetch_sales(
        identity,
        since=datetime.now(timezone.utc) + timedelta(days=1),
    )

    assert len(result.evidence) == 1
    assert result.evidence[0].external_sale_id == "item:123456"
