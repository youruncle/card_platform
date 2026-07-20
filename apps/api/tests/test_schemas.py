from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from card_platform.schemas import CardIdentityInput, CollectionItemCreate, ValuationCreate


def test_card_can_be_rookie_auto_and_patch() -> None:
    card = CardIdentityInput(
        player_name="Jalen Duren",
        brand_name="National Treasures",
        season_start=2022,
        season_end=2023,
        product_name="National Treasures Basketball",
        is_rookie=True,
        is_autograph=True,
        is_patch=True,
        print_run=99,
    )

    assert card.is_rookie and card.is_autograph and card.is_patch


def test_invalid_serial_is_rejected() -> None:
    with pytest.raises(ValidationError, match="serial_number cannot exceed"):
        CollectionItemCreate(
            owner_id=uuid4(),
            card={
                "player_name": "Player",
                "brand_name": "Brand",
                "season_start": 2024,
                "product_name": "Product",
            },
            serial_number=100,
            serial_total=99,
        )


def test_invalid_valuation_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="low_value <= fair_value"):
        ValuationCreate(
            valued_at=datetime.now(timezone.utc),
            currency="CNY",
            low_value=Decimal("300"),
            fair_value=Decimal("200"),
            high_value=Decimal("400"),
        )
