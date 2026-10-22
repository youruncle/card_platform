"""Mainstream reference cards must not fall back to cheap ordinary base cards."""
from datetime import date

import pytest

from card_platform.player_prices.domain import calculate_market, calculate_snapshot, select_basket
from test_player_price_reference import rows_for
from test_player_prices import D, samples

DAY = date(2026, 10, 21)


def identified_rows(key, *, identity=None, **counts):
    fields = dict(player_name='Stephen Curry', brand_name='Topps', product_name='Topps Chrome',
                  season_start=2025, card_number='201', parallel_name='Refractor')
    fields.update(identity or {})
    return [dict(row, identity=fields) for row in rows_for(key, **counts)]


def test_busy_ordinary_base_cannot_displace_a_main_set_refractor():
    rows = identified_rows('cheap', identity={'parallel_name': 'Base'}, current=30, previous=30,
                           now=3, before=1)
    rows += identified_rows('chrome', current=3, previous=3, now=120, before=100)
    market = calculate_market(rows, DAY)
    assert market['reference_card']['card_key'] == 'chrome'
    assert market['reference_card']['change_pct'] == '20.0000'
    assert market['sale_count'] == 66  # Ordinary base remains visible evidence.
    assert market['card_count'] == 2


def test_sparse_silver_never_falls_back_to_the_real_curry_base_failure():
    rows = identified_rows('base', identity={'brand_name': 'Panini', 'product_name': 'Prizm',
                                            'card_number': '134', 'parallel_name': 'Base'},
                           current=3, previous=3, now=3, before=1)
    rows += identified_rows('silver', identity={'brand_name': 'Panini', 'product_name': 'Prizm',
                                              'card_number': '134', 'parallel_name': 'Silver Prizm'},
                            current=1, previous=0, now='52.10')
    market = calculate_market(rows, DAY)
    assert market['reference_card'] is None
    assert market['reference_status'] == 'insufficient_mainstream_sales'
    assert market['sale_count'] == 7
    assert market['cards'][0]['card_key'] == 'silver'
    base = next(card for card in market['cards'] if card['card_key'] == 'base')
    assert base['change_pct'] == '200.0000'  # Own-card fact, never player signal.


@pytest.mark.parametrize('identity', [
    {'parallel_name': 'Base'},
    {'parallel_name': None},
    {'parallel_name': 'Unknown'},
    {'parallel_name': '基础卡'},
    {'parallel_name': 'Chrome'},
    {'parallel_name': 'Base Prizm'},
    {'parallel_name': 'Photo Variation'},
    {'parallel_name': 'Unknown Refractor'},
    {'parallel_name': 'Non Refractor'},
    {'parallel_name': '未知折射'},
    {'brand_name': 'Panini', 'product_name': 'Prizm', 'parallel_name': 'Prizm'},
    {'brand_name': 'Panini', 'product_name': 'Prizm', 'parallel_name': 'Base Prizms'},
    {'product_name': 'Topps', 'parallel_name': 'Silver Foil'},
    {'product_name': 'Topps Chrome Cosmic'},
    {'product_name': 'Topps Chrome Update'},
    {'product_name': 'Topps Now'},
    {'insert_name': 'Deep Space'},
    {'is_autograph': True},
    {'is_memorabilia': True},
    {'is_patch': True},
    {'is_redemption': True},
    {'brand_name': 'Panini'},
    {'parallel_name': 'Base', 'print_run': True},
    {'parallel_name': 'Base', 'print_run': 0},
    {'parallel_name': 'Base', 'print_run': 99.5},
    {'parallel_name': 'Base', 'print_run': 99, 'serial_total': 199},
])
def test_non_mainstream_or_uncertain_cards_keep_prices_but_no_reference(identity):
    market = calculate_market(identified_rows('excluded', identity=identity), DAY)
    assert market['reference_card'] is None
    assert market['reference_status'] == 'no_mainstream_cards'
    assert market['cards'][0]['current_price'] == '120'


@pytest.mark.parametrize('identity', [
    {},
    {'product_name': 'Chrome'},
    {'parallel_name': 'Blue Refractor', 'print_run': 150},
    {'parallel_name': 'Base', 'print_run': 199},
    {'brand_name': 'Panini', 'product_name': 'Prizm', 'parallel_name': 'Silver Prizm'},
    {'brand_name': 'Panini', 'product_name': 'Donruss Optic', 'parallel_name': 'Holo'},
    {'brand_name': 'Panini', 'product_name': 'Select', 'parallel_name': 'Silver Prizm'},
])
def test_regular_refractors_or_numbered_cards_can_represent_the_player(identity):
    market = calculate_market(identified_rows('eligible', identity=identity), DAY)
    assert market['reference_card']['card_key'] == 'eligible'
    assert market['reference_status'] == 'ready'


def test_eligible_cards_still_require_same_card_liquidity_and_stable_ties():
    rows = identified_rows('a') + identified_rows('b', older=2)
    rows += identified_rows('sparse', current=100, previous=2)
    for ordered in (rows, list(reversed(rows))):
        assert calculate_market(ordered, DAY)['reference_card']['card_key'] == 'b'


def test_basket_and_discovery_targets_exclude_base_even_with_high_volume():
    from card_platform.player_prices.service import discovery_targets
    rows = samples()
    for row in rows:
        row['identity'] = dict(brand_name='Topps', product_name='Topps Chrome',
                               parallel_name='Base', card_number=row['card_key'])
    for row in [dict(r, card_key='refractor', identity={**r['identity'], 'parallel_name':'Refractor'})
                    for r in rows[:6]]:
        rows.append(row)
    # Unique sources must be independent of the base observations.
    for i, row in enumerate(rows):
        row['source_id'] = str(i)
    assert [card['card_key'] for card in select_basket(rows, D)] == ['refractor']
    assert [card['card_key'] for card in discovery_targets(rows, D)] == ['refractor']


def test_previously_selected_base_cannot_generate_a_legacy_three_card_index():
    rows = samples()
    basket = [dict(card_key=key, identity={'product_name':'Prizm', 'parallel_name':'Base'},
                   selection_count=6) for key in ['a','b','c']]
    assert calculate_snapshot(rows, basket, D)['change_pct'] is None
