"""Exact seller text does not establish what is on a candidate photograph."""
import hashlib
from decimal import Decimal
import pytest
from card_platform.comparable_image_verifier import (
    MODEL_OBSERVATION_CONTRACT, VisualCandidate, visual_asset_binding,
)
from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput, ComparableTier
from card_platform.domain.finish_evidence import build_visual_match_facts
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
from card_platform.ingestion.cardhobby import CardHobbySale


def fixture():
    identity = dict(player_name='Ace Bailey', brand_name='Topps', product_name='Bowman Chrome',
                    season_start=2025, card_number='GL-5', insert_name='Greatness Loading',
                    parallel_name='Refractor', image_front_url='/uploads/fixture-front.jpg',
                    image_back_url='/uploads/fixture-back.jpg',
                    image_content_hashes={view:hashlib.sha256(view.encode()).hexdigest()
                                          for view in ('front','back')})
    sale = CardHobbySale(item_id='123', title='2025 Bowman Chrome Ace Bailey Greatness Loading GL-5 Refractor',
                        source_url='https://cardhobby.com.cn/Market/Item/123',
                        image_url='https://images.cardhobby.com/fixture.jpg',
                        occurred_at='2026-10-01T00:00:00Z', sale_price='10', bid_count=3,
                        sale_type='auction', evidence_level='ended_auction',
                        sold_status_evidence='Status=-2, 3 bids', match_confidence='.95')
    return identity, sale


def classify(identity, sale, observation):
    facts = build_visual_match_facts(identity, sale.source_url, sale.title, sale.image_url, observation)
    preliminary = ComparableClassifier().classify(CardIdentityResolver().resolve(identity),
        ComparableInput(title=sale.title, source_code='cardhobby', visual_facts=facts,
                        visual_verdict=observation.get('visual_verdict'),
                        visual_confidence=Decimal(observation.get('visual_confidence') or 0),
                        visual_reasons=tuple(observation.get('visual_reasons') or [])))
    return enforce_formal_cardhobby_identity(identity, sale.model_dump(mode='json'), preliminary,
        visual_verdict=observation.get('visual_verdict'), visual_facts=facts,
        visual_observation=observation)


@pytest.mark.parametrize('verdict', [None, 'uncertain', 'compatible', 'same'])
def test_exact_seller_title_needs_current_paired_card_confirmation(verdict):
    identity, sale = fixture()
    result = classify(identity, sale, {'visual_verdict':verdict, 'visual_confidence':'.99'})
    assert result.tier == ComparableTier.REJECTED
    assert 'current_paired_card_image_confirmation_required' in result.hard_conflicts


def observed(identity, sale):
    candidate = VisualCandidate(sale.source_url, sale.title, sale.image_url)
    return dict(visual_verdict='same', visual_confidence='.99',
                visual_reasons=['candidate_single_physical_card',
                    'geometry_same_template:matches=100,inliers=85,ratio=0.850'],
                visual_evidence_binding={**visual_asset_binding(identity,candidate,b'front',b'back',b'candidate'),
                                         'model_observation_contract':MODEL_OBSERVATION_CONTRACT,
                                         'candidate_supporting_images':[dict(url='https://images.cardhobby.com/back.jpg',sha256=hashlib.sha256(b'back-source').hexdigest())]},
                visual_match_evidence=[dict(kind=kind,target=text,candidate=text,source='images',
                    both_visible=True,target_view='front',candidate_view='front',
                    version_marker_observations=dict(target=dict(status='readable',text='REFRACTOR',view='back',source='images',image_ref='target_back'),
                        candidate=dict(status='readable',text='REFRACTOR',view='back',source='images',image_ref='candidate_support_1')) if kind=='finish' else None) for kind,text in [
                        ('layout','Two player portraits over square pixel blocks'),
                        ('finish','Silver rainbow foil over a fine square grid')]])


def test_current_positive_single_card_layout_and_finish_remain_eligible():
    identity, sale = fixture()
    result = classify(identity, sale, observed(identity,sale))
    assert result.tier in {ComparableTier.T0,ComparableTier.T1}
    assert not result.hard_conflicts


@pytest.mark.parametrize('change', ['geometry_only','old_contract','changed_back','lot'])
def test_geometry_old_observation_changed_target_and_lot_cannot_replace_paired_confirmation(change):
    identity,sale=fixture();raw=observed(identity,sale)
    if change == 'geometry_only': raw['visual_verdict']='uncertain';raw['visual_match_evidence']=[]
    elif change == 'old_contract':raw['visual_evidence_binding']['model_observation_contract']='paired-image-only-evidence-v5'
    elif change == 'changed_back':identity['image_content_hashes']['back']='c'*64
    else:raw['visual_reasons']=['candidate_multiple_physical_cards']
    assert classify(identity,sale,raw).tier == ComparableTier.REJECTED


def test_bound_target_back_cannot_be_ignored_by_front_only_marker_claims():
    identity,sale=fixture();raw=observed(identity,sale)
    markers=dict(target=dict(status='not_printed',view='front',source='images',whole_view_visible=True),
                 candidate=dict(status='not_printed',view='front',source='images',whole_view_visible=True))
    raw['visual_match_evidence'][1]['version_marker_observations']=markers
    assert classify(identity,sale,raw).tier==ComparableTier.REJECTED


def test_candidate_back_marker_claim_requires_an_uploaded_bound_back_asset():
    identity,sale=fixture();raw=observed(identity,sale)
    raw['visual_evidence_binding'].pop('candidate_supporting_images',None)
    assert classify(identity,sale,raw).tier==ComparableTier.REJECTED
