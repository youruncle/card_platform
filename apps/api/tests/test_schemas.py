from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from card_platform.schemas import (
    CardIdentityInput,
    CardRecognitionResult,
    CollectionItemCreate,
    SheetCardRecognitionItem,
    ValuationCreate,
)


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


def test_multi_card_crop_bbox_must_be_valid_normalized_coordinates() -> None:
    result = CardRecognitionResult(confidence="0.8")
    item = SheetCardRecognitionItem(result=result, card_image_bbox=(10, 20, 300, 500))
    assert item.card_image_bbox == (10, 20, 300, 500)
    with pytest.raises(ValidationError, match="positive width"):
        SheetCardRecognitionItem(result=result, card_image_bbox=(300, 20, 10, 500))


def test_recognition_normalizes_scalar_field_evidence() -> None:
    result = CardRecognitionResult.model_validate(
        {
            "confidence": "0.8",
            "player_name": "Collin Murray-Boyles",
            "field_evidence": {
                "player_name": "Collin Murray-Boyles",
                "season_start": "2025",
                "parallel_name": None,
            },
        }
    )

    assert result.field_evidence == {
        "player_name": ["Collin Murray-Boyles"],
        "season_start": ["2025"],
        "parallel_name": [],
    }


def test_recognition_recovers_ocr_serial_fraction_from_decimal_separator() -> None:
    result = CardRecognitionResult.model_validate(
        {"confidence": "0.8", "serial_number": "7.23", "serial_total": None}
    )

    assert result.serial_number == 7
    assert result.serial_total == 23
    assert result.is_numbered is True
