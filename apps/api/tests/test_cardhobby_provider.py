from decimal import Decimal

from card_platform.ingestion.cardhobby import (
    CARDHOBBY_SINGLE_CARD_CATEGORY_ID,
    CardHobbyProvider,
    CardHobbySale,
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
    assert sale.source_url.endswith("/market/item/124126863")


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
