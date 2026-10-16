"""Sort after the shared final projection, before any page window."""

from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from card_platform import api
from card_platform.auth import AuthenticatedUser
from card_platform.schemas import PortfolioItem


def user():
    return AuthenticatedUser(
        id=uuid4(),
        email="sort@example.invalid",
        display_name="Sort",
        base_currency="CNY",
        role="member",
        session_id=uuid4(),
    )


def item(number, **values):
    # Only fields read by the sorting boundary matter to this projection unit fixture.
    return PortfolioItem.model_construct(
        id=UUID(int=number),
        player_name=f"Player {number}",
        season_start=2025,
        remaining_cost=Decimal(number),
        purchase_price=Decimal(number),
        fair_value=Decimal(number),
        valuation_currency="CNY",
        **values,
    )


@pytest.mark.asyncio
async def test_sort_full_candidate_set_before_paging(monkeypatch):
    rows = [item(i) for i in range(1, 42)]
    listing = AsyncMock(return_value=rows)
    monkeypatch.setattr(api.repository, "list_items", listing)
    result = await api.list_items_page(
        page=1, page_size=10, sort_by="fair_value", order="desc", user=user(), connection=object()
    )
    assert [row.id.int for row in result.items] == list(range(41, 31, -1))
    assert result.total == 41
    assert "limit" not in listing.await_args.kwargs


@pytest.mark.asyncio
async def test_filters_do_not_page_candidate_ids_before_sorting(monkeypatch):
    ids = [UUID(int=i) for i in range(1, 42)]
    monkeypatch.setattr(api, "search_item_ids", AsyncMock(return_value=ids))
    listing = AsyncMock(return_value=[item(i) for i in range(1, 42)])
    monkeypatch.setattr(api.repository, "list_items", listing)
    result = await api.list_items_page(
        page=2,
        page_size=10,
        search='{"q":"Player"}',
        sort_by="remaining_cost",
        order="asc",
        user=user(),
        connection=object(),
    )
    assert [row.id.int for row in result.items] == list(range(11, 21))
    assert listing.await_args.kwargs["item_ids"] == ids


def test_decimal_zero_currency_missing_and_stable_ties():
    from card_platform.portfolio_sort import sorted_items

    rows = [item(i) for i in range(1, 7)]
    rows[0].fair_value = Decimal("1.00000000000000001")
    rows[1].fair_value = Decimal("1.00000000000000002")
    rows[2].fair_value = Decimal("0")
    rows[3].fair_value = None
    rows[3].shadow_fair_value = Decimal("99999")
    rows[4].valuation_currency = "USD"
    rows[5].fair_value = Decimal("0")
    assert [row.id.int for row in sorted_items(rows, "fair_value", "asc", "CNY")] == [
        3,
        6,
        1,
        2,
        4,
        5,
    ]
    assert [row.id.int for row in sorted_items(rows, "fair_value", "desc", "CNY")] == [
        2,
        1,
        3,
        6,
        4,
        5,
    ]
    rows[0].player_name, rows[1].player_name = " Z  Last ", "a First"
    assert [row.id.int for row in sorted_items(rows[:2], "player_name", "asc", "CNY")] == [2, 1]


def test_snapshot_cache_limits_expiry_binding_and_capacity(monkeypatch):
    import card_platform.portfolio_sort as module
    from fastapi import HTTPException

    now = [0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    cache = module.SnapshotCache(max_bytes=100_000, max_entries=4)
    owner, other = uuid4(), uuid4()
    tokens = [cache.create(owner, ("query",), [item(1)])[0] for _ in range(4)]
    assert len(cache.entries) == 3
    for token, who, binding in [
        (tokens[0], owner, ("query",)),
        (tokens[-1], other, ("query",)),
        (tokens[-1], owner, ("other query",)),
    ]:
        with pytest.raises(HTTPException) as exc:
            cache.get(token, who, binding)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "snapshot_expired"
    cache.create(other, ("query",), [])
    cache.create(uuid4(), ("query",), [])
    assert len(cache.entries) == 4
    assert cache.size <= cache.max_bytes
    now[0] = 61
    with pytest.raises(HTTPException):
        cache.get(tokens[-1], owner, ("query",))
    assert cache.size == 0
    with pytest.raises(HTTPException) as exc:
        module.SnapshotCache(max_bytes=10).create(owner, (), [item(1)])
    assert exc.value.status_code == 503


def test_sort_http_whitelist():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    application = FastAPI()
    application.include_router(api.router)
    application.dependency_overrides[api.current_user] = user
    application.dependency_overrides[api.get_connection] = lambda: object()
    with TestClient(application) as client:
        for query in ["sort_by=drop_table", "order=sideways"]:
            assert client.get("/v1/portfolio/items/page?" + query).status_code == 422


def test_profit_sort_uses_final_projection_and_keeps_unknown_last():
    from card_platform.portfolio_sort import sorted_items
    from typing import get_args
    from card_platform.portfolio_sort import SortBy

    assert "unrealized_profit" in get_args(SortBy)
    rows = [item(i, unrealized_profit=value) for i, value in enumerate(
        [Decimal("-20"), Decimal("0"), Decimal("15"), None, Decimal("15")], 1)]
    assert [r.id.int for r in sorted_items(rows, "unrealized_profit", "desc", "CNY")] == [3, 5, 2, 1, 4]
    assert [r.id.int for r in sorted_items(rows, "unrealized_profit", "asc", "CNY")] == [1, 2, 3, 5, 4]


@pytest.mark.asyncio
async def test_combined_summary_uses_one_full_projection_before_filtering(monkeypatch):
    ids = [UUID(int=2)]
    monkeypatch.setattr(api, "search_item_ids", AsyncMock(return_value=ids))
    rows = [item(i, realized_profit=Decimal(0), quantity_held=1, unrealized_profit=Decimal(0)) for i in range(1, 4)]
    listing = AsyncMock(return_value=rows)
    monkeypatch.setattr(api.repository, "list_items", listing)
    result = await api.list_items_page(
        page=1, page_size=10, search='{"q":"Player"}', sort_by="unrealized_profit",
        include_summary=True, user=user(), connection=object(),
    )
    listing.assert_awaited_once()
    assert listing.await_args.kwargs["item_ids"] is None
    assert result.summary.item_count == 3
    assert result.summary.total_cost == 6
    assert [r.id for r in result.items] == ids


@pytest.mark.asyncio
async def test_relevance_preserves_all_candidate_ranks_before_paging(monkeypatch):
    ids = [UUID(int=i) for i in [3, 1, 2]]
    monkeypatch.setattr(api, "search_item_ids", AsyncMock(return_value=ids))
    monkeypatch.setattr(api.repository, "list_items", AsyncMock(return_value=[item(i) for i in range(1, 4)]))
    result = await api.list_items_page(page=1, page_size=2, search='{"q":"Player"}',
        sort_by="relevance", order="desc", user=user(), connection=object())
    assert [r.id for r in result.items] == ids[:2]
