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
)


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
    provider = CardHobbyProvider(None, "unused")

    async def no_direct_sales(_identity):
        from card_platform.ingestion.cardhobby import CardHobbySearchResult

        return CardHobbySearchResult(notes=["direct empty"])

    async def model_candidates(_identity):
        from card_platform.ingestion.cardhobby import CardHobbySearchResult

        return CardHobbySearchResult(sales=[_sale()], notes=["model discovery"])

    monkeypatch.setattr(provider, "_search_direct", no_direct_sales)
    monkeypatch.setattr(provider, "_search_model", model_candidates)

    result = await provider.search(_raw_identity())

    assert result.sales == []
    assert any("仅作发现线索" in note for note in result.notes)


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
    assert "标题未命中目标卡号或卡种" in conflicts


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
                "2024-25 Panini Immaculate 布泽利斯 达席尔瓦 11/25编 "
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


def test_same_player_product_variant_is_retained_only_as_low_confidence_analog() -> None:
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
    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert conflicts


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


@pytest.mark.parametrize("grade_label", ["BGN8.5分", "GBTC9.5分"])
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
    assert all("None" not in query for query in chrome_queries)


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
