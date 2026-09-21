from datetime import UTC, datetime
from decimal import Decimal

import pytest

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput, ComparableTier
from card_platform.domain.grade_basis import UNGRADED_SAME_CARD_BASIS
from card_platform.domain.product_identity import ProductMatchStatus, assess_product_match
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
from card_platform.ingestion.cardhobby import CardHobbySale, assess_cardhobby_candidate


def test_manufacturer_only_title_does_not_assert_the_flagship_product():
    assert assess_product_match("2025 Topps Test Player", "NBA Hoops") == ProductMatchStatus.UNKNOWN
    assert assess_product_match(
        "2025 Topps Basketball Test Player", "Topps Basketball"
    ) == ProductMatchStatus.MATCH


@pytest.mark.parametrize(("seller_product", "expected"), [
    ("Flux Prizm", ProductMatchStatus.UNKNOWN),
    ("Flux Prizm Basketball", ProductMatchStatus.EXPLICIT_CONFLICT),
    ("Flux Panini Prizm", ProductMatchStatus.EXPLICIT_CONFLICT),
])
def test_optional_sport_alias_does_not_invent_product_specificity(seller_product, expected):
    assert assess_product_match(
        f"2021-22 Chris Duarte {seller_product} #FRA-CD 12/68",
        "Chronicles Flux Draft Picks",
        target_insert="Flux",
        target_parallel="Prizm",
    ) == expected


@pytest.mark.parametrize(("parent", "seller_product"), [
    ("Topps Chrome", "Topps Chrome Basketball Update"),
    ("Topps Chrome", "Topps Chrome Basketball Updates"),
    ("Topps Chrome", "Topps Chrome Basketball Sapphire"),
    ("Topps Chrome", "Topps Basketball Chrome Update"),
    ("Donruss", "Donruss Basketball Optic"),
    ("Prizm", "Prizm Basketball Draft Picks"),
    ("Contenders", "Contenders Basketball Optic"),
    ("Hoops", "Hoops Basketball Premium Stock"),
])
def test_optional_sport_position_does_not_hide_a_different_child_product(parent, seller_product):
    assert assess_product_match(
        f"2025-26 {seller_product} Example Player #7", parent,
    ) == ProductMatchStatus.EXPLICIT_CONFLICT


@pytest.mark.parametrize(("target", "seller_product"), [
    ("Topps Chrome Update Basketball", "Topps Chrome Basketball Update"),
    ("Topps Chrome Basketball Update", "Topps Chrome Update"),
    ("Topps Chrome Sapphire", "Topps Chrome Basketball Sapphire"),
    ("Donruss Optic Basketball", "Donruss Basketball Optic"),
    ("Topps Chrome", "Topps Chrome Basketball"),
])
def test_same_child_product_matches_despite_optional_sport_position(target, seller_product):
    assert assess_product_match(
        f"2025-26 {seller_product} Example Player #7", target,
    ) == ProductMatchStatus.MATCH


@pytest.mark.parametrize(("target", "seller_product"), [
    ("Topps Chrome Update", "Topps Chrome Basketball Sapphire"),
    ("Topps Chrome Sapphire", "Topps Chrome Basketball Update"),
    ("Topps Chrome Update Basketball", "Topps Chrome Baseball Update"),
    ("Topps Chrome Baseball Update", "Topps Chrome Basketball Update"),
    ("Donruss Basketball", "Donruss Football"),
])
def test_sport_normalization_preserves_sibling_product_and_cross_sport_conflicts(target, seller_product):
    assert assess_product_match(
        f"2025-26 {seller_product} Example Player #7", target,
    ) == ProductMatchStatus.EXPLICIT_CONFLICT


@pytest.mark.parametrize("subtype", ["Update", "Sapphire"])
def test_graded_card_baseline_cannot_admit_a_child_product_using_parent_name_and_team(subtype):
    identity = {
        "player_name": "Example Player", "brand_name": "Topps",
        "product_name": "Topps Chrome", "season_start": 2025, "season_end": 2026,
        "card_number": "7", "insert_name": "LAKERS",
        "grade_company": "BGS", "grade_value": Decimal("8.6"),
    }
    candidate = CardHobbySale(
        item_id="12345",
        title=f"2025-26 Topps Chrome Basketball {subtype} Example Player LAKERS 正Base #7",
        source_url="https://cardhobby.com.cn/Market/Item/12345",
        occurred_at=datetime(2026, 3, 1, tzinfo=UTC), sale_price=Decimal("3.20"),
        bid_count=4, evidence_level="ended_auction", sale_type="auction",
        sold_status_evidence="Status=-2, OutCount=4", match_confidence=Decimal("0.95"),
        matching_fields=[UNGRADED_SAME_CARD_BASIS],
    )
    assert assess_cardhobby_candidate(identity, candidate)[2] is None
    preliminary = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity),
        ComparableInput(candidate.title, source_code="cardhobby"),
    )
    formal = enforce_formal_cardhobby_identity(identity, candidate.model_dump(mode="json"), preliminary)
    assert formal.tier == ComparableTier.REJECTED
    assert "product_conflict" in formal.hard_conflicts
