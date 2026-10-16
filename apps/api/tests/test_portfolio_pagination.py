from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform import api
from card_platform.auth import AuthenticatedUser


@pytest.mark.asyncio
async def test_portfolio_page_clamps_page_and_uses_thirty_item_window(monkeypatch) -> None:
    owner_id = uuid4()
    user = AuthenticatedUser(
        id=owner_id,
        email="collector@example.com",
        display_name="Collector",
        base_currency="CNY",
        role="member",
        session_id=uuid4(),
    )
    count_items = AsyncMock(return_value=61)
    list_items = AsyncMock(return_value=[])
    monkeypatch.setattr(api.repository, "count_items", count_items)
    monkeypatch.setattr(api.repository, "list_items", list_items)
    connection = object()

    result = await api.list_items_page(
        owner_id=uuid4(),
        page=99,
        page_size=30,
        user=user,
        connection=connection,
    )

    assert result.total == 61
    assert result.page == 3
    assert result.page_size == 30
    assert result.total_pages == 3
    count_items.assert_awaited_once_with(connection, owner_id)
    list_items.assert_awaited_once_with(connection, owner_id, limit=30, offset=60)


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [10, 20, 30, 50, 100])
async def test_configurable_page_size(monkeypatch, page_size):
    user = AuthenticatedUser(id=uuid4(), email="collector@example.com", display_name="Collector",
                             base_currency="CNY", role="member", session_id=uuid4())
    monkeypatch.setattr(api.repository, "count_items", AsyncMock(return_value=161))
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(api.repository, "list_items", listing)
    connection = object()
    result = await api.list_items_page(page=2, page_size=page_size, user=user, connection=connection)
    assert result.page_size == page_size
    assert result.total_pages == (161 + page_size - 1) // page_size
    listing.assert_awaited_once_with(connection, user.id, limit=page_size, offset=page_size)


def test_page_size_http_and_response_boundaries():
    from fastapi import FastAPI
    from pydantic import ValidationError
    from card_platform.schemas import PortfolioPage
    app = FastAPI()
    app.include_router(api.router)
    parameter = next(p for p in app.openapi()["paths"]["/v1/portfolio/items/page"]["get"]["parameters"] if p["name"] == "page_size")
    assert parameter["schema"]["default"] == 30
    assert parameter["schema"]["maximum"] == 100
    for size in [0, 101]:
        with pytest.raises(ValidationError):
            PortfolioPage(items=[], total=0, page=1, page_size=size, total_pages=1)


@pytest.mark.asyncio
async def test_filtered_page_size_and_last_page(monkeypatch):
    user = AuthenticatedUser(id=uuid4(), email="collector@example.com", display_name="Collector",
                             base_currency="CNY", role="member", session_id=uuid4())
    ids = [uuid4() for _ in range(121)]
    search = AsyncMock(return_value=ids)
    listing = AsyncMock(return_value=[])
    monkeypatch.setattr(api, "search_item_ids", search)
    monkeypatch.setattr(api.repository, "list_items", listing)
    connection = object()
    result = await api.list_items_page(page=99, page_size=100, user=user,
                                       connection=connection, search='{"q":"Curry"}')
    assert result.page == 2
    assert result.total_pages == 2
    assert result.total == 121
    assert result.page_size == 100
    assert search.await_args.args[1] == user.id
    listing.assert_awaited_once_with(connection, user.id, item_ids=ids[100:])
