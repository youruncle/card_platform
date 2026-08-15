from decimal import Decimal

from card_platform.domain.card_identity import CardIdentityResolver, canonical_insert
from card_platform.domain.card_identity import normalize_text
from card_platform.domain.comparables import (
    Classification,
    ComparableClassifier,
    ComparableInput,
    ComparableTier,
)
from card_platform.domain.hierarchical_estimator import ClassifiedSale, HierarchicalEstimator
from card_platform.valuation_worker import AutomatedValuationWorker


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
    assert identity.is_patch
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


def test_luka_confirmed_blue_identity_loads_exact_sparse_market_sales() -> None:
    identity = {
        "player_name": "LUKA DONČIĆ",
        "season_start": 2023,
        "season_end": 2024,
        "product_name": "Crown Royale Basketball",
        "card_number": "4",
        "insert_name": "Test of Time",
        "parallel_name": "Blue",
        "serial_number": 10,
        "serial_total": 75,
        "grade_company": None,
    }
    sales = AutomatedValuationWorker._curated_exact_comparables(identity)
    assert len(sales) == 6
    assert {sale.evidence_level for sale in sales} == {"aggregated_sale"}
    assert all("Blue" in sale.title and "/75" in sale.title for sale in sales)
    assert all(sale.grade_company is None for sale in sales)


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


def test_luka_curated_sales_require_confirmed_exact_parallel_identity() -> None:
    identity = {
        "player_name": "LUKA DONČIĆ",
        "season_start": 2023,
        "product_name": "Crown Royale Basketball",
        "card_number": "4",
        "insert_name": "Test of Time",
        "parallel_name": "Fusion Red and Yellow",
        "serial_total": 75,
        "grade_company": None,
    }
    assert AutomatedValuationWorker._curated_exact_comparables(identity) == []


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
