from decimal import Decimal

import pytest

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import (
    ComparableClassifier,
    ComparableInput,
    ComparableTier,
)
from card_platform.ingestion.cardhobby import (
    CardHobbySale,
    assess_cardhobby_candidate,
)


def _identity(**updates: object) -> dict[str, object]:
    identity: dict[str, object] = {
        "player_name": "Kobe Bryant",
        "brand_name": "Panini",
        "season_start": 2018,
        "season_end": 2019,
        "product_name": "Donruss Basketball",
        "set_name": None,
        "card_number": "19",
        "insert_name": "All Heart",
        "parallel_name": None,
        "serial_total": None,
        "is_autograph": False,
        "is_patch": False,
        "is_memorabilia": False,
        "grade_company": None,
        "grade_value": None,
    }
    identity.update(updates)
    return identity


def _sale(title: str, *, image_url: str | None = None) -> CardHobbySale:
    return CardHobbySale(
        title=title,
        source_url="https://www.cardhobby.com.cn/market/feedback/12345",
        occurred_at="2026-07-01T20:00:00+08:00",
        sale_price=Decimal("100"),
        shipping_price=Decimal("0"),
        bid_count=3,
        sale_type="auction",
        evidence_level="confirmed_transaction",
        sold_status_evidence="来自售出的宝贝列表，交易完成",
        matching_fields=[],
        match_confidence=Decimal("0.90"),
        image_url=image_url,
    )


@pytest.mark.parametrize(
    ("identity_updates", "title_suffix", "hard_conflict"),
    [
        pytest.param(
            {},
            "Mojo",
            "目标为基础/未识别平行版，但标题明确为其他平行",
            id="explicit-technology-parallel",
        ),
        pytest.param(
            {},
            "/25",
            "目标未识别为限编版本，但标题明确为限编平行",
            id="unexpected-print-run",
        ),
        pytest.param(
            {"serial_total": 149},
            "/25",
            "明确限编与目标卡不一致",
            id="different-print-run",
        ),
    ],
)
def test_missing_card_kind_never_hides_later_hard_version_conflict(
    identity_updates: dict[str, object],
    title_suffix: str,
    hard_conflict: str,
) -> None:
    identity = _identity(
        product_name="Court Kings Basketball",
        card_number="TT-1",
        insert_name="Timeless Talents",
        **identity_updates,
    )
    sale = _sale(
        f"2018-19 Panini Court Kings Kobe Bryant {title_suffix}"
    )

    score, conflicts, tier = assess_cardhobby_candidate(identity, sale)

    assert score == Decimal("0")
    assert tier is None
    assert hard_conflict in conflicts
    assert "标题未命中目标卡种，需图像确认" in conflicts


@pytest.mark.parametrize(
    ("target_product", "child_product"),
    [
        pytest.param(
            "Topps Chrome Basketball",
            "Topps Chrome Black",
            id="topps-chrome-black",
        ),
        pytest.param(
            "Prizm Basketball",
            "Panini Prizm Deca",
            id="prizm-deca",
        ),
    ],
)
def test_parent_product_does_not_accept_explicit_child_product(
    target_product: str,
    child_product: str,
) -> None:
    identity = _identity(
        product_name=target_product,
        insert_name=None,
        parallel_name=None,
        serial_total=None,
    )

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(f"2018-19 {child_product} Kobe Bryant #19"),
    )

    assert score == Decimal("0")
    assert tier is None
    assert "标题未命中目标产品系列" in conflicts


@pytest.mark.parametrize("grade_text", ["AICCG 9", "AICCG9.5"])
def test_raw_target_rejects_domestic_graded_sale(grade_text: str) -> None:
    score, conflicts, tier = assess_cardhobby_candidate(
        _identity(),
        _sale(f"2018-19 Donruss Kobe Bryant All Heart #19 {grade_text}"),
    )

    assert score == Decimal("0")
    assert tier is None
    assert "目标为裸卡，但卡淘成交疑似评级卡" in conflicts


