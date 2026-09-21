from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from card_platform.domain.portfolio import (
    LedgerEntry,
    TransactionKind,
    calculate_position,
)
from card_platform.repository import PortfolioRepository, build_portfolio_summary
from card_platform.schemas import CardIdentityInput, CollectionItemCreate


def entry(
    kind: TransactionKind,
    quantity: str,
    unit_price: str,
    fee: str = "0",
    fx: str = "1",
) -> LedgerEntry:
    return LedgerEntry(
        kind=kind,
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price),
        fee_amount=Decimal(fee),
        fx_rate_to_base=Decimal(fx),
    )


def test_buy_position_includes_fee_and_fx() -> None:
    position = calculate_position([entry(TransactionKind.BUY, "1", "100", fee="5", fx="7.2")])

    assert position.quantity_held == Decimal("1")
    assert position.remaining_cost == Decimal("756.0")
    assert position.realized_profit == Decimal("0")


def test_partial_sale_uses_moving_average_cost() -> None:
    position = calculate_position(
        [
            entry(TransactionKind.BUY, "1", "100", fee="10"),
            entry(TransactionKind.BUY, "1", "200", fee="10"),
            entry(TransactionKind.SELL, "1", "220", fee="5"),
        ]
    )

    assert position.quantity_held == Decimal("1")
    assert position.remaining_cost == Decimal("160")
    assert position.realized_profit == Decimal("55")


def test_full_sale_leaves_no_cost() -> None:
    position = calculate_position(
        [
            entry(TransactionKind.BUY, "1", "260"),
            entry(TransactionKind.SELL, "1", "310", fee="10"),
        ]
    )

    assert position.quantity_held == Decimal("0")
    assert position.remaining_cost == Decimal("0")
    assert position.realized_profit == Decimal("40")


def test_oversell_is_rejected() -> None:
    with pytest.raises(ValueError, match="exceeds current position"):
        calculate_position(
            [
                entry(TransactionKind.BUY, "1", "100"),
                entry(TransactionKind.SELL, "2", "100"),
            ]
        )


def test_portfolio_summary_uses_cost_as_provisional_value_when_unvalued() -> None:
    owner_id = uuid4()
    valued = SimpleNamespace(
        id=uuid4(),
        remaining_cost=Decimal("160"),
        realized_profit=Decimal("20"),
        fair_value=Decimal("110"),
        valuation_currency="CNY",
        quantity_held=Decimal("2"),
        unrealized_profit=Decimal("60"),
    )
    unvalued = SimpleNamespace(
        id=uuid4(),
        remaining_cost=Decimal("75"),
        realized_profit=Decimal("0"),
        fair_value=None,
        valuation_currency=None,
        quantity_held=Decimal("1"),
        unrealized_profit=None,
    )

    summary = build_portfolio_summary([valued, unvalued], owner_id, "CNY")

    assert summary.total_cost == Decimal("235")
    assert summary.total_market_value == Decimal("295")
    assert summary.unrealized_profit == Decimal("60")
    assert summary.valued_item_count == 1
    assert summary.unvalued_item_count == 1


class _SetResult:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _SetConnection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def execute(self, query, params):
        self.calls.append((query, params))
        if query.lstrip().startswith("SELECT"):
            return _SetResult(self.row)
        return _SetResult(None)


@pytest.mark.asyncio
async def test_existing_calendar_year_set_upgrades_to_confirmed_cross_year() -> None:
    set_id = uuid4()
    connection = _SetConnection({"id": set_id, "season_end": 2025})
    payload = CollectionItemCreate(
        owner_id=uuid4(),
        card=CardIdentityInput(
            player_name="Luka Doncic",
            brand_name="Topps",
            season_start=2025,
            season_end=2026,
            product_name="NBA Hoops",
        ),
    )

    resolved = await PortfolioRepository()._get_or_create_set(
        connection, uuid4(), payload
    )

    assert resolved == set_id
    assert any(
        call[0].startswith("UPDATE card_set SET season_end")
        and call[1] == (2026, set_id)
        for call in connection.calls
    )
