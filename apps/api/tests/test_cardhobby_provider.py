from decimal import Decimal

import httpx
import pytest

from card_platform.ingestion.cardhobby import (
    CARDHOBBY_SINGLE_CARD_CATEGORY_ID,
    CardHobbyProvider,
    CardHobbySale,
    assess_cardhobby_candidate,
    assess_cardhobby_match,
    cardhobby_item_id,
    extract_cardhobby_images,
    is_exact_cardhobby_reference,
    product_search_aliases,
    _normalized,
)
from card_platform.knowledge import KnowledgeResolver


def _sale(**updates) -> CardHobbySale:
    values = {
        "item_id": None,
        "title": "2021-22 Panini Immaculate Tim Duncan 75周年 /75 球衣物料",
        "source_url": "https://www.cardhobby.com.cn/market/feedback/469520",
        "occurred_at": "2026-07-01T20:00:00+08:00",
        "sale_price": Decimal("380"),
        "shipping_price": Decimal("18"),
        "bid_count": 12,
        "sale_type": "auction",
        "evidence_level": "confirmed_transaction",
        "sold_status_evidence": "来自售出的宝贝列表，竞价已结束",
        "matching_fields": ["player", "season", "product", "print_run"],
        "match_confidence": Decimal("0.95"),
    }
    values.update(updates)
    return CardHobbySale(**values)


def _raw_identity() -> dict:
    return {
        "player_name": "Tim Duncan",
        "brand_name": "Panini",
        "season_start": 2021,
        "season_end": 2022,
        "product_name": "Immaculate",
        "card_number": None,
        "insert_name": None,
        "parallel_name": "75周年",
        "serial_total": 75,
        "is_autograph": False,
        "is_patch": True,
        "grade_company": None,
        "grade_value": None,
    }


@pytest.mark.asyncio
async def test_model_fallback_is_discovery_only(monkeypatch) -> None:
    provider = CardHobbyProvider(object(), "unused")

    async def no_direct_sales(_identity):
        from card_platform.ingestion.cardhobby import CardHobbySearchResult

        return CardHobbySearchResult(
            search_queries=["卡淘已售接口[strong_anchor]：库里 34"],
            notes=["direct empty"],
        )

    async def model_candidates(_identity):
        from card_platform.ingestion.cardhobby import CardHobbySearchResult

        return CardHobbySearchResult(sales=[_sale()], notes=["model discovery"])

    monkeypatch.setattr(provider, "_search_direct", no_direct_sales)
    monkeypatch.setattr(provider, "_search_model", model_candidates)

    result = await provider.search(_raw_identity())

    assert result.sales == []
    assert "卡淘已售接口[strong_anchor]：库里 34" in result.search_queries
    assert any("仅作发现线索" in note for note in result.notes)


@pytest.mark.asyncio
async def test_direct_cardhobby_search_does_not_require_a_model_client(monkeypatch) -> None:
    provider = CardHobbyProvider(None, "unused")

    async def no_direct_sales(_identity):
        from card_platform.ingestion.cardhobby import CardHobbySearchResult

        return CardHobbySearchResult(notes=["卡淘公开接口已执行"])

    async def forbidden_model_search(_identity):
        raise AssertionError("model fallback must not run without a client")

    monkeypatch.setattr(provider, "_search_direct", no_direct_sales)
    monkeypatch.setattr(provider, "_search_model", forbidden_model_search)

    result = await provider.search(_raw_identity())

    assert result.sales == []
    assert any("公开成交接口仍已独立执行" in note for note in result.notes)


def test_memorabilia_identity_does_not_require_patch_flag() -> None:
    identity = {
        **_raw_identity(),
        "is_patch": False,
        "is_memorabilia": True,
    }

    score, conflicts = assess_cardhobby_match(identity, _sale())

    assert score >= Decimal("0.8")
    assert conflicts == []


def test_adaptive_query_plan_starts_with_two_strong_anchors() -> None:
    plan = CardHobbyProvider.build_query_plan(
        {
            "player_name": "Shaquille O'Neal",
            "product_name": "Topps Chrome",
            "insert_name": "Fanatical",
            "card_number": "FAN-16",
        }
    )

    assert plan[:2] == [
        ("strong_anchor", "奥尼尔 FAN-16"),
        ("strong_anchor", "奥尼尔 Fanatical"),
    ]
    assert any(stage == "refine_product" for stage, _query in plan)
    assert all("CMP" not in query for _stage, query in plan)


def test_query_budget_reserves_player_season_insert_across_products() -> None:
    identity = {
        "player_name": "Kobe Bryant",
        "season_start": 2018,
        "season_end": 2019,
        "brand_name": "Panini",
        "product_name": "Donruss Basketball",
        "card_number": "19",
        "insert_name": "All Heart",
    }

    plan = CardHobbyProvider.build_query_plan(identity)
    legacy = CardHobbyProvider.build_queries(identity)

    assert ("season_insert", "Kobe Bryant 2018-19 All Heart") in plan
    assert "Kobe Bryant 2018-19 All Heart" in legacy
    assert len(plan) <= 12
    assert len(legacy) <= 12


@pytest.mark.parametrize(
    ("product_name", "expected_alias"),
    [
        ("National Treasures", "National Treasures"),
        ("Mosaic", "马赛克"),
        ("Photogenic Basketball", "上镜"),
        ("MJ's Back", "MJ's Back"),
    ],
)
def test_query_budget_reserves_a_script_diverse_product_alias(
    product_name: str, expected_alias: str
) -> None:
    plan = CardHobbyProvider.build_query_plan(
        {
            "player_name": "Kobe Bryant",
            "season_start": 2018,
            "season_end": 2019,
            "product_name": product_name,
            "card_number": "19",
        }
    )

    assert any(
        stage == "product_alias_rotation" and expected_alias in query
        for stage, query in plan
    )
    assert len(plan) <= 12


@pytest.mark.asyncio
async def test_runtime_query_budget_reserves_reviewed_knowledge_aliases(
    monkeypatch,
) -> None:
    resolver = KnowledgeResolver(
        {
            "version": 1,
            "entities": [
                {
                    "id": "product-reviewed",
                    "entity_type": "product",
                    "canonical_name": "Resolver Catalog",
                    "aliases": ["审核产品"],
                }
            ],
        }
    )
    provider = CardHobbyProvider(
        None,
        "unused",
        resolver=resolver,
        min_interval=0,
    )
    monkeypatch.setattr(
        provider,
        "build_query_plan",
        lambda _identity: [
            ("provider_query", f"provider-query-{index}") for index in range(12)
        ],
    )
    executed_queries: list[str] = []

    async def empty_search(_client, query: str, _page_index: int) -> dict:
        executed_queries.append(query)
        return {"data": {"PagedMarketItemList": []}}

    monkeypatch.setattr(provider, "_get_search_payload", empty_search)

    result = await provider._search_direct(
        {
            "player_name": "Test Player",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Resolver Catalog",
            "card_number": "42",
        }
    )

    assert len(result.query_diagnostics) == 12
    assert executed_queries == [item.query for item in result.query_diagnostics]
    knowledge_queries = [
        item.query
        for item in result.query_diagnostics
        if item.stage == "knowledge_alias"
    ]
    assert len(knowledge_queries) == 2
    assert all("审核产品" in query for query in knowledge_queries)
    assert executed_queries[:10] == [f"provider-query-{index}" for index in range(10)]


def test_query_plan_does_not_invent_a_season_insert_anchor() -> None:
    plan = CardHobbyProvider.build_query_plan(
        {
            "player_name": "Kobe Bryant",
            "product_name": "Donruss Basketball",
            "insert_name": "All Heart",
        }
    )

    assert not any(stage == "season_insert" for stage, _query in plan)


def test_discovery_only_nickname_cannot_cross_match_another_player() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Magic Johnson",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Chrome Update",
        "insert_name": "Stratospheric Stars",
        "card_number": "ST-22",
        "serial_total": 150,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Topps Chrome Update Josh Giddey 52/150编 "
                "Stratospheric 澳大利亚魔术师"
            )
        ),
    )

    assert score == 0
    assert "标题未命中目标球员" in conflicts


def test_jason_kidd_short_alias_cannot_match_kerry_kittles() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Jason Kidd",
        "season_start": 1997,
        "season_end": 1998,
        "product_name": "UD3",
        "insert_name": "Starstruck",
        "card_number": "28",
        "serial_total": None,
        "is_patch": False,
    }

    wrong_score, wrong_conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "1997 Upper Deck UD3 Kerry Kittles 基德尔斯 "
                "红色平行透明胶片特卡"
            )
        ),
    )
    right_score, right_conflicts = assess_cardhobby_match(
        identity,
        _sale(title="1997 Upper Deck UD3 贾森基德 太阳 折射 老卡"),
    )

    assert wrong_score == 0
    assert "标题未命中目标球员" in wrong_conflicts
    assert right_score >= Decimal("0.5")
    assert "标题未命中目标卡种，需图像确认" in right_conflicts


