import pytest
from card_platform.ingestion.cardhobby import CardHobbyProvider


def row(i):
    return {
        "ID": i,
        "Status": -2,
        "ByWay": 2,
        "OutCount": 2,
        "EffectiveDate": "2026-01-01 20:00:00",
        "Title": "2024 Panini Revolution LeBron James #31",
        "LowestPrice": "10",
    }


@pytest.mark.asyncio
async def test_full_page_does_not_hide_second_page_after_three_title_matches(monkeypatch):
    provider = CardHobbyProvider(None, "unused")
    monkeypatch.setattr(
        provider, "build_query_plan", lambda _: [("one", "first"), ("two", "second")]
    )
    requests = []

    async def fetch(client, query, page):
        requests.append((query, page))
        return {
            "data": {
                "PagedMarketItemList": [row(i) for i in range(1, 61)] if page == 1 else [row(61)]
            }
        }

    monkeypatch.setattr(provider, "_get_search_payload", fetch)
    result = await provider._search_direct(
        {
            "player_name": "LeBron James",
            "season_start": 2024,
            "product_name": "Revolution",
            "card_number": "31",
        }
    )
    assert ("first", 2) in requests
    assert ("second", 1) in requests  # title scores are not final same-card decisions
    assert len(result.sales) <= 20


@pytest.mark.asyncio
async def test_page_budget_includes_second_pages(monkeypatch):
    provider = CardHobbyProvider(None, "unused", max_search_pages=3)
    monkeypatch.setattr(
        provider, "build_query_plan", lambda _: [("one", "first"), ("two", "second")]
    )
    requests = []

    async def fetch(client, query, page):
        requests.append((query, page))
        return {"data": {"PagedMarketItemList": [{}] * 60}}

    monkeypatch.setattr(provider, "_get_search_payload", fetch)
    await provider._search_direct({"player_name": "LeBron James"})
    assert requests == [("first", 1), ("second", 1), ("first", 2)]


@pytest.mark.asyncio
async def test_full_pages_do_not_starve_later_query_families(monkeypatch):
    provider = CardHobbyProvider(None, "unused", max_search_pages=3)
    monkeypatch.setattr(
        provider,
        "build_query_plan",
        lambda _: [("one", "first"), ("two", "second"), ("three", "third")],
    )
    requests = []

    async def fetch(client, query, page):
        requests.append((query, page))
        return {"data": {"PagedMarketItemList": [{}] * 60}}

    monkeypatch.setattr(provider, "_get_search_payload", fetch)
    await provider._search_direct({"player_name": "LeBron James"})
    assert requests == [("first", 1), ("second", 1), ("third", 1)]
