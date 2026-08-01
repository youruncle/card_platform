from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from card_platform.market_repository import MarketRepository
from card_platform.schemas import MarketCompCreate


def comp(**updates) -> MarketCompCreate:
    values = {
        "owner_id": uuid4(),
        "occurred_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "sale_price": Decimal("100"),
        "currency": "USD",
        "listing_title": "2023 Prizm Test Card",
        **updates,
    }
    return MarketCompCreate(**values)


def test_ebay_import_requires_ebay_source_url() -> None:
    with pytest.raises(ValueError, match="matching source URL"):
        MarketRepository._validate_source(
            comp(source_code="ebay", source_url="https://example.com/item/1")
        )
    external_id = MarketRepository._validate_source(
        comp(
            source_code="ebay",
            source_url="https://www.ebay.com/itm/123",
            external_id="123",
        )
    )
    assert external_id == "123"


def test_ebay_import_derives_item_id_from_official_url() -> None:
    assert MarketRepository._validate_source(
        comp(
            source_code="ebay",
            source_url="https://www.ebay.com/itm/2024-Prizm-Test/123456789012",
        )
    ) == "123456789012"


def test_cardhobby_import_derives_item_id_from_official_url() -> None:
    assert MarketRepository._validate_source(
        comp(
            source_code="cardhobby",
            source_url="https://www.cardhobby.com.cn/market/item/123390745",
        )
    ) == "123390745"


def test_import_fingerprint_is_deterministic() -> None:
    payload = comp(
        source_code="130point",
        source_url="https://130point.com/sales/abc",
    )
    assert MarketRepository._validate_source(payload) == MarketRepository._validate_source(
        payload
    )


def test_130point_aggregated_sale_requires_underlying_id() -> None:
    with pytest.raises(ValueError, match="underlying marketplace external_id"):
        MarketRepository._validate_source(
            comp(
                source_code="130point",
                origin_source_code="ebay",
                source_url="https://130point.com/sales/abc",
            )
        )
    assert MarketRepository._validate_source(
        comp(
            source_code="130point",
            origin_source_code="ebay",
            source_url="https://130point.com/sales/abc",
            external_id="ebay:123456789012",
        )
    ) == "ebay:123456789012"


def test_weikajia_import_requires_export_identifier() -> None:
    with pytest.raises(ValueError, match="external_id or source_url"):
        MarketRepository._validate_source(comp(source_code="weikajia"))
