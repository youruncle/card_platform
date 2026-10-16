from decimal import Decimal
import pytest
from card_platform.cardhobby_order_html import Document, detail_amounts
from card_platform.purchase_cost import OrderCapture, allocate_shipping, order_errors, evidence_hash


def capture(**overrides):
    return OrderCapture.model_validate(dict(
        external_order_id='123', ordered_at='2026-09-01', seller='Fixture', status='已完成',
        source_url='https://cardhobby.com.cn/Order/detail/123', fetched_at='2026-09-01T00:00:00Z',
        shipping='18.00', shipping_method='sf_collect', shipping_basis='owner_default',
        shipping_card_counts={'1': 1, '2': 2}, complete=True,
        paid_total='30.00', displayed_total='30.00',
        lines=[dict(item_id='1',title='Single card',item_url='https://cardhobby.com.cn/market/item/1',unit_price='10',quantity=1),
               dict(item_id='2',title='两张打包',item_url='https://cardhobby.com.cn/market/item/2',unit_price='20',quantity=1)],
    ) | overrides)


def test_collect_terms_are_explicit_and_not_zero_shipping():
    parsed = detail_amounts(Document('<li><div>运费：</div><div>顺丰到付</div></li><li>实付总额：￥30.00</li>'))
    assert parsed['shipping_method'] == 'sf_collect'
    assert parsed['shipping'] is None
    assert parsed['paid_total'] == '30.00'


def test_collect_default_checks_online_total_without_collect_fee_and_splits_all_cards():
    order = capture()
    assert order_errors(order) == []
    units = allocate_shipping(order)
    assert [u['shipping'] for u in units] == [Decimal('6.00'), Decimal('12.00')]
    assert sum(u['shipping'] for u in units) == Decimal('18.00')
    assert order_errors(capture(paid_total='29.00'))


def test_collect_unknown_bundle_size_or_no_owner_default_stays_blocked():
    assert order_errors(capture(shipping_card_counts={'1':1}))
    assert order_errors(capture(shipping_basis='order', shipping=None))


def test_collect_rounding_is_stable_and_cent_exact():
    units = allocate_shipping(capture(shipping_card_counts={'1':1,'2':6}))
    assert [u['shipping'] for u in units] == [Decimal('2.58'), Decimal('15.42')]
    assert sum(u['shipping'] for u in units) == Decimal('18.00')


def test_prepaid_evidence_hash_remains_compatible():
    import hashlib
    import json
    order = capture(shipping_method='prepaid', shipping_basis='order', shipping_card_counts={})
    value = order.model_dump(mode='json', exclude={'fetched_at', 'shipping_method', 'shipping_basis', 'shipping_card_counts'})
    value['lines'] = sorted(value['lines'], key=lambda line: line['item_id'])
    old_digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert evidence_hash(order) == old_digest


def test_collect_multiple_quantities_and_bundle_children_are_equal_per_card():
    from card_platform.purchase_cost_bundle import split_bundle
    order = capture()
    order.lines[0].quantity = 2
    units = allocate_shipping(order)
    assert [u['shipping'] for u in units] == [Decimal('4.50'), Decimal('4.50'), Decimal('9.00')]
    children = split_bundle(units[2]['unit_price'], units[2]['shipping'], 2)
    assert [u['shipping'] for u in children] == [Decimal('4.50'), Decimal('4.50')]


@pytest.mark.asyncio
async def test_reviewed_physical_counts_survive_titles_without_bundle_keywords():
    from card_platform.purchase_cost_shipping import apply_collect_shipping_policy
    class Result:
        def __init__(self, row): self.row = row
        async def fetchone(self): return self.row
    class Connection:
        async def execute(self, sql, params):
            return Result({'collect_shipping_default': Decimal(18)} if 'cardhobby_account' in sql else None)
    order = capture(shipping_card_counts={'1':10, '2':1})
    order.lines[1].title = 'Single card'
    # Explicit review is authoritative even when there is no bundle keyword.
    order.lines[0].title = '球星卡照片'
    result = (await apply_collect_shipping_policy(Connection(), 'owner', [order]))[0]
    assert result.shipping_card_counts == {'1':10, '2':1}


def test_multi_card_titles_never_default_to_one_card():
    from card_platform.purchase_cost import BUNDLE
    for title in ['10张卡', '11张一起', '三张打包', '三张卡', '一套球星卡', '20张lot']:
        assert BUNDLE.search(title), title
    assert not BUNDLE.search('1张卡')