def test_unnumbered_target_rejects_explicit_numbered_parallel() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Shaquille O'Neal",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Chrome",
        "insert_name": "Fanatical",
        "card_number": "FAN-16",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Topps Chrome Shaquille O'Neal FAN-16 "
                "Fanatical 金折 05/50编"
            )
        ),
    )

    assert score == 0
    assert "目标未识别为限编版本，但标题明确为限编平行" in conflicts


def test_cardhobby_item_url_requires_official_item_path() -> None:
    assert (
        cardhobby_item_id("https://www.cardhobby.com.cn/market/item/123456789")
        == "123456789"
    )
    assert cardhobby_item_id("https://example.com/market/item/123456789") is None
    assert cardhobby_item_id("https://www.cardhobby.com.cn/market/search?keyword=x") is None


def test_feedback_url_requires_numeric_account_id() -> None:
    sale = _sale(source_url="https://www.cardhobby.com.cn/market/feedback?keyword=Tim+Duncan")
    score, conflicts = assess_cardhobby_match(_raw_identity(), sale)
    assert score == 0
    assert any("不是受支持" in conflict for conflict in conflicts)


def test_exact_raw_cardhobby_sale_gets_high_match_score() -> None:
    score, conflicts = assess_cardhobby_match(_raw_identity(), _sale())
    assert score >= Decimal("0.8")
    assert conflicts == []


def test_mcdonalds_product_matches_split_chinese_seller_vocabulary() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "AJ Dybantsa",
        "brand_name": "Topps",
        "season_start": 2025,
        "season_end": 2025,
        "product_name": "Topps Chrome McDonald's All American",
        "card_number": "45",
        "parallel_name": "Red Lava Refractor",
        "serial_total": 125,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025 Topps Chrome AJ Dybantsa 麦当劳全美 #45 "
                "红熔岩折 47/125编"
            )
        ),
    )

    assert score >= Decimal("0.5")
    assert conflicts == []


def test_cardhobby_accepts_end_year_shorthand_for_cross_year_product() -> None:
    score, conflicts = assess_cardhobby_match(
        {
            **_raw_identity(),
            "player_name": "Collin Murray-Boyles",
            "brand_name": "Topps",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Three",
            "parallel_name": "Base",
            "serial_total": 49,
            "is_autograph": True,
        },
        _sale(
            item_id="123866225",
            source_url="https://www.cardhobby.com.cn/market/item/123866225",
            evidence_level="ended_auction",
            title=(
                "2026 Topps 3 Collin Murray-Boyles RC 41/49编 "
                "三窗 RPA 球衣patch"
            ),
        ),
    )
    assert score >= Decimal("0.5")
    assert conflicts == []


def test_lot_is_rejected_even_when_identity_tokens_match() -> None:
    score, conflicts = assess_cardhobby_match(
        _raw_identity(), _sale(title="2021-22 Immaculate Tim Duncan /75 5卡 lot 打包")
    )
    assert score == 0
    assert any("多卡" in conflict for conflict in conflicts)


def test_mjs_back_silver_set_card_phrase_is_not_misclassified_as_a_lot() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Michael Jordan",
        "brand_name": "Upper Deck",
        "season_start": 2001,
        "season_end": 2002,
        "product_name": "MJ's Back",
        "card_number": "MJ-90",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            item_id="102913627",
            source_url="https://cardhobby.com.cn/Market/Item/102913627",
            evidence_level="ended_auction",
            title=(
                "2001 Upper Deck MJ'S Back Michael Jordan 乔丹 "
                "经典系列银标套卡 专收凑套收藏必备"
            ),
        ),
    )

    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert not any("多卡" in conflict for conflict in conflicts)
    assert any("卡号" in conflict for conflict in conflicts)


def test_mjs_back_search_queries_include_cardhobby_silver_nickname() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Michael Jordan",
            "season_start": 2001,
            "season_end": 2002,
            "product_name": "MJ's Back",
            "card_number": "MJ-90",
        }
    )

    assert queries[0] == "Michael Jordan 2001 银标"


def test_stained_glass_queries_include_chinese_church_seller_alias() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "LeBron James",
            "season_start": 2020,
            "season_end": 2021,
            "brand_name": "Panini",
            "product_name": "Mosaic Basketball",
            "insert_name": "Stained Glass",
            "card_number": "1",
        }
    )

    assert any("教堂" in query for query in queries)


def test_bowman_rookies_and_stars_queries_use_seller_short_name_and_release_year() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Allen Iverson",
            "brand_name": "Topps",
            "season_start": 2003,
            "season_end": 2004,
            "product_name": "Bowman Rookies and Stars",
            "card_number": "10",
        }
    )

    assert queries[0] == "Allen Iverson 2003 Bowman"


def test_bowman_basketball_queries_start_with_release_year_seller_name() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Bill Russell",
        "brand_name": "Bowman",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Bowman Basketball",
        "card_number": "RV-BR",
        "insert_name": "Retrofractor Variations",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    queries = CardHobbyProvider.build_queries(identity)

    assert queries[0] == "Bill Russell 2025 Bowman"


def test_bowman_retrofractor_seller_vocabulary_is_exact_candidate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Bill Russell",
        "brand_name": "Bowman",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Bowman Basketball",
        "card_number": "RV-BR",
        "insert_name": "Retrofractor Variations",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            title=(
                "2025-26 Topps Bowman Bill Russell 1st 比尔拉塞尔 银折 "
                "Retro Refractor 复古 变化版"
            )
        ),
    )

    assert score >= Decimal("0.65")
    assert tier == "exact_or_near_exact"
    assert not conflicts


def test_common_color_parallel_aliases_filter_wrong_color_before_images() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LeBron James",
        "brand_name": "Topps",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps NBA Hoops",
        "card_number": "BH-2",
        "insert_name": "Bounce House",
        "parallel_name": "Silver",
        "serial_total": None,
        "is_patch": False,
    }
    silver_score, silver_conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Topps NBA Hoops LeBron James #BH-2 "
                "Bounce House 跳跳屋 正银折"
            )
        ),
    )
    purple_score, purple_conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Topps NBA Hoops LeBron James Bounce House "
                "26/99 紫折"
            )
        ),
    )

    assert silver_score >= Decimal("0.65")
    assert not silver_conflicts
    assert purple_score == 0
    assert any("平行版本" in conflict for conflict in purple_conflicts)


def test_stained_glass_chinese_seller_alias_is_an_exact_insert_match() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kevin Durant",
        "season_start": 2020,
        "season_end": 2021,
        "product_name": "Mosaic Basketball",
        "card_number": "3",
        "insert_name": "Stained Glass",
        "parallel_name": None,
        "serial_total": None,
        "grade_company": None,
        "grade_value": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2020-21 Panini Mosaic Kevin Durant 凯文杜兰特 "
                "教堂玻璃 特卡 大比例 SSP 马赛克折"
            )
        ),
    )

    assert score >= Decimal("0.65")
    assert not conflicts


@pytest.mark.parametrize(
    "title",
    [
        "2003 Topps Bowman's Best Allen Iverson 特卡",
        "2003-04 Bowman Chrome Allen Iverson #10",
        "2003 Bowman Draft Allen Iverson",
    ],
)
def test_bowman_short_alias_does_not_cross_into_other_product_families(
    title: str,
) -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Allen Iverson",
        "brand_name": "Topps",
        "season_start": 2003,
        "season_end": 2004,
        "product_name": "Bowman Rookies and Stars",
        "card_number": "10",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(identity, _sale(title=title))

    assert score == 0
    assert any("产品" in conflict for conflict in conflicts)


def test_plain_bowman_seller_title_is_discoverable_as_same_product_analog() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Allen Iverson",
        "brand_name": "Topps",
        "season_start": 2003,
        "season_end": 2004,
        "product_name": "Bowman Rookies and Stars",
        "card_number": "10",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            item_id="107549728",
            source_url="https://www.cardhobby.com.cn/market/item/107549728",
            evidence_level="ended_auction",
            title="2003-04 Topps Bowman Allen Iverson 艾弗森 银版SP 特卡",
        ),
    )

    assert score > 0
    assert tier == "same_player_product_analog"
    assert not any("产品系列" in conflict for conflict in conflicts)


def test_donruss_catalog_sport_suffix_may_be_omitted_by_seller() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kobe Bryant",
        "brand_name": "Panini",
        "season_start": 2018,
        "season_end": 2019,
        "product_name": "Donruss Basketball",
        "card_number": "19",
        "insert_name": "All Heart",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2018-19 Panini Donruss Kobe Bryant 科比 "
                "All Heart 特卡 base"
            )
        ),
    )

    assert score >= Decimal("0.65")
    assert conflicts == []


