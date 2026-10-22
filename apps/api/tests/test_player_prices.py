from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from card_platform.player_prices.domain import calculate_snapshot, select_basket
from card_platform.player_prices.discovery import identify_sale
from card_platform.ingestion.cardhobby import CardHobbySale

TZ = ZoneInfo("Asia/Shanghai")
D = date(2026, 10, 10)


def sale(key="a", price="100", day=10, source_id=None):
    return dict(
        source_id=source_id or f"{key}-{day}-{price}",
        card_key=key,
        identity={"player_name": "Stephen Curry", "card_number": key,
                  "brand_name": "Topps", "product_name": "Topps Chrome",
                  "season_start": 2025, "parallel_name": "Refractor"},
        sale_price=Decimal(price),
        occurred_at=datetime(2026, 10, day, 12, tzinfo=TZ),
        source_url="https://cardhobby.com.cn/Market/Item/123",
        raw_title="test",
    )


def samples():
    rows = []
    for key, old, new in [("a", "100", "110"), ("b", "100", "100"), ("c", "100", "90")]:
        for i in range(3):
            rows += [sale(key, old, 3, f"{key}-old-{i}"), sale(key, new, 10, f"{key}-new-{i}")]
    return rows


def test_three_cards_equal_weight_with_same_basket_and_auditable_samples():
    rows = samples()
    basket = select_basket(rows, D)
    result = calculate_snapshot(rows, basket, D)
    assert result["status"] == "ready"
    assert result["change_pct"] == "0.0000"
    assert [c["change_pct"] for c in result["cards"]] == ["10.0000", "0.0000", "-10.0000"]
    assert result["cards"][0]["current_count"] == 3
    assert result["cards"][0]["previous_count"] == 3
    assert result["cards"][0]["sources"][0]["source_url"].startswith("https://cardhobby.com.cn/")


def test_missing_third_card_or_thin_window_has_no_ranking():
    rows = samples()[:-1]
    result = calculate_snapshot(rows, select_basket(rows, D), D)
    assert result["status"] == "insufficient"
    assert result["change_pct"] is None
    assert any(c["status"] == "insufficient" for c in result["cards"])
    assert calculate_snapshot(rows, select_basket(rows, D)[:2], D)["change_pct"] is None


def test_duplicates_count_once_and_conflicting_identity_is_excluded():
    rows = samples()
    rows += [rows[0], dict(rows[0], card_key="other")]
    basket = select_basket(rows, D)
    assert sum(c["selection_count"] for c in basket) == 17
    assert len(basket) == 3


def test_window_boundaries_use_shanghai_and_no_future_sales():
    rows = samples()
    future = dict(sale("a", "999"), occurred_at=datetime(2026, 10, 11, tzinfo=TZ))
    rows.append(future)
    result = calculate_snapshot(rows, select_basket(rows, D), D)
    assert result["cards"][0]["current_count"] == 3
    assert result["current_window"]["start"].startswith("2026-10-04T00:00:00+08:00")
    assert result["current_window"]["end"].startswith("2026-10-11T00:00:00+08:00")
    assert result["previous_window"]["start"].startswith("2026-10-03")


def test_ninety_day_selection_and_deterministic_ties():
    rows = samples() + [dict(sale("old"), occurred_at=datetime(2026, 7, 1, tzinfo=TZ))]
    assert [c["card_key"] for c in select_basket(rows, D)] == ["a", "b", "c"]


def market_sale(title):
    return CardHobbySale(
        item_id="123",
        title=title,
        source_url="https://cardhobby.com.cn/Market/Item/123",
        occurred_at=datetime(2026, 10, 10, tzinfo=TZ),
        sale_price=100,
        sale_type="auction",
        evidence_level="ended_auction",
        bid_count=2,
        sold_status_evidence="Status=-2",
        match_confidence="0.95",
    )


