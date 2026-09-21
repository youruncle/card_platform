import pytest
from card_platform.formal_cardhobby_gate import title_requires_named_parallel

@pytest.mark.parametrize('title', ['2024 Revolution 詹姆斯 中子折', '库里 宇宙折', 'Revolution 星爆折', 'Revolution Cosmic parallel', 'Revolution Galactic parallel'])
def test_unknown_target_cannot_inherit_named_non_color_parallel(title):
    assert title_requires_named_parallel({'parallel_name':None},title)

@pytest.mark.parametrize('title',['杜兰特 基础卡 拍摄彩虹反光','Revolution base','原生镭射特卡','宇宙主题插画'])
def test_generic_finish_or_theme_is_not_a_named_parallel(title):
    assert not title_requires_named_parallel({'parallel_name':None},title)


def test_explicit_surface_conflict_overrides_generic_same_verdict():
    import card_platform.comparable_image_verifier as module
    decision=module.VisualDecision(candidate_id='candidate',verdict='same',confidence='.99',reasons=['same player layout'],design_match='same',finish_match='different')
    assert module.enforce_visual_observations(decision).verdict=='different'


def test_same_design_with_unobserved_finish_cannot_be_exact_same():
    import card_platform.comparable_image_verifier as module
    decision=module.VisualDecision(candidate_id='candidate',verdict='same',confidence='.99',reasons=['same player layout'],design_match='same',finish_match='unknown')
    assert module.enforce_visual_observations(decision).verdict!='same'


def test_rainbow_reflection_with_matching_fixed_print_is_allowed():
    import card_platform.comparable_image_verifier as module
    decision=module.VisualDecision(candidate_id='candidate',verdict='same',confidence='.95',reasons=['same monochrome portrait and fixed foil structure; rainbow reflection varies'],design_match='same',finish_match='same')
    assert module.enforce_visual_observations(decision).verdict=='same'


def test_review_cannot_be_reused_with_only_same_urls():
    from card_platform.comparable_image_verifier import VisualCandidate,bound_reviewed_visual_decision
    identity={'image_front_url':'/uploads/front.jpg','image_back_url':None}
    candidate=VisualCandidate('source','title','https://images.cardhobby.com/front.jpg')
    review={'source':'human_paired_image_review','target_front_url':'/uploads/front.jpg','target_back_url':None,
            'candidate_image_url':candidate.image_url,'candidate_source_url':candidate.id,'verdict':'same','confidence':'.99','reasons':['paired review']}
    assert bound_reviewed_visual_decision(identity,candidate,review) is None


@pytest.mark.parametrize('changed',['target','back','candidate','title','identity'])
def test_same_url_review_is_invalidated_by_any_asset_or_identity_change(changed):
    from card_platform.comparable_image_verifier import VisualCandidate,bound_reviewed_visual_decision,visual_asset_binding
    identity={'image_front_url':'/uploads/front.jpg','image_back_url':'/uploads/back.jpg','card_number':'12'}
    candidate=VisualCandidate('source','title','https://images.cardhobby.com/front.jpg')
    binding=visual_asset_binding(identity,candidate,b'front',b'back',b'candidate')
    review={'source':'human_paired_image_review','target_front_url':identity['image_front_url'],'target_back_url':identity['image_back_url'],
            'candidate_image_url':candidate.image_url,'candidate_source_url':candidate.id,'verdict':'same','confidence':'.99',
            'reasons':['independently observed same printing'], 'evidence_binding':binding}
    current=visual_asset_binding({**identity,'card_number':'13'} if changed=='identity' else identity,
        VisualCandidate(candidate.id,'changed',candidate.image_url) if changed=='title' else candidate,
        b'changed' if changed=='target' else b'front', b'changed' if changed=='back' else b'back',
        b'changed' if changed=='candidate' else b'candidate')
    assert bound_reviewed_visual_decision(identity,candidate,review,current_binding=binding) is not None
    assert bound_reviewed_visual_decision(identity,candidate,review,current_binding=current) is None


def test_visual_binding_survives_discovered_evidence_serialization():
    from card_platform.valuation_worker import DiscoveredComparable
    record={'title':'card','source_url':'https://cardhobby.com.cn/Market/Item/1','occurred_at':'2026-01-01T00:00:00+08:00',
            'sale_price':'1','currency':'CNY','fx_rate_to_base':'1','match_confidence':'.95',
            'visual_evidence_binding':{'target_front_sha256':'a'*64,'candidate_sha256':'b'*64}}
    assert DiscoveredComparable.model_validate(record).model_dump()['visual_evidence_binding']==record['visual_evidence_binding']