@pytest.mark.parametrize(
    ("target_parallel", "seller_version", "expected_conflict"),
    [
        pytest.param(
            "Blue",
            "base",
            "标题明确为基础卡，与目标平行版本不一致",
            id="target-blue-seller-base",
        ),
        pytest.param(
            None,
            "蓝折",
            "目标为基础/未识别平行版，但标题明确为其他平行",
            id="target-base-seller-blue",
        ),
    ],
)
def test_base_and_explicit_colour_parallel_never_cross_match(
    target_parallel: str | None,
    seller_version: str,
    expected_conflict: str,
) -> None:
    identity = _identity(
        product_name="Prizm Basketball",
        card_number="54",
        insert_name=None,
        parallel_name=target_parallel,
    )

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(f"2018-19 Panini Prizm Kobe Bryant #54 {seller_version}"),
    )

    assert score == Decimal("0")
    assert tier is None
    assert expected_conflict in conflicts


@pytest.mark.parametrize(
    "other_insert",
    [
        pytest.param("Hall of Fame", id="hall-of-fame"),
        pytest.param("Calligraphy", id="calligraphy"),
        pytest.param("Serenity", id="serenity"),
    ],
)
def test_explicit_other_global_insert_is_not_softened_to_t2(
    other_insert: str,
) -> None:
    identity = _identity(
        product_name="Court Kings Basketball",
        card_number="TT-1",
        insert_name="Timeless Talents",
    )

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            "2018-19 Panini Court Kings Kobe Bryant "
            f"{other_insert} #TT-1"
        ),
    )

    assert score == Decimal("0")
    assert tier is None
    assert "标题明确命中其他卡种，与目标卡种不一致" in conflicts


@pytest.mark.parametrize(
    ("identity_updates", "seller_title"),
    [
        pytest.param(
            {
                "season_start": 2020,
                "season_end": 2021,
                "product_name": "Court Kings Basketball",
                "card_number": "TT-5",
                "insert_name": "Timeless Talents",
            },
            "2020-21 Court Kings Kobe Bryant",
            id="court-kings-without-maker-or-sport-suffix",
        ),
        pytest.param(
            {
                "player_name": "Shaquille O'Neal",
                "brand_name": "Topps",
                "season_start": 2024,
                "season_end": 2025,
                "product_name": "Topps Chrome Basketball",
                "card_number": "FAN-16",
                "insert_name": "Fanatical",
            },
            "2024-25 Topps Chrome Shaquille O'Neal",
            id="topps-chrome-without-sport-suffix",
        ),
        pytest.param(
            {
                "player_name": "LeBron James",
                "season_start": 2022,
                "season_end": 2023,
                "product_name": "Mosaic Basketball",
                "card_number": "JM-10",
                "insert_name": "Jam Masters",
            },
            "2022-23 Panini Mosaic LeBron James",
            id="mosaic-without-sport-suffix",
        ),
    ],
)
def test_terse_same_player_season_product_sale_remains_image_reviewable(
    identity_updates: dict[str, object],
    seller_title: str,
) -> None:
    identity = _identity(**identity_updates)

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(
            seller_title,
            image_url="https://images.cardhobby.com/reference.jpg",
        ),
    )

    assert score == Decimal("0.45")
    assert conflicts == ["标题未命中目标卡种，需图像确认"]
    assert tier == "image_review_candidate"


@pytest.mark.parametrize(
    ("identity_updates", "seller_title"),
    [
        pytest.param(
            {},
            "2018-19 Donruss Kobe Bryant All Heart #19",
            id="kobe-all-heart",
        ),
        pytest.param(
            {
                "player_name": "LeBron James",
                "season_start": 2022,
                "season_end": 2023,
                "product_name": "Mosaic Basketball",
                "card_number": "JM-10",
                "insert_name": "Jam Masters",
            },
            "2022-23 Panini Mosaic LeBron James Jam Masters #JM-10",
            id="mosaic-jam-masters",
        ),
        pytest.param(
            {
                "player_name": "Shaquille O'Neal",
                "brand_name": "Topps",
                "season_start": 2024,
                "season_end": 2025,
                "product_name": "Topps Chrome Basketball",
                "card_number": "FAN-16",
                "insert_name": "Fanatical",
            },
            "2024-25 Topps Chrome Shaquille O'Neal Fanatical #FAN-16",
            id="topps-chrome-fanatical",
        ),
    ],
)
def test_exact_sales_remain_usable_across_product_families(
    identity_updates: dict[str, object],
    seller_title: str,
) -> None:
    identity = _identity(**identity_updates)

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(seller_title),
    )

    assert score >= Decimal("0.5")
    assert conflicts == []
    assert tier == "exact_or_near_exact"