def test_donruss_parent_product_does_not_accept_optic_child() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kobe Bryant",
        "brand_name": "Panini",
        "season_start": 2018,
        "season_end": 2019,
        "product_name": "Donruss Basketball",
        "card_number": "19",
        "insert_name": "All Heart",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2018-19 Panini Donruss Optic Kobe Bryant 科比 "
                "All Heart 特卡"
            )
        ),
    )

    assert score == 0
    assert any("产品系列" in conflict for conflict in conflicts)


def test_parent_product_uses_target_set_context_but_keeps_serial_gate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kyle Anderson",
        "brand_name": "Panini",
        "season_start": 2014,
        "season_end": 2015,
        "product_name": "Donruss Basketball",
        "set_name": "Elite",
        "card_number": "31",
        "insert_name": "Rookie Autographs Die-Cuts",
        "parallel_name": None,
        "serial_total": 49,
        "is_autograph": True,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2014-15 Panini Donruss Elite Kyle Anderson RC "
                "Rookie Autographs Die-Cuts 签字 3/99编"
            )
        ),
    )

    assert score == 0
    assert not any("产品系列" in conflict for conflict in conflicts)
    assert any("限编与目标卡不一致" in conflict for conflict in conflicts)


def test_parent_product_uses_target_set_context_for_image_review_candidate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kyle Anderson",
        "brand_name": "Panini",
        "season_start": 2014,
        "season_end": 2015,
        "product_name": "Donruss Basketball",
        "set_name": "Elite",
        "card_number": "31",
        "insert_name": "Rookie Autographs Die-Cuts",
        "parallel_name": None,
        "serial_total": 49,
        "is_autograph": True,
        "is_patch": False,
    }

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            title=(
                "2014-15 Panini Donruss Elite Kyle Anderson RC "
                "蓝平行 异形 签字 3/49编"
            ),
            image_url="https://images.cardhobby.com/kyle-anderson.jpg",
        ),
    )

    assert score == Decimal("0.45")
    assert tier == "image_review_candidate"
    assert not any("产品系列" in conflict for conflict in conflicts)


def _unanchored_midnight_identity() -> dict:
    return {
        **_raw_identity(),
        "player_name": "Kon Knueppel",
        "brand_name": "Topps",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Midnight",
        "card_number": None,
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "print_run": None,
        "is_patch": False,
    }


def test_unanchored_target_keeps_plain_same_product_sale_as_analog() -> None:
    score, conflicts, tier = assess_cardhobby_candidate(
        _unanchored_midnight_identity(),
        _sale(
            title=(
                "2025-26 Topps Midnight Kon Knueppel 康 克努佩尔 "
                "黄蜂 新秀 RC"
            )
        ),
    )

    assert score == Decimal("0.40")
    assert conflicts == ["目标身份缺少卡号、卡种、平行或限编锚点"]
    assert tier == "same_player_product_analog"


@pytest.mark.parametrize(
    "variant_label",
    ["Twilight 暮光特卡", "Twilight SSP", "Twilight insert", "Twilight variation"],
)
def test_unanchored_target_demotes_explicit_named_card_to_product_analog(
    variant_label: str,
) -> None:
    score, conflicts, tier = assess_cardhobby_candidate(
        _unanchored_midnight_identity(),
        _sale(
            title=(
                "2025-26 Topps Midnight Kon Knueppel 康 克努佩尔 "
                f"黄蜂 新秀 RC {variant_label}"
            )
        ),
    )

    assert score == Decimal("0.40")
    assert "目标缺少卡片锚点，但标题明确为命名特卡或变体" in conflicts
    assert tier == "same_player_product_analog"


@pytest.mark.parametrize(
    "variant_label",
    [
        "Red Cracked Ice",
        "Twilight SSP Red Cracked Ice",
        "Twilight SSP 12/25编",
        "星座平行 Base 折射",
        "月光粉 base 十二宫",
        "zodiac平行 Base Refractor",
        "Base 银闪版 蓝平行",
    ],
)
def test_unanchored_target_still_rejects_material_or_numbered_parallel(
    variant_label: str,
) -> None:
    score, conflicts, tier = assess_cardhobby_candidate(
        _unanchored_midnight_identity(),
        _sale(
            title=(
                "2025-26 Topps Midnight Kon Knueppel 康 克努佩尔 "
                f"黄蜂 新秀 RC {variant_label}"
            )
        ),
    )

    assert score == 0
    assert tier is None
    assert any("平行" in conflict for conflict in conflicts)


def test_explicit_base_with_ambiguous_finish_and_no_card_number_is_an_analog() -> None:
    identity = {
        **_unanchored_midnight_identity(),
        "card_number": "64",
    }
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            title=(
                "2025-26 Topps Midnight Kon Knueppel 康 克努佩尔 "
                "黄蜂 新秀 RC Base 折射"
            )
        ),
    )

    assert score == Decimal("0.40")
    assert "标题未命中目标卡号，基础/原生闪卡需图像确认" in conflicts
    assert tier == "same_player_product_analog"


def test_unnumbered_insert_rejects_explicit_red_diamond_parallel() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LeBron James",
        "brand_name": "Panini",
        "season_start": 2023,
        "season_end": 2024,
        "product_name": "Donruss Basketball",
        "card_number": "9",
        "insert_name": "Unleashed",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            title=(
                "2023-24 Panini Donruss LeBron James 詹姆斯 "
                "Unleashed #9 红钻石折"
            )
        ),
    )

    assert score == 0
    assert tier is None
    assert any("其他平行" in conflict for conflict in conflicts)


def test_uniform_description_is_not_misclassified_as_jersey_material() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Michael Jordan",
        "brand_name": "Upper Deck",
        "season_start": 2001,
        "season_end": 2002,
        "product_name": "MJ's Back",
        "card_number": "MJ-90",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            item_id="102913627",
            source_url="https://cardhobby.com.cn/Market/Item/102913627",
            evidence_level="ended_auction",
            title=(
                "2001 Upper Deck MJ'S Back Michael Jordan 乔丹 经典系列银标套卡 "
                "公牛客场23号球衣"
            ),
        ),
    )

    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert not any("物料" in conflict for conflict in conflicts)


def test_actual_jersey_material_still_conflicts_with_raw_base_target() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Michael Jordan",
        "product_name": "MJ's Back",
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2001 Upper Deck MJ'S Back Michael Jordan 球衣物料切割卡"),
    )

    assert score == 0
    assert any("物料" in conflict for conflict in conflicts)


def test_explicit_full_mjs_back_set_remains_rejected() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Michael Jordan",
        "product_name": "MJ's Back",
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2001 Upper Deck MJ'S Back Michael Jordan 银标套卡 90张整套"),
    )

    assert score == 0
    assert any("多卡" in conflict for conflict in conflicts)


def test_chinese_multi_card_same_listing_is_rejected() -> None:
    score, conflicts = assess_cardhobby_match(
        _raw_identity(),
        _sale(title="2021-22 Immaculate Tim Duncan 邓肯 4张同出 不保卡品"),
    )

    assert score == 0
    assert any("多卡" in conflict for conflict in conflicts)


@pytest.mark.parametrize("quantity_phrase", ["4张通行品", "共2张", "3 张"])
def test_chinese_multi_card_quantity_listing_is_rejected(
    quantity_phrase: str,
) -> None:
    score, conflicts = assess_cardhobby_match(
        _raw_identity(),
        _sale(
            title=(
                "2021-22 Immaculate Tim Duncan 邓肯 "
                f"{quantity_phrase} 不保卡品"
            )
        ),
    )

    assert score == 0
    assert any("多卡" in conflict for conflict in conflicts)


def test_numeric_card_number_does_not_match_digits_inside_season() -> None:
    identity = {
        "player_name": "Victor Wembanyama",
        "season_start": 2024,
        "season_end": 2025,
        "product_name": "Mosaic Basketball",
        "card_number": "4",
        "insert_name": "Jam Masters",
        "is_autograph": False,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2024-25 Panini Mosaic Victor Wembanyama "
                "Thunder Road 特卡 马赛克折"
            )
        ),
    )

    assert score == 0
    assert "标题明确命中其他卡种，与目标卡种不一致" in conflicts


def test_cross_sell_player_mention_is_not_accepted_as_target_card() -> None:
    score, conflicts = assess_cardhobby_match(
        {
            **_raw_identity(),
            "player_name": "John Stockton",
            "season_start": 2000,
            "season_end": 2001,
            "product_name": "Fleer Showcase",
            "is_patch": False,
        },
        _sale(
            title=(
                "2000 Fleer Showcase Karl Malone 卡尔马龙 银折，"
                "主页同版 John Stockton 斯托克顿在拍"
            )
        ),
    )
    assert score == 0
    assert any("交叉推广" in conflict for conflict in conflicts)


