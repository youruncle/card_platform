"""Only a declared purchase source constrains allocation; reference links do not."""
import pytest

from card_platform.purchase_cost import purchase_source_conflicts


@pytest.mark.parametrize('notes,url,conflict', [
    ('来源 https://cardhobby.com.cn/market/item/123。', 'https://cardhobby.com.cn/market/item/456', True),
    ('来源商品：https://www.cardhobby.com.cn/Market/Item/123/', 'https://cardhobby.com.cn/market/item/123', False),
    ('Source: https://cardhobby.com.cn/market/item/123', 'https://cardhobby.com.cn/market/item/456', True),
    ('比较价格 https://cardhobby.com.cn/market/item/123', 'https://cardhobby.com.cn/market/item/456', False),
    ('来源 https://cardhobby.com.cn.evil/market/item/123', 'https://cardhobby.com.cn/market/item/456', False),
    ('来源 https://cardhobby.com.cn/market/item/123', '', True),
    ('参考来源 https://cardhobby.com.cn/market/item/123', 'https://cardhobby.com.cn/market/item/456', False),
    ('图片来源 https://cardhobby.com.cn/market/item/123', 'https://cardhobby.com.cn/market/item/456', False),
    ('估值来源 https://cardhobby.com.cn/market/item/123', 'https://cardhobby.com.cn/market/item/456', False),
    ('Reference source: https://cardhobby.com.cn/market/item/123', 'https://cardhobby.com.cn/market/item/456', False),
    ('识别录入。\n2026-09-28 订单逐项图像复核：来源 https://cardhobby.com.cn/market/item/123 商品编号：789。', 'https://cardhobby.com.cn/market/item/456', True),
    ('', 'https://cardhobby.com.cn/market/item/456', False),
])
def test_declared_source_allocation_constraint(notes, url, conflict):
    assert bool(purchase_source_conflicts({'notes': notes}, {'item_url': url})) is conflict
