"""Bounded runs must make durable progress through real date windows."""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from card_platform.player_prices.discovery import collect_player

D = date(2026, 10, 21)
NAME = "Exact Testperson"


def page(day="2026-10-20", count=60, offset=0):
    return [{"ID": offset + n + 1,
             "Title": "2024-25 Panini Prizm Exact Testperson #23 base",
             "EffectiveDate": day + " 12:00:00", "Status": -2, "ByWay": 2,
             "OutCount": 2, "LowestPrice": "100", "TitImg": "https://images.cardhobby.com/test.jpg"}
            for n in range(count)]


class Provider:
    timeout = 1

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    async def _get_search_payload(self, client, query, number):
        self.calls.append((query, number))
        default = page(offset=number * 100)
        for index, row in enumerate(default):
            row['EffectiveDate'] = (datetime(2026,10,20,12)
                - timedelta(seconds=(number-1)*60+index)).strftime('%Y-%m-%d %H:%M:%S')
        response = self.responses.get(number, default)
        if isinstance(response, Exception):
            raise response
        return {"data": {"PagedMarketItemList": response}}


def collect(provider, previous=None, budget=3, basket=None):
    return asyncio.run(collect_player(NAME, basket or [], D, provider=provider,
                                      page_budget=budget, query_state=previous))


def test_history_cursor_resumes_and_always_checks_latest_page_without_mutating_previous_state():
    first = Provider()
    _, coverage = collect(first)
    assert first.calls == [(NAME, 1), (NAME, 2), (NAME, 3)]
    states = coverage["history_queries"]
    assert states[NAME]["next_page"] == 4
    assert coverage["history_complete"] is False
    before = deepcopy(states)
    second = Provider()
    _, again = collect(second, states)
    assert second.calls == [(NAME, 1), (NAME, 4), (NAME, 5)]
    assert again["history_queries"][NAME]["next_page"] == 6
    assert states == before


def test_failed_page_is_retried_without_skipping_and_completed_query_only_checks_fresh_head():
    first = Provider({2: ValueError("source failed")})
    _, failed = collect(first)
    assert failed["errors"] and failed["history_queries"][NAME]["next_page"] == 2
    retry = Provider({2: page("2026-09-01", count=2)})
    rows, done = collect(retry, failed["history_queries"])
    assert retry.calls == [(NAME, 1), (NAME, 2)]
    assert len(rows) == 62 and done["history_complete"] is True
    refreshed = Provider()
    _, fresh = collect(refreshed, done["history_queries"])
    assert refreshed.calls == [(NAME, 1)]
    assert fresh["history_complete"] is True


def test_ninety_day_boundary_ends_history_without_accepting_old_or_future_sales():
    provider = Provider({1: page("2026-10-22"), 2: page("2026-07-23")})
    rows, coverage = collect(provider)
    assert rows == []
    assert provider.calls == [(NAME, 1), (NAME, 2)]
    assert coverage["history_complete"] is True


def test_new_query_does_not_reuse_another_card_cursor():
    previous = {"removed precise query": {"next_page": 99, "complete": True}}
    provider = Provider()
    _, coverage = collect(provider, previous)
    assert provider.calls == [(NAME, 1), (NAME, 2), (NAME, 3)]
    assert "removed precise query" not in coverage["history_queries"]


def test_incremental_burst_has_its_own_resumable_gap_before_continuing_history():
    first = Provider({1: page("2026-10-18"), 2: page("2026-10-17", offset=100)})
    _, initial = collect(first, budget=2)
    # More than a page of new sales arrived. Stop only after the saved head date.
    burst = Provider({1: page("2026-10-21", offset=10000), 2: page("2026-10-20", offset=20000)})
    _, pending = collect(burst, initial["history_queries"], budget=2)
    assert pending["incremental_complete"] is False
    assert pending["history_queries"][NAME]["recent_next_page"] == 3
    assert pending["history_queries"][NAME]["next_page"] == 3
    resume = Provider({1: page("2026-10-21", offset=10000),
                       2: page("2026-10-20", offset=20000), 3: page("2026-10-17")})
    _, done = collect(resume, pending["history_queries"], budget=3)
    assert resume.calls == [(NAME, 1), (NAME, 2), (NAME, 3)]
    assert done["incremental_complete"] is True
    assert done["history_queries"][NAME]["next_page"] == 3