def test_mosaic_texture_in_another_product_is_not_a_product_match() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Jaren Jackson Jr.",
        "product_name": "Mosaic Basketball",
        "season_start": 2019,
        "season_end": 2020,
        "serial_total": 50,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2026 Topps Finest Jaren Jackson Jr. 金马赛克折 3/50编"),
    )
    assert score == 0
    assert any("产品系列" in conflict for conflict in conflicts)


def test_checkerboard_finish_is_not_a_base_card_comparable() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Shaedon Sharpe",
        "product_name": "Topps Chrome",
        "season_start": 2025,
        "season_end": 2026,
        "card_number": "106",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2026 Topps Chrome Shaedon Sharpe "
                "棋盘格折射 弹跳新星"
            )
        ),
    )

    assert score == 0
    assert any("目标为基础卡" in conflict for conflict in conflicts)


@pytest.mark.parametrize(
    "finish", ["变形虫折", "龙鳞折射", "银脉冲折", "Amoeba", "Dragon Scale"]
)
def test_named_technology_finish_is_not_a_base_card_comparable(
    finish: str,
) -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Bobby Portis",
        "product_name": "Topps Chrome",
        "season_start": 2024,
        "season_end": 2025,
        "card_number": "124",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title=f"2025 Topps Chrome Bobby Portis {finish}"),
    )

    assert score == 0
    assert any("目标为基础卡" in conflict for conflict in conflicts)


def test_legendary_tandems_chinese_alias_matches_both_players() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Magic Johnson, Kareem Abdul-Jabbar",
        "product_name": "Contenders Optic",
        "season_start": 2022,
        "season_end": 2023,
        "card_number": "9",
        "insert_name": "Legendary Tandems",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            title=(
                "2022-23 Panini Contenders Optic Magic Johnson 魔术师约翰逊 "
                "Kareem Abdul-Jabbar 贾巴尔 传奇双人特卡 银折"
            )
        ),
    )

    assert score > 0
    assert tier is not None
    assert not any("卡号或卡种" in conflict for conflict in conflicts)


def test_chinese_multi_player_list_is_rejected() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Jaren Jackson Jr.",
        "player_name_aliases": ["小贾伦 杰克逊"],
        "product_name": "Mosaic Basketball",
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "Panini Mosaic 杜蕾斯 小贾伦 杰克逊 阿德巴约 欧文等 "
                "特卡 Base 银折 红圈圈折"
            )
        ),
    )
    assert score == 0
    assert any("多人卡" in conflict for conflict in conflicts)


def test_confirmed_dual_player_memorabilia_card_is_not_rejected_as_a_lot() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Matas Buzelis, Tristan da Silva",
        "player_name_aliases": ["布泽利斯 达席尔瓦"],
        "season_start": 2024,
        "season_end": 2025,
        "product_name": "Immaculate Collection",
        "card_number": "12",
        "insert_name": "Remarkable Rookie Jerseys Dual",
        "parallel_name": "Blue",
        "serial_total": 25,
        "is_autograph": False,
        "is_patch": True,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2024-25 Panini Immaculate 马塔斯布泽利斯 特里斯坦达席尔瓦 11/25编 "
                "蓝平行 双人双窗球衣物料 Patch"
            )
        ),
    )
    assert score >= Decimal("0.5")
    assert conflicts == []


def test_numbered_target_requires_target_print_run_in_title() -> None:
    score, conflicts = assess_cardhobby_match(
        _raw_identity(),
        _sale(title="2021-22 Panini Immaculate Tim Duncan 球衣物料"),
    )
    assert score == 0
    assert any("未命中目标总限编" in conflict for conflict in conflicts)


def test_base_target_rejects_different_insert_when_card_number_is_omitted() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Scottie Barnes",
        "brand_name": "Topps",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Flagship",
        "card_number": "31",
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2025-26 Topps Flagship Scottie Barnes Power Players 特卡"),
    )
    assert score == 0
    assert any("基础卡" in conflict for conflict in conflicts)


def test_known_other_insert_cannot_price_a_numbered_base_target() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Scottie Barnes",
        "brand_name": "Topps",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Flagship",
        "card_number": "31",
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(title="2025-26 Topps Flagship Scottie Barnes Power Players 特卡"),
    )
    assert score == Decimal("0")
    assert tier is None
    assert any("命名特卡或变体" in conflict for conflict in conflicts)


def test_different_product_is_never_retained_as_an_analog() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Luka Doncic",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Chrome",
        "serial_total": 21,
        "is_patch": False,
    }
    score, _conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(title="2025-26 Topps Flagship Luka Doncic /21"),
    )
    assert score == 0
    assert tier is None


def test_insert_anchor_is_required_for_specific_identity() -> None:
    identity = {
        **_raw_identity(),
        "card_number": "358",
        "insert_name": "Team Checklist",
        "parallel_name": None,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2021-22 Panini Immaculate Tim Duncan /75 球衣物料 base card"),
    )
    assert score == 0
    assert any("卡号或卡种" in conflict for conflict in conflicts)


def test_base_card_omitting_card_number_is_only_an_analog() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Shareef Abdur-Rahim",
        "brand_name": "Fleer",
        "product_name": "Fleer Showcase",
        "season_start": 2000,
        "season_end": 2001,
        "card_number": "74",
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(title="2000-01 Fleer Showcase Shareef Abdur-Rahim 灰熊 拉希姆 老卡"),
    )
    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert any("卡号" in conflict for conflict in conflicts)


def test_player_and_product_without_a_card_anchor_is_rejected() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Jaren Jackson Jr.",
        "brand_name": "Panini",
        "product_name": "Mosaic Basketball",
        "season_start": 2019,
        "season_end": 2020,
        "card_number": "29",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="Panini Mosaic Jaren Jackson Jr. 小贾伦 杰克逊 折射看图"),
    )
    assert score == 0
    assert any("卡号" in conflict for conflict in conflicts)


def test_player_alias_can_match_and_generates_broad_query() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "John Stockton",
        "player_name_aliases": ["约翰 斯托克顿", "斯托克顿"],
        "product_name": "Fleer Showcase",
        "season_start": 2000,
        "season_end": 2001,
        "card_number": "90",
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2000-01 Fleer Showcase 约翰 斯托克顿 #90"),
    )
    assert score >= Decimal("0.5")
    assert conflicts == []
    queries = CardHobbyProvider.build_queries(identity)
    assert "斯托克顿 Fleer Showcase" in queries


def test_curated_chinese_player_aliases_are_used_without_identity_metadata() -> None:
    queries = CardHobbyProvider.build_queries(
        {"player_name": "Stephen Curry", "product_name": "NBA Hoops"}
    )
    assert "斯蒂芬 库里 NBA Hoops" in queries
    assert "库里 NBA Hoops" in queries


def test_cross_year_finest_identity_includes_seller_copyright_year_query() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Tracy McGrady",
            "season_start": 1998,
            "season_end": 1999,
            "brand_name": "Topps",
            "product_name": "Finest",
            "card_number": "28",
        }
    )

    assert "Tracy McGrady 1998 Finest" in queries


def test_cross_year_black_diamond_includes_end_year_and_card_number_query() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "MICHAEL JORDAN",
            "season_start": 1998,
            "season_end": 1999,
            "brand_name": "Upper Deck",
            "product_name": "Black Diamond",
            "card_number": "12",
        }
    )

    assert "MICHAEL JORDAN 1999 Black Diamond 12" in queries
    assert "MICHAEL JORDAN 1998 Black Diamond 12" in queries


@pytest.mark.asyncio
async def test_direct_search_keeps_results_when_a_later_query_fails(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, query: str) -> None:
            self.query = query

        def raise_for_status(self) -> None:
            if self.query == "bad":
                request = httpx.Request("GET", "https://cardhobby.com.cn")
                raise httpx.HTTPStatusError(
                    "bad query", request=request, response=httpx.Response(400, request=request)
                )

        def json(self) -> dict:
            return {
                "data": {
                    "PagedMarketItemList": [
                        {
                            "Status": -2,
                            "OutCount": 2,
                            "ID": 105080690,
                            "Title": "2000-01 Fleer Showcase Shareef Abdur-Rahim",
                            "EffectiveDate": "2026-04-04 20:00:00",
                            "LowestPrice": 1,
                            "ByWay": 2,
                            "NewPostageMoney": "18",
                        }
                    ]
                }
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, params):
            return FakeResponse(params["searchKey"])

    monkeypatch.setattr(
        "card_platform.ingestion.cardhobby.httpx.AsyncClient",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        CardHobbyProvider,
        "build_queries",
        staticmethod(lambda _identity: ["good", "bad"]),
    )
    result = await CardHobbyProvider(None, "unused")._search_direct(
        {
            "player_name": "Shareef Abdur-Rahim",
            "season_start": 2000,
            "season_end": 2001,
            "product_name": "Fleer Showcase",
            "is_autograph": False,
            "is_patch": False,
        }
    )
    assert [sale.item_id for sale in result.sales] == ["105080690"]
    assert any("部分查询失败" in note for note in result.notes)
    assert any(item.outcome == "accepted" for item in result.query_diagnostics)
    assert any(item.outcome == "error" for item in result.query_diagnostics)
    assert all(item.raw_count >= item.eligible_count for item in result.query_diagnostics)


