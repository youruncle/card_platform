from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest

from card_platform.market_data.ebay_provider import (
    EBAY_SCOPE,
    EbayMarketplaceInsightsProvider,
)
from card_platform.market_data.models import MarketIdentity


def _identity() -> MarketIdentity:
    return MarketIdentity(
        player_name="Victor Wembanyama",
        season_start=2023,
        season_end=2024,
        brand_name="Panini",
        product_name="Prizm",
        card_number="136",
        parallel_name="Silver",
        grade_company="PSA",
        grade_value=Decimal("10"),
    )


@pytest.mark.asyncio
async def test_official_ebay_sales_are_filtered_and_normalized() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/oauth2/token"):
            assert request.headers["authorization"].startswith("Basic ")
            assert parse_qs(request.content.decode())["scope"] == [EBAY_SCOPE]
            return httpx.Response(200, json={"access_token": "official-token"})
        assert request.headers["authorization"] == "Bearer official-token"
        assert request.headers["x-ebay-c-marketplace-id"] == "EBAY_US"
        return httpx.Response(
            200,
            json={
                "itemSales": [
                    {
                        "itemId": "v1|123456789|0",
                        "title": (
                            "2023-24 Panini Prizm Victor Wembanyama "
                            "Silver #136 PSA 10"
                        ),
                        "lastSoldDate": "2026-07-20T12:30:00.000Z",
                        "lastSoldPrice": {"value": "525.50", "currency": "USD"},
                        "itemWebUrl": "https://www.ebay.com/itm/123456789",
                    },
                    {
                        "itemId": "wrong-grade",
                        "title": (
                            "2023-24 Panini Prizm Victor Wembanyama "
                            "Silver #136 PSA 9"
                        ),
                        "lastSoldDate": "2026-07-21T12:30:00.000Z",
                        "lastSoldPrice": {"value": "300", "currency": "USD"},
                        "itemWebUrl": "https://www.ebay.com/itm/wrong-grade",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = EbayMarketplaceInsightsProvider(
            "client-id",
            "client-secret",
            token_url="https://api.ebay.test/identity/v1/oauth2/token",
            insights_url=(
                "https://api.ebay.test/buy/marketplace_insights/"
                "v1_beta/item_sales/search"
            ),
            client=client,
        )
        result = await provider.fetch_sales(
            _identity(),
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )

    query_count = len(EbayMarketplaceInsightsProvider.build_queries(_identity()))
    assert len(requests) == 1 + query_count
    assert all("Victor+Wembanyama" in str(request.url) for request in requests[1:])
    assert all("lastSoldDate" in str(request.url) for request in requests[1:])
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.provider_code == "ebay"
    assert evidence.external_sale_id == "v1|123456789|0"
    assert evidence.sale_price == Decimal("525.50")
    assert evidence.currency == "USD"
    assert evidence.grade_company == "PSA"
    assert evidence.grade_value == Decimal("10")
    assert evidence.evidence_level == "confirmed_transaction"
    assert evidence.identity_classification["origin_source_code"] == "ebay"


def test_ebay_provider_requires_credentials_and_builds_specific_query() -> None:
    with pytest.raises(ValueError, match="credentials"):
        EbayMarketplaceInsightsProvider("", "")
    query = EbayMarketplaceInsightsProvider.build_query(_identity())
    assert "Victor Wembanyama" in query
    assert "2023-24" in query
    assert "Prizm" in query
    assert "136" in query
    assert "Silver" in query
    assert "PSA 10" in query
    queries = EbayMarketplaceInsightsProvider.build_queries(_identity())
    assert any("/" not in candidate for candidate in queries[1:])
    assert "Victor Wembanyama 136 PSA 10" in queries
