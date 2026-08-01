from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from card_platform.cardhobby_import import (
    CARDHOBBY_ITEM_URL,
    CardHobbyOrderParser,
    full_size_cardhobby_image_url,
    recover_sale_price,
)
from card_platform.config import Settings
from card_platform.schemas import CardHobbyOrderCapture, CardIdentityInput


def capture(**updates) -> CardHobbyOrderCapture:
    values = {
        "external_order_id": "ORDER-1",
        "cardhobby_item_id": "123390745",
        "item_url": "https://www.cardhobby.com.cn/market/item/123390745",
        "title": (
            "2024-25 Panini Court Kings Matas Buzelis First Steps "
            "Red 113/149"
        ),
        "purchased_at": datetime(2026, 7, 18, tzinfo=timezone.utc),
        "sale_price": Decimal("75.10"),
        "shipping_price": Decimal("18"),
    }
    values.update(updates)
    return CardHobbyOrderCapture(**values)


def test_cardhobby_item_url_must_have_matching_numeric_id() -> None:
    match = CARDHOBBY_ITEM_URL.match(capture().item_url)
    assert match
    assert match.group("item_id") == "123390745"
    assert not CARDHOBBY_ITEM_URL.match("https://example.com/market/item/123390745")


def test_cardhobby_import_uses_full_size_source_image() -> None:
    resized = "https://images.cardhobby.com/a/b.jpg?imageView2/2/h/210"
    assert full_size_cardhobby_image_url(resized) == "https://images.cardhobby.com/a/b.jpg"
    external = "https://example.com/a.jpg?width=210"
    assert full_size_cardhobby_image_url(external) == external


def test_build_item_keeps_purchase_and_shipping_separate() -> None:
    parser = CardHobbyOrderParser(Settings(vision_api_key=None))
    result = parser._build_item(
        uuid4(),
        capture(),
        CardIdentityInput(
            player_name="Matas Buzelis",
            brand_name="Panini",
            season_start=2024,
            season_end=2025,
            product_name="Court Kings",
            card_number="5",
            insert_name="First Steps",
            parallel_name="Red",
            print_run=149,
        ),
    )
    assert result.serial_number == 113
    assert result.serial_total == 149
    assert result.purchase
    assert result.purchase.price == Decimal("75.10")
    assert result.purchase.fee_amount == Decimal("18")
    assert result.purchase.external_order_id == "ORDER-1"
    assert result.purchase.venue == "CardHobby"


@pytest.mark.asyncio
async def test_without_model_incomplete_title_stays_preview_only() -> None:
    parser = CardHobbyOrderParser(Settings(vision_api_key=None))
    proposed, warnings = await parser.parse(
        uuid4(),
        capture(title="球星卡一张，卡品如图"),
    )
    assert proposed is None
    assert "球员姓名需要确认" in warnings


def test_recovers_unit_price_from_cardhobby_order_text() -> None:
    order = capture(
        raw_text=(
            "Walker Kessler 商品编号: 260709030517168 "
            "￥415.00 x1 总额 ￥430.00 含运费：￥15.00 已完成"
        ),
        sale_price=None,
        shipping_price=Decimal("15"),
    )
    assert recover_sale_price(order) == Decimal("415.00")


def test_recovers_price_from_total_less_shipping_as_fallback() -> None:
    order = capture(
        raw_text="商品 总额 ￥430.00 含运费：￥15.00 已完成",
        sale_price=None,
        shipping_price=Decimal("15"),
    )
    assert recover_sale_price(order) == Decimal("415.00")
