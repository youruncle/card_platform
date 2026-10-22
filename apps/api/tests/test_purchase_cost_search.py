import importlib.util

import pytest

from card_platform.domain import card_identity
from card_platform import purchase_cost_search
from card_platform.knowledge import KnowledgeResolver
from card_platform.purchase_cost_search import build_purchase_cost_search_plan


def knowledge(canonical="Kyrie Irving", aliases=None):
    return KnowledgeResolver(
        {
            "version": 7,
            "entities": [
                {
                    "id": "player-irving",
                    "entity_type": "player",
                    "canonical_name": canonical,
                    "aliases": aliases
                    if aliases is not None
                    else ["K. Irving", "Kyrie A. Irving", "凯里·欧文", "欧文", "欧神仙"],
                }
            ],
        }
    )


def test_public_number_is_distinct_from_internal_url_id_and_is_searched_first():
    plan = build_purchase_cost_search_plan(
        {
            "player_name": "欧文",
            "notes": "https://cardhobby.com.cn/market/item/900002 商品编号：260903092050782",
        },
        knowledge(),
    )
    assert plan["keywords"] == ["260903092050782", "Kyrie Irving", "欧文"]
    assert plan["keyword_sources"] == ["public_item_number", "canonical_player", "player_alias"]
    assert "900002" not in plan["keywords"]


def test_internal_id_without_public_number_never_becomes_a_keyword():
    plan = build_purchase_cost_search_plan(
        {"player_name": "Test Player", "notes": "https://cardhobby.com.cn/market/item/900002"}
    )
    assert plan["keywords"] == ["Test Player"]


def test_english_aliases_cannot_crowd_out_chinese_names():
    plan = build_purchase_cost_search_plan({"player_name": "K. Irving"}, knowledge())
    assert plan["keywords"] == ["Kyrie Irving", "欧文", "欧神仙"]
    assert "凯里·欧文" not in plan["keywords"]  # Covered by the shorter literal query.


def test_chinese_canonical_reserves_space_for_english_alias():
    plan = build_purchase_cost_search_plan(
        {"player_name": "凯里·欧文"},
        knowledge("凯里·欧文", ["欧文", "欧神仙", "Kyrie Irving", "K. Irving"]),
    )
    assert plan["keywords"] == ["凯里·欧文", "Kyrie Irving", "欧文"]


def test_explicit_card_aliases_work_without_published_knowledge():
    plan = build_purchase_cost_search_plan(
        {"player_name": "Kyrie Irving", "player_name_aliases": ["K. Irving", "欧文"]}
    )
    assert plan["keywords"] == ["Kyrie Irving", "欧文", "K. Irving"]


def test_explicit_alias_can_recover_unknown_name_automatically():
    plan = build_purchase_cost_search_plan(
        {"player_name": "unknown", "player_name_aliases": "欧文"}, knowledge()
    )
    assert plan["status"] == "ready"
    assert plan["keywords"][:2] == ["Kyrie Irving", "欧文"]


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "none",
        "NULL",
        "unknown",
        "unspecified",
        "not applicable",
        "not identified",
        "N/A",
        "NA",
        "未知",
        "未识别",
        "无法识别",
        "!!!",
        "12345",
    ],
)
def test_missing_identity_skips_without_asking_for_keywords(name):
    plan = build_purchase_cost_search_plan(
        {"player_name": name, "season_start": 2025, "product_name": "NBA Hoops"}
    )
    assert plan["status"] == "skipped"
    assert plan["keywords"] == []
    assert plan["keyword_sources"] == []
    assert "关键词" not in plan["reason"]
    assert "已跳过" in plan["reason"]


def test_public_code_can_locate_order_even_when_player_is_missing():
    plan = build_purchase_cost_search_plan({"notes": "商品编号 260903092050782"})
    assert plan["status"] == "ready"
    assert plan["keywords"] == ["260903092050782"]


def test_punctuation_whitespace_and_duplicate_aliases_do_not_waste_queries():
    plan = build_purchase_cost_search_plan(
        {
            "player_name": " 【Kyrie\u200b   Irving】 ",
            "player_name_aliases": ["KYRIE IRVING", "Kyrie Irving!!!", None, "？？？"],
        },
        knowledge(aliases=["欧文"]),
    )
    assert plan["keywords"][:2] == ["Kyrie Irving", "欧文"]
    assert len(plan["keywords"]) == len(set(plan["keywords"]))


def test_punctuation_inside_names_is_preserved():
    plan = build_purchase_cost_search_plan({"player_name": "D'Angelo Russell"})
    assert plan["keywords"] == ["D'Angelo Russell", "拉塞尔", "DAngelo Russell"]


def test_card_fields_are_for_identity_matching_not_invented_search_operators():
    plan = build_purchase_cost_search_plan(
        {
            "player_name": "Test Player",
            "season_start": 2025,
            "product_name": "Principal Powers",
            "insert_name": "Upper Deck Reserve",
            "parallel_name": "Gold",
            "card_number": "123",
        }
    )
    assert plan["keywords"] == ["Test Player"]
    assert plan["label"] == "Test Player 2025 Upper Deck Reserve"


def test_invalid_overlong_keywords_are_skipped_before_budget_is_applied():
    plan = build_purchase_cost_search_plan(
        {
            "player_name": "X" * 121,
            "player_name_aliases": ["Y" * 121, "Test Player", "测试球员"],
            "notes": "商品编号：" + "1" * 41,
        }
    )
    assert plan["keywords"] == ["Test Player", "测试球员"]
    assert all(0 < len(keyword) <= 120 for keyword in plan["keywords"])


def test_plan_is_deterministic_bounded_and_does_not_modify_card():
    card = {"player_name": "Kyrie Irving", "notes": "商品编号：123；商品编号：123"}
    original = dict(card)
    first = build_purchase_cost_search_plan(card, knowledge(), max_keywords=2)
    assert first == build_purchase_cost_search_plan(card, knowledge(), max_keywords=2)
    assert first["keywords"] == ["123", "Kyrie Irving"]
    assert card == original


@pytest.mark.parametrize("limit", [0, -1, 7])
def test_invalid_query_budget_is_rejected(limit):
    with pytest.raises(ValueError):
        build_purchase_cost_search_plan({"player_name": "Test Player"}, max_keywords=limit)


def test_planner_does_not_require_newer_identity_placeholder_helper(monkeypatch):
    monkeypatch.delattr(card_identity, "is_placeholder_identity_value", raising=False)
    spec = importlib.util.spec_from_file_location(
        "purchase_cost_search_compatibility", purchase_cost_search.__file__
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Importability is the compatibility boundary: the resolver itself is
    # supplied by whichever application version hosts this feature.
    assert module.CardIdentityResolver is card_identity.CardIdentityResolver
    assert module._clean_name("N/A") == ""
    assert module._clean_name("Test Player") == "Test Player"

def test_three_subject_roster_reserves_chinese_recall_in_three_keyword_budget():
    plan=build_purchase_cost_search_plan({'player_name':'Jason Kidd / Jason Terry / Dirk Nowitzki'})
    assert len(plan['keywords'])==3
    assert all(any('\u4e00' <= char <= '\u9fff' for char in term) for term in plan['keywords'])
