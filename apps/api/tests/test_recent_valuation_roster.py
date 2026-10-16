from decimal import Decimal

import pytest

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import (
    ComparableClassifier,
    ComparableInput,
    ComparableTier,
    _contains_player,
)


@pytest.mark.parametrize(
    "target,title",
    [
        ("C.J. McCollum", "CJ McCollum"),
        ("CJ McCollum", "C. J. McCollum"),
        ("A.J. Griffin", "AJ Griffin"),
    ],
)
def test_initial_punctuation_is_not_a_different_player(target, title):
    assert _contains_player(title, target)
    assert not _contains_player(
        title.replace("McCollum", "Miles").replace("Griffin", "Green"), target
    )


@pytest.mark.parametrize(
    "verdict,confidence,accepted",
    [
        ("same", "0.96", True),
        ("same", "0.75", False),
        ("compatible", "0.99", False),
        ("different", "0.99", False),
        (None, "0", False),
    ],
)
def test_omitted_third_subject_requires_positive_template_proof(verdict, confidence, accepted):
    target = CardIdentityResolver().resolve(
        dict(
            player_name="Alpha Smith, Beta Jones, Gamma Brown",
            season_start=2017,
            season_end=2018,
            product_name="Status",
            insert_name="Factions",
            card_number="1",
        )
    )
    comp = ComparableInput(
        title="2017-18 Status Factions Alpha Smith / Beta Jones #1",
        visual_verdict=verdict,
        visual_confidence=Decimal(confidence),
    )
    result = ComparableClassifier().classify(target, comp)
    assert ("player_conflict" not in result.hard_conflicts) == accepted
    if accepted:
        assert result.tier in {ComparableTier.T0, ComparableTier.T1}
        assert result.field_matches["partial_multi_player_visual_confirmation"]


def test_single_subject_cannot_identify_triple_even_with_same_image():
    target = CardIdentityResolver().resolve(
        dict(
            player_name="Alpha Smith, Beta Jones, Gamma Brown",
            season_start=2017,
            season_end=2018,
            product_name="Status",
            insert_name="Factions",
            card_number="1",
        )
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title="2017-18 Status Factions Alpha Smith #1",
            visual_verdict="same",
            visual_confidence=Decimal("0.99"),
        ),
    )
    assert "player_conflict" in result.hard_conflicts


@pytest.mark.parametrize(
    "verdict,confidence,accepted",
    [
        ("same", ".97", True),
        ("same", ".90", False),
        ("uncertain", ".99", False),
        ("different", ".98", False),
        (None, "0", False),
    ],
)
def test_one_translated_roster_name_needs_full_image_and_explicit_dual_insert(
    verdict, confidence, accepted
):
    target = CardIdentityResolver().resolve(
        dict(
            player_name="Donovan Mitchell, Ja Morant",
            season_start=2021,
            season_end=2022,
            product_name="Donruss",
            insert_name="Duos",
            card_number="5",
        )
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title="2021-22 Donruss Ja Morant 贾莫兰特 双人 DUOS 特卡",
            source_code="cardhobby",
            visual_verdict=verdict,
            visual_confidence=Decimal(confidence),
        ),
    )
    assert ("player_conflict" not in result.hard_conflicts) == accepted
    if accepted:
        assert result.tier == ComparableTier.T2


@pytest.mark.parametrize("extra", ["3张", "autograph", " /25"])
def test_partial_roster_image_does_not_clear_lot_or_version_conflicts(extra):
    target = CardIdentityResolver().resolve(
        dict(
            player_name="Donovan Mitchell, Ja Morant",
            season_start=2021,
            season_end=2022,
            product_name="Donruss",
            insert_name="Duos",
            card_number="5",
        )
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title="2021-22 Donruss Ja Morant 双人 DUOS 特卡 " + extra,
            source_code="cardhobby",
            visual_verdict="same",
            visual_confidence=Decimal(".99"),
        ),
    )
    assert result.hard_conflicts and result.tier == ComparableTier.REJECTED


