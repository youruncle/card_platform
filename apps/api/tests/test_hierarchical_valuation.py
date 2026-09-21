from decimal import Decimal

import pytest

from card_platform.domain.card_identity import (
    CardIdentity,
    CardIdentityResolver,
    canonical_insert,
    normalize_text,
)
from card_platform.domain.comparables import (
    Classification,
    ComparableClassifier,
    ComparableInput,
    ComparableTier,
)
from card_platform.domain.hierarchical_estimator import ClassifiedSale, HierarchicalEstimator
from card_platform.knowledge import KnowledgeResolver


def test_memorabilia_insert_is_not_automatically_a_patch() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Jaylen Brown",
            "product_name": "National Treasures",
            "insert_name": "Colossal Materials",
            "is_memorabilia": False,
            "is_patch": False,
        }
    )

    assert identity.is_memorabilia is True
    assert identity.is_patch is False


def matas_identity():
    return CardIdentityResolver().resolve(
        {
            "player_name": "Matas Buzelis",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Court Kings",
            "card_number": "149",
            "serial_number": 1,
            "serial_total": 149,
            "insert_name": "RC",
            "parallel_name": "Red",
        },
        [
            {
                "source": "physical_back",
                "fields": {
                    "card_number": "5",
                    "serial_number": 113,
                    "serial_total": 149,
                    "insert": "First Steps",
                },
            }
        ],
    )


def test_physical_back_wins_and_aliases_resolve() -> None:
    identity = matas_identity()
    assert identity.card_number == "5"
    assert identity.serial_number == 113
    assert identity.insert == "First Steps"
    assert canonical_insert("鞋灯") == "First Steps"
    assert canonical_insert("第一步") == "First Steps"


@pytest.mark.parametrize(
    "placeholder", ["None", "null", "UNKNOWN", "N/A", "未识别", "无法识别"]
)
def test_identity_placeholders_are_not_treated_as_card_attributes(
    placeholder: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Bobby Portis",
            "product_name": "Topps Chrome",
            "insert_name": placeholder,
            "parallel_name": placeholder,
            "card_number": placeholder,
            "grade_company": placeholder,
            "grade_value": placeholder,
        }
    )

    assert identity.insert is None
    assert identity.parallel is None
    assert identity.card_number is None
    assert identity.grade_company is None
    assert identity.grade_value is None


def test_spaced_chinese_player_alias_matches_same_entity() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Magic Johnson, Kareem Abdul-Jabbar",
            "season_start": 2022,
            "season_end": 2023,
            "product_name": "Contenders Optic",
            "insert_name": "Legendary Tandems",
            "card_number": "9",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2022-23 Panini Contenders Optic Kareem Abdul-Jabbar "
                "贾巴尔 魔术师 约翰逊 传奇双人特卡 银折"
            )
        ),
    )

    assert result.tier != ComparableTier.REJECTED
    assert result.field_matches["player"] is True
    assert result.field_matches["insert"] is True


def test_exact_card_number_softens_ambiguous_finish_when_insert_name_is_omitted() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Stephon Marbury",
            "season_start": 2003,
            "season_end": 2004,
            "product_name": "Fleer Showcase",
            "insert_name": "Honored",
            "card_number": "71",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2003-04 Fleer Showcase Stephon Marbury #71 "
                "折射 大头 特卡"
            )
        ),
    )

    assert result.tier == ComparableTier.T2
    assert result.hard_conflicts == []


def test_listing_context_repairs_serial_total_misread_as_card_number() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Matas Buzelis",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Court Kings",
            "card_number": "149",
            "serial_total": 149,
            "insert_name": "油画",
            "parallel_name": "红平行",
            "purchase_context": (
                "2024-25 Panini Court Kings Matas Buzelis "
                "第一步 鞋灯 红平行 149编"
            ),
        }
    )
    assert identity.card_number is None
    assert identity.serial_total == 149
    assert identity.insert == "First Steps"
    assert identity.parallel == "Red"


@pytest.mark.parametrize("card_number", ["CMP132931", "CODE#CMP144337", "132874"])
def test_manufacturer_tracking_code_is_not_a_checklist_card_number(
    card_number: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Paolo Banchero",
            "brand_name": "Topps",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "NBA Hoops",
            "insert_name": "Oasis",
            "card_number": card_number,
        }
    )

    assert identity.card_number is None
    assert identity.insert == "Oasis"


def test_mosaic_prizm_logo_is_not_an_insert() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Devin Vassell",
            "brand_name": "Panini",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Mosaic",
            "insert_name": "Prizm",
            "card_number": "154",
        }
    )

    assert identity.insert is None


def test_product_logo_is_not_retained_as_parallel_name() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "AJ Dybantsa",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Chrome",
            "card_number": "45",
            "parallel_name": "Chrome",
        }
    )

    assert identity.parallel is None


def test_2000_fleer_showcase_base_allows_intrinsic_holofoil_wording() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Shareef Abdur-Rahim",
            "season_start": 2000,
            "season_end": 2001,
            "product_name": "Fleer Showcase",
            "card_number": "74",
        }
    )

    same_template = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2000-01 Fleer Showcase Shareef Abdur-Rahim "
                "金版SP Holofoil折射"
            ),
            visual_verdict="compatible",
            visual_confidence=Decimal("0.95"),
        ),
    )
    numbered_legacy = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2000-01 Fleer Showcase Shareef Abdur-Rahim "
                "Legacy Row 2 蓝字 /99编"
            )
        ),
    )

    assert same_template.tier == ComparableTier.T1
    assert not same_template.hard_conflicts
    assert numbered_legacy.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in numbered_legacy.hard_conflicts


def test_rc_parallel_is_rookie_and_matching_serial_total_is_not_card_number() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Walker Kessler",
            "season_start": 2022,
            "season_end": 2023,
            "product_name": "One And One",
            "card_number": "49",
            "serial_total": 49,
            "parallel_name": "RC",
            "is_rookie": False,
            "purchase_context": (
                "Walker Kessler 2022-23 Panini One And One OAO RC新秀 "
                "/49编 RPA 签字 卡签 球衣 patch切割"
            ),
        }
    )
    assert identity.card_number is None
    assert identity.parallel is None
    assert identity.is_rookie
    assert identity.is_autograph
    assert identity.is_memorabilia
    assert identity.is_patch
    assert identity.insert == "Rookie Patch Autographs"
    same_without_rpa_token = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2022-23 Panini One And One Walker Kessler 爵士 "
                "新秀rc 沃克 凯斯勒 签字卡签 球衣物料 49编 原封砖"
            )
        ),
    )
    assert same_without_rpa_token.tier == ComparableTier.T0


def test_matas_exact_titles_are_t0_and_other_inserts_are_rejected() -> None:
    classifier = ComparableClassifier()
    identity = matas_identity()
    exact = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2024-25 Panini Court Kings Matas Buzelis RC 鞋灯 "
                "红平行 149编"
            ),
        ),
    )
    assert exact.tier == ComparableTier.T0
    lot = classifier.classify(
        identity,
        ComparableInput(title="Matas Buzelis Court Kings 多卡 lot First Steps /149"),
    )
    assert lot.tier == ComparableTier.REJECTED
    assert "lot_or_multi_card" in lot.hard_conflicts


