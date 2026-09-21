from decimal import Decimal
from uuid import uuid4
import pytest

from card_platform.domain.product_identity import named_child_product_for_parent,assess_product_match,ProductMatchStatus
from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier,ComparableInput,ComparableTier
from card_platform.ingestion.cardhobby import CardHobbySale,assess_cardhobby_candidate
from card_platform.valuation_worker import DiscoveredComparable,cached_candidate_needs_image_review


def identity(**updates):
    return {'player_name':'Jaylen Brown','product_name':'Topps Chrome','card_number':'CG-10','insert_name':'Clutch Gene',
            'season_start':2025,'season_end':2026,'serial_total':99,**updates}


def sale(title='2025-26 Topps Chrome Update Jaylen Brown Clutch Gene #CG-10', **updates):
    return CardHobbySale(item_id='12345',title=title,source_url='https://cardhobby.com.cn/Market/Item/12345',occurred_at='2026-07-01T00:00:00+08:00',
        sale_price=Decimal('2'),bid_count=3,sale_type='auction',evidence_level='ended_auction',sold_status_evidence='Status=-2 with bids',
        match_confidence=Decimal('.9'),image_url='https://img.cardhobby.com/123.jpg',**updates)


def test_parent_and_child_stay_distinct_but_strong_anchors_can_request_image():
    candidate=sale()
    assert assess_product_match(candidate.title,'Topps Chrome')==ProductMatchStatus.EXPLICIT_CONFLICT
    assert named_child_product_for_parent(candidate.title,'Topps Chrome')=='topps chrome update'
    score,conflicts,tier=assess_cardhobby_candidate(identity(),candidate)
    assert score==Decimal('.45') and tier=='image_review_candidate'
    assert 'cardhobby_parent_child_image_required' in candidate.matching_fields
    comp=DiscoveredComparable(title=candidate.title,source_url=candidate.source_url,occurred_at=candidate.occurred_at,
        sale_price=candidate.sale_price,currency='CNY',fx_rate_to_base=Decimal('1'),image_url=candidate.image_url,match_confidence=score,matching_fields=candidate.matching_fields)
    assert cached_candidate_needs_image_review(identity(),comp,['product_conflict'],tier)


@pytest.mark.parametrize('verdict,confidence,expected',[('same','.97',ComparableTier.T2),('same','.90',ComparableTier.REJECTED),('uncertain','.99',ComparableTier.REJECTED),('different','.98',ComparableTier.REJECTED)])
def test_parent_child_needs_verified_image_and_stays_proxy(verdict,confidence,expected):
    result=ComparableClassifier().classify(CardIdentityResolver().resolve(identity()),ComparableInput(
        title=sale().title,source_code='cardhobby',visual_verdict=verdict,visual_confidence=Decimal(confidence)))
    assert result.tier==expected
    if expected==ComparableTier.T2:
        assert result.field_matches['product_lineage_unverified'] is True
        assert not result.hard_conflicts


@pytest.mark.parametrize('title',[
    '2025-26 Topps Chrome Update Jaylen Brown Clutch Gene #CG-10 /25',
    '2025-26 Topps Chrome Update Jaylen Brown Clutch Gene #CG-11',
    '2024-25 Topps Chrome Update Jaylen Brown Clutch Gene #CG-10',
    '2025-26 Topps Chrome Update Jaylen Brown Clutch Gene #CG-10 autograph',
    '2025-26 Topps Chrome Update Jaylen Brown Clutch Gene #CG-10 3张',
    '2025-26 Topps Chrome Update Jaylen Brown Clutch Gene',
])
def test_child_discovery_never_hides_other_conflicts_or_missing_anchors(title):
    assert assess_cardhobby_candidate(identity(),sale(title))[2] is None


def test_confirmed_parent_identity_is_not_silently_reinterpreted():
    assert assess_cardhobby_candidate(identity(confirmed_identity_version_id=str(uuid4())),sale())[2] is None


def test_child_does_not_recurse_or_equate_a_sibling():
    assert named_child_product_for_parent(sale().title,'Topps Chrome Sapphire') is None