def test_team_checklist_accepts_common_cardhobby_misspelling() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Michael Jordan",
        "season_start": 1990,
        "season_end": 1991,
        "product_name": "Hoops",
        "card_number": "358",
        "insert_name": "Team Checklist",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="1990-91 Hoops Michael Jordan Team Checklits 艺术家插画"),
    )
    assert score >= Decimal("0.5")
    assert conflicts == []


@pytest.mark.parametrize(
    ("insert_name", "seller_title"),
    [
        ("Bounce House", "跳跳屋 特卡"),
        ("Jam Masters", "扣篮大师 大头特卡"),
        ("Bomb Squad!", "爆炸小队 特卡"),
        ("Magicians", "魔术师 特卡"),
        ("Penmanship", "书法签 卡签"),
        ("Jumbo Jersey Autographs", "大窗球衣签字 卡签 球衣物料"),
    ],
)
def test_cardhobby_insert_chinese_seller_aliases(
    insert_name: str, seller_title: str
) -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LeBron James",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "NBA Hoops",
        "card_number": None,
        "insert_name": insert_name,
        "serial_total": None,
        "is_autograph": "签" in seller_title,
        "is_patch": "球衣" in seller_title,
    }
    title = f"2025-26 NBA Hoops LeBron James {seller_title}"
    score, conflicts = assess_cardhobby_match(identity, _sale(title=title))
    assert score >= Decimal("0.5")
    assert conflicts == []


@pytest.mark.parametrize(
    "title",
    [
        "1991-92 Upper Deck Michael Jordan ward Winner Holograms 3D全息",
        "1991-92 Upper Deck Michael Jordan UD 全息 特卡",
    ],
)
def test_award_winner_hologram_accepts_cardhobby_title_variants(title: str) -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Michael Jordan",
        "brand_name": "Upper Deck",
        "product_name": "Upper Deck",
        "season_start": 1991,
        "season_end": 1992,
        "card_number": "AW4",
        "insert_name": "Award Winner Holograms",
        "serial_total": None,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(identity, _sale(title=title))
    assert score >= Decimal("0.5")
    assert conflicts == []


def test_green_mosaic_requires_matching_parallel_or_chinese_alias() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LeBron James",
        "season_start": 2020,
        "season_end": 2021,
        "product_name": "Mosaic",
        "card_number": "297",
        "insert_name": "Finals MVPs",
        "parallel_name": "Green Mosaic",
        "serial_total": None,
        "is_patch": False,
    }
    wrong_score, wrong_conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2020-21 Panini Mosaic LeBron James #297 Finals MVPs Red Mosaic"),
    )
    assert wrong_score == 0
    assert any("平行版本" in conflict for conflict in wrong_conflicts)

    exact_score, exact_conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2020-21 Panini Mosaic LeBron James #297 Finals MVPs 绿马赛克折"),
    )
    assert exact_score >= Decimal("0.5")
    assert exact_conflicts == []


def test_adjacent_season_is_rejected_even_when_year_tokens_overlap() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LeBron James",
        "brand_name": "Panini",
        "product_name": "Mosaic",
        "season_start": 2020,
        "season_end": 2021,
        "card_number": "297",
        "insert_name": "Finals MVPs",
        "parallel_name": "Green Mosaic",
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title="2021-22 Panini Mosaic LeBron James FMVP 捧杯 绿马赛克折"
        ),
    )
    assert score == 0
    assert any("赛季" in conflict for conflict in conflicts)


def test_graded_card_is_rejected_for_raw_target() -> None:
    score, conflicts = assess_cardhobby_match(
        _raw_identity(),
        _sale(
            title="2021-22 Panini Immaculate Tim Duncan /75 球衣物料 PSA 10",
            grade_company="PSA",
            grade_value=Decimal("10"),
        ),
    )
    assert score == 0
    assert any("裸卡" in conflict for conflict in conflicts)


@pytest.mark.parametrize("grade_label", ["PSA8分", "PSA10分", "BGN8.5分", "GBTC9.5分"])
def test_domestic_graded_card_is_rejected_for_raw_target(grade_label: str) -> None:
    score, conflicts = assess_cardhobby_match(
        _raw_identity(),
        _sale(title=f"2021-22 Panini Immaculate Tim Duncan /75 球衣物料 {grade_label}"),
    )
    assert score == 0
    assert any("裸卡" in conflict for conflict in conflicts)


def test_graded_target_requires_exact_company_and_grade() -> None:
    identity = {**_raw_identity(), "grade_company": "PSA", "grade_value": Decimal("10")}
    score, _ = assess_cardhobby_match(
        identity,
        _sale(
            title="2021-22 Panini Immaculate Tim Duncan /75 球衣物料 PSA 9",
            grade_company="PSA",
            grade_value=Decimal("9"),
        ),
    )
    assert score == 0


def test_ended_auction_requires_item_page_and_positive_bid_count() -> None:
    ended = _sale(
        item_id="123456789",
        source_url="https://www.cardhobby.com.cn/market/item/123456789",
        evidence_level="ended_auction",
        bid_count=12,
    )
    score, conflicts = assess_cardhobby_match(_raw_identity(), ended)
    assert Decimal("0.5") <= score <= Decimal("1")
    assert ended.source_reliability == Decimal("0.70")
    assert conflicts == []

    no_bid_score, no_bid_conflicts = assess_cardhobby_match(
        _raw_identity(), ended.model_copy(update={"bid_count": 0})
    )
    assert no_bid_score == 0
    assert any("结束拍卖" in conflict for conflict in no_bid_conflicts)


def test_item_page_cannot_claim_confirmed_transaction() -> None:
    score, conflicts = assess_cardhobby_match(
        _raw_identity(),
        _sale(
            item_id="123456789",
            source_url="https://www.cardhobby.com.cn/market/item/123456789",
            evidence_level="confirmed_transaction",
        ),
    )
    assert score == 0
    assert any("不能证明交易完成" in conflict for conflict in conflicts)


def test_search_params_use_cardhobby_sold_single_card_filters() -> None:
    params = CardHobbyProvider.search_params("Tim Duncan", 2)
    assert params["pageIndex"] == 2
    assert params["sortType"] == "DESC"
    assert '"Key":"Status","Value":-2' in str(params["searchJson"])
    assert CARDHOBBY_SINGLE_CARD_CATEGORY_ID in str(params["searchJson"])


def test_rpa_search_prioritizes_exact_print_run_and_uppercase_card_type() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Collin Murray-Boyles",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Three",
            "serial_total": 49,
            "is_autograph": True,
            "is_patch": True,
        }
    )
    assert queries[0] == "Collin Murray-Boyles 2025-26 49编 Topps 3"
    assert queries[1] == "Collin Murray-Boyles Topps 3 RPA 49编"


def test_cardhobby_queries_use_seller_aliases_and_progressively_remove_terms() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "JASON KIDD",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "National Treasures",
            "parallel_name": "Silver Parallel",
            "serial_total": 35,
            "is_autograph": True,
            "is_patch": False,
        }
    )
    assert queries[0] == "JASON KIDD 2023-24 35编 国宝"
    assert "JASON KIDD 国宝 AUTO 35编" in queries
    assert "JASON KIDD 国宝 银折 35编" in queries
    assert "JASON KIDD 国宝" in queries
    assert len(queries[-1].split()) < len(queries[0].split())


def test_one_and_one_queries_keep_short_serial_product_search() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Walker Kessler",
            "season_start": 2022,
            "season_end": 2023,
            "product_name": "One And One",
            "serial_total": 49,
            "is_rookie": True,
        }
    )
    assert queries[0] == "Walker Kessler 2022-23 49编 One and One"
    assert "Walker Kessler One and One RC 49编" in queries
    assert "Walker Kessler One and One" in queries


def test_catalog_product_suffixes_expand_to_cardhobby_seller_vocabulary() -> None:
    chrome_queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Luka Doncic",
            "product_name": "Topps Chrome Basketball",
            "season_start": 2025,
            "season_end": 2026,
            "card_number": "2",
            "parallel_name": "Red, White, and Blue Refractor",
        }
    )
    signature_queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Kevin Garnett",
            "product_name": "Panini Signature Series Basketball",
            "season_start": 2025,
            "season_end": 2026,
            "card_number": "94",
            "parallel_name": "Plaid",
            "serial_total": 15,
        }
    )

    assert any("Luka Doncic Topps Chrome 2" in query for query in chrome_queries)
    assert any("Kevin Garnett Signature Series" in query for query in signature_queries)
    assert "Kevin Garnett 2025-26 Signature Series 15" in signature_queries
    assert all("None" not in query for query in chrome_queries)