@pytest.mark.parametrize(
    "quantity_phrase",
    ["4张通行品", "共2张", "3 张", "5张同出"],
)
def test_comparable_classifier_rejects_chinese_quantity_lot(
    quantity_phrase: str,
) -> None:
    target = CardIdentity(
        player_name="Stephen Curry",
        season_start=2025,
        season_end=2026,
        product_name="Topps Basketball",
        insert="Stars of the NBA",
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2025-26 Topps Basketball Stephen Curry Stars of the NBA "
                f"{quantity_phrase}"
            )
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "lot_or_multi_card" in result.hard_conflicts


@pytest.mark.parametrize("single_card_label", ["第1张", "第 2 张", "260504张总"])
def test_chinese_single_card_sequence_labels_are_not_lots(
    single_card_label: str,
) -> None:
    target = CardIdentity(
        player_name="LeBron James",
        season_start=2025,
        season_end=2026,
        product_name="NBA Hoops",
        insert="Bounce House",
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2025-26 Topps NBA Hoops LeBron James Bounce House "
                f"{single_card_label}"
            )
        ),
    )

    assert "lot_or_multi_card" not in result.hard_conflicts


def test_product_name_that_is_also_a_known_insert_is_not_an_insert_conflict() -> None:
    resolver = KnowledgeResolver(
        {
            "entities": [
                {
                    "id": "starquest",
                    "entity_type": "insert",
                    "canonical_name": "Starquest",
                    "aliases": ["Star Quest"],
                },
                {
                    "id": "ud-choice",
                    "entity_type": "insert",
                    "canonical_name": "UD Choice",
                    "aliases": [],
                },
                {
                    "id": "jam-masters",
                    "entity_type": "insert",
                    "canonical_name": "Jam Masters",
                    "aliases": ["大头"],
                },
            ]
        }
    )
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Allen Iverson",
            "season_start": 1998,
            "season_end": 1999,
            "brand_name": "Upper Deck",
            "product_name": "UD Choice",
            "card_number": "SQ20",
            "insert_name": "Starquest",
            "parallel_name": "Blue",
        }
    )

    result = ComparableClassifier(resolver).classify(
        identity,
        ComparableInput(
            title=(
                "1998-99 Upper Deck UD Choice Allen Iverson 76人 "
                "Starquest SQ20 Blue 大头预"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert "insert_conflict" not in result.hard_conflicts


def test_starquest_star_count_identifies_parallel_tier() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Allen Iverson",
            "season_start": 1998,
            "season_end": 1999,
            "product_name": "UD Choice",
            "card_number": "SQ20",
            "insert_name": "Starquest",
            "parallel_name": "Blue",
        }
    )
    classifier = ComparableClassifier()

    blue = classifier.classify(
        identity,
        ComparableInput(title="1998 UD Choice Allen Iverson Starquest SQ20 一星"),
    )
    red = classifier.classify(
        identity,
        ComparableInput(title="1998 UD Choice Allen Iverson Starquest SQ20 三星"),
    )

    assert blue.tier == ComparableTier.T0
    assert red.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in red.hard_conflicts


def test_two_t0_control_center_and_shipping_is_separate() -> None:
    identity = matas_identity()
    classifier = ComparableClassifier()
    t0 = classifier.classify(
        identity,
        ComparableInput(title="2024-25 Court Kings Matas Buzelis 鞋灯 红平行 149编"),
    )
    t2 = classifier.classify(
        identity,
        ComparableInput(title="2024-25 Court Kings Matas Buzelis First Steps"),
    )
    estimate = HierarchicalEstimator().estimate(
        [
            ClassifiedSale("a", Decimal("75.10"), Decimal("18"), 5, t0),
            ClassifiedSale("b", Decimal("81.60"), Decimal("15"), 25, t0),
            ClassifiedSale("c", Decimal("5"), Decimal("18"), 4, t2),
        ]
    )
    assert Decimal("77") <= estimate.fair <= Decimal("80")
    assert estimate.landed_fair - estimate.fair == Decimal("16.50")
    secondary = next(item for item in estimate.evidence if item.sale.id == "c")
    assert secondary.final_weight == 0
    assert not secondary.used_for_center
    assert estimate.confidence < Decimal("0.90")


def test_nba_hoops_catalog_suffix_does_not_reject_seller_title() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "NBA Hoops Basketball",
            "card_number": "BH-2",
            "insert_name": "Bounce House",
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps NBA Hoops LeBron James #BH-2 "
                "Bounce House 弹跳屋 特卡"
            )
        ),
    )
    assert classification.tier == ComparableTier.T0
    assert classification.hard_conflicts == []


@pytest.mark.parametrize(
    "variant",
    ["射线折", "彩虹折", "碎闪折", "碎点折", "紫点点折", "波纹折", "棋盘折"],
)
def test_unnamed_insert_parallel_rejects_chinese_variant_vocab(
    variant: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "NBA Hoops Basketball",
            "card_number": "BH-2",
            "insert_name": "Bounce House",
        }
    )

    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps NBA Hoops LeBron James #BH-2 "
                f"Bounce House {variant}"
            )
        ),
    )

    assert classification.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in classification.hard_conflicts


def test_bowman_seller_short_name_reaches_visual_base_classification() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Allen Iverson",
            "season_start": 2003,
            "season_end": 2004,
            "product_name": "Bowman Rookies and Stars",
            "card_number": "10",
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2003-04 Topps Bowman Allen Iverson 艾弗森 银版SP 特卡",
            visual_verdict="compatible",
            visual_confidence=Decimal("0.95"),
            visual_reasons=("local_geometry_verified_same_printed_template",),
        ),
    )

    assert classification.tier == ComparableTier.T1
    assert classification.hard_conflicts == []


def test_bowman_short_name_rejects_distinct_prefixed_product() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Allen Iverson",
            "season_start": 2003,
            "season_end": 2004,
            "product_name": "Bowman Rookies and Stars",
            "card_number": "10",
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2003 Topps Bowman's Best Allen Iverson 特卡",
        ),
    )

    assert classification.tier == ComparableTier.REJECTED
    assert "product_conflict" in classification.hard_conflicts


def test_uncertain_visual_check_cannot_remain_t0() -> None:
    identity = matas_identity()
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2024-25 Court Kings Matas Buzelis RC 鞋灯 红平行 149编",
            visual_verdict="uncertain",
            visual_confidence=Decimal("0"),
            visual_reasons=("vision_model_omitted_candidate",),
        ),
    )
    assert classification.tier == ComparableTier.T1


def test_high_confidence_compatible_visual_can_confirm_unnumbered_insert() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "NBA Hoops Basketball",
            "card_number": "BH-2",
            "insert_name": "Bounce House",
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2025-26 Topps NBA Hoops LeBron James #BH-2 Bounce House",
            visual_verdict="compatible",
            visual_confidence=Decimal("0.95"),
        ),
    )
    assert classification.tier == ComparableTier.T0


def test_same_visual_cannot_replace_missing_insert_text_anchor() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Victor Wembanyama",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Panini Prizm Basketball",
            "card_number": "10",
            "insert_name": "Fractals",
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2024-25 Panini Prizm Victor Wembanyama Black Base",
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
        ),
    )

    assert classification.tier == ComparableTier.REJECTED
    assert "insert_conflict" in classification.hard_conflicts