@pytest.mark.parametrize(
    "title",
    [
        "2024-25 Panini Prizm Stephen Curry #23 base",
        "2024-25 Panini Prizm 斯蒂芬库里 #23 银折",
        "2024-25 Panini Prizm Stephen Curry #23 base PSA 10",
    ],
)
def test_explicit_common_titles_are_identified(title):
    identity = identify_sale(market_sale(title), "Stephen Curry", [])
    assert identity and identity["card_number"] == "23"
    assert identity["season_start"] == 2024


@pytest.mark.parametrize("tail", ["未知版本", "base lot 3张", "base PSA", "base 银折", "base 特卡"])
def test_unknown_finish_lot_incomplete_grade_or_conflicting_title_is_rejected(tail):
    assert (
        identify_sale(
            market_sale("2024-25 Panini Prizm Stephen Curry #23 " + tail), "Stephen Curry", []
        )
        is None
    )


def test_different_grade_is_a_different_type():
    raw = identify_sale(
        market_sale("2024-25 Panini Prizm Stephen Curry #23 base"), "Stephen Curry", []
    )
    graded = market_sale("2024-25 Panini Prizm Stephen Curry #23 base PSA 10")
    graded.grade_company = "PSA"
    graded.grade_value = Decimal("10")
    identity = identify_sale(graded, "Stephen Curry", [])
    assert raw != identity
    assert identity["grade_value"] == "10"


def test_search_only_nickname_does_not_establish_player_identity():
    assert (
        identify_sale(market_sale("2024-25 Panini Prizm 库里 #23 base"), "Stephen Curry", [])
        is None
    )


def test_card_key_ignores_query_metadata_and_normalizes_default_fields():
    from card_platform.player_prices.discovery import card_key

    base = {
        "player_name": "Stephen Curry",
        "season_start": 2024,
        "product_name": "Prizm",
        "parallel_name": "Base",
        "card_number": "23",
    }
    equivalent = {
        **base,
        "is_rookie": False,
        "is_autograph": False,
        "public_source_urls": ["https://cardhobby.com.cn/Market/Item/123"],
        "grade_value": None,
        "insert_name": None,
        "grade_company": None,
    }
    assert card_key(base) == card_key(equivalent)


def test_discovery_budget_is_shared_between_aliases_and_fixed_cards():
    import asyncio
    from card_platform.player_prices.discovery import collect_player

    class Provider:
        timeout = 1
        calls = []

        async def _get_search_payload(self, client, query, page):
            self.calls.append((query, page))
            return {"data": {"PagedMarketItemList": [{}] * 60}}

    provider = Provider()
    basket = [
        {
            "identity": {
                "season_start": 2024,
                "product_name": p,
                "card_number": 23,
                "parallel_name": "Base",
            }
        }
        for p in ["Prizm", "Hoops", "Select"]
    ]
    rows, coverage = asyncio.run(collect_player("Stephen Curry", basket, D, provider=provider))
    assert rows == [] and coverage["pages"] == 12 and coverage["budget_exhausted"]
    queries = set(query for query, page in provider.calls)
    assert len(queries) == 6 and '库里 pz' in queries
    assert {f'Stephen Curry 2024 {product} Base 23'
            for product in ['Prizm', 'Hoops', 'Select']} <= queries
    assert coverage["errors"] == []


def test_discovery_failure_is_not_reported_as_zero_sales_success():
    import asyncio
    from card_platform.player_prices.discovery import collect_player

    class Provider:
        timeout = 1

        async def _get_search_payload(self, *args):
            raise ValueError("API source failed")

    rows, coverage = asyncio.run(collect_player("Stephen Curry", [], D, provider=Provider()))
    assert rows == [] and len(coverage["errors"]) == 2
    assert coverage["pages"] == 2