def test_omitted_product_is_a_low_confidence_comparable_not_no_evidence() -> None:
    identity = _identity()
    sale = _sale("2018-19 Kobe Bryant All Heart #19")

    score, conflicts, provider_tier = assess_cardhobby_candidate(identity, sale)
    downstream = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity),
        ComparableInput(title=sale.title, source_code="cardhobby"),
    )

    assert score == Decimal("0.40")
    assert "标题未提供可验证的目标产品信息" in conflicts
    assert provider_tier == "same_player_product_analog"
    assert downstream.tier == ComparableTier.T2
    assert downstream.field_matches["product"] is None


def test_product_role_collision_keeps_chronicles_flux_sale_as_t2() -> None:
    identity = _identity(
        player_name="Chris Duarte",
        season_start=2021,
        season_end=2022,
        product_name="Chronicles Flux Draft Picks",
        card_number="FRA-CD",
        insert_name="Flux",
        parallel_name="Prizm",
        serial_total=68,
    )
    sale = _sale(
        "2021编年史 Chris Duarte Flux Prizm #FRA-CD 12/68"
    )

    score, conflicts, provider_tier = assess_cardhobby_candidate(identity, sale)
    downstream = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity),
        ComparableInput(title=sale.title, source_code="cardhobby"),
    )

    assert score == Decimal("0.40")
    assert conflicts == ["标题未提供可验证的目标产品信息"]
    assert provider_tier == "same_player_product_analog"
    assert downstream.tier == ComparableTier.T2
    assert downstream.field_matches["product"] is None


def test_role_collision_does_not_soften_a_fully_named_other_product() -> None:
    identity = _identity(
        player_name="Chris Duarte",
        season_start=2021,
        season_end=2022,
        product_name="Chronicles Flux Draft Picks",
        card_number="FRA-CD",
        insert_name="Flux",
        parallel_name="Prizm",
        serial_total=68,
    )

    score, conflicts, provider_tier = assess_cardhobby_candidate(
        identity,
        _sale(
            "2021-22 Panini Prizm Basketball Chris Duarte Flux #FRA-CD 12/68"
        ),
    )

    assert score == Decimal("0")
    assert provider_tier is None
    assert "标题未命中目标产品系列" in conflicts


def test_explicit_other_product_remains_a_hard_conflict() -> None:
    score, conflicts, tier = assess_cardhobby_candidate(
        _identity(),
        _sale("2018-19 Panini Prizm Kobe Bryant All Heart #19"),
    )

    assert score == Decimal("0")
    assert tier is None
    assert "标题未命中目标产品系列" in conflicts