def test_geometry_verified_template_can_replace_omitted_insert_seller_text() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Brandon Ingram",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "One and One",
            "card_number": "JJA-ING",
            "insert_name": "Jumbo Jersey Autographs",
            "serial_total": 75,
            "is_autograph": True,
            "is_memorabilia": True,
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2024-25 Panini One and One Brandon Ingram "
                "签字 卡签 球衣 大窗 58/75编"
            ),
            visual_verdict="compatible",
            visual_confidence=Decimal("0.95"),
            visual_reasons=(
                "local_geometry_verified_same_printed_template",
                "geometry_same_template:matches=130,inliers=91,ratio=0.700",
            ),
        ),
    )

    assert classification.tier == ComparableTier.T0
    assert not classification.hard_conflicts


def test_high_confidence_visual_template_promotes_plain_base_without_card_number_to_t1() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Michael Jordan",
            "season_start": 2001,
            "season_end": 2002,
            "product_name": "MJ's Back",
            "card_number": "MJ-90",
            "parallel_name": "Base",
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2001 Upper Deck MJ'S Back Michael Jordan 银标套卡",
            visual_verdict="compatible",
            visual_confidence=Decimal("0.95"),
        ),
    )

    assert classification.tier == ComparableTier.T1
    assert classification.similarity == Decimal("0.86")


def test_same_image_and_explicit_base_promote_missing_card_number_to_t0() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kobe Bryant",
            "season_start": 2015,
            "season_end": 2016,
            "product_name": "Revolution Basketball",
            "card_number": "7",
        }
    )

    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2015-16 Panini Revolution 革命 元年 湖人 base "
                "科比 布莱恩特 Kobe Bryant"
            ),
            visual_verdict="same",
            visual_confidence=Decimal("0.98"),
        ),
    )

    assert classification.tier == ComparableTier.T0
    assert classification.hard_conflicts == []


def test_same_image_promotes_intrinsic_encore_holofoil_base_to_t0() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Hakeem Olajuwon",
            "season_start": 1998,
            "season_end": 1999,
            "product_name": "Encore",
            "card_number": "30",
        }
    )

    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "1999 Upper Deck Encore Hakeem Olajuwon 火箭 "
                "哈基姆 奥拉朱旺 大梦 折射 经典老卡"
            ),
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
        ),
    )

    assert classification.tier == ComparableTier.T0
    assert classification.hard_conflicts == []


def test_gu_jersey_plain_card_number_and_unnumbered_parallel_are_t0() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2021,
            "season_end": 2022,
            "product_name": "Origins",
            "card_number": "01",
            "insert_name": "GU Jersey",
            "parallel_name": "Cur",
            "is_patch": True,
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2021-22 Panini Origins LeBron James 起源 "
                "GU亲穿 球衣物料 【Cur】 01"
            )
        ),
    )
    assert classification.tier == ComparableTier.T0
    assert classification.similarity == Decimal("1.00")
    assert classification.field_matches["insert"]
    assert classification.field_matches["card_number"]
    assert classification.field_matches["parallel"]


def test_plain_card_number_does_not_match_inside_season_or_other_number() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2021,
            "product_name": "Origins",
            "card_number": "01",
            "insert_name": "GU Jersey",
            "parallel_name": "Cur",
        }
    )
    classification = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2021-22 Origins LeBron James GU亲穿 Cur card 101"
        ),
    )
    assert not classification.field_matches["card_number"]


def test_sweet_stroke_material_rejects_other_sweet_sigs_inserts() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Tracy McGrady",
            "season_start": 2004,
            "season_end": 2005,
            "product_name": "Sweet Sigs",
            "card_number": "NNO",
            "purchase_context": (
                "2004-05 Fleer Sweet Sigs Tracy McGrady "
                "Sweet Stroke 物料 切割"
            ),
        }
    )
    assert identity.card_number is None
    assert identity.insert == "Sweet Stroke"
    assert identity.is_memorabilia
    assert not identity.is_patch
    classifier = ComparableClassifier()
    exact_insert = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2004-05 Fleer Sweet Sigs Tracy McGrady "
                "Sweet Stroke 特卡"
            )
        ),
    )
    assert exact_insert.tier == ComparableTier.T0
    other_insert = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2004-05 Fleer Sweet Sigs Tracy McGrady "
                "Hardcourt Heroics #18HH"
            )
        ),
    )
    assert other_insert.tier == ComparableTier.REJECTED
    assert "insert_conflict" in other_insert.hard_conflicts
    base = classifier.classify(
        identity,
        ComparableInput(title="2004-05 Fleer Sweet Sigs Tracy McGrady #64"),
    )
    assert base.tier == ComparableTier.REJECTED
    assert "material_conflict" in base.hard_conflicts


def test_collin_three_window_uses_text_and_visual_identity_gates() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Collin Murray-Boyles",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Three",
            "card_number": "RAPCM",
            "insert_name": "Prime Relic Autographs",
            "serial_total": 49,
            "is_rookie": True,
            "is_autograph": True,
            "is_patch": True,
        }
    )
    classifier = ComparableClassifier()
    same = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2026 Topps 3 Collin Murray-Boyles RC /49 "
                "三窗 RPA 签字 球衣"
            ),
        ),
    )
    assert same.tier == ComparableTier.T0
    same_with_material_words = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Three Collin Murray-Boyles RC /49 "
                "三窗 RPA 卡签 球衣物料 暴力patch"
            ),
        ),
    )
    assert same_with_material_words.tier == ComparableTier.T0
    large_window = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Three Collin Murray-Boyles RC /49 "
                "大窗暴力Patch Rookie Patch Auto"
            ),
        ),
    )
    assert large_window.tier == ComparableTier.REJECTED
    assert "insert_conflict" in large_window.hard_conflicts
    wrong_print_run = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Three Collin Murray-Boyles RC "
                "9/25遍 三窗 RPA 签字 球衣"
            ),
        ),
    )
    assert wrong_print_run.tier == ComparableTier.REJECTED
    assert "serial_total_conflict" in wrong_print_run.hard_conflicts
    visually_different = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Three Collin Murray-Boyles RC /49 "
                "三窗 RPA 卡签 球衣物料"
            ),
            visual_verdict="different",
            visual_confidence=Decimal("0.95"),
        ),
    )
    assert visually_different.tier == ComparableTier.REJECTED
    assert "visual_identity_conflict" in visually_different.hard_conflicts

    low_resolution_difference = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Three Collin Murray-Boyles RC /49 "
                "三窗 RPA 卡签 球衣物料"
            ),
            visual_verdict="different",
            visual_confidence=Decimal("0.79"),
        ),
    )
    assert low_resolution_difference.tier == ComparableTier.T1
    assert not low_resolution_difference.hard_conflicts


def test_vj_dreamland_rejects_night_owls_insert_aliases() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "VJ Edgecombe",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Midnight",
            "insert_name": "Dreamland",
            "parallel_name": "SSP",
        }
    )
    classifier = ComparableClassifier()
    exact = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Midnight VJ Edgecombe Dreamland "
                "梦幻之地 大比例 SSP"
            )
        ),
    )
    assert exact.tier == ComparableTier.T0

    for alias in ("Night Owls", "夜莺特卡", "猫头鹰特卡", "夜枭"):
        different = classifier.classify(
            identity,
            ComparableInput(
                title=f"2025-26 Topps Midnight VJ Edgecombe {alias} SSP"
            ),
        )
        assert different.tier == ComparableTier.REJECTED
        assert "insert_conflict" in different.hard_conflicts


