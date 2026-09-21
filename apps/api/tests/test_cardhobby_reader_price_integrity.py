from datetime import UTC, datetime

import pytest

from scripts.parse_cardhobby_review_sources import parse_reader_item


PAGE = """Synthetic Player Card 卡淘 (https://www.cardhobby.com.cn/market/item/123)
L28: 结束时间  |  | 2025-03-28 21:24:17
L29: 当前价  |  | 3,200.00 cite7†41 次出价 约$ 469.76)
"""


def test_primary_rmb_price_is_not_the_approximate_usd_conversion():
    result = parse_reader_item(PAGE)
    assert result["price"] == "3200.00"
    assert result["currency"] == "CNY"
    assert result["sale"]["evidence_level"] == "ended_auction"
    assert result["payment_confirmed"] is False
    assert result["occurred_at"] == "2025-03-28T21:24:17+08:00"


@pytest.mark.parametrize("page", [
    PAGE.replace("41 次出价", "0 次出价"),
    PAGE.replace("2025-03-28", "2099-03-28"),
    PAGE.replace("结束时间", "上架时间"),
    PAGE.replace("当前价", "挂牌价"),
])
def test_incomplete_or_active_page_never_creates_sale(page):
    result = parse_reader_item(page, now=datetime(2026, 9, 16, tzinfo=UTC))
    assert "sale" not in result
    assert result["reasons"]


def test_feedback_bundles_do_not_become_individual_item_prices():
    assert parse_reader_item(PAGE.replace("market/item/123", "market/feedback/123")) is None


def test_page_title_grading_is_retained_as_a_hard_identity_field():
    result = parse_reader_item(PAGE.replace("Synthetic Player Card", "Synthetic Player Card PSA 10"))
    assert result["sale"]["grade_company"] == "PSA"
    assert result["sale"]["grade_value"] == "10"