@pytest.mark.parametrize(
    ("identity_updates", "title"),
    [
        pytest.param(
            {
                "product_name": "Court Kings Basketball",
                "card_number": "35",
                "insert_name": None,
                "parallel_name": "Purple",
            },
            "2022-23 Panini Court Kings Kobe Bryant 油画Base 凑套必备",
            id="base-glued-to-han-vs-purple",
        ),
        pytest.param(
            {
                "product_name": "Bowman Chrome",
                "card_number": None,
                "insert_name": "Chrome Prospect Autograph Card",
                "parallel_name": "Chrome",
                "is_autograph": True,
            },
            "2018-19 Topps Bowman Chrome Kobe Bryant 签字 银Mojo折",
            id="intrinsic-chrome-vs-mojo",
        ),
        pytest.param(
            {
                "product_name": "Prizm Basketball",
                "card_number": "15",
                "insert_name": None,
                "parallel_name": "Green",
            },
            "2018-19 Panini Prizm Kobe Bryant 虎皮折",
            id="green-vs-tiger-skin",
        ),
        pytest.param(
            {
                "product_name": "Mosaic Basketball",
                "card_number": "30",
                "insert_name": None,
                "parallel_name": "Blue",
            },
            "2018-19 Panini Mosaic Kobe Bryant 绿马赛克折",
            id="blue-vs-green-mosaic",
        ),
        pytest.param(
            {
                "product_name": "Mosaic Basketball",
                "card_number": "30",
                "insert_name": None,
                "parallel_name": "Blue",
            },
            "2018-19 Panini Mosaic Kobe Bryant Thunder Road 雷霆之路特卡",
            id="numbered-target-vs-named-insert",
        ),
        pytest.param(
            {
                "product_name": "Topps Flagship",
                "card_number": "178",
                "insert_name": None,
                "parallel_name": "Gold",
            },
            "2018-19 Topps Flagship Kobe Bryant 冰雪嘉年华 折射",
            id="gold-vs-winter-technology",
        ),
    ],
)
def test_soft_missing_fields_do_not_hide_reviewed_version_markers(
    identity_updates: dict[str, object], title: str
) -> None:
    score, conflicts, tier = assess_cardhobby_candidate(
        _identity(**identity_updates), _sale(title)
    )

    assert score == Decimal("0")
    assert tier is None
    assert conflicts


def test_lot_suffix_and_promotional_player_tail_are_not_single_card_evidence() -> None:
    lot = assess_cardhobby_candidate(
        _identity(product_name="Court Kings Basketball", card_number="8"),
        _sale("2018-19 Panini Court Kings Kobe Bryant Base lot2"),
    )
    promo = assess_cardhobby_candidate(
        _identity(player_name="Stephon Marbury", product_name="Fleer Basketball"),
        _sale(
            "1997 Fleer Roy Rogers Rookie Rewind 罗伊罗杰斯 灰熊名宿 "
            "凑套必备 马布里艾弗森"
        ),
    )

    assert lot[0] == Decimal("0")
    assert "标题疑似多卡、套卡或未拆产品" in lot[1]
    assert promo[0] == Decimal("0")
    assert "目标球员仅出现在卖家推广尾词中，不是本商品主体" in promo[1]


def test_season_text_misparsed_as_serial_is_removed_before_comparable_matching() -> None:
    identity = _identity(
        season_start=1998,
        season_end=1999,
        product_name="SkyBox Metal Universe",
        card_number="METAL/2/*97-98",
        insert_name=None,
        parallel_name=None,
        serial_number=2,
        serial_total=98,
    )
    resolved = CardIdentityResolver().resolve(identity)
    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale("1998-99 SkyBox Metal Universe Kobe Bryant 经典老卡"),
    )

    assert resolved.card_number is None
    assert resolved.serial_number is None
    assert resolved.serial_total is None
    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert "目标身份缺少卡号、卡种、平行或限编锚点" in conflicts


def test_seller_inventory_path_is_not_treated_as_a_print_run() -> None:
    identity = _identity(
        player_name="Zach LaVine",
        season_start=2015,
        season_end=2016,
        product_name="Revolution Basketball",
        card_number="12",
        insert_name=None,
        parallel_name="Green Splash",
        is_autograph=True,
    )
    title = (
        "2015-16 Panini Revolution Zach LaVine 签字 卡签 折射 "
        "/瑕疵见详图/7075cea/46"
    )

    score, conflicts, provider_tier = assess_cardhobby_candidate(
        identity, _sale(title)
    )
    downstream = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity),
        ComparableInput(title=title, source_code="cardhobby"),
    )

    assert score > 0
    assert provider_tier is not None
    assert not any("限编" in conflict for conflict in conflicts)
    assert downstream.tier == ComparableTier.T2


def test_balanced_hash_inventory_code_is_not_a_different_card_number() -> None:
    identity = _identity(
        player_name="Jaylen Brown",
        season_start=2016,
        season_end=2017,
        card_number="7",
        insert_name=None,
        parallel_name=None,
    )

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale("2016-17 Panini Donruss Jaylen Brown RC #WGY2#"),
    )

    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert "标题明确卡号与目标卡号不一致" not in conflicts