def test_recent_60_day_t0_sales_set_center_and_older_t0_only_checks_range() -> None:
    classification = Classification(
        tier=ComparableTier.T0,
        similarity=Decimal("1"),
        source_reliability=Decimal("0.70"),
    )
    sales = [
        ClassifiedSale(str(index), price, Decimal("18"), age, classification)
        for index, (price, age) in enumerate(
            [
                (Decimal("745.08"), 2),
                (Decimal("775"), 2),
                (Decimal("745.08"), 2),
                (Decimal("406"), 61),
                (Decimal("586"), 91),
            ]
        )
    ]
    estimate = HierarchicalEstimator().estimate(sales)
    assert Decimal("745") <= estimate.fair <= Decimal("765")
    recent = [item for item in estimate.evidence if item.sale.age_days == 2]
    older = [item for item in estimate.evidence if item.sale.age_days > 60]
    assert all(item.used_for_center and item.final_weight > 0 for item in recent)
    assert all(not item.used_for_center and item.final_weight == 0 for item in older)


def test_recent_60_day_sales_use_arithmetic_average() -> None:
    classification = Classification(
        tier=ComparableTier.T0,
        similarity=Decimal("1"),
        source_reliability=Decimal("0.70"),
    )
    estimate = HierarchicalEstimator().estimate(
        [
            ClassifiedSale("new", Decimal("55"), Decimal("0"), 1, classification),
            ClassifiedSale("mid", Decimal("141.16"), Decimal("0"), 2, classification),
            ClassifiedSale("latest", Decimal("150.10"), Decimal("0"), 2, classification),
            ClassifiedSale("old", Decimal("39"), Decimal("0"), 90, classification),
        ]
    )
    assert estimate.fair == Decimal("115.42")
    old = next(item for item in estimate.evidence if item.sale.id == "old")
    assert not old.used_for_center
    assert old.final_weight == 0


def test_single_t0_sets_center_and_lower_tiers_only_expand_interval() -> None:
    exact = Classification(
        tier=ComparableTier.T0,
        similarity=Decimal("1"),
        source_reliability=Decimal("0.70"),
    )
    weak = Classification(
        tier=ComparableTier.T2,
        similarity=Decimal("0.60"),
        source_reliability=Decimal("0.70"),
    )
    estimate = HierarchicalEstimator().estimate(
        [
            ClassifiedSale("dreamland", Decimal("1013.89"), Decimal("0"), 2, exact),
            ClassifiedSale("base-low", Decimal("61"), Decimal("18"), 58, weak),
            ClassifiedSale("base-high", Decimal("1059"), Decimal("18"), 82, weak),
        ]
    )
    assert estimate.fair == Decimal("1013.89")
    exact_evidence = next(item for item in estimate.evidence if item.sale.id == "dreamland")
    weak_evidence = [item for item in estimate.evidence if item.sale.id != "dreamland"]
    assert exact_evidence.used_for_center
    assert exact_evidence.final_weight == Decimal("1")
    assert all(not item.used_for_center and item.final_weight == 0 for item in weak_evidence)
    assert estimate.confidence < Decimal("0.70")
    assert estimate.low < estimate.fair < estimate.high


def test_kidd_timeless_talents_aliases_accept_same_insert_and_reject_greatest() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "JASON KIDD",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "National Treasures",
            "insert_name": "Timeless Talents",
            "parallel_name": "Silver Parallel",
            "serial_total": 35,
            "is_autograph": True,
        }
    )
    classifier = ComparableClassifier()
    same = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "JASON KIDD 2023-24 National Treasures 国宝 "
                "29/35编 永恒天才签 银平行 卡签"
            )
        ),
    )
    assert same.tier == ComparableTier.T0
    same_without_parallel_word = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "JASON KIDD 2023-24 National Treasures 国宝 "
                "35编 永恒天赋 卡签"
            )
        ),
    )
    assert same_without_parallel_word.tier == ComparableTier.T0
    assert same_without_parallel_word.field_matches["parallel"]
    different_insert = classifier.classify(
        identity,
        ComparableInput(
            title=(
                "JASON KIDD 2023-24 National Treasures 国宝 "
                "35编 Greatest 伟大签 卡签"
            )
        ),
    )
    assert different_insert.tier == ComparableTier.REJECTED
    assert "insert_conflict" in different_insert.hard_conflicts


def test_explicit_wrong_card_number_is_a_hard_conflict() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Brandon Miller",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Prizm Basketball",
            "card_number": "152",
            "parallel_name": "Base",
        }
    )
    result = ComparableClassifier().classify(
        identity,
        ComparableInput(title="2023-24 Prizm Brandon Miller RC #153 base"),
    )
    assert result.tier == ComparableTier.REJECTED
    assert "card_number_conflict" in result.hard_conflicts


def test_prefixed_card_number_ignores_unrelated_seller_suffix() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Victor Wembanyama",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Topps Now",
            "card_number": "VW-6",
            "insert_name": "Rookie of the Year",
        }
    )
    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2023-24 Topps Now Victor Wembanyama Rookie of the Year "
                "RC 原封砖 #819"
            )
        ),
    )
    assert result.tier == ComparableTier.T0
    assert "card_number_conflict" not in result.hard_conflicts


def test_explicit_wrong_prefixed_card_number_is_a_hard_conflict() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Victor Wembanyama",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Topps Now",
            "card_number": "VW-6",
            "insert_name": "Rookie of the Year",
        }
    )
    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2023-24 Topps Now Victor Wembanyama Rookie of the Year "
                "RC #VW-7"
            )
        ),
    )
    assert result.tier == ComparableTier.REJECTED
    assert "card_number_conflict" in result.hard_conflicts


def test_bowman_retrofractor_allows_intrinsic_silver_finish() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Bill Russell",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Bowman Basketball",
            "card_number": "RV-BR",
            "insert_name": "Retrofractor Variations",
        }
    )
    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Bowman University Bill Russell 1st "
                "Retro Refractor 复古银折 变化版"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert not result.hard_conflicts


def test_unknown_knowledge_insert_falls_back_to_legacy_aliases() -> None:
    resolver = KnowledgeResolver(
        {
            "version": 1,
            "entities": [
                {
                    "id": "unrelated-insert",
                    "entity_type": "insert",
                    "canonical_name": "Unrelated Insert",
                    "aliases": [],
                }
            ],
        }
    )
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Bill Russell",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Bowman Basketball",
            "card_number": "RV-BR",
            "insert_name": "Retrofractor Variations",
        }
    )
    result = ComparableClassifier(resolver=resolver).classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Bowman Bill Russell Retro Refractor "
                "复古银折 变化版"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert not result.hard_conflicts


def test_bowman_retrofractor_rejects_explicit_gold_numbered_parallel() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Bill Russell",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Bowman Basketball",
            "card_number": "RV-BR",
            "insert_name": "Retrofractor Variations",
        }
    )
    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Bowman Chrome Bill Russell Retrofractor "
                "Gold 金折 20/50"
            )
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