def test_extraterrestrial_talent_uses_bounded_seller_shortcuts() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Victor Wembanyama",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Cosmic Chrome Basketball",
            "card_number": "ET-7",
            "insert_name": "Extraterrestrial Talent",
            "parallel_name": "Refractor",
        }
    )

    assert "Victor Wembanyama Talent 2025" in queries


def test_cosmic_chrome_product_accepts_seller_word_order() -> None:
    identity = {
        "player_name": "Victor Wembanyama",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Cosmic Chrome Basketball",
        "card_number": "ET-7",
        "insert_name": "Extraterrestrial Talent",
        "parallel_name": "Refractor",
    }
    sale = CardHobbySale(
        item_id="127925097",
        title=(
            "2025-26 Topps Chrome Cosmic Victor Wembanyama "
            "银折 Extraterrestrial Talent"
        ),
        source_url="https://cardhobby.com.cn/Market/Item/127925097",
        occurred_at="2026-08-27T01:00:00+08:00",
        sale_price=Decimal("218"),
        bid_count=15,
        sale_type="auction",
        evidence_level="ended_auction",
        sold_status_evidence="Status=-2; BiddingCount=15",
        match_confidence=Decimal("0.5"),
    )

    score, conflicts, tier = assess_cardhobby_candidate(identity, sale)

    assert score >= Decimal("0.5")
    assert "标题未命中目标产品系列" not in conflicts
    assert tier is not None


def test_dual_player_queries_reserve_each_player_name_without_none_tokens() -> None:
    queries = CardHobbyProvider.build_queries(
        {
            "player_name": "Matas Buzelis, Tristan da Silva",
            "product_name": "Panini Immaculate Collection Basketball",
            "season_start": 2024,
            "season_end": 2025,
            "card_number": "12",
            "insert_name": "Remarkable Rookie Jerseys Dual",
            "parallel_name": "Blue",
            "serial_total": 25,
            "is_rookie": True,
            "is_patch": True,
        }
    )

    assert any(query.startswith("Matas Buzelis Immaculate") for query in queries)
    assert any(query.startswith("Tristan da Silva Immaculate") for query in queries)
    assert all("None" not in query for query in queries)


def test_topps_chrome_catalog_suffix_does_not_block_exact_seller_title() -> None:
    identity = {
        "player_name": "Luka Doncic",
        "product_name": "Topps Chrome Basketball",
        "season_start": 2025,
        "season_end": 2026,
        "card_number": "2",
        "parallel_name": "Red, White, and Blue Refractor",
        "is_autograph": False,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title="2026 Topps Chrome Luka Doncic #2 红白蓝三色平行折射卡"),
    )

    assert score >= Decimal("0.5")
    assert conflicts == []


def test_numbered_card_can_use_matching_print_run_when_seller_omits_card_number() -> None:
    identity = {
        "player_name": "Kevin Garnett",
        "product_name": "Panini Signature Series Basketball",
        "season_start": 2025,
        "season_end": 2026,
        "card_number": "94",
        "parallel_name": "Plaid",
        "serial_total": 15,
        "is_autograph": False,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Panini Signature Series Kevin Garnett 凯文 加内特 "
                "Base 07/15编"
            )
        ),
    )

    assert score >= Decimal("0.5")
    assert conflicts == []


def test_numbered_card_still_rejects_explicit_different_card_number() -> None:
    identity = {
        "player_name": "Kevin Garnett",
        "product_name": "Panini Signature Series Basketball",
        "season_start": 2025,
        "season_end": 2026,
        "card_number": "94",
        "parallel_name": "Plaid",
        "serial_total": 15,
        "is_autograph": False,
        "is_patch": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Panini Signature Series Kevin Garnett #95 Base 07/15编"
            )
        ),
    )

    assert score == 0
    assert "标题明确卡号与目标卡号不一致" in conflicts


def test_mosaic_basketball_catalog_name_expands_to_seller_aliases() -> None:
    identity = {
        "player_name": "Jaren Jackson Jr.",
        "season_start": 2020,
        "season_end": 2021,
        "brand_name": "Panini",
        "product_name": "Mosaic Basketball",
        "card_number": "29",
        "insert_name": "Prizm",
        "parallel_name": "Mosaic Prizm",
        "serial_total": 88,
        "is_autograph": False,
        "is_patch": False,
    }
    queries = CardHobbyProvider.build_queries(identity)
    assert any("Mosaic" in query and "88编" in query for query in queries)
    assert any("马赛克" in query for query in queries)

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2020-21 Panini Prizm Jaren Jackson Jr. "
                "Prizm系列 蓝波纹折"
            )
        ),
    )
    assert score == 0
    assert "标题未命中目标产品系列" in conflicts


def test_nba_hoops_basketball_matches_common_cardhobby_seller_title() -> None:
    identity = {
        "player_name": "LeBron James",
        "season_start": 2025,
        "season_end": 2026,
        "brand_name": "Topps",
        "product_name": "NBA Hoops Basketball",
        "card_number": "BH-2",
        "insert_name": "Bounce House",
        "is_autograph": False,
        "is_patch": False,
    }
    queries = CardHobbyProvider.build_queries(identity)
    assert "LeBron James 2025-26 Topps NBA Hoops Bounce House" in queries
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Topps NBA Hoops LeBron James #BH-2 "
                "Bounce House 弹跳屋 特卡"
            )
        ),
    )
    assert score >= Decimal("0.5")
    assert conflicts == []


def test_parse_market_item_uses_final_price_and_shipping() -> None:
    sale = CardHobbyProvider.parse_market_item(
        {
            "ID": 124126863,
            "Title": "2023-24 Panini Immaculate Tim Duncan /99 球衣物料",
            "Status": -2,
            "OutCount": 3,
            "PriceCount": 3,
            "Price": "1.00",
            "LowestPrice": 403.85,
            "NewPostageMoney": "18",
            "EffectiveDate": "2026-07-21 22:51:43",
            "ByWay": 2,
        }
    )
    assert sale is not None
    assert sale.sale_price == Decimal("403.85")
    assert sale.shipping_price == Decimal("18")
    assert sale.bid_count == 3
    assert sale.evidence_level == "ended_auction"
    assert sale.source_url == "https://cardhobby.com.cn/Market/Item/124126863"


def test_parse_market_item_rejects_active_or_zero_bid_rows() -> None:
    active = {
        "ID": 1,
        "Title": "Tim Duncan card",
        "Status": 1,
        "OutCount": 2,
        "LowestPrice": 10,
        "EffectiveDate": "2026-07-21 22:51:43",
    }
    assert CardHobbyProvider.parse_market_item(active) is None
    assert CardHobbyProvider.parse_market_item({**active, "Status": -2, "OutCount": 0}) is None


def test_cardhobby_item_images_keep_front_back_order_and_remove_duplicates() -> None:
    html = """
    <img src="https://images.cardhobby.com/front.jpg">
    <img src="https://images.cardhobby.com/back.jpg">
    <img src="https://images.cardhobby.com/front.jpg">
    """
    assert extract_cardhobby_images(html) == [
        "https://images.cardhobby.com/front.jpg",
        "https://images.cardhobby.com/back.jpg",
    ]


def test_reference_image_requires_exact_player_product_and_card_number() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kyrie Irving",
        "season_start": 2012,
        "season_end": 2013,
        "product_name": "Elite",
        "card_number": "B52",
        "serial_total": None,
        "is_autograph": True,
        "is_patch": False,
    }
    exact = _sale(
        title="2012-13 Panini Elite Kyrie Irving RC 新秀签字 B52",
        image_url="https://images.cardhobby.com/front.jpg",
    )
    assert is_exact_cardhobby_reference(identity, exact)
    assert not is_exact_cardhobby_reference(
        identity,
        exact.model_copy(update={"title": "2012-13 Panini Elite Giannis B52"}),
    )
    assert not is_exact_cardhobby_reference(
        identity,
        exact.model_copy(update={"title": "2012-13 Panini Threads Kyrie Irving B52"}),
    )
    assert not is_exact_cardhobby_reference(
        identity,
        exact.model_copy(update={"title": "2012-13 Panini Elite Kyrie Irving B51"}),
    )


def test_autograph_multi_player_wrong_season_and_wrong_print_run_are_rejected() -> None:
    titles = [
        "2021-22 Panini Immaculate Tim Duncan /75 球衣物料 签字 卡签",
        "2021-22 Panini Immaculate Tim Duncan Parker 双人 /75 球衣物料",
        "2023-24 Panini Immaculate Tim Duncan /75 球衣物料",
        "2021-22 Panini Immaculate Tim Duncan /15 球衣物料",
    ]
    for title in titles:
        score, conflicts = assess_cardhobby_match(
            _raw_identity(), _sale(title=title)
        )
        assert score == 0
        assert conflicts