def test_malformed_payload_is_an_error_with_unchanged_page_cursor():
    class Malformed(Provider):
        async def _get_search_payload(self, client, query, number):
            return {"data": {"PagedMarketItemList": {"bad": "not a list"}}}
    _, result = collect(Malformed())
    assert result["errors"] and result["history_complete"] is False
    assert result["history_queries"][NAME]["next_page"] == 2


def test_unqueried_alias_due_to_budget_cannot_be_called_a_complete_incremental_run():
    _, coverage = asyncio.run(collect_player("Stephen Curry", [], D, provider=Provider(),
                                             page_budget=1))
    assert coverage["incremental_complete"] is False


def test_market_abbreviation_finds_numberless_title_without_establishing_identity():
    # Actual public titles use 三球/PZ and often omit #278. An exact English
    # name+number query cannot be the only way to discover this candidate.
    raw = page(count=1)[0]
    raw["Title"] = "2020-21 Panini Prizm LaMelo Ball 三球 黄蜂 新秀 base"

    class Market(Provider):
        async def _get_search_payload(self, client, query, number):
            self.calls.append((query, number))
            return {"data": {"PagedMarketItemList": [raw] if query == "三球 pz" else []}}

    provider = Market()
    basket = [
        dict(
            identity=dict(
                season_start=2020, product_name="Prizm", parallel_name="Base", card_number="278"
            )
        )
    ]
    rows, coverage = asyncio.run(collect_player("LaMelo Ball", basket, D, provider=provider))
    assert [row["sale"]["title"] for row in rows] == [raw["Title"]]
    assert ("三球 pz", 1) in provider.calls
    assert len(provider.calls) <= coverage["page_budget"]
    from card_platform.player_prices.discovery import identify_sale
    from card_platform.public_source_catalog import validated_sale

    sale, _ = validated_sale(rows[0])
    assert identify_sale(sale, "LaMelo Ball", [basket[0]["identity"]]) is None


def test_market_queries_keep_their_own_history_cursor_and_bound_the_query_count():
    basket = [
        dict(
            identity=dict(
                season_start=year, product_name="Prizm", parallel_name="Base", card_number="278"
            )
        )
        for year in (2020, 2021, 2022)
    ]
    first = Provider()
    _, initial = asyncio.run(
        collect_player("LaMelo Ball", basket, D, provider=first, page_budget=21)
    )
    state = initial["history_queries"]
    assert "三球 pz" in state and len(state) <= 8
    next_page = state["三球 pz"]["next_page"]
    assert next_page > 2
    again = Provider()
    asyncio.run(
        collect_player("LaMelo Ball", basket, D, provider=again, page_budget=21, query_state=state)
    )
    assert ("三球 pz", next_page) in again.calls
    assert len(again.calls) <= 21


def test_market_query_nicknames_do_not_become_identity_aliases():
    from card_platform.player_prices.collection import collection_player_aliases
    from card_platform.domain.player_aliases import matching_player_aliases

    assert "三球" in collection_player_aliases("LaMelo Ball")
    assert "华子" in collection_player_aliases("Anthony Edwards")
    assert "七七" not in collection_player_aliases("Luka Doncic")
    assert "三球" not in matching_player_aliases("lamelo ball")


def test_market_nickname_is_also_queried_before_a_representative_card_exists():
    provider = Provider()
    asyncio.run(collect_player('LaMelo Ball', [], D, provider=provider))
    assert ('三球', 1) in provider.calls


def test_default_budget_rotates_enough_pages_to_advance_each_anchored_gap():
    basket = [dict(identity=dict(season_start=year, product_name='Prizm',
                                parallel_name='Base', card_number='278'))
              for year in (2020, 2021, 2022)]
    _, initial = asyncio.run(collect_player('LaMelo Ball', basket, D,
        provider=SnapshotProvider({1: page('2026-10-18'),
            2: page('2026-10-17', count=1, offset=60)})))
    # Every query has the same burst, so no easy/empty query can subsidise it.
    snapshot = {1: page('2026-10-21', offset=1000),
                2: page('2026-10-20', offset=1060),
                3: page('2026-10-17', count=1, offset=60)}
    result = initial
    seen = set()
    for _ in range(10):
        provider = SnapshotProvider(snapshot)
        _, result = asyncio.run(collect_player('LaMelo Ball', basket, D,
            provider=provider, query_state=result['history_queries']))
        assert len(provider.calls) <= 12
        seen.update(query for query, number in provider.calls if number == 3)
        if result['incremental_complete']:
            break
    assert seen == set(result['history_queries'])
    assert result['incremental_complete']