def test_luka_number_and_blue_parallel_are_exact_matches() -> None:
    target = CardIdentityResolver().resolve(
        {
            "player_name": "LUKA DONČIĆ",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Crown Royale Basketball",
            "card_number": "No. 4",
            "insert_name": "Test of Time",
            "parallel_name": "Blue",
            "serial_number": 10,
            "serial_total": 75,
        }
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2023-24 Panini Crown Royale Basketball LUKA DONČIĆ "
                "Test of Time Blue /75 #4 Ungraded"
            )
        ),
    )
    assert result.tier == ComparableTier.T0
    assert result.field_matches["card_number"]
    assert result.field_matches["parallel"]


def test_luka_wrong_fusion_parallel_is_rejected_for_confirmed_blue_identity() -> None:
    target = CardIdentityResolver().resolve(
        {
            "player_name": "LUKA DONČIĆ",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Crown Royale Basketball",
            "card_number": "4",
            "insert_name": "Test of Time",
            "parallel_name": "Blue",
            "serial_number": 10,
            "serial_total": 75,
        }
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2023-24 Panini Crown Royale Basketball LUKA DONČIĆ "
                "Test of Time Fusion Red & Yellow /75 #4 Ungraded"
            )
        ),
    )
    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


def test_generic_refractor_word_does_not_reject_matching_insert_finish() -> None:
    target = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2020,
            "season_end": 2021,
            "product_name": "Mosaic Basketball",
            "card_number": "1",
            "insert_name": "Stained Glass",
        }
    )
    generic_finish = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2020-21 Panini Mosaic LeBron James Stained Glass "
                "教堂特卡 折射卡 #1"
            )
        ),
    )
    explicit_parallel = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2020-21 Panini Mosaic LeBron James Stained Glass "
                "教堂特卡 红折 #1"
            )
        ),
    )

    assert generic_finish.tier == ComparableTier.T0
    assert explicit_parallel.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in explicit_parallel.hard_conflicts


def test_contenders_optic_base_insert_accepts_silver_finish_but_rejects_cracked_ice() -> None:
    target = CardIdentityResolver().resolve(
        {
            "player_name": "Jayson Tatum",
            "season_start": 2019,
            "season_end": 2020,
            "product_name": "Contenders Optic",
            "card_number": "8",
            "insert_name": "Superstar",
        }
    )
    silver_base = ComparableClassifier().classify(
        target,
        ComparableInput(
            title="2019-20 Contenders Optic Jayson Tatum Superstar 特卡 银折 折射"
        ),
    )
    red_cracked_ice = ComparableClassifier().classify(
        target,
        ComparableInput(
            title="2019-20 Contenders Optic Jayson Tatum Superstar 特卡 红碎冰折"
        ),
    )

    assert silver_base.tier == ComparableTier.T0
    assert "parallel_conflict" not in silver_base.hard_conflicts
    assert red_cracked_ice.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in red_cracked_ice.hard_conflicts


def test_unicode_player_name_and_short_product_title_match_luka_exactly() -> None:
    assert normalize_text("Dončić") == normalize_text("Doncic")
    target = CardIdentityResolver().resolve(
        {
            "player_name": "LUKA DONČIĆ",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Crown Royale Basketball",
            "card_number": "4",
            "insert_name": "Test of Time",
            "parallel_name": "Blue",
            "serial_total": 75,
        }
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2023-24 Panini Crown Royale Luka Doncic Test of Time "
                "Blue /75 #4 Raw"
            )
        ),
    )
    assert result.tier == ComparableTier.T0
    assert not result.hard_conflicts


def test_base_card_without_insert_can_be_t0() -> None:
    target = CardIdentityResolver().resolve(
        {
            "player_name": "BRANDON MILLER",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Prizm Basketball",
            "card_number": "152",
            "set_name": "Base",
            "is_rookie": True,
        }
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title="2023-24 Panini Prizm Brandon Miller RC Rookie #152 Base Raw"
        ),
    )
    assert result.tier == ComparableTier.T0


def test_numbered_parallel_without_insert_can_be_t0() -> None:
    target = CardIdentityResolver().resolve(
        {
            "player_name": "JAREN JACKSON JR.",
            "season_start": 2020,
            "season_end": 2021,
            "product_name": "Mosaic Basketball",
            "card_number": "29",
            "parallel_name": "Fast Break Purple Mosaic",
            "serial_number": 22,
            "serial_total": 50,
        }
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2020-21 Panini Mosaic Jaren Jackson Jr #29 "
                "Fast Break Purple Prizm /50 Raw"
            )
        ),
    )
    assert result.tier == ComparableTier.T0


def test_autograph_and_parallel_mismatches_are_hard_conflicts() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Jaren Jackson Jr",
            "season_start": 2020,
            "season_end": 2021,
            "product_name": "Mosaic Basketball",
            "card_number": "29",
            "parallel_name": "Blue",
            "is_autograph": False,
        }
    )
    autograph = ComparableClassifier().classify(
        identity,
        ComparableInput(title="2020-21 Mosaic Jaren Jackson Jr #29 Blue auto 签字"),
    )
    wrong_parallel = ComparableClassifier().classify(
        identity,
        ComparableInput(title="2020-21 Mosaic Jaren Jackson Jr #29 Gold 金折"),
    )
    assert "autograph_conflict" in autograph.hard_conflicts
    assert "parallel_conflict" in wrong_parallel.hard_conflicts


def test_signature_series_numbered_sale_uses_serial_total_as_exact_anchor() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kevin Garnett",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Panini Signature Series Basketball",
            "card_number": "94",
            "parallel_name": "Plaid",
            "serial_number": 7,
            "serial_total": 15,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Panini Signature Series Kevin Garnett "
                "凯文 加内特 Base 07/15编"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_same_image_and_exact_print_run_recover_title_missing_insert_name() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Cade Cunningham",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Panini Signature Series Basketball",
            "card_number": "3",
            "insert_name": "Perpetual Talents",
            "parallel_name": "Gold",
            "serial_total": 10,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Panini Signature Series Cade Cunningham "
                "特卡 金平行 07/10编"
            ),
            visual_verdict="same",
            visual_confidence=Decimal("0.98"),
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_chrome_refractor_accepts_seller_silver_term() -> None:
    identity = CardIdentityResolver().resolve(
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

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Cosmic Chrome Victor Wembanyama "
                "银折 Extraterrestrial Talent ET-7"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_dual_jersey_alias_is_exact_insert_anchor() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Matas Buzelis, Tristan da Silva",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Panini Immaculate Collection Basketball",
            "card_number": "12",
            "insert_name": "Remarkable Rookie Jerseys Dual",
            "parallel_name": "Blue",
            "serial_number": 24,
            "serial_total": 25,
            "is_memorabilia": True,
            "is_patch": False,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2024-25 Panini Immaculate Collection Matas Buzelis RC 公牛 "
                "马塔斯布泽利斯 - 魔术 Tristan da Silva 25编双窗球衣"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_immaculate_collection_short_title_is_not_a_product_conflict() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Scoot Henderson",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Immaculate Collection",
            "set_name": "Base Variation",
            "card_number": "98",
            "parallel_name": "Gold",
            "serial_number": 3,
            "serial_total": 10,
            "is_rookie": True,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2023-24 Panini Immaculate Scoot Henderson RC "
                "Base Variation #98 Gold 03/10"
            )
        ),
    )

    assert "product_conflict" not in result.hard_conflicts
    assert result.tier == ComparableTier.T0


