"""Search discovery must not change valuation/cost identity matching."""

import pytest
from pydantic import ValidationError

from card_platform import portfolio_search as search


def card(**values):
    return (
        dict(
            display_id=519,
            player_name="KEVIN DURANT",
            brand_name="Topps",
            product_name="Flagship",
            insert_name="Oasis",
            parallel_name="Black and White",
            card_number="157",
            season_start=2025,
            season_end=2026,
            is_rookie=False,
            is_autograph=False,
            is_memorabilia=False,
            is_numbered=True,
            serial_number=7,
            serial_total=50,
            grade_company="PSA",
            grade_value=9,
            purchase_total_cost=0,
            fair_value=100,
            valuation_status="succeeded",
        )
        | values
    )


@pytest.mark.parametrize(
    "q",
    [
        "KEVIN DURANT",
        "凯文杜兰特",
        "凯文 杜兰特",
        "杜兰特",
        "durant",
        "kevn durant",
        "519",
        "ID 519",
        "杜兰特 2025 黑白",
    ],
)
def test_global_keywords_aliases_and_small_english_typos(q):
    assert search.matches(card(), search.SearchFilters(q=q))


@pytest.mark.parametrize(
    "q",
    ["51", "ID 51", "加内特", "LeBron James", "durxxx", "158", "2024", "杜兰特 Panini", "%", "_"],
)
def test_numbers_and_different_players_do_not_fuzzy_match(q):
    assert not search.matches(card(), search.SearchFilters(q=q))


def test_bidirectional_aliases_and_published_knowledge():
    assert search.matches(card(player_name="凯文·杜兰特"), search.SearchFilters(q="Kevin Durant"))
    knowledge = {
        "entities": [
            {
                "id": "p1",
                "entity_type": "player",
                "canonical_name": "Test Player",
                "aliases": ["测试球员"],
            }
        ]
    }
    assert search.matches(
        card(player_name="Test Player"), search.SearchFilters(q="测试球员"), knowledge
    )
    assert search.matches(
        card(player_name="测试球员"), search.SearchFilters(q="Test Player"), knowledge
    )
    assert search.matches(card(player_name="Luka Dončić"), search.SearchFilters(q="luka doncic"))


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({"brand": ["Topps", "Panini"], "player": ["杜兰特"], "autograph": False}, True),
        ({"brand": ["Panini"], "player": ["杜兰特"]}, False),
        (
            {
                "product": ["Flagship"],
                "insert": ["Oasis"],
                "parallel": ["黑白"],
                "card_number": ["157"],
            },
            True,
        ),
        ({"card_number": ["15"]}, False),
        ({"year_min": 2026, "year_max": 2026}, True),
        ({"year_max": 2024}, False),
        ({"rookie": False, "autograph": False, "memorabilia": False, "numbered": True}, True),
        ({"rookie": True}, False),
        ({"autograph": True}, False),
        ({"memorabilia": True}, False),
        ({"numbered": False}, False),
        ({"serial_number": 7, "serial_min": 50, "serial_max": 50}, True),
        ({"serial_max": 49}, False),
        ({"graded": True, "grade_company": ["PSA"], "grade_min": 9, "grade_max": 9}, True),
        ({"grade_min": 10}, False),
        ({"cost_min": 0, "cost_max": 0, "has_cost": True}, True),
        ({"has_cost": False}, False),
        ({"value_min": 100, "value_max": 100, "valuation_status": ["valued"]}, True),
        ({"valuation_status": ["failed", "unvalued"]}, False),
    ],
)
def test_advanced_filters_intersect_and_multi_values_union(filters, expected):
    assert search.matches(card(), search.SearchFilters(**filters)) is expected


def test_unknown_fields_are_not_zero_or_known_false():
    missing = card(
        purchase_total_cost=None,
        grade_company=None,
        grade_value=None,
        fair_value=None,
        serial_total=None,
        is_numbered=False,
        valuation_status="insufficient_evidence",
    )
    assert search.matches(
        missing, search.SearchFilters(has_cost=False, graded=False, valuation_status=["unvalued"])
    )
    assert not search.matches(missing, search.SearchFilters(cost_max=10))
    assert not search.matches(missing, search.SearchFilters(value_max=10))
    assert not search.matches(missing, search.SearchFilters(grade_max=10))
    assert not search.matches(missing, search.SearchFilters(serial_max=99))
    assert search.matches(missing, search.SearchFilters(valuation_status=["insufficient_evidence"]))


@pytest.mark.parametrize(
    "data",
    [
        {"q": "x" * 161},
        {"year_min": 2026, "year_max": 2025},
        {"cost_min": -1},
        {"value_max": "NaN"},
        {"grade_min": 11},
        {"serial_min": 0},
        {"brand": ["x"] * 21},
        {"brand": [""]},
        {"valuation_status": ["made-up"]},
        {"owner_id": "another"},
    ],
)
def test_invalid_filters_rejected(data):
    with pytest.raises(ValidationError):
        search.SearchFilters(**data)


def test_empty_query_matches_all_without_changing_fields():
    original = card()
    assert search.matches(original, search.SearchFilters(q="   "))
    assert original == card()


@pytest.mark.parametrize("state", ["identifying", "searching", "calculating"])
def test_running_filter_covers_real_valuation_worker_phases(state):
    assert search.matches(
        card(fair_value=None, valuation_status=state),
        search.SearchFilters(valuation_status=["running"]),
    )


@pytest.mark.parametrize(
    "player,query", [("Matas Buzelis", "Tristan da Silva"), ("Tristan da Silva", "Matas Buzelis")]
)
def test_roster_discovery_is_directional_not_individual_identity_alias(player, query):
    assert not search.matches(card(player_name=player), search.SearchFilters(q=query))
    assert not search.matches(card(player_name=player), search.SearchFilters(player=[query]))
    assert search.matches(
        card(player_name="Matas Buzelis Tristan da Silva"), search.SearchFilters(q=query)
    )
