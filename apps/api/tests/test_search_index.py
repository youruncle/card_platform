import asyncio

import httpx

from card_platform.search_index import SearchIndexResearch


def test_brave_response_preserves_query_and_verbatim_snippet() -> None:
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200,
            request=request,
            json={
                "web": {
                    "results": [{
                        "title": "Card sold",
                        "url": "https://www.ebay.com/itm/123456789012",
                        "description": "Sold Aug 12, 2026 US $8.50",
                        "page_age": "2026-08-12",
                    }]
                }
            },
        ))
        research = SearchIndexResearch(brave_api_key="test")
        async with httpx.AsyncClient(transport=transport) as client:
            return await research._search_brave(client, "exact query")

    hits = asyncio.run(run())

    assert len(hits) == 1
    assert hits[0].provider == "brave"
    assert hits[0].query == "exact query"
    assert hits[0].snippet == "Sold Aug 12, 2026 US $8.50"


def test_serpapi_response_preserves_organic_result_evidence() -> None:
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200,
            request=request,
            json={
                "organic_results": [{
                    "title": "Old eBay item",
                    "link": "https://www.ebay.com/itm/123456789012",
                    "snippet": "Sold on 2026-08-12 USD 8.50",
                    "date": "Aug 12, 2026",
                }]
            },
        ))
        research = SearchIndexResearch(serpapi_key="test")
        async with httpx.AsyncClient(transport=transport) as client:
            return await research._search_serpapi(client, "exact query")

    hits = asyncio.run(run())

    assert len(hits) == 1
    assert hits[0].provider == "serpapi"
    assert hits[0].result_date == "Aug 12, 2026"
