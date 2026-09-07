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