def test_reviewed_parallel_alias_is_used_by_query_and_identity_gate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Ace Bailey",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Chrome Basketball",
        "card_number": "255",
        "insert_name": None,
        "parallel_name": "Basketball Refractor",
        "serial_total": None,
        "is_autograph": False,
        "is_patch": False,
    }
    title = "2025 Topps Chrome Ace Bailey RC #255 艾斯贝利 球皮折"

    score, conflicts = assess_cardhobby_match(identity, _sale(title=title))

    assert score > 0
    assert conflicts == []
    assert any("球皮折" in query for query in CardHobbyProvider.build_queries(identity))


def test_reviewed_insert_alias_and_short_print_run_query_precede_budget_cutoff() -> None:
    fractal = {
        **_raw_identity(),
        "player_name": "Victor Wembanyama",
        "product_name": "Panini Prizm Basketball",
        "insert_name": "Fractals",
        "parallel_name": None,
        "card_number": "10",
        "serial_total": None,
        "is_autograph": False,
        "is_patch": False,
    }
    numbered = {
        **fractal,
        "player_name": "Justin Champagnie",
        "insert_name": "Hoops Retro Autographs",
        "serial_total": 199,
        "is_autograph": True,
    }

    assert any("Fractal" in query for query in CardHobbyProvider.build_queries(fractal))
    numbered_queries = CardHobbyProvider.build_queries(numbered)
    assert any(query == "Justin Champagnie 199编" for query in numbered_queries)


def test_numbered_autograph_with_sparse_insert_title_reaches_image_review() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Justin Champagnie",
        "product_name": "Chronicles Draft Picks",
        "insert_name": "Hoops Retro Autographs",
        "card_number": "HR-JCH",
        "serial_total": 199,
        "is_autograph": True,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2021 Panini Chronicles Justin Champagnie 大学 Hoops "
                "039/199编 签字"
            )
        ),
    )

    assert score >= Decimal("0.35")
    assert conflicts == ["标题未命中目标卡种，需图像确认"]


def test_multi_player_query_uses_individual_short_aliases_and_strict_gate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Bronny James Jr., LeBron James, Anthony Davis",
        "season_start": 2024,
        "season_end": 2025,
        "product_name": "Origins Basketball",
        "insert_name": "Nucleus",
        "card_number": "11",
        "serial_total": 115,
        "parallel_name": None,
        "is_patch": False,
    }

    plan = CardHobbyProvider.build_query_plan(identity)
    queries = [query for _stage, query in plan]
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2024-25 Panini Origins 勒布朗詹姆斯 布朗尼詹姆斯 "
                "安东尼戴维斯 Nucleus #11 83/115编"
            )
        ),
    )

    assert any("詹姆斯" in query and "Nucleus" in query for query in queries)
    assert all("Bronny James Jr., LeBron James, Anthony Davis" not in query for query in queries)
    assert score >= Decimal("0.5")
    assert conflicts == []


def test_starquest_query_rotates_to_cardhobby_one_star_alias() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Stephon Marbury",
        "season_start": 1998,
        "season_end": 1999,
        "product_name": "UD Choice",
        "insert_name": "Starquest",
        "card_number": "SQ16",
    }

    plan = CardHobbyProvider.build_query_plan(identity)

    assert any("马布里 一星" in query for _stage, query in plan)


def test_feedback_plan_uses_short_seller_terms_before_full_catalog_phrase() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Derik Queen",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Chrome Updates Basketball",
        "insert_name": "Topps Chrome Autographs",
        "card_number": "TCA-DQ",
        "parallel_name": "Silver Refractor",
        "serial_total": None,
        "is_autograph": True,
        "is_patch": False,
    }

    plan = CardHobbyProvider.build_query_plan(identity)
    queries = [query for _stage, query in plan]

    assert "奎因 新秀签" in queries
    assert "奎因 Topps Chrome Update 新秀签" in queries
    assert len(queries) <= 12


def test_plain_base_plan_adds_explicit_base_feedback_query() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kobe Bryant",
        "season_start": 2015,
        "season_end": 2016,
        "product_name": "Revolution Basketball",
        "card_number": "7",
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    plan = CardHobbyProvider.build_query_plan(identity)

    assert ("explicit_base", "科比 Revolution base") in plan


def test_exact_typed_card_with_omitted_insert_is_admitted_for_image_gate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Derik Queen",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Chrome Updates Basketball",
        "insert_name": "Topps Chrome Autographs",
        "card_number": "TCA-DQ",
        "parallel_name": "Silver Refractor",
        "serial_total": None,
        "is_autograph": True,
        "is_patch": False,
    }
    sale = _sale(
        title=(
            "2025-26 Topps Chrome Update Derik Queen RC 德里克 奎因 "
            "TCU 新秀签 签字"
        )
    )

    score, conflicts = assess_cardhobby_match(identity, sale)

    assert score >= Decimal("0.5")
    assert "标题未写银折基础签版本，需图像确认" in conflicts


def test_explicit_base_without_card_number_is_admitted_for_image_gate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kobe Bryant",
        "season_start": 2015,
        "season_end": 2016,
        "product_name": "Revolution Basketball",
        "card_number": "7",
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    sale = _sale(
        title=(
            "2015-16 Panini Revolution 革命 元年 湖人 base "
            "科比 布莱恩特 Kobe Bryant"
        )
    )

    score, conflicts = assess_cardhobby_match(identity, sale)

    assert score >= Decimal("0.5")
    assert "标题未命中目标卡号，基础/原生闪卡需图像确认" in conflicts


def test_sp_authentic_product_name_is_not_mistaken_for_short_print_variant() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kobe Bryant",
        "season_start": 2008,
        "season_end": 2009,
        "product_name": "SP Authentic",
        "card_number": "89",
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    sale = _sale(
        title="2008-09 Upper Deck SP Authentic Kobe Bryant 科比 base"
    )

    score, conflicts = assess_cardhobby_match(identity, sale)

    assert score >= Decimal("0.5")
    assert "标题未命中目标卡号，基础/原生闪卡需图像确认" in conflicts


def test_intrinsic_encore_foil_without_card_number_is_admitted_for_image_gate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Hakeem Olajuwon",
        "season_start": 1998,
        "season_end": 1999,
        "product_name": "Encore",
        "card_number": "30",
        "insert_name": None,
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }
    sale = _sale(
        title=(
            "1999 Upper Deck Encore Hakeem Olajuwon 火箭 "
            "哈基姆 奥拉朱旺 大梦 折射 经典老卡"
        )
    )

    score, conflicts = assess_cardhobby_match(identity, sale)

    assert score >= Decimal("0.5")
    assert "标题未命中目标卡号，基础/原生闪卡需图像确认" in conflicts


def test_ud_game_floor_seller_alias_is_exact_insert_evidence() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Jason Terry",
        "season_start": 2001,
        "season_end": 2002,
        "product_name": "Upper Deck Hardcourt",
        "card_number": "JT",
        "insert_name": "UD Game Floor",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
        "is_memorabilia": True,
    }
    sale = _sale(
        title=(
            "2001-02 Upper Deck Hardcourt Jason Terry 老鹰 贾森 特里 "
            "地板 物料"
        )
    )

    score, conflicts = assess_cardhobby_match(identity, sale)

    assert score >= Decimal("0.5")
    assert conflicts == []


def test_signature_class_star_cast_uses_short_adaptive_queries() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Stephen Curry",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Signature Class Basketball",
        "card_number": "SC-1",
        "insert_name": "Star Cast",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
        "is_memorabilia": False,
    }

    plan = CardHobbyProvider.build_query_plan(identity)
    queries = [query for _stage, query in plan]

    assert any("库里" in query and "Star Cast" in query for query in queries)
    assert any("库里" in query and "胶片" in query for query in queries)
    assert any("Signature Class" in query for query in queries)


def test_signature_class_star_cast_seller_title_is_exact_identity() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Stephen Curry",
        "season_start": 2025,
        "season_end": 2026,
        "product_name": "Topps Signature Class Basketball",
        "card_number": "SC-1",
        "insert_name": "Star Cast",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
        "is_memorabilia": False,
    }
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Topps Signature Class Stephen Curry 库里 "
                "Star Cast 胶片 特卡 大比例 SSP"
            )
        ),
    )

    assert score >= Decimal("0.5")
    assert conflicts == []


def test_unicode_player_name_and_hoopnotic_use_short_seller_queries() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LUKA DONČIĆ",
        "season_start": 2025,
        "season_end": 2026,
        "brand_name": "Topps",
        "product_name": "NBA Hoops",
        "card_number": "HN-10",
        "insert_name": "Hoopnotic",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    plan = CardHobbyProvider.build_query_plan(identity)
    queries = [query for _stage, query in plan]
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2025-26 Topps NBA Hoops Luka Doncic 卢卡 东契奇 "
                "Hoopnotic 迷幻特卡 HN-10"
            )
        ),
    )

    assert _normalized("Dončić") == "doncic"
    assert "东契奇 Hoopnotic" in queries
    assert score >= Decimal("0.65")
    assert conflicts == []