def test_discovery_uses_public_search_browser_headers(monkeypatch):
    """Exercise the real provider/client boundary: missing headers close the connection."""
    import asyncio
    import httpx
    from card_platform.ingestion.cardhobby import CardHobbyProvider
    from card_platform.player_prices.discovery import collect_player

    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        if (not request.headers.get("user-agent", "").startswith("Mozilla/5.0")
                or request.headers.get("referer") != "https://www.cardhobby.com.cn/"):
            raise httpx.ReadError("public endpoint closed the connection", request=request)
        return httpx.Response(200, json={"result": 1, "data": {"PagedMarketItemList": []}})

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    provider = CardHobbyProvider(None, "", min_interval=0)
    rows, coverage = asyncio.run(collect_player("Stephen Curry", [], D, provider=provider))
    assert rows == []
    assert coverage["errors"] == []
    assert len(requests) == 2


def test_schedule_and_manual_default_are_completed_days():
    from card_platform.player_prices.service import report_day
    from datetime import timezone

    assert report_day(datetime(2026, 10, 12, 22, 30, tzinfo=timezone.utc)) == date(2026, 10, 11)
    assert report_day(datetime(2026, 10, 12, 23, 0, tzinfo=timezone.utc)) == date(2026, 10, 12)


@pytest.mark.parametrize(
    "title",
    [
        "Panini Prizm Stephen Curry #23 base",
        "2024-25 Panini Prizm Stephen Curry base",
        "2024-25 Panini Prizm Stephen Curry #23",
        "2024-25 Panini Prizm Stephen Curry #23 base /99",
    ],
)
def test_known_identity_cannot_fill_missing_title_anchors_or_numbered_base(title):
    known = {
        "player_name": "Stephen Curry",
        "season_start": 2024,
        "season_end": 2025,
        "brand_name": "Panini",
        "product_name": "Prizm",
        "card_number": "23",
        "parallel_name": "Base",
    }
    assert identify_sale(market_sale(title), "Stephen Curry", [known]) is None


def test_conflicting_public_observations_are_detected_before_catalog_overwrite():
    from card_platform.player_prices.service import conflicting_sources
    from card_platform.public_source_catalog import observe_public_sale

    base = {
        "ID": 123,
        "Title": "2024-25 Panini Prizm Stephen Curry #23 base",
        "EffectiveDate": "2026-10-10 12:00:00",
        "Status": -2,
        "ByWay": 2,
        "OutCount": 2,
        "LowestPrice": 100,
        "TitImg": "https://images.cardhobby.com/test.jpg",
    }
    first = observe_public_sale(base)
    second = observe_public_sale({**base, "LowestPrice": 999})
    assert conflicting_sources([first, second]) == {"item:123"}
    assert conflicting_sources([first, first]) == set()


def test_effective_numbering_is_part_of_card_key():
    from card_platform.player_prices.discovery import card_key

    base = {
        "player_name": "Stephen Curry",
        "season_start": 2024,
        "product_name": "Prizm",
        "parallel_name": "Red",
        "card_number": "23",
    }
    assert card_key({**base, "serial_total": 99}) != card_key({**base, "serial_total": 199})
    assert card_key({**base, "serial_total": 99}) == card_key({**base, "print_run": 99})
    with pytest.raises(ValueError):
        card_key({**base, "serial_total": 99, "print_run": 199})


@pytest.mark.parametrize("known", [[], [{
    "player_name": "Stephen Curry", "season_start": 2025, "season_end": 2026,
    "brand_name": "Topps", "product_name": "Topps Chrome",
    "card_number": "XBW10", "parallel_name": "Silver",
}]])
def test_real_seller_hash_chain_cannot_establish_catalog_card_number_even_from_cache(known):
    title = ("【古都代拍】 2025-26 Topps Chrome Stephen Curry 斯蒂芬 库里 勇士 "
             "银折 正TC 品相如图 选图帅气 实卡好看 凑套收藏必备#132B#AIR#XBW10#")
    assert identify_sale(market_sale(title), "Stephen Curry", known) is None


