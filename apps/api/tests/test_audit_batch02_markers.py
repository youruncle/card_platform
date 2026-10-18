from card_platform.comparable_image_verifier import VisualDecision, normalize_model_visual_decision
import pytest


def label(text=None, status='readable', view='back'):
    return dict(text=text,status=status,view=view,source='images',whole_view_visible=True,image_ref=('target_back' if view=='back' else 'target_front'))


def decision(markers=None, candidate_layout='Two portraits over a purple word mark'):
    if markers:
        markers['candidate']['image_ref']='candidate_support_1' if markers['candidate']['view']=='back' else 'candidate_front'
    binding=dict(target_front_sha256='a'*64,target_back_sha256='b'*64 if markers and markers['target']['view']=='back' else None,candidate_sha256='c'*64,candidate_supporting_images=[dict(url='https://images.cardhobby.com/back.jpg',sha256='d'*64)])
    return VisualDecision(evidence_binding=binding,candidate_id='source',verdict='same',confidence='.98',
        design_match='same',finish_match='same',candidate_card_count=1,
        match_evidence=[dict(kind='layout',target='Two portraits over a purple word mark',
            candidate=candidate_layout,source='images',both_visible=True,target_view='front',candidate_view='front'),
        dict(kind='finish',target='Smooth silver rainbow foil with purple printed letters',
            candidate='Smooth silver rainbow foil with purple printed letters',source='images',
            both_visible=True,target_view='front',candidate_view='front',version_marker_observations=markers)])


@pytest.mark.parametrize('candidate', [label(status='not_visible',view='unknown'),
                                      label(status='not_printed',view='front'),
                                      label('PRIZM')])
def test_target_printed_version_cannot_be_supplied_by_unseen_or_different_candidate(candidate):
    assert normalize_model_visual_decision(decision(dict(target=label('REFRACTOR'),candidate=candidate))).verdict=='uncertain'


def test_missing_marker_observations_cannot_claim_same():
    assert normalize_model_visual_decision(decision()).verdict=='uncertain'


@pytest.mark.parametrize('text', ['同上布局完全一致','同目标','与目标一致','Same as the target'])
def test_referential_description_is_not_an_independent_card_observation(text):
    markers=dict(target=label('REFRACTOR'),candidate=label('REFRACTOR'))
    assert normalize_model_visual_decision(decision(markers,text)).verdict=='uncertain'


@pytest.mark.parametrize('markers', [dict(target=label('REFRACTOR'),candidate=label('refractor')),
    dict(target=label(status='not_printed',view='front'),candidate=label(status='not_printed',view='front'))])
def test_observed_matching_marker_or_complete_unmarked_views_preserve_match(markers):
    assert normalize_model_visual_decision(decision(markers)).verdict=='same'
