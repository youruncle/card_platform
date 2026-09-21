from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput, ComparableTier
from card_platform.domain.listing_title import is_multi_card_listing


@pytest.mark.parametrize("title", [
    "2025-26 Panini Prizm VJ Edgecombe RC 【梦云】PZ 新秀 埃奇库姆、奎因、特雷 约翰逊 三世 Fireworks、Brilliance 特卡 银折 不保卡品 情",
    "2024 Chrome 球员甲、球员乙、球员丙 First Flight、Sky High 特卡",
])
def test_independent_roster_and_insert_enumerations_are_mixed_lots(title):
    assert is_multi_card_listing(title)


@pytest.mark.parametrize("title", [
    "2024 Revolution 球员甲、球员乙、球员丙 Nucleus 特卡 三人卡",
    "2024 Chrome 球员甲 First Flight 特卡",
    "2024 Chrome 球员甲 First Flight、中文特卡名字 银折",
])
def test_shared_roster_or_translation_is_not_a_lot(title):
    assert not is_multi_card_listing(title)


@pytest.mark.parametrize("reasons", [
    ("geometry_same_template:matches=31,inliers=17,ratio=0.548",),
    ("local_geometry_verified_same_printed_template",),
    ("geometry_same_template:matches=80,inliers=61,ratio=0.762", "球员不同，卡型、Insert、视觉模板均不一致"),
    ("geometry_same_template:matches=80,inliers=61,ratio=0.762", "different player in listing photo"),
])
def test_local_geometry_cannot_erase_strong_visual_identity_conflict(reasons):
    identity = CardIdentityResolver().resolve({
        "player_name": "Kobe Bryant", "season_start": 1998,
        "product_name": "Collector's Choice", "card_number": "M13",
    })
    result = ComparableClassifier().classify(identity, ComparableInput(
        title="1998 Collector's Choice Kobe Bryant #M13 base",
        visual_verdict="different", visual_confidence=Decimal(".98"), visual_reasons=reasons,
    ))
    assert result.tier == ComparableTier.REJECTED
    assert "visual_identity_conflict" in result.hard_conflicts


def test_unmarked_same_insert_remains_numbered_card_proxy():
    identity = CardIdentityResolver().resolve({
        "player_name": "Derik Queen", "season_start": 2025, "season_end": 2026,
        "product_name": "Prizm", "insert_name": "Brilliance", "card_number": "19",
        "serial_number": 194, "serial_total": 249,
    })
    result = ComparableClassifier().classify(identity, ComparableInput(
        title="2025-26 Prizm Derik Queen Brilliance 特卡 银折",
        source_code="cardhobby", visual_verdict="same", visual_confidence=Decimal(".97"),
    ))
    assert result.tier == ComparableTier.T2
    assert not result.hard_conflicts
    assert result.field_matches["serial_total"] is False


def test_rotation_complaint_does_not_override_strong_measured_same_template():
    identity = CardIdentityResolver().resolve({
        "player_name":"Tyrese Haliburton", "season_start":2025,
        "product_name":"NBA Hoops", "insert_name":"Bounce House", "serial_total":50,
    })
    result = ComparableClassifier().classify(identity, ComparableInput(
        title="2025-26 Topps NBA Hoops Tyrese Haliburton Bounce House 特卡",
        visual_verdict="different", visual_confidence=Decimal('.98'),
        visual_reasons=("整张卡被旋转90度，因此判定为不同卡型/方向。",
                        "geometry_same_template:matches=138,inliers=120,ratio=0.870"),
    ))
    assert not result.hard_conflicts
    assert result.field_matches['visual_geometry_override'] is True
    # Production's provider contract demotes unspecified print runs to a
    # same-player/product analogue after the generic template classifier.
    from card_platform.valuation_worker import reconcile_cardhobby_classification
    result, _ = reconcile_cardhobby_classification(result, 'same_player_product_analog')
    assert result.tier == ComparableTier.T2


@pytest.mark.asyncio
async def test_published_numbered_proxy_explains_unknown_premium_without_hiding_value():
    from card_platform.valuation_worker import AutomatedValuationWorker, ComparableSearchResult
    conn = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value={"id":uuid4()}))))
    shadow = SimpleNamespace(used_ids=(), low=Decimal('.55'), fair=Decimal('1'), high=Decimal('1.45'),
                             confidence=Decimal('.22'), rejected=(), review_reasons=('cardhobby_same_type_proxy_range',))
    await AutomatedValuationWorker._publish_relative_valuation(conn, uuid4(),
        {'collection_item_id':uuid4(), 'base_currency':'CNY', 'serial_total':249}, shadow, ComparableSearchResult(), {})
    evidence = conn.execute.await_args.args[1][6].obj
    assert evidence['automatic_publication'] is True
    assert evidence['exact_version_verified'] is False
    assert evidence['numbered_premium_applied'] is False
    assert evidence['pricing_basis'] == 'same_type_proxy'
    assert any('溢价未知' in value for value in evidence['notes'])
