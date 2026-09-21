import pytest

from card_platform.domain.comparables import _contains_product
from card_platform.domain.listing_title import explicit_print_runs
from card_platform.domain.product_identity import (
    ProductMatchStatus,
    assess_product_match,
    product_matches_title,
)
from card_platform.ingestion.cardhobby import _matches_product


@pytest.mark.parametrize(
    ("target", "title"),
    [
        ("Donruss Basketball", "2018-19 Panini Donruss Kobe Bryant All Heart"),
        ("National Treasures", "2021-22 Panini 国宝 Jason Kidd Timeless Talents"),
        ("One & One", "2023-24 Panini 壹和壹 LeBron James"),
        ("MJ's Back", "2001 Upper Deck MJs Back Michael Jordan"),
        ("Flair Showcase", "1997 Fleer Showcase Patrick Ewing Row 1"),
        (
            "Donruss Optic Basketball",
            "2023-24 Panini Donruss Optic Stephen Curry Splash",
        ),
        (
            "Topps Finest Basketball",
            "1997-98 Topps Finest Michael Jordan #154",
        ),
        (
            "2023-24 Panini – Premium Stock Basketball",
            "2023-24 Panini NBA Hoops Premium Stock Amen Thompson Anti-Gravity",
        ),
        (
            "Photogenic Basketball",
            "2023-24 Panini 上镜 Amen Thompson Rookie Focus",
        ),
        (
            "Complete Basketball",
            "2016-17 Panini Complete Dirk Nowitzki HOME",
        ),
    ],
)
def test_provider_and_valuation_share_positive_product_contract(
    target: str, title: str
) -> None:
    assert _matches_product(title, target)
    assert _contains_product(title, target)


@pytest.mark.parametrize(
    ("target", "title"),
    [
        ("Donruss Basketball", "2018-19 Panini Donruss Optic Kobe Bryant All Heart"),
        ("Donruss Basketball", "2018-19 Panini Donruss Elite Kobe Bryant"),
        ("Topps Chrome", "2025-26 Topps Chrome Update LeBron James"),
        ("Topps Chrome", "2025-26 Topps Cosmic Chrome LeBron James"),
        ("Prizm Basketball", "2025 Panini Prizm Football Caleb Williams"),
        ("Donruss Basketball", "2021 Chronicles Donruss Draft Picks Cade Cunningham"),
        ("Contenders Basketball", "2019-20 Panini Contenders Optic Jaylen Brown"),
        ("Contenders Basketball", "2020 Panini Contenders Draft Picks Ben Simmons"),
        ("Topps Chrome", "2025-26 Topps Chrome UpdatesShaedon Sharpe"),
        ("Select Basketball", "2024 Panini Select Draft Picks LeBron James"),
        ("Hoops Basketball", "2023-24 NBA Hoops Winter LeBron James #290"),
        ("Hoops Basketball", "2023-24 NBA Hoops 冬季雪花 LeBron James #290"),
    ],
)
def test_provider_and_valuation_share_child_product_rejection_contract(
    target: str, title: str
) -> None:
    assert not _matches_product(title, target)
    assert not _contains_product(title, target)


@pytest.mark.parametrize(
    ("target", "title"),
    [
        ("Flair Showcase", "1996-97 Fleer Showcase折射 Patrick Ewing"),
        ("Prizm Basketball", "2023-24 Panini Prizm艾弗森 Allen Iverson"),
        (
            "Signature Series Basketball",
            "2025-26 Panini Signature Series篮球 Dirk Nowitzki",
        ),
        ("Daily Dribble", "2025 Topps Daily Dribble特卡 SGA"),
    ],
)
def test_latin_han_transition_is_a_product_alias_boundary(
    target: str, title: str
) -> None:
    assert _matches_product(title, target)
    assert _contains_product(title, target)