@pytest.mark.parametrize("number", ["23", "PA5", "SS-12"])
def test_real_catalog_number_before_seller_hash_chain_is_preserved(number):
    title = f"2024-25 Panini Prizm Stephen Curry #{number} base #132B#AIR#XBW10#"
    result = identify_sale(market_sale(title), "Stephen Curry", [])
    assert result and result["card_number"] == number


def test_seller_auction_sequence_before_season_is_not_a_catalog_card_number():
    title = ("【甲子拍卖】#17 2019-20 Panini Prizm LeBron James PZ 湖人 勒布朗 "
             "詹姆斯 正base PSA10分 评级卡 完美卡品 专收必备 鹰SH9A")
    assert identify_sale(market_sale(title), "LeBron James", []) is None


def test_cached_product_word_cannot_act_as_both_insert_and_parallel_anchor():
    known = dict(player_name="Stephen Curry", season_start=2023, season_end=2024,
                 brand_name="Panini", product_name="Prizm", insert_name="prizm",
                 card_number="152", parallel_name="prizm")
    assert identify_sale(market_sale("2023-24 Panini Prizm Stephen Curry 银折"),
                         "Stephen Curry", [known]) is None


@pytest.mark.parametrize('finish',[
    '三色折射 红白蓝折射', 'Red White Blue Prizm', '双色折射 蓝折', '红蓝双色折 蓝折',
    'Red-White-Blue Prizm','Red/White/Blue Prizm','Red White & Blue Prizm',
    '红 白 蓝折射','红蓝折射','拼色蓝折','Black Gold Prizm','黑金折',
    'Red Blue Prizm','Red and Blue Prizm',
])
def test_compound_finish_cannot_be_admitted_as_one_component_colour(finish):
    title = '2024-25 Panini Prizm LeBron James #130 ' + finish
    known = dict(player_name='LeBron James',season_start=2024,season_end=2025,
                 brand_name='Panini',product_name='Prizm',card_number='130',parallel_name='Blue')
    assert identify_sale(market_sale(title),'LeBron James',[known]) is None


@pytest.mark.parametrize('finish',['Ice','Silver','Basketball','Blue Ice','Black Gold'])
def test_official_base_prizms_variant_section_cannot_be_collapsed_into_plain_base(finish):
    title = ('2024-25 Panini Prizm Basketball #16 Scottie Barnes 斯科蒂 巴恩斯 '
             'Toronto Raptors 多伦多猛龙 Base Prizms ' + finish + ' 凑套专收必备')
    known = dict(player_name='Scottie Barnes',season_start=2024,season_end=2025,
                 brand_name='Panini',product_name='Prizm',card_number='16',parallel_name='Base')
    assert identify_sale(market_sale(title),'Scottie Barnes',[known]) is None


@pytest.mark.parametrize('name',['Coby White','Coby  White','Coby-White','Coby/White','Coby\tWhite'])
@pytest.mark.parametrize('number_first',[True,False])
def test_colour_word_in_a_verified_player_name_is_not_a_compound_finish(name,number_first):
    title = ('2024-25 Panini Prizm #23 '+name+' Blue Prizm' if number_first else
             '2024-25 Panini Prizm '+name+' #23 Blue Prizm')
    assert identify_sale(market_sale(title),'Coby White',[]) is not None


@pytest.mark.parametrize('parallel',['Blue Parallel','Blue Prizm Parallel','蓝折','Red Parallel','Gold Vinyl'])
def test_compound_claim_cannot_be_absorbed_by_a_single_colour_cached_synonym(parallel):
    from test_player_price_catalog import catalog,fact
    c = catalog([fact(player='LeBron James',card_number='130')])
    known = dict(player_name='LeBron James',season_start=2024,season_end=2025,brand_name='Panini',
                 product_name='Prizm',card_number='130',parallel_name=parallel)
    assert identify_sale(market_sale('2024-25 Panini Prizm LeBron James #130 红白蓝折射'),
                         'LeBron James',[known],catalog=c) is None