def test_optional_manufacturer_and_sport_wrappers_match_parent_product() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Example Player",
            "season_start": 2018,
            "season_end": 2019,
            "product_name": "Donruss Basketball",
            "card_number": "19",
            "insert_name": "All Heart",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2018-19 Panini Donruss Example Player All Heart 特卡 base"
            )
        ),
    )

    assert "product_conflict" not in result.hard_conflicts
    assert result.tier == ComparableTier.T0


def test_parent_product_short_alias_does_not_cross_into_child_product() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Example Player",
            "season_start": 2018,
            "season_end": 2019,
            "product_name": "Donruss Basketball",
            "card_number": "19",
            "insert_name": "All Heart",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2018-19 Panini Donruss Optic Example Player All Heart 特卡"
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "product_conflict" in result.hard_conflicts


def test_explicit_coloured_technology_parallel_is_not_treated_as_generic_finish() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Donruss Basketball",
            "card_number": "9",
            "insert_name": "Unleashed",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2023-24 Panini Donruss LeBron James Unleashed #9 "
                "红钻石折"
            )
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


def test_same_image_can_resolve_ambiguous_silver_wording_for_a_base_card() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Revolution Basketball",
            "card_number": "31",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2023-24 Panini Revolution LeBron James #31 银折 折射",
            visual_verdict="same",
            visual_confidence=Decimal("0.98"),
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_same_image_and_explicit_base_can_cover_a_missing_catalog_card_number() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kon Knueppel",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Midnight",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2025-26 Topps Midnight Kon Knueppel RC Base 折射",
            source_code="cardhobby",
            visual_verdict="same",
            visual_confidence=Decimal("0.98"),
        ),
    )

    assert result.tier != ComparableTier.REJECTED
    assert result.hard_conflicts == []


@pytest.mark.parametrize("ambiguous_finish", ["折射", "Refractor", "Foil"])
def test_explicit_base_resolves_ambiguous_finish_without_an_image_verdict(
    ambiguous_finish: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kon Knueppel",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Midnight",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Midnight Kon Knueppel RC "
                f"Base {ambiguous_finish}"
            ),
            source_code="cardhobby",
        ),
    )

    assert result.tier == ComparableTier.T2
    assert result.hard_conflicts == []


@pytest.mark.parametrize("ambiguous_finish", ["折射", "银折", "Refractor", "Foil"])
def test_unverified_ambiguous_finish_is_a_low_confidence_comparable(
    ambiguous_finish: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Bobby Portis",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Topps Chrome",
            "card_number": "124",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2024-25 Topps Chrome Bobby Portis #124 "
                f"{ambiguous_finish}"
            )
        ),
    )

    assert result.tier == ComparableTier.T2
    assert result.hard_conflicts == []
    assert result.field_matches["ambiguous_finish_softened"] is True


def test_explicit_colour_finish_stays_a_hard_parallel_conflict() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Bobby Portis",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Topps Chrome",
            "card_number": "124",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2024-25 Topps Chrome Bobby Portis #124 Blue Refractor"
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


def test_checkerboard_finish_stays_a_hard_parallel_conflict() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Shaedon Sharpe",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Chrome",
            "card_number": "106",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2026 Topps Chrome Shaedon Sharpe "
                "棋盘格折射 弹跳新星"
            )
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


@pytest.mark.parametrize(
    "finish", ["变形虫折", "龙鳞折射", "银脉冲折", "Amoeba", "Dragon Scale"]
)
def test_named_technology_finish_stays_a_hard_parallel_conflict(
    finish: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Bobby Portis",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Topps Chrome",
            "card_number": "124",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(title=f"2025 Topps Chrome Bobby Portis {finish}"),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


def test_omitted_insert_with_generic_foil_wording_remains_t2() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Jason Kidd",
            "season_start": 1997,
            "season_end": 1998,
            "product_name": "UD3",
            "card_number": "28",
            "insert_name": "Starstruck",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(title="1997 Upper Deck UD3 Jason Kidd 折射 老卡"),
    )

    assert result.tier == ComparableTier.T2
    assert result.hard_conflicts == []
    assert result.field_matches["ambiguous_finish_softened"] is True


def test_named_card_is_only_an_analog_when_target_has_no_card_anchor() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kon Knueppel",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Midnight",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Midnight Kon Knueppel RC "
                "Twilight 暮光特卡 大比例 SSP"
            ),
            source_code="cardhobby",
        ),
    )

    assert result.tier == ComparableTier.T2
    assert result.hard_conflicts == []


@pytest.mark.parametrize(
    "explicit_variant",
    [
        "Base 红钻石折",
        "Base Silver Refractor",
        "Base Gold Foil",
        "Base Refractor 12/25编",
        "Base 折射 Twilight SSP",
        "星座平行 Base 折射",
        "月光粉 base 十二宫",
        "zodiac平行 Base Refractor",
        "Base 银闪版 蓝平行",
    ],
)
def test_explicit_base_does_not_override_material_or_named_variant(
    explicit_variant: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kon Knueppel",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Midnight",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=f"2025-26 Topps Midnight Kon Knueppel RC {explicit_variant}",
            source_code="cardhobby",
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert set(result.hard_conflicts) & {"parallel_conflict", "insert_conflict"}


def test_same_image_cannot_resolve_an_explicit_coloured_parallel_as_base() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Revolution Basketball",
            "card_number": "31",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2023-24 Panini Revolution LeBron James #31 红钻石折",
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


def test_hoops_winter_variant_cannot_pollute_regular_hoops_base() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Hoops Basketball",
            "card_number": "290",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2023-24 NBA Hoops LeBron James #290 冬季雪花限定",
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "product_conflict" in result.hard_conflicts or "parallel_conflict" in result.hard_conflicts


def test_rookie_focus_short_alias_is_exact_with_player_season_and_product() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Ausar Thompson",
            "season_start": 2023,
            "season_end": 2024,
            "product_name": "Photogenic Basketball",
            "card_number": "6",
            "insert_name": "Rookie Focus",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2023-24 Panini Photogenic Ausar Thompson RC "
                "上镜 Focus 聚焦特卡 选秀夜定妆照"
            ),
            source_code="cardhobby",
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_same_image_and_card_code_turn_seller_year_disagreement_into_t1() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2018,
            "product_name": "Winter",
            "card_number": "W-3",
            "insert_name": "W-3",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2003-04 Upper Deck Winter LeBron James W-3",
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
        ),
    )

    assert result.tier == ComparableTier.T1
    assert result.hard_conflicts == []
    assert result.field_matches["season"] is False
    assert result.field_matches["season_visual_override"] is True


def test_geometry_can_audit_but_not_hide_an_explicit_parallel_conflict() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kobe Bryant",
            "season_start": 1998,
            "product_name": "Collector's Choice",
            "card_number": "M13",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="1998 Upper Deck Collector's Choice Kobe Bryant #M13 红钻石折",
            visual_verdict="different",
            visual_confidence=Decimal("0.92"),
            visual_reasons=(
                "geometry_same_template:matches=80,inliers=61,ratio=0.762",
            ),
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


