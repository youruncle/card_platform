from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.repository import NotFoundError, PortfolioRepository


@pytest.mark.asyncio
async def test_detail_limits_projection_to_requested_card():
    repository = PortfolioRepository()
    owner_id, item_id = uuid4(), uuid4()
    repository.list_items = AsyncMock(return_value=[])
    connection = SimpleNamespace()

    with pytest.raises(NotFoundError):
        await repository.item_detail(connection, owner_id, item_id)

    repository.list_items.assert_awaited_once_with(
        connection, owner_id, item_ids=[item_id]
    )


@pytest.mark.asyncio
async def test_filtered_projection_limits_ledger_to_requested_cards():
    owner_id, item_id = uuid4(), uuid4()
    result = SimpleNamespace(fetchall=AsyncMock(return_value=[]))
    connection = SimpleNamespace(execute=AsyncMock(return_value=result))

    await PortfolioRepository()._owner_ledger_entries(connection, owner_id, item_ids=[item_id])

    ledger_sql, parameters = connection.execute.call_args.args
    assert "ci.owner_id = %s" in ledger_sql
    assert "t.collection_item_id = ANY(%s::uuid[])" in ledger_sql
    assert parameters == (owner_id, [item_id])


@pytest.mark.asyncio
@pytest.mark.parametrize("item_ids", [None, []])
async def test_ledger_scope_preserves_full_collection_and_empty_selection(item_ids):
    owner_id = uuid4()
    result = SimpleNamespace(fetchall=AsyncMock(return_value=[]))
    connection = SimpleNamespace(execute=AsyncMock(return_value=result))

    entries = await PortfolioRepository()._owner_ledger_entries(
        connection, owner_id, item_ids=item_ids
    )

    sql, parameters = connection.execute.call_args.args
    assert not entries
    assert "ci.owner_id = %s" in sql
    assert "ci.deleted_at IS NULL" in sql
    if item_ids is None:
        assert "ANY" not in sql
        assert parameters == (owner_id,)
    else:
        assert "t.collection_item_id = ANY(%s::uuid[])" in sql
        assert parameters == (owner_id, [])


@pytest.mark.asyncio
async def test_empty_page_does_not_scan_owner_ledger():
    result = SimpleNamespace(fetchall=AsyncMock(return_value=[]))
    connection = SimpleNamespace(execute=AsyncMock(return_value=result))
    assert await PortfolioRepository().list_items(connection, uuid4(), limit=30) == []
    assert connection.execute.await_count == 1
