from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class TransactionKind(StrEnum):
    BUY = "buy"
    SELL = "sell"
    FEE = "fee"
    ADJUSTMENT = "adjustment"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    kind: TransactionKind
    quantity: Decimal
    unit_price: Decimal
    fee_amount: Decimal
    fx_rate_to_base: Decimal


@dataclass(frozen=True, slots=True)
class Position:
    quantity_held: Decimal
    remaining_cost: Decimal
    realized_profit: Decimal


def calculate_position(entries: list[LedgerEntry]) -> Position:
    """Calculate moving-average cost and realized profit in base currency."""
    quantity = Decimal("0")
    remaining_cost = Decimal("0")
    realized_profit = Decimal("0")

    for entry in entries:
        gross = entry.quantity * entry.unit_price * entry.fx_rate_to_base
        fee = entry.fee_amount * entry.fx_rate_to_base

        if entry.kind == TransactionKind.BUY:
            quantity += entry.quantity
            remaining_cost += gross + fee
        elif entry.kind == TransactionKind.SELL:
            if entry.quantity > quantity:
                raise ValueError("sell quantity exceeds current position")
            average_cost = remaining_cost / quantity
            sold_cost = average_cost * entry.quantity
            realized_profit += gross - fee - sold_cost
            remaining_cost -= sold_cost
            quantity -= entry.quantity
        elif entry.kind == TransactionKind.FEE:
            if quantity:
                remaining_cost += fee
            else:
                realized_profit -= fee
        elif entry.kind == TransactionKind.ADJUSTMENT:
            remaining_cost += gross

    return Position(
        quantity_held=quantity,
        remaining_cost=remaining_cost,
        realized_profit=realized_profit,
    )