@pytest.mark.parametrize(
    ("target", "title"),
    [
        ("Flair Showcase", "1996-97 Fleer Showcased Patrick Ewing"),
        ("Prizm Basketball", "2023-24 Panini Prizmatic Allen Iverson"),
        ("Daily Dribble", "2025 Topps Daily Dribbler SGA"),
    ],
)
def test_product_alias_does_not_match_inside_an_english_token(
    target: str, title: str
) -> None:
    assert not _matches_product(title, target)
    assert not _contains_product(title, target)


def test_parent_product_accepts_only_the_child_named_by_target_context() -> None:
    elite_title = "2014-15 Panini Donruss Elite Kyle Anderson"
    optic_title = "2014-15 Panini Donruss Optic Kyle Anderson"

    assert product_matches_title(
        elite_title,
        "Donruss Basketball",
        target_set="Elite",
    )
    assert not product_matches_title(elite_title, "Donruss Basketball")
    assert not product_matches_title(
        optic_title,
        "Donruss Basketball",
        target_set="Elite",
    )


def test_parent_product_child_context_is_shared_by_provider_and_valuation() -> None:
    title = "2014-15 Panini Donruss Elite Kyle Anderson"
    context = {
        "set_name": "Elite",
        "insert_name": "Rookie Autographs Die-Cuts",
        "parallel_name": None,
    }

    assert _matches_product(title, "Donruss Basketball", context)
    assert _contains_product(
        title,
        "Donruss Basketball",
        target_set="Elite",
        target_insert="Rookie Autographs Die-Cuts",
    )


@pytest.mark.parametrize(
    ("target", "title"),
    [
        ("Prizm Basketball", "2023-24 Prizm Kobe Bryant #8"),
        ("Mosaic Basketball", "2023-24 Mosaic Kobe Bryant #8"),
        ("Mosaic Basketball", "2023-24 马赛克折 Kobe Bryant #8"),
        ("MJ's Back", "2001 Upper Deck 银标Michael Jordan #8"),
        ("National Treasures", "2023-24 国宝卡签 Kobe Bryant"),
        ("Court Kings Basketball", "2023-24 油画特卡 Kobe Bryant"),
        ("Photogenic Basketball", "2023-24 上镜新秀 Kobe Bryant"),
        ("Origins Basketball", "2023-24 起源签字 Kobe Bryant"),
        ("Donruss Basketball", "2023-24 杜蕾斯特卡 Kobe Bryant"),
        ("Crown Royale Basketball", "2023-24 皇冠切割 Kobe Bryant"),
        ("Immaculate Collection", "2023-24 爱咪咪签字 Kobe Bryant"),
    ],
)
def test_reviewed_product_aliases_survive_marketplace_shorthand_and_han_glue(
    target: str, title: str
) -> None:
    assert assess_product_match(title, target) == ProductMatchStatus.MATCH


def test_product_omission_is_unknown_but_explicit_other_product_is_conflict() -> None:
    assert (
        assess_product_match("2023-24 Kobe Bryant #8", "Mosaic Basketball")
        == ProductMatchStatus.UNKNOWN
    )
    assert (
        assess_product_match(
            "2023-24 Panini Prizm Kobe Bryant #8", "Mosaic Basketball"
        )
        == ProductMatchStatus.EXPLICIT_CONFLICT
    )


def test_low_information_target_product_is_unknown_not_a_hard_mismatch() -> None:
    assert (
        assess_product_match("2023-24 Panini Prizm Kobe Bryant #8", "Panini")
        == ProductMatchStatus.UNKNOWN
    )


@pytest.mark.parametrize(
    "title",
    [
        "油画风格",
        "油画般设计",
        "上镜效果",
        "实卡很上镜",
        "革命性设计",
        "起源故事",
        "皇冠造型",
        "银标特卡",
    ],
)
def test_short_chinese_product_alias_in_descriptive_prose_is_unknown(
    title: str,
) -> None:
    assert (
        assess_product_match(f"2023-24 Kobe Bryant {title}", "Prizm Basketball")
        == ProductMatchStatus.UNKNOWN
    )