def test_generic_ratio_word_can_describe_a_known_target_parallel() -> None:
    identity = _identity(
        player_name="Dirk Nowitzki",
        season_start=1998,
        season_end=1999,
        product_name="Brilliants",
        card_number="109B",
        insert_name=None,
        parallel_name="H",
    )

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale("1998-99 Fleer Brilliants Dirk Nowitzki RC 大比例 特卡"),
    )

    assert score > 0
    assert tier is not None
    assert "目标为基础卡/未识别卡种，但标题明确为命名特卡或变体" not in conflicts


@pytest.mark.parametrize(
    "seller_version",
    ["2025编", "2025编 gold 金平行", "金折 855/2025编"],
)
def test_numbered_sale_survives_when_named_target_parallel_lacks_total(
    seller_version: str,
) -> None:
    identity = _identity(
        player_name="Reed Sheppard",
        season_start=2025,
        season_end=None,
        product_name="Flagship",
        card_number="178",
        insert_name=None,
        parallel_name="Gold",
        serial_total=None,
    )

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(f"2025 Topps Flagship Reed Sheppard {seller_version}"),
    )

    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert "目标平行版缺少限编总数，卡淘限编成交按低置信类比" in conflicts


@pytest.mark.parametrize(
    ("seller_version", "parallel_name"),
    [
        ("gold 金平行 3/50编", "Gold"),
        ("Red Shimmer 红shimmer折 4/5编", "Gold"),
        ("2025编", None),
    ],
)
def test_missing_target_total_does_not_open_arbitrary_numbered_parallels(
    seller_version: str, parallel_name: str | None
) -> None:
    identity = _identity(
        player_name="Reed Sheppard",
        season_start=2025,
        season_end=None,
        product_name="Flagship",
        card_number="178",
        insert_name=None,
        parallel_name=parallel_name,
        serial_total=None,
    )

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale(f"2025 Topps Flagship Reed Sheppard {seller_version}"),
    )

    assert score == Decimal("0")
    assert tier is None
    assert conflicts


def test_release_year_numbered_parallel_rule_is_not_a_2025_literal() -> None:
    identity = _identity(
        player_name="Example Player",
        season_start=2026,
        season_end=None,
        product_name="Flagship",
        card_number="1",
        insert_name=None,
        parallel_name="Gold",
        serial_total=None,
    )

    score, conflicts, tier = assess_cardhobby_candidate(
        identity,
        _sale("2026 Topps Flagship Example Player Gold 17/2026编"),
    )

    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert "目标平行版缺少限编总数，卡淘限编成交按低置信类比" in conflicts


@pytest.mark.parametrize(
    ("identity_updates", "seller_title", "expected_conflict"),
    [
        (
            {
                "player_name": "Tidjane Salaun",
                "season_start": 2024,
                "season_end": 2025,
                "product_name": "Revolution Basketball",
                "card_number": "5",
                "insert_name": "Vortex",
                "parallel_name": "Astro",
            },
            "2024-25 Panini Revolution Tidjane Salaun RC Astro 74/299编",
            "目标未识别为限编版本，但标题明确为限编平行",
        ),
        (
            {
                "player_name": "Scottie Barnes",
                "season_start": 2024,
                "season_end": 2025,
                "product_name": "NBA Hoops",
                "card_number": "101",
                "insert_name": None,
                "parallel_name": "Green",
            },
            "2024-25 Panini NBA Hoops Scottie Barnes 75编 龙纹折",
            "标题明确为其他平行版本，与目标平行不一致",
        ),
        (
            {
                "player_name": "Jaylen Brown",
                "season_start": 2016,
                "season_end": 2017,
                "card_number": "7",
                "insert_name": None,
                "parallel_name": None,
            },
            "2016-17 Panini Donruss Jaylen Brown 大比例 大合照 全家福",
            "目标为基础卡/未识别卡种，但标题明确为命名特卡或变体",
        ),
        (
            {
                "player_name": "Evan Mobley",
                "season_start": 2024,
                "season_end": 2025,
                "product_name": "NBA Hoops",
                "card_number": "82",
                "insert_name": None,
                "parallel_name": "Blue",
            },
            "2024-25 NBA Hoops Evan Mobley /999 Road To The Finals 特卡",
            "标题明确命中其他卡种，与目标卡种不一致",
        ),
    ],
)
def test_missing_metadata_never_overrides_an_explicit_other_version(
    identity_updates: dict[str, object],
    seller_title: str,
    expected_conflict: str,
) -> None:
    score, conflicts, tier = assess_cardhobby_candidate(
        _identity(**identity_updates), _sale(seller_title)
    )

    assert score == Decimal("0")
    assert tier is None
    assert expected_conflict in conflicts


