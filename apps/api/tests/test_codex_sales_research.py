from copy import deepcopy
from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)
IDENTITY = dict(player_name='Kobe Bryant', brand_name='Panini', product_name='Court Kings', season_start=2016,
                card_number='167', insert_name='Rookies III', parallel_name='Base', serial_total=None,
                grade_company=None, grade_value=None, is_autograph=False, is_memorabilia=False)


def research():
    searches = [dict(platform=p, query='Court Kings 167 III sold',
                     executed_at='2026-10-18T15:00:00+00:00', outcome='found' if p == 'cardhobby' else 'blocked',
                     capture_ref='capture.json', diagnostic='403' if p == 'ebay' else None)
                for p in ['ebay', 'cardhobby']]
    sales = [dict(platform='cardhobby', url=f'https://www.cardhobby.com.cn/market/item/{123+i}',
        checked_at='2026-10-18T15:00:00+00:00', capture_ref='item.html',
        identity=IDENTITY, identity_confirmed=True, images_reviewed=True,
        finish=dict(target='printed base', candidate='printed base', comparable=True),
        card_images=[dict(role='card_front', sha256=str(i+1) * 64, capture_ref='card.jpg')],
        sale_status='ended_with_bid', bid_count=3, actual_price_known=True,
        price_basis='auction_final_price', price=price, currency='CNY',
        sold_at='2026-10-01T12:00:00+08:00', shipping=None, shipping_status='unknown',
        condition=dict(target='raw; unknown back surface', candidate='raw; no disclosed damage',
                       comparable=True), seller_id=f'seller-{i}', lot=False,
        duplicate_check='distinct_copy', serial_number=None, serial_total=None)
        for i, price in enumerate([34, 39, 62])]
    return dict(contract_version='codex-sales-research-1', identity=IDENTITY,
                front_sha256='f'*64, back_sha256='e'*64, searches=searches, sales=sales,
                currency='CNY', fx=[])


def check(data):
    from card_platform.domain.codex_sales_research import assess_sales_research
    return assess_sales_research(data, now=NOW)


def test_both_markets_must_actually_be_searched_even_for_insufficient_result():
    data = research(); data['searches'] = data['searches'][:1]
    assert check(data)['status'] == 'research_incomplete'


def test_one_market_blocked_and_three_independent_sales_give_observation_not_fabricated_international_support():
    result = check(research())
    assert result['status'] == 'supported_observation'
    assert result['markets']['ebay']['status'] == 'blocked'
    assert result['markets']['cardhobby']['accepted_count'] == 3
    assert result['low_value'] == 34 and result['high_value'] == 62
    assert result['confidence'] <= .65


@pytest.mark.parametrize('change', [
    {'sale_status':'active'}, {'actual_price_known':False, 'price_basis':'best_offer_unknown'},
    {'sale_status':'removed'}, {'lot':True}, {'images_reviewed':False},
    {'currency':None}, {'sold_at':None}, {'condition':{'comparable':False}},
    {'identity':{**IDENTITY, 'parallel_name':'Aqua'}},
    {'identity':{**IDENTITY, 'product_name':'Preferred'}},
    {'identity':{**IDENTITY, 'season_start':2015}},
    {'identity':{**IDENTITY, 'grade_company':'PSA', 'grade_value':'10'}},
])
def test_unqualified_sources_do_not_supply_a_price(change):
    data = research(); data['sales'] = [{**s, **deepcopy(change)} for s in data['sales']]
    result = check(data)
    assert result['status'] == 'insufficient_evidence'
    assert result['fair_value'] is None and result['rejected']


def test_duplicate_card_face_and_owned_serial_are_not_independent_sales():
    data = research()
    data['sales'][1]['card_images'] = data['sales'][0]['card_images']
    data['sales'][2].update(duplicate_check='owned_copy_history')
    result = check(data)
    assert len(result['accepted']) == 1
    assert result['fair_value'] is None


def test_fx_requires_a_dated_source_and_shipping_is_separate():
    data = research()
    for sale in data['sales']:
        sale.update(currency='USD', price=10, shipping=5, shipping_status='known')
    assert check(data)['fair_value'] is None
    data['fx'] = [dict(base='USD', quote='CNY', rate=7, date='2026-10-18', url='https://example.com/fx')]
    result = check(data)
    assert result['fair_value'] == 70
    assert result['accepted'][0]['shipping'] == 5


def test_zero_or_cached_queries_never_satisfy_active_research():
    data = research()
    for search in data['searches']:
        search['outcome'] = 'cache_replay'
    assert check(data)['status'] == 'research_incomplete'


def test_legacy_visual_prices_cannot_be_promoted_without_research():
    from card_platform.domain.codex_sales_research import research_backed_prices
    source = dict(raw_result={'front_sha256':'f'*64, 'back_sha256':'e'*64},
                  fair_value_low=34, fair_value=39, fair_value_high=62, currency='CNY')
    assert research_backed_prices(source, now=NOW) is False


def test_unpriced_or_conflicting_snapshot_cannot_be_promoted():
    from card_platform.domain.codex_sales_research import research_backed_prices
    source = dict(raw_result={'market_research':research(), 'research_captures_verified':True},
                  fair_value_low=34, fair_value=999, fair_value_high=62, currency='CNY')
    assert research_backed_prices(source, now=NOW) is False
    source['fair_value'] = 39
    assert research_backed_prices(source, now=NOW) is True


def test_malformed_bid_count_fails_closed_without_crashing_validation():
    data = research()
    for sale in data['sales']:
        sale['bid_count'] = 'unknown'
    assert check(data)['fair_value'] is None


@pytest.mark.parametrize('field', ['season_start', 'parallel_name', 'grade_company', 'is_autograph'])
def test_missing_identity_roles_cannot_be_verified_by_two_missing_values(field):
    data = research(); data['identity'] = dict(data['identity'])
    data['identity'].pop(field)
    for sale in data['sales']:
        sale['identity'] = dict(sale['identity']); sale['identity'].pop(field)
    assert check(data)['fair_value'] is None


def test_tracking_urls_are_still_the_same_market_item():
    data = research()
    for i, sale in enumerate(data['sales']):
        sale['url'] = f'https://www.cardhobby.com.cn/market/item/123?tracking={i}'
    assert check(data)['fair_value'] is None


def test_zero_padded_serial_strings_do_not_create_three_copies():
    data = research()
    for sale, number, total in zip(data['sales'], [97,'097','97'], [199,'199',199]):
        sale.update(serial_number=number, serial_total=total)
    assert check(data)['fair_value'] is None


def test_captured_before_the_auction_ended_is_not_confirmed_completion():
    data = research()
    for sale in data['sales']:
        sale.update(checked_at='2026-10-01T00:00:00+00:00', sold_at='2026-10-18T00:00:00+00:00')
    assert check(data)['fair_value'] is None


def test_read_serial_total_cannot_contradict_the_claimed_version():
    data = research()
    for i, sale in enumerate(data['sales']):
        sale.update(serial_number=97+i, serial_total=199)
    assert check(data)['fair_value'] is None


def test_owned_photo_cannot_reenter_via_relisting_without_serial():
    data=research(); data['identity']={**data['identity'],'serial_number':97,'serial_total':199}
    for sale in data['sales']:
        sale['identity']={**sale['identity'],'serial_total':199}
    owned=deepcopy(data['sales'][0]);owned.update(url='https://cardhobby.com.cn/market/item/999',
        serial_number=97,serial_total=199,duplicate_check='owned_copy_history')
    data['sales'].insert(0,owned)
    assert check(data)['fair_value'] is None