def test_partial_roster_provider_only_proposes_image_review():
    from card_platform.ingestion.cardhobby import CardHobbySale, assess_cardhobby_candidate

    candidate = CardHobbySale(
        item_id="12345",
        title="2021-22 Donruss Ja Morant 贾莫兰特 双人 DUOS 特卡",
        source_url="https://cardhobby.com.cn/Market/Item/12345",
        occurred_at="2026-07-01T00:00:00+08:00",
        sale_price=Decimal("2"),
        bid_count=3,
        sale_type="auction",
        evidence_level="ended_auction",
        sold_status_evidence="Status=-2 with bids",
        match_confidence=Decimal(".9"),
        image_url="https://img.cardhobby.com/123.jpg",
    )
    score, _, tier = assess_cardhobby_candidate(
        dict(
            player_name="Donovan Mitchell, Ja Morant",
            season_start=2021,
            season_end=2022,
            product_name="Donruss",
            insert_name="Duos",
            card_number="5",
        ),
        candidate,
    )
    assert score == Decimal(".45") and tier == "image_review_candidate"
    assert "cardhobby_partial_roster_image_required" in candidate.matching_fields


def test_delimiterless_dual_roster_cannot_be_satisfied_by_one_subject_without_image():
    from card_platform.ingestion.cardhobby import CardHobbySale, assess_cardhobby_candidate

    raw_identity = dict(
        player_name="Matas Buzelis Tristan da Silva",
        season_start=2024,
        season_end=2025,
        product_name="Panini Immaculate Collection Basketball",
        insert_name="Remarkable Rookie Jerseys Dual",
        card_number="12",
        parallel_name="Blue",
        serial_total=25,
        is_patch=True,
    )
    title = (
        "2024-25 Panini Immaculate Collection Matas Buzelis RC #12 "
        "Remarkable Rookie Jerseys Dual Blue 11/25 Patch"
    )
    sale = CardHobbySale(
        item_id="12346",
        title=title,
        source_url="https://cardhobby.com.cn/Market/Item/12346",
        occurred_at="2026-07-01T00:00:00+08:00",
        sale_price=Decimal("2"),
        bid_count=3,
        sale_type="auction",
        evidence_level="ended_auction",
        sold_status_evidence="Status=-2 with bids",
        match_confidence=Decimal(".9"),
        image_url="https://images.cardhobby.com/123.jpg",
    )

    score, _, tier = assess_cardhobby_candidate(raw_identity, sale)
    assert score == Decimal(".45")
    assert tier == "image_review_candidate"
    assert "cardhobby_partial_roster_image_required" in sale.matching_fields

    target = CardIdentityResolver().resolve(raw_identity)
    unverified = ComparableClassifier().classify(
        target,
        ComparableInput(title=title, source_code="cardhobby"),
    )
    assert unverified.tier == ComparableTier.REJECTED
    assert "player_conflict" in unverified.hard_conflicts

    verified = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=title,
            source_code="cardhobby",
            visual_verdict="same",
            visual_confidence=Decimal(".97"),
        ),
    )
    assert verified.tier == ComparableTier.T2
    assert not verified.hard_conflicts


def test_delimiterless_dual_roster_still_accepts_both_named_subjects():
    target = CardIdentityResolver().resolve(
        dict(
            player_name="Matas Buzelis Tristan da Silva",
            season_start=2024,
            season_end=2025,
            product_name="Panini Immaculate Collection Basketball",
            insert_name="Remarkable Rookie Jerseys Dual",
            card_number="12",
        )
    )
    result = ComparableClassifier().classify(
        target,
        ComparableInput(
            title=(
                "2024-25 Panini Immaculate Collection Matas Buzelis "
                "Tristan da Silva Remarkable Rookie Jerseys Dual #12"
            ),
            source_code="cardhobby",
        ),
    )
    assert result.tier in {ComparableTier.T0, ComparableTier.T1}
    assert not result.hard_conflicts
