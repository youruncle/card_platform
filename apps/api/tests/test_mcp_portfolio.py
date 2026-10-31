from datetime import datetime, timezone
import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from card_platform.mcp.schemas import McpFailure, McpPrincipal
from card_platform.repository import PortfolioRepository
from card_platform.schemas import CardIdentityInput, CollectionItemCreate, PurchaseInput, TransactionCreate, ValuationCreate

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def card_case(mcp_database):
    from card_platform.mcp.portfolio import PortfolioTools

    repo = PortfolioRepository()
    async with mcp_database.connection() as conn:
        owners = []
        for _ in range(2):
            owners.append((await (await conn.execute(
                "INSERT INTO app_user(email,display_name) VALUES (%s,'Portfolio test') RETURNING id",
                (f"portfolio-{uuid4()}@example.invalid",),
            )).fetchone())["id"])
        ids = []
        cases = [
            ("Stephen Curry", 10, True, "PSA", "9", "100", "CNY", "10"),
            ("Stephen Curry", 11, True, "PSA", "9.5", "300", "CNY", "0"),
            ("Kobe Bryant", None, False, None, None, "100", "USD", "0"),
            ("LeBron James", None, False, None, None, "10", "USD", "0"),
        ]
        for player, serial, auto, grade_co, grade, price, currency, fee in cases:
            item_id = await repo.create_item(conn, CollectionItemCreate(owner_id=owners[0],
                card=CardIdentityInput(player_name=player, brand_name="Panini", product_name="Prizm",
                                       season_start=2023, season_end=2024, card_number="30", is_autograph=auto),
                serial_number=serial, serial_total=25 if serial else None,
                grade_company=grade_co, grade_value=grade,
                purchase=PurchaseInput(transacted_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    price=price, fee_amount=fee, currency=currency, fx_rate_to_base="7" if currency == "USD" else "1")))
            ids.append(item_id)
        await repo.add_valuation(conn, ids[0], owners[0], ValuationCreate(currency="CNY", low_value=190, fair_value=200, high_value=210))
        await repo.add_valuation(conn, ids[3], owners[0], ValuationCreate(currency="USD", low_value=19, fair_value=20, high_value=21))
        await repo.add_transaction(conn, ids[2], owners[0], TransactionCreate(transaction_type="sell",
            transacted_at=datetime(2026, 10, 10, tzinfo=timezone.utc), quantity=1, unit_price=200,
            currency="USD", fx_rate_to_base=7))
        await conn.execute("UPDATE collection_item SET holding_status='sold' WHERE id=%s", (ids[2],))
        foreign = await repo.create_item(conn, CollectionItemCreate(owner_id=owners[1],
            card=CardIdentityInput(player_name="Stephen Curry",brand_name="Panini",product_name="Prizm",season_start=2023)))
    principal = McpPrincipal(owner_id=owners[0], connection_id=uuid4(), scopes={"cards:read", "cards:write"})
    return PortfolioTools(mcp_database), principal, ids, foreign


@pytest.mark.parametrize("filters,indices", [
    ({"q": "库里"}, [0, 1]),
    ({"autograph": True}, [0, 1]),
    ({"numbered": True, "serial_max": 25}, [0, 1]),
    ({"graded": True, "grade_min": "9.5"}, [1]),
    ({"cost_min": "250"}, [1, 2]),
    ({"holding_status": "sold"}, [2]),
    ({"holding_status": "held", "autograph": False}, [3]),
    ({"brand": ["Panini"], "product": ["Prizm"], "year_min": 2023, "year_max": 2024}, [0, 1, 2, 3]),
    ({"valuation_status": ["unvalued"], "autograph": True}, [1]),
    ({"serial_number": 10}, [0]),
    ({"q": "Kobe"}, [2]),
    ({"q": "zzzz-no-such-player"}, []),
])
async def test_twelve_queries_have_explicit_expected_item_ids(card_case, filters, indices):
    from card_platform.mcp.portfolio import SearchCardsRequest
    tools, principal, ids, _ = card_case
    result = await tools.search_cards(principal, SearchCardsRequest(filters=filters))
    assert {UUID(item["id"]) for item in result["items"]} == {ids[i] for i in indices}
    assert result["total"] == len(indices)
    assert all(item["display_id"] >= 0 and item["revision"] for item in result["items"])


