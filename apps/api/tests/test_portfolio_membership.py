from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from card_platform.portfolio_sort import sorted_page, SnapshotCache


@pytest.mark.asyncio
async def test_membership_snapshot_projects_only_requested_page_and_keeps_order():
    ids = [UUID(int=i) for i in range(65, 0, -1)]
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=[{"id": i} for i in ids]))))
    repository = SimpleNamespace(list_items=AsyncMock(side_effect=lambda *args, **kw: [SimpleNamespace(id=i) for i in reversed(kw["item_ids"])]))
    user = SimpleNamespace(id=uuid4(), base_currency="CNY")
    # Projection fixtures must serialize through the page contract.
    from test_portfolio_sorting import item
    repository.list_items.side_effect = lambda *args, **kw: [item(i.int) for i in reversed(kw["item_ids"])]
    kwargs = dict(filters=None, candidate_ids=None, sort_by="created_at", order="desc", page_size=30, snapshot=True, snapshot_mode="membership")
    first = await sorted_page(connection, repository, user, page=1, snapshot_token=None, **kwargs)
    assert [i.id for i in first.items] == ids[:30]
    assert repository.list_items.await_args.kwargs["item_ids"] == ids[:30]
    second = await sorted_page(connection, repository, user, page=2, snapshot_token=first.snapshot_token, **kwargs)
    assert [i.id for i in second.items] == ids[30:60]
    assert second.total == 65
    assert repository.list_items.await_args.kwargs["item_ids"] == ids[30:60]


def test_membership_cache_is_bounded_and_owner_bound():
    from fastapi import HTTPException
    cache = SnapshotCache(max_bytes=20000)
    owner = uuid4()
    token, cached = cache.create(owner, ("membership",), [], ids=[UUID(int=i) for i in range(100)])
    assert not cached.rows
    assert len(cached.ids) == 100
    with pytest.raises(HTTPException):
        cache.get(token, uuid4(), ("membership",))
    with pytest.raises(HTTPException):
        SnapshotCache(max_bytes=1000).create(owner, (), [], ids=[uuid4()] * 100)
