from decimal import Decimal

import pytest

from card_platform.domain.card_identity import CardIdentity
from card_platform.domain.comparables import ComparableClassifier, ComparableInput
from card_platform.domain.season import explicit_seasons
from card_platform.ingestion.cardhobby import (
    PRODUCT_SEARCH_ALIASES,
    CardHobbyProvider,
    CardHobbySale,
    assess_cardhobby_match,
)


@pytest.mark.parametrize("separator", ["-", "–", "—", "‑", "−", "/", "／"])
@pytest.mark.parametrize("end", ["00", "2000"])
def test_abbreviated_seasons_roll_forward_across_centuries(separator, end) -> None:
    assert explicit_seasons(f"1999{separator}{end} Topps Chrome") == {(1999, 2000)}


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("1998-99", {(1998, 1999)}),
        ("2000-01", {(2000, 2001)}),
        ("2025-26", {(2025, 2026)}),
        ("2099-00", {(2099, 2100)}),
        ("1999-1900", {(1999, 1900)}),
        ("1999-01", {(1999, 2001)}),
        ("1999-00 and 2000-01", {(1999, 2000), (2000, 2001)}),
        ("#1999 / 1000", set()),
    ],
)
def test_explicit_season_parsing_preserves_other_years_and_conflicts(title, expected) -> None:
    assert explicit_seasons(title) == expected


def _sale(title: str) -> CardHobbySale:
    return CardHobbySale(
        item_id="123456",
        title=title,
        source_url="https://cardhobby.com.cn/Market/Item/123456",
        occurred_at="2026-07-01T20:00:00+08:00",
        sale_price=Decimal("10"),
        bid_count=1,
        sale_type="auction",
        evidence_level="ended_auction",
        sold_status_evidence="Status=-2",
        match_confidence=Decimal("0.8"),
    )


@pytest.mark.parametrize("season", ["1999-00", "1999-2000", "1999—00"])
def test_sale_and_comparable_matchers_agree_on_century_boundary(season) -> None:
    target = CardIdentity(
        player_name="Tim Duncan",
        product_name="Topps Chrome",
        brand_name="Topps",
        season_start=1999,
        season_end=2000,
        card_number="10",
    )
    title = f"{season} Topps Chrome Tim Duncan #10 base"

    score, conflicts = assess_cardhobby_match(target.as_dict(), _sale(title))
    classified = ComparableClassifier().classify(target, ComparableInput(title=title))

    assert score >= Decimal("0.65")
    assert not any("赛季" in conflict for conflict in conflicts)
    assert classified.field_matches["season"] is True
    assert "season_conflict" not in classified.hard_conflicts


@pytest.mark.parametrize("season", ["1998-99", "1999-1900", "2000-01"])
def test_other_explicit_seasons_remain_conflicts(season) -> None:
    target = CardIdentity(
        player_name="Tim Duncan",
        product_name="Topps Chrome",
        season_start=1999,
        season_end=2000,
        card_number="10",
    )
    title = f"{season} Topps Chrome Tim Duncan #10 base"

    _score, conflicts = assess_cardhobby_match(target.as_dict(), _sale(title))
    classified = ComparableClassifier().classify(target, ComparableInput(title=title))

    assert any("赛季" in conflict for conflict in conflicts)
    assert classified.tier == "rejected"
    assert "season_conflict" in classified.hard_conflicts


@pytest.mark.parametrize(
    ("product", "primary", "alternate"),
    [
        ("FLAIR SHOWCASE", "Fleer Showcase", "FLAIR SHOWCASE"),
        ("Fleer Showcase", "Fleer Showcase", "Flair Showcase"),
        ("UD Choice", "UD Choice", "Upper Deck Choice"),
        ("Panini Signature Series", "Signature Series", "Panini Signature Series"),
    ],
)
def test_bounded_plan_keeps_same_language_product_alternative(product, primary, alternate) -> None:
    plan = CardHobbyProvider.build_query_plan(
        {
            "player_name": "Example Player",
            "product_name": product,
            "season_start": 1999,
            "season_end": 2000,
            "card_number": "19",
            "insert_name": "NEXT",
        }
    )
    queries = [query for _stage, query in plan]

    assert len(plan) <= 12
    assert f"Example Player {alternate}" in queries
    assert f"Example Player {primary}" in queries


def test_alias_recall_survives_dense_query_budget_for_any_registered_product(monkeypatch) -> None:
    monkeypatch.setitem(
        PRODUCT_SEARCH_ALIASES,
        "example catalog family",
        ("Example Seller Family", "Example Catalog Family"),
    )
    plan = CardHobbyProvider.build_query_plan(
        {
            "player_name": "Stephen Curry",
            "brand_name": "Example Maker",
            "product_name": "Example Catalog Family",
            "season_start": 2025,
            "season_end": 2026,
            "card_number": "AB-12",
            "insert_name": "Dreamland",
            "parallel_name": "Blue",
            "serial_total": 99,
        }
    )

    assert len(plan) == 12
    assert ("product_spelling_rotation", "库里 Example Catalog Family") in plan
    assert any("Example Seller Family" in query for _stage, query in plan)
    assert any(stage == "season_insert" for stage, _query in plan)