@pytest.mark.parametrize(
    ("identity_updates", "seller_title"),
    [
        (
            {
                "player_name": "Stephon Marbury",
                "season_start": 1997,
                "season_end": 1998,
                "product_name": "Rookie Rewind",
                "card_number": "S019",
                "insert_name": None,
                "parallel_name": None,
            },
            "1997-98 Fleer Stephon Marbury Rookie Rewind 特卡",
        ),
        (
            {
                "player_name": "Jerry Stackhouse",
                "season_start": 1998,
                "season_end": None,
                "product_name": "Metal Universe",
                "card_number": "47",
                "insert_name": "Metal/2",
                "parallel_name": None,
            },
            "1998 SkyBox Metal Universe Jerry Stackhouse 经典老卡 特卡 46",
        ),
    ],
)
def test_target_product_or_bare_variant_words_are_not_other_insert_claims(
    identity_updates: dict[str, object], seller_title: str
) -> None:
    identity = _identity(**identity_updates)
    score, conflicts, tier = assess_cardhobby_candidate(identity, _sale(seller_title))
    downstream = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity),
        ComparableInput(title=seller_title, source_code="cardhobby"),
    )

    assert score == Decimal("0.40")
    assert tier == "same_player_product_analog"
    assert downstream.tier == ComparableTier.T2
    assert not any("明确为命名特卡" in conflict for conflict in conflicts)


@pytest.mark.parametrize("other_variant", ["油画特卡", "影棚拍摄特卡"])
def test_proper_chinese_variant_name_still_rejects_a_different_target_insert(
    other_variant: str,
) -> None:
    identity = _identity(
        player_name="Michael Jordan",
        season_start=1990,
        season_end=None,
        product_name="NBA Hoops",
        card_number="358",
        insert_name="Chicago Bulls",
        parallel_name="Gold",
    )
    title = f"1990-91 Fleer NBA Hoops Michael Jordan {other_variant}"

    score, conflicts, tier = assess_cardhobby_candidate(identity, _sale(title))
    downstream = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity),
        ComparableInput(title=title, source_code="cardhobby"),
    )

    assert score == Decimal("0")
    assert tier is None
    assert "标题明确为其他命名卡种，与目标卡种不一致" in conflicts
    assert downstream.tier == ComparableTier.REJECTED
    assert "insert_conflict" in downstream.hard_conflicts


def test_only_audited_single_card_set_shorthand_bypasses_lot_gate() -> None:
    silver_label = assess_cardhobby_candidate(
        _identity(
            product_name="MJ's Back",
            season_start=2001,
            season_end=2002,
            card_number="64",
            insert_name=None,
        ),
        _sale("2001 Upper Deck 银标套卡 Kobe Bryant #64"),
    )
    unrelated_set = assess_cardhobby_candidate(
        _identity(
            product_name="National Treasures Basketball",
            card_number="64",
            insert_name=None,
        ),
        _sale("2018-19 Panini 国宝套卡 Kobe Bryant #64"),
    )
    explicit_quantity = assess_cardhobby_candidate(
        _identity(
            product_name="MJ's Back",
            season_start=2001,
            season_end=2002,
            card_number="64",
            insert_name=None,
        ),
        _sale("2001 Upper Deck 银标套卡 2张同出 Kobe Bryant #64"),
    )

    assert silver_label[0] > 0
    assert unrelated_set[0] == Decimal("0")
    assert "标题疑似多卡、套卡或未拆产品" in unrelated_set[1]
    assert explicit_quantity[0] == Decimal("0")
    assert "标题疑似多卡、套卡或未拆产品" in explicit_quantity[1]