@pytest.mark.parametrize('first_report', [D, date(2026, 10, 20)])
def test_previously_empty_query_captures_a_new_multi_page_head_before_claiming_complete(first_report):
    _, result = asyncio.run(collect_player(NAME, [], first_report,
        provider=SnapshotProvider({1: []})))
    assert result['history_complete']
    snapshot = {1: page(offset=1000), 2: page('2026-10-19', count=1, offset=2000)}
    seen = set()
    for _ in range(4):
        rows, result = collect(SnapshotProvider(snapshot), result['history_queries'])
        seen |= ids(rows)
        if result['history_complete'] and result['incremental_complete']:
            assert 2001 in seen
    assert 2001 in seen


def test_same_second_auction_burst_scans_past_the_entire_boundary_timestamp():
    _, initial = collect(Provider({1: page()}), budget=1)
    new_ids_same_timestamp = Provider({1: page(offset=10000), 2: page(offset=20000)})
    _, pending = collect(new_ids_same_timestamp, initial["history_queries"], budget=2)
    assert pending["incremental_complete"] is False
    resume = Provider({1: page(offset=10000), 2: page(offset=20000),
                       3: page('2026-10-19', offset=30000)})
    _, done = collect(resume, pending["history_queries"], budget=3)
    assert done["incremental_complete"] is True


class SnapshotProvider(Provider):
    async def _get_search_payload(self, client, query, number):
        self.calls.append((query, number))
        return {"data": {"PagedMarketItemList": self.responses.get(number, [])}}


def ids(observations):
    return {int(item['sale']['item_id']) for item in observations}


def test_second_burst_during_resumed_gap_cannot_promote_an_uncaptured_head():
    _, initial = collect(SnapshotProvider({1: page('2026-10-18')}), budget=1)
    seen, pending = collect(SnapshotProvider({1: page(offset=1000), 2: page(offset=1060)}),
                            initial['history_queries'], budget=2)
    snapshot = {1: page('2026-10-21', offset=2000), 2: page('2026-10-21', offset=2060),
                3: page(offset=1000), 4: page(offset=1060), 5: page('2026-10-18')}
    latest, result = collect(SnapshotProvider(snapshot), pending['history_queries'], budget=4)
    seen_ids = ids(seen + latest)
    required = set(range(2061, 2121))
    assert not result['incremental_complete'] or required <= seen_ids
    for _ in range(8):
        if result['incremental_complete']:
            break
        more, result = collect(SnapshotProvider(snapshot), result['history_queries'], budget=4)
        seen_ids |= ids(more)
    assert result['incremental_complete'] and required <= seen_ids


def test_rolling_report_captures_previously_deferred_pages_before_completion():
    snapshot = {1: page('2026-10-21', offset=3000), 2: page('2026-10-21', offset=3060),
                3: page(count=1, offset=3120)}
    first, initial = asyncio.run(collect_player(NAME, [], date(2026,10,20),
        provider=SnapshotProvider(snapshot), page_budget=3))
    latest, result = collect(SnapshotProvider(snapshot), initial['history_queries'], budget=3)
    required = set(range(3061,3121))
    assert not result['incremental_complete'] or required <= ids(first + latest)
    assert required <= ids(latest)


def test_partial_id_overlap_never_closes_a_timestamp_tie():
    _, initial = collect(SnapshotProvider({1: page(offset=4000),
        2: page('2026-10-19',count=1,offset=4060)}), budget=2)
    snapshot = {1: page(count=30,offset=4000) + page(count=30,offset=5000),
                2: page(offset=5030),
                3: page(count=30,offset=4030) + page('2026-10-19',count=1,offset=4060)}
    latest, result = collect(SnapshotProvider(snapshot), initial['history_queries'], budget=3)
    assert set(range(5031,5091)) <= ids(latest)
    assert result['incremental_complete']