def test_strong_geometry_and_exact_text_demote_false_visual_conflict_to_t1() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kobe Bryant",
            "season_start": 1998,
            "product_name": "Collector's Choice",
            "card_number": "M13",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="1998 Upper Deck Collector's Choice Kobe Bryant #M13 base",
            visual_verdict="different",
            visual_confidence=Decimal("0.92"),
            visual_reasons=(
                "geometry_same_template:matches=80,inliers=61,ratio=0.762",
            ),
        ),
    )

    assert result.tier == ComparableTier.T1
    assert result.hard_conflicts == []
    assert result.field_matches["visual_geometry_override"] is True


def test_mcdonalds_product_and_red_lava_chinese_aliases_match() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "AJ Dybantsa",
            "season_start": 2025,
            "season_end": 2025,
            "product_name": "Topps Chrome McDonald's All American",
            "card_number": "45",
            "parallel_name": "Red Lava Refractor",
            "serial_number": 106,
            "serial_total": 125,
            "is_rookie": True,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025 Topps Chrome AJ Dybantsa 麦当劳全美比赛 #45 "
                "红熔岩折 47/125编"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_reviewed_marketplace_parallel_alias_reaches_comparable_classifier() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Ace Bailey",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Chrome Basketball",
            "card_number": "255",
            "parallel_name": "Basketball Refractor",
            "is_rookie": True,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(title="2025 Topps Chrome Ace Bailey RC #255 球皮折"),
    )

    assert result.field_matches["parallel"]
    assert result.tier == ComparableTier.T0


def test_reviewed_marketplace_insert_alias_reaches_comparable_classifier() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Victor Wembanyama",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Panini Prizm Basketball",
            "insert_name": "Fractals",
            "card_number": "10",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2024-25 Panini Prizm Victor Wembanyama #10 分形特卡"
        ),
    )

    assert result.field_matches["insert"]
    assert result.tier == ComparableTier.T0


def test_unicode_season_dash_and_chinese_player_alias_match() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kevin Garnett",
            "season_start": 1997,
            "season_end": 1998,
            "product_name": "Metal Universe",
            "card_number": "109",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="97‑98 Skybox Metal Universe #109 凯文加内特 森林狼",
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_bare_inventory_digit_does_not_impersonate_numeric_card_number() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "AJ Dybantsa",
            "season_start": 2025,
            "season_end": 2025,
            "product_name": "Topps Chrome McDonald's All American",
            "card_number": "7",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2025 Topps Chrome AJ Dybantsa 麦当劳 base「7.25琳」"
        ),
    )

    assert result.tier == ComparableTier.T2
    assert result.field_matches["card_number"] is False


def test_base_target_rejects_explicit_parallel_variant() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "AJ Dybantsa",
            "season_start": 2025,
            "season_end": 2025,
            "product_name": "Topps Chrome McDonald's All American",
            "card_number": "7",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025 Topps Chrome AJ Dybantsa 麦当劳 #7 "
                "McFlurry Refractor"
            )
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


def test_dual_jersey_matches_mixed_english_and_chinese_player_names() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Matas Buzelis, Tristan da Silva",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Panini Immaculate Collection Basketball",
            "card_number": "12",
            "insert_name": "Remarkable Rookie Jerseys Dual",
            "parallel_name": "Blue",
            "serial_number": 24,
            "serial_total": 25,
            "is_patch": True,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2024-25 Panini Immaculate Collection Matas Buzelis RC 公牛 "
                "马塔斯布泽利斯 - 魔术 特里斯坦达席尔瓦 "
                "新秀 25编双窗球衣 暴力切割"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_same_image_promotes_typed_chrome_autograph_when_seller_omits_code() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Alex Sarr",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Chrome Updates Basketball",
            "card_number": "TCA-AS",
            "is_autograph": True,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2025-26 Topps Chrome Update Alex Sarr 奇才 base签 签字",
            visual_verdict="same",
            visual_confidence=Decimal("0.96"),
            visual_reasons=("same printed subject, border and autograph window",),
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_same_image_promotes_distinctive_insert_when_seller_omits_insert_name() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Jason Kidd",
            "season_start": 1997,
            "season_end": 1998,
            "product_name": "UD3",
            "card_number": "28",
            "insert_name": "Starstruck",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="1997 Upper Deck UD3 贾森基德 太阳 折射 老卡",
            visual_verdict="same",
            visual_confidence=Decimal("0.98"),
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_geometry_match_cannot_promote_explicit_base_to_insert() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Stephen Curry",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Basketball",
            "card_number": "SA-5",
            "insert_name": "Stars of the NBA",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2025 Topps Basketball Stephen Curry base 大卡 大尺寸特卡",
            visual_verdict="compatible",
            visual_confidence=Decimal("0.95"),
            visual_reasons=(
                "local_geometry_verified_same_printed_template",
                "geometry_same_template:matches=81,inliers=45,ratio=0.556",
            ),
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "insert_conflict" in result.hard_conflicts


def test_geometry_match_alone_cannot_promote_untyped_insert_to_t0() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Stephen Curry",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Basketball",
            "card_number": "SA-5",
            "insert_name": "Stars of the NBA",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="2025-26 Topps Basketball Stephen Curry Topps折 折射",
            visual_verdict="compatible",
            visual_confidence=Decimal("0.95"),
            visual_reasons=(
                "local_geometry_verified_same_printed_template",
                "geometry_same_template:matches=90,inliers=61,ratio=0.678",
            ),
        ),
    )

    assert result.tier != ComparableTier.T0


def test_geometry_match_does_not_promote_untyped_foil_insert_to_t0() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Jason Kidd",
            "season_start": 1997,
            "season_end": 1998,
            "product_name": "UD3",
            "card_number": "28",
            "insert_name": "Starstruck",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title="1997 Upper Deck UD3 贾森基德 太阳 折射 老卡",
            visual_verdict="compatible",
            visual_confidence=Decimal("0.95"),
            visual_reasons=(
                "local_geometry_verified_same_printed_template",
                "geometry_same_template:matches=80,inliers=61,ratio=0.762",
            ),
        ),
    )

    assert result.tier == ComparableTier.T2
    assert result.hard_conflicts == []


def test_jason_kidd_alias_does_not_match_kerry_kittles() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Jason Kidd",
            "season_start": 1997,
            "season_end": 1998,
            "product_name": "UD3",
            "card_number": "28",
            "insert_name": "Starstruck",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "1997 Upper Deck UD3 Kerry Kittles 基德尔斯 "
                "红色平行透明胶片特卡"
            ),
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "player_conflict" in result.hard_conflicts