def test_product_discovery_strips_optional_catalog_wrappers_for_unknown_family() -> None:
    aliases = product_search_aliases("2024-25 Panini Example Family Basketball")

    assert aliases[0] == "example family"
    assert "2024-25 Panini Example Family Basketball" in aliases


def test_photogenic_discovery_uses_real_seller_vocabulary() -> None:
    assert product_search_aliases("Photogenic Basketball") == (
        "Photogenic",
        "上镜",
    )


def test_rookie_focus_short_title_is_exact_only_with_full_identity_anchors() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Amen Thompson",
        "season_start": 2023,
        "season_end": 2024,
        "product_name": "Photogenic Basketball",
        "card_number": "7",
        "insert_name": "Rookie Focus",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2023-24 Panini Photogenic Amen Thompson RC "
                "上镜 聚焦特卡 Focus 选秀夜定妆照"
            )
        ),
    )

    assert score >= Decimal("0.5")
    assert conflicts == []


def test_named_maze_finish_is_not_accepted_as_rookie_focus_base() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Amen Thompson",
        "season_start": 2023,
        "season_end": 2024,
        "product_name": "Photogenic Basketball",
        "card_number": "7",
        "insert_name": "Rookie Focus",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2023-24 Panini Photogenic Amen Thompson RC "
                "Rookie Focus 特卡 迷宫折"
            )
        ),
    )

    assert score == Decimal("0")
    assert "目标为基础/未识别平行版，但标题明确为其他平行" in conflicts


def test_coloured_cracked_ice_without_zhe_suffix_is_still_a_parallel() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LeBron James",
        "season_start": 2024,
        "season_end": 2025,
        "product_name": "Select Basketball",
        "card_number": "54",
        "insert_name": "Concourse",
        "parallel_name": None,
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=(
                "2024-25 Panini Select LeBron James Concourse "
                "蓝碎冰 Color Match"
            )
        ),
    )

    assert score == Decimal("0")
    assert "目标为基础/未识别平行版，但标题明确为其他平行" in conflicts


@pytest.mark.parametrize("compound", ["Blue Ice", "Blue Cracked Ice", "Blue Laser"])
def test_short_blue_parallel_does_not_absorb_compound_parallel(
    compound: str,
) -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LeBron James",
        "season_start": 2024,
        "season_end": 2025,
        "product_name": "Prizm Basketball",
        "card_number": "54",
        "insert_name": None,
        "parallel_name": "Blue",
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(title=f"2024-25 Panini Prizm LeBron James #54 {compound}"),
    )

    assert score == Decimal("0")
    assert "标题为比目标更具体的其他平行版本" in conflicts


@pytest.mark.parametrize("seller_parallel", ["蓝", "蓝折", "蓝镭射"])
def test_blue_prizm_accepts_audited_short_chinese_seller_names(
    seller_parallel: str,
) -> None:
    identity = {
        **_raw_identity(),
        "player_name": "LeBron James",
        "season_start": 2024,
        "season_end": 2025,
        "product_name": "Prizm Basketball",
        "card_number": "54",
        "insert_name": None,
        "parallel_name": "Blue Prizm",
        "serial_total": None,
        "is_patch": False,
    }

    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title=f"2024-25 Panini Prizm LeBron James #54 {seller_parallel}"
        ),
    )

    assert score >= Decimal("0.5")
    assert conflicts == []


def test_sparse_autograph_insert_is_retained_only_as_an_image_review_candidate() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kyle Anderson",
        "season_start": 2014,
        "season_end": 2015,
        "product_name": "Donruss Basketball",
        "card_number": "31",
        "insert_name": "Elite",
        "parallel_name": None,
        "serial_total": None,
        "is_autograph": True,
        "is_patch": False,
    }
    sale = _sale(
        title=(
            "2014-15 Panini Donruss Kyle Anderson RC 新秀 "
            "杜蕾斯 马刺 凯尔安德森 刺绣 卡签 签字"
        ),
        image_url="https://images.cardhobby.com/kyle-anderson.jpg",
    )

    score, conflicts, tier = assess_cardhobby_candidate(identity, sale)

    assert score == Decimal("0.45")
    assert conflicts == ["标题未命中目标卡种，需图像确认"]
    assert tier == "image_review_candidate"
    assert "cardhobby_image_review_required" in sale.matching_fields


def test_query_plan_uses_catalog_code_and_query_only_player_initials() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Nickeil Alexander-Walker",
        "season_start": 2021,
        "season_end": 2022,
        "product_name": "Spectra Basketball",
        "card_number": "CAS-NAW",
        "insert_name": "Catalysts",
        "parallel_name": None,
        "serial_total": None,
        "is_autograph": True,
        "is_patch": False,
    }

    plan = CardHobbyProvider.build_query_plan(identity)
    by_stage: dict[str, list[str]] = {}
    for stage, query in plan:
        by_stage.setdefault(stage, []).append(query)

    assert any(
        query.casefold() == "cas-naw spectra"
        for query in by_stage["catalog_code_product"]
    )
    assert any(
        query.casefold() == "catalysts spectra"
        for query in by_stage["insert_product_no_player"]
    )
    assert any(
        query.casefold().startswith("naw cas-naw spectra")
        for query in by_stage["player_discovery_alias"]
    )
    assert any('沃克' in query or '亚历山大' in query for _, query in plan)
    # Initials are recall vocabulary only; they do not satisfy the player gate.
    score, conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title="2021-22 Panini Spectra NAW Catalysts CAS-NAW 签字",
            image_url="https://images.cardhobby.com/naw.jpg",
        ),
    )
    assert score == Decimal("0")
    assert conflicts == ["标题未命中目标球员"]
    given_only_score, given_only_conflicts = assess_cardhobby_match(
        identity,
        _sale(
            title="2021-22 Panini Nickeil Spectra Catalysts CAS-NAW 签字",
            image_url="https://images.cardhobby.com/nickeil.jpg",
        ),
    )
    assert given_only_score == Decimal("0")
    assert given_only_conflicts == ["标题未命中目标球员"]


def test_query_budget_deduplicates_normalized_and_display_player_aliases() -> None:
    identity = {
        **_raw_identity(),
        # MarketIdentity cache normalization case-folds the canonical value but
        # retains a display-cased alias.  These are one search, not two.
        "player_name": "nickeil alexander-walker",
        "player_name_aliases": ["Nickeil Alexander-Walker"],
        "season_start": 2021,
        "season_end": 2022,
        "product_name": "spectra basketball",
        "card_number": "cas-naw",
        "insert_name": "catalysts signatures",
        "parallel_name": None,
        "serial_total": 99,
        "is_autograph": True,
        "is_patch": False,
    }

    plan = CardHobbyProvider.build_query_plan(identity)
    normalized_queries = [_normalized(query) for _stage, query in plan]

    assert len(normalized_queries) == len(set(normalized_queries))
    # Full bilingual knowledge now supplies the Chinese surname; the given-name
    # fallback is intentionally only used when no curated alias exists.
    assert any('沃克' in query or '亚历山大' in query for _, query in plan)
    assert ("refine_print_run", "nickeil alexander-walker spectra 99编") in plan
    assert len(plan) <= 12


def test_query_plan_reserves_insert_and_product_without_player() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kyle Anderson",
        "season_start": 2014,
        "season_end": 2015,
        "product_name": "Donruss Basketball",
        "card_number": "31",
        "insert_name": "Elite",
        "parallel_name": None,
        "serial_total": None,
        "is_autograph": True,
        "is_patch": False,
    }

    plan = CardHobbyProvider.build_query_plan(identity)

    assert (
        "insert_product_no_player",
        "Elite Donruss",
    ) in plan


def test_explicit_other_insert_cannot_enter_omitted_insert_image_review() -> None:
    identity = {
        **_raw_identity(),
        "player_name": "Kyle Anderson",
        "season_start": 2014,
        "season_end": 2015,
        "product_name": "Donruss Basketball",
        "card_number": "31",
        "insert_name": "Elite",
        "parallel_name": None,
        "serial_total": None,
        "is_autograph": True,
        "is_patch": False,
    }
    sale = _sale(
        title=(
            "2014-15 Panini Donruss Kyle Anderson Penmanship "
            "书法签 卡签 签字"
        ),
        image_url="https://images.cardhobby.com/kyle-anderson-penmanship.jpg",
    )

    score, conflicts, tier = assess_cardhobby_candidate(identity, sale)

    assert score == Decimal("0")
    assert tier is None
    assert "标题明确命中其他卡种，与目标卡种不一致" in conflicts
    assert "cardhobby_image_review_required" not in sale.matching_fields