async def test_pagination_sort_and_duplicate_physical_cards(card_case):
    from card_platform.mcp.portfolio import SearchCardsRequest
    tools, principal, ids, _ = card_case
    first = await tools.search_cards(principal, SearchCardsRequest(filters={"autograph": True},
                                    sort_by="remaining_cost", order="asc", page_size=1))
    second = await tools.search_cards(principal, SearchCardsRequest(filters={"autograph": True},
                                     sort_by="remaining_cost", order="asc", page_size=1, page=2))
    assert [first["items"][0]["id"], second["items"][0]["id"]] == list(map(str, ids[:2]))
    assert first["next_page"] == 2
    assert second["next_page"] is None


async def test_detail_owner_isolation_and_revision_includes_ledger(card_case):
    from card_platform.mcp.portfolio import GetCardDetailRequest
    tools, principal, ids, foreign = card_case
    with pytest.raises(McpFailure) as error:
        await tools.get_card_detail(principal, GetCardDetailRequest(item_id=foreign))
    assert error.value.code == "NOT_FOUND"
    first = await tools.get_card_detail(principal, GetCardDetailRequest(item_id=ids[0]))
    assert len(first["transactions"]) == 1
    assert len(first["valuations"]) == 1
    async with tools.database.connection() as conn:
        await conn.execute("UPDATE card_transaction SET fee_amount=11 WHERE collection_item_id=%s", (ids[0],))
    second = await tools.get_card_detail(principal, GetCardDetailRequest(item_id=ids[0]))
    assert first["revision"] != second["revision"]


async def test_summary_separates_unknown_values_and_original_currencies(card_case):
    tools, principal, _, _ = card_case
    summary = await tools.get_portfolio_summary(principal, None)
    assert summary["held_item_count"] == 3
    assert summary["unvalued_item_count"] == 1
    assert {currency: Decimal(value) for currency, value in summary["known_market_value_by_currency"].items()} == {
        "CNY": Decimal("200"), "USD": Decimal("20"),
    }
    assert summary["total_market_value"] is None
    assert Decimal(summary["base_currency_cost"]) == Decimal("480")
    assert Decimal(summary["base_currency_realized_profit"]) == Decimal("700")
    assert summary["mixed_valuation_currencies"] is True


async def test_empty_portfolio_and_unknown_filters_are_explicit(card_case):
    from pydantic import ValidationError
    from card_platform.mcp.portfolio import SearchCardsRequest
    tools, principal, _, _ = card_case
    with pytest.raises(ValidationError):
        SearchCardsRequest(filters={"owner_id": str(uuid4())})
    for size in (0, 101):
        with pytest.raises(ValidationError):
            SearchCardsRequest(page_size=size)
    async with tools.database.connection() as conn:
        empty_owner = (await (await conn.execute(
            "INSERT INTO app_user(email,display_name) VALUES (%s,'Empty') RETURNING id",
            (f"empty-{uuid4()}@example.invalid",),
        )).fetchone())["id"]
    summary = await tools.get_portfolio_summary(principal.model_copy(update={"owner_id": empty_owner}), None)
    assert summary["held_item_count"] == 0
    assert summary["known_market_value_by_currency"] == {}


async def test_revision_after_waiting_for_item_lock_sees_committed_ledger(card_case):
    from card_platform.mcp.portfolio import card_revision
    tools, principal, ids, _ = card_case
    async with tools.database.connection() as conn:
        before = await card_revision(conn, principal.owner_id, ids[0])

    async def blocked_snapshot():
        async with tools.database.connection() as other:
            return await card_revision(other, principal.owner_id, ids[0], lock=True)

    async with tools.database.connection() as writer:
        await writer.execute("SELECT id FROM collection_item WHERE id=%s FOR UPDATE", (ids[0],))
        await writer.execute("UPDATE card_transaction SET fee_amount=17 WHERE collection_item_id=%s", (ids[0],))
        task = asyncio.create_task(blocked_snapshot())
        for _ in range(50):
            await writer.execute("SELECT pg_stat_clear_snapshot()")
            waiting = await (await writer.execute("""SELECT 1 FROM pg_stat_activity
                WHERE datname=current_database() AND wait_event_type='Lock'
                AND query LIKE '%%collection_item%%' AND pid<>pg_backend_pid()""")).fetchone()
            if waiting:
                break
            await asyncio.sleep(0.01)
        assert waiting, "snapshot did not reach the blocked item lock"
    after = await asyncio.wait_for(task, timeout=3)
    assert after != before
