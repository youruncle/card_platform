from decimal import Decimal
import pytest

from card_platform.comparable_image_verifier import VisualDecision, normalize_model_visual_decision
from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.comparables import ComparableClassifier, ComparableInput, ComparableTier
from card_platform.domain.listing_title import is_multi_card_listing
from card_platform.domain.listing_condition import condition_rejection_reasons
from card_platform.formal_cardhobby_gate import _corroborated_same_card


@pytest.mark.parametrize(('identity', 'title'), [
    (dict(player_name='Zach LaVine', product_name='Impeccable', season_start=2016, season_end=2017,
          card_number='44', is_autograph=True, is_memorabilia=True),
     '2016-17 Panini Preferred Zach LaVine Silhouettes Autograph Relics 58/60'),
    (dict(player_name='Shaedon Sharpe', product_name='Topps Chrome Updates Basketball', season_start=2025,
          season_end=2026, card_number='106', parallel_name='Magenta', serial_total=299),
     '2025-26 Topps Chrome Update Shaedon Sharpe Aqua 062/299'),
    (dict(player_name='Kobe Bryant', product_name='Ultra', season_start=1998, season_end=1999, card_number='61'),
     '1997-98 Fleer Ultra Kobe Bryant #1'),
    (dict(player_name='Michael Jordan', product_name="Collector's Choice", season_start=1995, card_number='20'),
     "1995 Collector's Choice Michael Jordan Professor Dunk #195"),
])
def test_reviewed_wrong_product_parallel_year_and_card_number_remain_rejected(identity, title):
    result = ComparableClassifier().classify(CardIdentityResolver().resolve(identity), ComparableInput(title=title))
    assert result.tier == ComparableTier.REJECTED and result.hard_conflicts


def test_two_named_inserts_in_one_listing_are_not_a_single_card_price():
    assert is_multi_card_listing('2024-25 Panini Prizm Victor Wembanyama Fractal分形特卡 Talismen护身符特卡')


def test_model_cannot_promote_one_target_visible_inside_two_card_bundle():
    raw = VisualDecision(candidate_id='listing', verdict='same', confidence=Decimal('.99'),
        design_match='same', finish_match='same', candidate_card_count=2,
        candidate_card_count_evidence='Two distinct front designs shown side by side')
    result = normalize_model_visual_decision(raw)
    assert result.verdict != 'same'
    assert 'candidate_multiple_physical_cards' in result.reasons


def test_unknown_image_quantity_cannot_claim_a_single_card_sale():
    evidence = [dict(kind=kind, target=description, candidate=description,
                     source='images', both_visible=True, target_view='front', candidate_view='front')
                for kind, description in [('layout','Player portrait inside a tall left frame'),
                                           ('finish','Smooth silver surface with fine horizontal lines')]]
    raw = VisualDecision(candidate_id='listing', verdict='same', confidence=Decimal('.99'),
                         design_match='same', finish_match='same', match_evidence=evidence)
    assert normalize_model_visual_decision(raw).verdict == 'uncertain'


def test_single_card_quantity_survives_persisted_fact_reconstruction():
    import hashlib
    from card_platform.comparable_image_verifier import VisualCandidate, visual_asset_binding, MODEL_OBSERVATION_CONTRACT
    from card_platform.domain.finish_evidence import build_visual_match_facts
    identity = dict(player_name='', product_name='Topps Chrome', parallel_name='Base',
                    season_start=2025,season_end=2026,card_number='169',insert_name='Karate Island',
                    image_front_url='/uploads/fixture.jpg',
                    image_content_hashes={'front':hashlib.sha256(b'target').hexdigest()})
    candidate = VisualCandidate('https://example.com/1', '2025-26 Topps Chrome Karate Island #169', 'https://example.com/front.jpg')
    evidence = [dict(kind=kind, target=text, candidate=text, source='images', both_visible=True,
                     target_view='front', candidate_view='front') for kind, text in [
                        ('layout','Portrait in a tall left frame'), ('finish','Smooth silver with narrow lines')]]
    decision = normalize_model_visual_decision(VisualDecision(candidate_id=candidate.id, verdict='same',
        confidence=Decimal('.99'), design_match='same', finish_match='same',
        candidate_card_count=1, match_evidence=evidence))
    binding = {**visual_asset_binding(identity, candidate, b'target', None, b'candidate'),
               'model_observation_contract':MODEL_OBSERVATION_CONTRACT}
    raw = dict(visual_verdict=decision.verdict, visual_confidence='.99',
               visual_reasons=decision.reasons, visual_match_evidence=evidence,
               visual_evidence_binding=binding)
    facts=build_visual_match_facts(identity, candidate.id, candidate.title, candidate.image_url, raw)
    assert facts is not None
    result=ComparableClassifier().classify(CardIdentityResolver().resolve(identity),ComparableInput(
        title=candidate.title,visual_verdict='same',visual_confidence=Decimal('.99'),visual_facts=facts))
    assert 'player_conflict' not in result.hard_conflicts


@pytest.mark.parametrize('verdict', ['same', 'compatible', 'uncertain'])
def test_geometry_cannot_replace_missing_single_card_scope(monkeypatch, verdict):
    from card_platform.comparable_image_verifier import MODEL_OBSERVATION_CONTRACT
    from card_platform.ingestion.cardhobby import CardHobbySale
    identity = dict(player_name='Kobe Bryant', product_name='Topps Chrome', season_start=2025,
                    card_number='1')
    sale = CardHobbySale(item_id='123', source_url='https://cardhobby.com.cn/Market/Item/123',
        title='2025 Topps Chrome Kobe Bryant #1', image_url='https://example.com/card.jpg',
        occurred_at='2026-10-01T00:00:00Z', sale_price='10', currency='CNY',
        evidence_level='ended_auction', sale_type='auction', bid_count=2,
        match_confidence='.7', sold_status_evidence='ended with bids')
    observed = dict(visual_verdict=verdict, visual_reasons=['geometry_same_template:matches=80,inliers=61,ratio=0.762'],
        visual_evidence_binding=dict(model_observation_contract=MODEL_OBSERVATION_CONTRACT, candidate_sha256='a'*64))
    monkeypatch.setattr('card_platform.comparable_image_verifier.current_persisted_visual', lambda *_: observed)
    result = _corroborated_same_card(identity, sale, observed, ['unknown_target_finish_requires_visual_confirmation'])
    assert result is None


@pytest.mark.parametrize('title', ['Michael Jordan 1999 MVP219 边角小翻',
                                    'LeBron James Prizm Dominance 微瑕如图',
                                    'LeBron James Prizm Fearless 默认微瑕'])
def test_disclosed_condition_is_not_an_unconditional_mint_price(title):
    assert condition_rejection_reasons({}, title)


def test_seller_title_error_does_not_invent_a_visual_conflict():
    identity = dict(player_name='SpongeBob SquarePants', product_name='Topps Chrome',
                    season_start=2025, season_end=2026, card_number='S-5', insert_name='Songs')
    title = '2025 Topps Chrome SpongeBob SquarePants THE BEST DAY EVER Song Songs S-5'
    result = ComparableClassifier().classify(CardIdentityResolver().resolve(identity), ComparableInput(title=title))
    assert not result.hard_conflicts