@pytest.mark.parametrize(
    ("target", "title", "expected"),
    [
        ("Court Kings Basketball", "Kobe Bryant 油画般设计", ProductMatchStatus.UNKNOWN),
        ("Photogenic Basketball", "Kobe Bryant 实卡很上镜", ProductMatchStatus.UNKNOWN),
        ("Revolution Basketball", "Kobe Bryant 革命性设计", ProductMatchStatus.UNKNOWN),
        (
            "Origins Basketball",
            "Mosaic Kobe Bryant 起源折",
            ProductMatchStatus.EXPLICIT_CONFLICT,
        ),
        ("MJ's Back", "Kobe Bryant 银标特卡", ProductMatchStatus.UNKNOWN),
    ],
)
def test_weak_han_alias_does_not_match_its_target_inside_descriptive_prose(
    target: str, title: str, expected: ProductMatchStatus
) -> None:
    assert assess_product_match(title, target) == expected


def test_short_chinese_alias_can_match_target_but_cannot_prove_other_product() -> None:
    assert (
        assess_product_match(
            "2023-24 油画特卡 Kobe Bryant", "Court Kings Basketball"
        )
        == ProductMatchStatus.MATCH
    )
    assert (
        assess_product_match(
            "2023-24 油画特卡 Kobe Bryant", "Prizm Basketball"
        )
        == ProductMatchStatus.UNKNOWN
    )


def test_product_like_target_role_is_unknown_not_another_product() -> None:
    title = (
        "2021-22 Chris Duarte Flux Prizm #FRA-CD 12/68"
    )

    assert (
        assess_product_match(
            title,
            "Chronicles Flux Draft Picks",
            target_insert="Flux",
            target_parallel="Prizm",
        )
        == ProductMatchStatus.UNKNOWN
    )


def test_cross_language_product_like_target_role_stays_unknown() -> None:
    assert (
        assess_product_match(
            "2021编年史 Chris Duarte Flux 马赛克 #FRA-CD 12/68",
            "Chronicles Flux Draft Picks",
            target_insert="Flux",
            target_parallel="Mosaic",
        )
        == ProductMatchStatus.UNKNOWN
    )
    assert (
        assess_product_match(
            "2021-22 Panini Mosaic Basketball Chris Duarte Flux #FRA-CD 12/68",
            "Chronicles Flux Draft Picks",
            target_insert="Flux",
            target_parallel="Mosaic",
        )
        == ProductMatchStatus.EXPLICIT_CONFLICT
    )


def test_parent_product_shorthand_is_unknown_for_chronicles_child() -> None:
    assert (
        assess_product_match(
            "2021编年史 Chris Duarte Flux Prizm #FRA-CD 12/68",
            "Chronicles Flux Draft Picks",
            target_insert="Flux",
            target_parallel="Prizm",
        )
        == ProductMatchStatus.UNKNOWN
    )


@pytest.mark.parametrize(
    "title",
    [
        "2021-22 Panini Prizm Basketball Chris Duarte Flux #FRA-CD 12/68",
        "2021-22 Topps Finest Basketball Chris Duarte Flux Prizm #FRA-CD 12/68",
    ],
)
def test_fully_named_other_product_stays_conflicting_despite_role_token(
    title: str,
) -> None:
    assert (
        assess_product_match(
            title,
            "Chronicles Flux Draft Picks",
            target_insert="Flux",
            target_parallel="Prizm",
        )
        == ProductMatchStatus.EXPLICIT_CONFLICT
    )


def test_non_target_product_token_is_not_softened_by_an_unrelated_role() -> None:
    assert (
        assess_product_match(
            "2021-22 Chris Duarte Prizm #FRA-CD 12/68",
            "Chronicles Flux Draft Picks",
            target_insert="Flux",
            target_parallel="Gold",
        )
        == ProductMatchStatus.EXPLICIT_CONFLICT
    )


def test_chronicles_year_is_not_a_print_run() -> None:
    assert explicit_print_runs("2021编年史 Flux Prizm 12/68") == {68}
    assert explicit_print_runs("2021编") == {2021}
    assert explicit_print_runs("Gold 17/2021编") == {2021}