def test_manual_review_cannot_override_its_own_explicit_finish_conflict():
    from card_platform.comparable_image_verifier import VisualCandidate,bound_reviewed_visual_decision,visual_asset_binding
    identity={'image_front_url':'/uploads/front.jpg','image_back_url':None}
    candidate=VisualCandidate('source','title','https://images.cardhobby.com/front.jpg')
    binding=visual_asset_binding(identity,candidate,b'front',None,b'candidate')
    review={'source':'human_paired_image_review','target_front_url':identity['image_front_url'],'target_back_url':None,
            'candidate_image_url':candidate.image_url,'candidate_source_url':candidate.id,'verdict':'same','confidence':'.99',
            'design_match':'same','finish_match':'different','reasons':['large stars versus fine grain'],'evidence_binding':binding}
    result=bound_reviewed_visual_decision(identity,candidate,review,current_binding=binding)
    assert result is None or result.verdict!='same'


def test_physical_redemption_state_changes_invalidate_visual_identity_binding():
    from card_platform.comparable_image_verifier import VisualCandidate,visual_asset_binding
    candidate=VisualCandidate('source','title','https://images.cardhobby.com/front.jpg')
    def binding(identity):return visual_asset_binding(identity,candidate,b'front',None,b'candidate')
    assert binding({'is_redemption':False})!=binding({'is_redemption':True})
    assert binding({'redemption_status':'unredeemed'})!=binding({'redemption_status':'redeemed'})


def test_persisted_visual_result_needs_current_target_and_identity_binding():
    from card_platform import comparable_image_verifier as module
    identity={'card_number':'31','image_content_hashes':{'front':module.content_hash(b'front'),'back':None}}
    candidate=module.VisualCandidate('source','title','image')
    raw={'visual_verdict':'same','visual_confidence':'.95','visual_reasons':['old same']}
    assert module.current_persisted_visual(identity,candidate,raw)['visual_verdict'] is None
    raw['visual_evidence_binding']=module.visual_asset_binding(identity,candidate,b'front',None,b'candidate')
    assert module.current_persisted_visual(identity,candidate,raw)['visual_verdict']=='same'
    assert module.current_persisted_visual({**identity,'card_number':'32'},candidate,raw)['visual_verdict'] is None
    assert module.current_persisted_visual({**identity,'image_content_hashes':{'front':module.content_hash(b'changed'),'back':None}},candidate,raw)['visual_verdict'] is None


def test_stale_negative_review_cannot_become_unreviewed_text_only_price():
    from card_platform.comparable_image_verifier import current_persisted_visual,VisualCandidate
    from card_platform.domain.comparables import Classification,ComparableTier
    from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
    from decimal import Decimal
    visual=current_persisted_visual({},VisualCandidate('source','title','image'),
        {'visual_verdict':'different','visual_confidence':'.99','visual_reasons':['different pose']})
    assert visual['visual_verdict'] is None
    assert visual['visual_review_required'] is True
    judged=enforce_formal_cardhobby_identity({},None,
        Classification(ComparableTier.T0,Decimal('.99'),Decimal('.7')),
        visual_review_required=visual['visual_review_required'])
    assert judged.tier==ComparableTier.REJECTED
    assert 'stale_visual_requires_reverification' in judged.hard_conflicts


@pytest.mark.parametrize('verdict', ['same', 'compatible', 'uncertain'])
def test_stale_positive_or_uncertain_review_cannot_fall_back_to_text_price(verdict):
    from card_platform.comparable_image_verifier import current_persisted_visual, VisualCandidate
    visual = current_persisted_visual({}, VisualCandidate('source', 'title', 'image'),
                                      {'visual_verdict': verdict, 'visual_confidence': '.95'})
    assert visual['visual_verdict'] is None
    assert visual['visual_review_required'] is True


def test_first_download_failure_is_unavailable_not_a_stale_historical_review():
    from card_platform.comparable_image_verifier import current_persisted_visual, VisualCandidate
    from scripts.audit_cardhobby_valuation import select_frozen_visual
    failed = {'visual_verdict': 'uncertain', 'visual_confidence': '0',
              'visual_reasons': ['candidate_image_download_failed']}
    current = current_persisted_visual({}, VisualCandidate('source', 'title', 'image'), failed)
    assert current.get('visual_review_required') is not True
    assert current['visual_reasons'] == ['candidate_image_download_failed']
    assert select_frozen_visual({'visual_verdict': None}, failed).get('visual_review_required') is not True