def test_same_image_confirms_unnumbered_chrome_auto_silver_finish() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Derik Queen",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Chrome Updates Basketball",
            "card_number": "TCA-DQ",
            "insert_name": "Topps Chrome Autographs",
            "parallel_name": "Silver Refractor",
            "is_autograph": True,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Chrome Update Derik Queen RC 德里克 奎因 "
                "TCU 新秀签 签字"
            ),
            visual_verdict="same",
            visual_confidence=Decimal("0.98"),
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_chrome_autograph_insert_does_not_treat_silver_finish_as_parallel() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Nate Ament",
            "season_start": 2025,
            "season_end": 2025,
            "product_name": "Topps Chrome McDonald's All American",
            "card_number": "WT-NA",
            "insert_name": "Winning Tags",
            "is_autograph": True,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025 Topps Chrome McDonald's Nate Ament 内特阿门特 "
                "Winning Tags 签字 银折 折射"
            ),
            visual_verdict="same",
            visual_confidence=Decimal("0.95"),
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_target_insert_name_does_not_conflict_with_its_substring_entity() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Magic Johnson",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Chrome Updates Basketball",
            "card_number": "ST-22",
            "insert_name": "Stratospheric Stars",
            "parallel_name": "Blue",
            "serial_number": 32,
            "serial_total": 150,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Chrome Update Magic Johnson "
                "Stratospheric Stars ST-22 蓝折 032/150编"
            ),
            visual_verdict="same",
            visual_confidence=Decimal("0.97"),
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_resolver_repairs_product_insert_hierarchy_swapped_by_ocr() -> None:
    reserve = CardIdentityResolver().resolve(
        {
            "player_name": "Elton Brand",
            "product_name": "Principal Powers",
            "insert_name": "UD Reserve",
            "card_number": "PP4",
        }
    )
    choice = CardIdentityResolver().resolve(
        {
            "player_name": "Stephon Marbury",
            "product_name": "Starquest",
            "insert_name": "UD Choice",
            "card_number": "SQ16",
        }
    )

    assert reserve.product_name == "Upper Deck Reserve"
    assert reserve.insert == "Principal Powers"
    assert choice.product_name == "UD Choice"
    assert choice.insert == "Starquest"


def test_resolver_normalizes_product_logo_and_parallel_technology_roles() -> None:
    hoops = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "product_name": "Hoops Basketball",
            "insert_name": "NBA Hoops",
            "card_number": "290",
        }
    )
    premium = CardIdentityResolver().resolve(
        {
            "player_name": "Amen Thompson",
            "product_name": "Premium Stock Basketball",
            "insert_name": "Anti Gravity",
            "parallel_name": "Prizm",
            "card_number": "16",
        }
    )
    chrome = CardIdentityResolver().resolve(
        {
            "player_name": "Ace Bailey",
            "product_name": "Topps Chrome",
            "insert_name": "Clutch City",
            "parallel_name": "Refactor",
            "card_number": "YQ-5",
        }
    )

    assert hoops.insert is None
    assert premium.insert == "Anti Gravity"
    assert premium.parallel is None
    assert chrome.parallel == "Refractor"


def test_resolver_repairs_known_insert_stored_as_product_for_all_players() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Shai Gilgeous-Alexander",
            "product_name": "Daily Dribble",
            "card_number": "DD-2",
        }
    )

    assert identity.product_name == "Topps Basketball"
    assert identity.insert == "Daily Dribble"


def test_three_player_chinese_seller_names_match_confirmed_nucleus_card() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Bronny James Jr., LeBron James, Anthony Davis",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Origins Basketball",
            "card_number": "11",
            "insert_name": "Nucleus",
            "parallel_name": "Red",
            "serial_number": 83,
            "serial_total": 115,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2024-25 Panini Origins 布朗尼詹姆斯 勒布朗詹姆斯 "
                "安东尼戴维斯 Nucleus #11 红平行 083/115编"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_player_nickname_does_not_impersonate_knowledge_insert_alias() -> None:
    resolver = KnowledgeResolver(
        {
            "version": 1,
            "entities": [
                {
                    "id": "magicians",
                    "entity_type": "insert",
                    "canonical_name": "Magicians",
                    "aliases": ["魔术师"],
                },
                {
                    "id": "stratospheric-stars",
                    "entity_type": "insert",
                    "canonical_name": "Stratospheric Stars",
                    "aliases": ["Stratospheric"],
                },
            ],
        }
    )
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Magic Johnson",
            "season_start": 2025,
            "season_end": 2026,
            "product_name": "Topps Chrome Updates Basketball",
            "insert_name": "Stratospheric Stars",
            "parallel_name": "Blue",
            "serial_total": 150,
        }
    )

    result = ComparableClassifier(resolver=resolver).classify(
        identity,
        ComparableInput(
            title=(
                "2025-26 Topps Chrome Update Magic Johnson 魔术师约翰逊 "
                "Stratospheric 特卡 蓝折 032/150编"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_obscured_hoopnotic_transcription_is_canonicalized() -> None:
    assert canonical_insert("Hoop-Itic") == "Hoopnotic"
    assert canonical_insert("Hoopotic") == "Hoopnotic"
    assert canonical_insert("HOOP NOTIC") == "Hoopnotic"


@pytest.mark.parametrize("compound", ["Blue Ice", "Blue Cracked Ice", "Blue Laser"])
def test_short_colour_parallel_rejects_a_more_specific_compound(
    compound: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Prizm Basketball",
            "card_number": "54",
            "parallel_name": "Blue",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=f"2024-25 Panini Prizm LeBron James #54 {compound}"
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "parallel_conflict" in result.hard_conflicts


@pytest.mark.parametrize("seller_parallel", ["蓝", "蓝折", "蓝镭射"])
def test_blue_prizm_matches_short_marketplace_spelling(
    seller_parallel: str,
) -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "LeBron James",
            "season_start": 2024,
            "season_end": 2025,
            "product_name": "Prizm Basketball",
            "card_number": "54",
            "parallel_name": "Blue Prizm",
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                f"2024-25 Panini Prizm LeBron James #54 {seller_parallel}"
            )
        ),
    )

    assert result.tier == ComparableTier.T0
    assert result.hard_conflicts == []


def test_omitted_insert_needs_high_confidence_same_template_to_be_exact() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kyle Anderson",
            "season_start": 2014,
            "season_end": 2015,
            "product_name": "Donruss Basketball",
            "card_number": "31",
            "insert_name": "Elite",
            "is_autograph": True,
        }
    )
    title = (
        "2014-15 Panini Donruss Kyle Anderson RC 新秀 "
        "杜蕾斯 马刺 凯尔安德森 刺绣 卡签 签字"
    )

    unverified = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=title,
            visual_verdict="same",
            visual_confidence=Decimal("0.89"),
        ),
    )
    verified = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=title,
            visual_verdict="same",
            visual_confidence=Decimal("0.96"),
        ),
    )

    assert unverified.tier == ComparableTier.T2
    assert not unverified.field_matches["visual_same_template_anchor"]
    assert verified.tier == ComparableTier.T0
    assert verified.field_matches["visual_same_template_anchor"]


def test_same_image_cannot_hide_an_explicit_other_insert() -> None:
    identity = CardIdentityResolver().resolve(
        {
            "player_name": "Kyle Anderson",
            "season_start": 2014,
            "season_end": 2015,
            "product_name": "Donruss Basketball",
            "card_number": "31",
            "insert_name": "Elite",
            "is_autograph": True,
        }
    )

    result = ComparableClassifier().classify(
        identity,
        ComparableInput(
            title=(
                "2014-15 Panini Donruss Kyle Anderson Penmanship "
                "书法签 卡签 签字"
            ),
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
        ),
    )

    assert result.tier == ComparableTier.REJECTED
    assert "insert_conflict" in result.hard_conflicts
