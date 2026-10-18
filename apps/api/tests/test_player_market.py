from datetime import date, datetime
from zoneinfo import ZoneInfo

from card_platform.player_prices import domain
from test_player_prices import sale

TZ = ZoneInfo('Asia/Shanghai')
D = date(2026, 10, 10)


def market_rows():
    rows = []
    for i in range(3):
        rows.append(dict(sale('one', '100', source_id=f'old{i}'), occurred_at=datetime(2026, 9, 5, 12, tzinfo=TZ)))
        rows.append(sale('one', '120', source_id=f'new{i}'))
    return rows


def test_single_card_has_independent_monthly_trend_and_latest_sources():
    rows = market_rows()
    result = domain.calculate_market(rows + [rows[0]], D)
    assert result['sale_count'] == 6
    assert result['card_count'] == 1 and result['trend_count'] == 1
    card = result['cards'][0]
    assert card['change_pct'] == '20.0000'
    assert (card['current_count'], card['previous_count']) == (3, 3)
    assert (card['current_price'], card['previous_price']) == ('120', '100')
    assert card['sources'][0]['sale_price'] == '120'
    assert result['current_window']['start'].startswith('2026-09-11')
    assert result['previous_window']['start'].startswith('2026-08-12')
    assert domain.calculate_snapshot(rows, domain.select_basket(rows, D), D)['change_pct'] is None


def test_market_filters_future_conflicts_and_respects_nonoverlapping_boundary():
    rows = market_rows()
    rows += [dict(rows[0], sale_price='999'), dict(sale(source_id='future'), occurred_at=datetime(2026, 10, 11, tzinfo=TZ))]
    rows += [dict(sale('boundary', source_id='boundary'), occurred_at=datetime(2026, 9, 11, tzinfo=TZ))]
    result = domain.calculate_market(rows, D)
    assert result['sale_count'] == 6
    assert result['trend_count'] == 0
    boundary = next(c for c in result['cards'] if c['card_key'] == 'boundary')
    assert (boundary['current_count'], boundary['previous_count']) == (1, 0)
    assert boundary['change_pct'] is None


def test_market_limits_cards_but_totals_cover_all_and_no_ancient_sales():
    rows = [sale(str(i), source_id=str(i)) for i in range(25)]
    rows.append(dict(sale(source_id='ancient'), occurred_at=datetime(2026, 1, 1, tzinfo=TZ)))
    result = domain.calculate_market(rows, D)
    assert result['sale_count'] == result['card_count'] == 25
    assert len(result['cards']) == 20 and result['cards_truncated'] is True


def test_basket_prefers_comparable_cards_over_inactive_historical_volume():
    from test_player_prices import samples
    rows = samples()
    rows += [dict(sale('inactive', source_id=f'x{i}'), occurred_at=datetime(2026, 8, 1, tzinfo=TZ)) for i in range(40)]
    assert {c['card_key'] for c in domain.select_basket(rows, D)} == {'a', 'b', 'c'}


def observation(item_id=123, title='2024-25 Panini Prizm Stephen Curry #23 base', price=100):
    from card_platform.public_source_catalog import observe_public_sale
    return observe_public_sale(dict(ID=item_id, Title=title, EffectiveDate='2026-10-10 12:00:00',
                                   Status=-2, ByWay=2, OutCount=2, LowestPrice=price,
                                   TitImg='https://images.cardhobby.com/test.jpg'))


def test_diagnostics_deduplicate_and_exclude_conflicting_sales():
    from card_platform.player_prices.service import analyze_observations
    first = observation()
    rows, coverage = analyze_observations([first, first, observation(price=999), observation(124),
                                          observation(125, 'Panini Prizm Stephen Curry #23 base')], 'Stephen Curry', [])
    assert len(rows) == 1 and rows[0]['source_id'] == 'item:124'
    assert coverage['sample_count'] == 3
    assert coverage['identified_count'] == 1 and coverage['rejected_count'] == 2
    assert coverage['rejection_reasons'] == {'conflicting_source': 1, 'season_unclear': 1}


def test_unfixed_discovery_queries_existing_exact_cards_before_broad_search():
    import asyncio
    from card_platform.player_prices.service import discovery_targets
    from card_platform.player_prices.discovery import collect_player

    rows = [dict(sale(source_id='one'), identity=dict(player_name='Stephen Curry', season_start=2024,
                 product_name='Prizm', card_number='23', parallel_name='Silver'))]
    targets = discovery_targets(rows, D)
    class Provider:
        timeout = 1
        def __init__(self): self.queries = []
        async def _get_search_payload(self, client, query, page):
            self.queries.append(query)
            return {'data': {'PagedMarketItemList': []}}
    provider = Provider()
    asyncio.run(collect_player('Stephen Curry', targets, D, provider=provider))
    assert provider.queries[0] == 'Stephen Curry 2024 Prizm Silver 23'
    assert 'Stephen Curry' in provider.queries


def test_latest_market_sale_compares_instants_across_timezones():
    from datetime import timezone
    rows = [dict(sale('earlier', source_id='a'), occurred_at=datetime(2026,10,10,12,tzinfo=TZ)),
            dict(sale('later', source_id='b'), occurred_at=datetime(2026,10,10,5,tzinfo=timezone.utc))]
    assert domain.calculate_market(rows,D)['latest_sale_at'] == '2026-10-10T05:00:00+00:00'


def test_audit_explains_availability_and_aggregates_per_player_rejections():
    from scripts.player_price_audit import coverage_breakdown
    players = [
        dict(market=dict(sale_count=2,trend_count=0),change_pct=None,refresh=dict(error=None,queued=False),coverage=dict(rejection_reasons={'season_unclear':3})),
        dict(market=dict(sale_count=0,trend_count=0),change_pct=None,refresh=dict(error='timeout',queued=False),coverage=dict(rejection_reasons={'multi_card':2})),
        dict(market=dict(sale_count=5,trend_count=1),change_pct=None,refresh=dict(error=None,queued=False),coverage={}),
    ]
    result = coverage_breakdown(players)
    assert result['availability'] == {'sales_only':1,'source_error_without_sales':1,'card_trend_only':1}
    assert result['per_player_rejection_totals'] == {'season_unclear':3,'multi_card':2}
    assert result['players_with_rejection_diagnostics'] == 2
