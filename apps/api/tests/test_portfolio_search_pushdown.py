from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform import portfolio_search as search


@pytest.mark.asyncio
async def test_structured_filters_are_applied_before_rows_leave_database(monkeypatch):
    monkeypatch.setattr(search, "search_vocabulary", AsyncMock(return_value=search.SearchVocabulary()))
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=[]))))
    owner = uuid4()
    filters = search.SearchFilters(holding_status="held", serial_number=4, serial_min=10,
        serial_max=99, year_min=2020, year_max=2025, grade_min=8, rookie=True,
        autograph=False, graded=True, has_cost=True, cost_max=100)

    assert await search.search_item_ids(connection, owner, filters) == []

    sql, parameters = connection.execute.call_args.args
    for fragment in ("ci.holding_status = %s", "ci.serial_number = %s",
                     "ci.serial_total >= %s", "ci.serial_total <= %s",
                     "COALESCE(cs.season_end, cs.season_start) >= %s", "cs.season_start <= %s",
                     "ci.grade_value >= %s", "cd.is_rookie = %s", "cd.is_autograph = %s",
                     "purchase.total IS NOT NULL", "purchase.total <= %s"):
        assert fragment in sql
    assert parameters == (owner, "held", True, False, 4, 2020, 2025, 10, 99, Decimal(8), Decimal(100))


@pytest.mark.asyncio
async def test_text_query_still_uses_alias_matching_and_owner_scope(monkeypatch):
    monkeypatch.setattr(search, "search_vocabulary", AsyncMock(return_value=search.SearchVocabulary()))
    owner, item_id = uuid4(), uuid4()
    row = {"id": item_id, "player_name": "Kevin Durant", "season_start": 2024}
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=[row]))))
    assert await search.search_item_ids(connection, owner, search.SearchFilters(q="杜兰特")) == [item_id]
    sql, parameters = connection.execute.call_args.args
    assert "ci.owner_id=%s" in sql
    assert parameters == (owner,)
