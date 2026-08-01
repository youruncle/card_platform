from decimal import Decimal
from pathlib import Path

from card_platform.knowledge_manifest import load_manifest, validate_manifest
from card_platform.recognition_catalog import rank_knowledge_cards
from card_platform.schemas import CardRecognitionResult


MANIFEST = (
    Path(__file__).parents[3]
    / "data"
    / "knowledge"
    / "recognition_failures_threads_1011_hoops_slam_2021_v1.json"
)


def test_recognition_failure_products_manifest_is_publishable() -> None:
    manifest = load_manifest(MANIFEST)
    report = validate_manifest(manifest)
    assert report.publishable, report.conflicts
    assert report.summary["covered_rows"] == 2
    assert all(report.replay.values())


def test_hoops_slam_luka_catalog_identity_is_recalled() -> None:
    manifest = load_manifest(MANIFEST)
    visual = CardRecognitionResult(
        player_name="Luka Doncic",
        season_start=2020,
        product_name="Hoops",
        insert_name="SLAM",
        card_number="5",
        confidence=Decimal("0.85"),
    )
    ranked = rank_knowledge_cards(visual, manifest["entities"])
    assert ranked[0]["identity"]["player_name"] == "Luka Doncic"
    assert ranked[0]["identity"]["card_number"] == "5"


def test_threads_paul_george_catalog_identity_is_recalled() -> None:
    manifest = load_manifest(MANIFEST)
    visual = CardRecognitionResult(
        player_name="Paul George",
        season_start=2010,
        product_name="Threads",
        insert_name="Team Threads",
        card_number="33",
        confidence=Decimal("0.85"),
    )
    ranked = rank_knowledge_cards(visual, manifest["entities"])
    assert ranked[0]["identity"]["player_name"] == "Paul George"
    assert ranked[0]["identity"]["is_rookie"] is True
    assert ranked[0]["identity"]["is_autograph"] is False
