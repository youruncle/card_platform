"""Identity gates against public listing descriptions; no private order data."""
from decimal import Decimal

import pytest

from card_platform.knowledge import KnowledgeResolver
from card_platform.purchase_cost import score_match
from card_platform.purchase_cost_inference import apply_inference
from card_platform.purchase_cost_search import build_purchase_cost_search_plan
from card_platform.image_service import sanitize_identity_field_roles
from card_platform.schemas import CardRecognitionResult


def roster_knowledge():
    return KnowledgeResolver({'entities': [
        {'id': 'sc', 'entity_type': 'player', 'canonical_name': 'Stephen Curry', 'aliases': ['斯蒂芬库里']},
        {'id': 'lj', 'entity_type': 'player', 'canonical_name': 'LeBron James', 'aliases': ['Lebron James', '勒布朗詹姆斯']},
        {'id': 'kd', 'entity_type': 'player', 'canonical_name': 'Kevin Durant', 'aliases': ['杜兰特']},
        {'id': 'jh', 'entity_type': 'player', 'canonical_name': 'James Harden', 'aliases': ['哈登']},
    ]})


def now_card(**kwargs):
    return dict(player_name='Darius Acuff Jr.', brand_name='Topps', product_name='Topps Now',
                season_start=2026, is_autograph=False, is_memorabilia=False,
                image_front_url='/uploads/front.jpg', image_back_url='/uploads/back.jpg',
                image_content_hashes={'front': 'a' * 64, 'back': 'e' * 64}, **kwargs)


def test_real_three_subject_card_matches_without_treating_subjects_as_extras():
    card = {**now_card(), 'player_name': 'Curry / James / Durant', 'season_start': 2024}
    line = {'title': 'Lebron James 勒布朗詹姆斯 斯蒂芬库里 杜兰特 2024 Topps Now 奥运会 三人 三巨头 Base 美国队 詹杜库/瑕疵见详图/15952壹ho/b59'}
    result = score_match(card, line, roster_knowledge())
    assert result['automatic'], result
    assert not result['conflicts'] and not result['missing']


@pytest.mark.parametrize('players', ['Stephen Curry', 'Stephen Curry LeBron James',
                                    'Stephen Curry LeBron James Kevin Durant James Harden'])
def test_incomplete_roster_or_additional_subject_still_blocks(players):
    card = {**now_card(), 'player_name': 'Curry / James / Durant', 'season_start': 2024}
    result = score_match(card, {'title': players + ' 2024 Topps Now Base'}, roster_knowledge())
    assert not result['automatic']


def test_seller_stock_suffix_is_not_a_serial_number():
    result = score_match(now_card(), {'title': '2026 Topps Now Darius Acuff Jr Base/瑕疵见详图/16086乐邦总/41'})
    assert result['automatic'], result


def test_real_serial_before_stock_suffix_remains_a_conflict():
    result = score_match({**now_card(), 'serial_number': 7, 'serial_total': 50},
                        {'title': '2026 Topps Now Darius Acuff Jr 8/50编/瑕疵见详图/16086乐邦总/41'})
    assert '限编分子冲突' in result['conflicts'] and not result['automatic']


def test_specific_product_fallback_is_available_for_mislabeled_order():
    plan = build_purchase_cost_search_plan(now_card())
    assert plan.get('fallback_keywords') == ['Topps Now']
    assert 'Darryn Peterson' not in plan['keywords']


def test_missing_product_never_creates_unbounded_order_fallback():
    plan = build_purchase_cost_search_plan({'player_name': 'Darius Acuff Jr.'})
    assert not plan.get('fallback_keywords')


def image_correction():
    result = dict(source='cost_visual_inference', verdict='same', confidence='.96',
                design_match='same', finish_match='same', conflict_evidence=[],
                reasons=['geometry_same_template:matches=180,inliers=150,ratio=0.833'],
                evidence_binding={'contract': 'paired-image-observations-v1',
                                  'target_front_sha256': 'a' * 64, 'target_back_sha256': 'e' * 64, 'candidate_sha256': 'b' * 64,
                                  'candidate_title_sha256': 'c' * 64, 'identity_sha256': '89753bb25bed95ccd340da0281a6e90817a234f6300208a2303dff31ee85400b'},
                match_evidence=[dict(kind=kind, source='images', both_visible=True,
                                     target_view='front', candidate_view='front',
                                     target='Darius Acuff printed name, purple border and Kings hat',
                                     candidate='Darius Acuff printed name, purple border and Kings hat')
                                for kind in ('layout', 'finish')])
    view = dict(layout='Topps Now portrait with Kings emblem at lower left',
                outer_frame_fixed_color='Purple perimeter with silver angular frame',
                fixed_surface_pattern='Plain dark photographic surface', pattern_location='Outside the central photograph',
                lighting_or_reflection='Small highlights on upper right border', visible=True, view='front',
                artwork_subjects=[dict(position='center',visible=True, body_pose='Man in dark suit facing camera',
                                      arms_hands_pose='Both hands held at waist forming downward triangle',
                                      held_object_position='No basketball or other handheld object',
                                      distinct_image_details='Red tinted glasses and Kings logo on black cap')])
    result['independent_confirmation'] = dict(contract='independent-front-finish-v2',model='fixture-vision',
        evidence_binding=result['evidence_binding'],observation=dict(target=view,candidate=dict(view),
        layout_match='same',fixed_color_match='same',texture_match='same',artwork_match='same',
        verdict='same',confidence='.96',reason='Corresponding complete front photographs and surfaces'))
    return result


def test_server_bound_images_can_correct_seller_player_and_season_typo():
    match = score_match(now_card(), {'title': 'Darryn Peterson 皮特森 2025 Topps Now Base'})
    result = apply_inference(now_card(), match, image_correction())
    assert result['automatic'] and result['recalled'], result
    assert result['match_method'] == 'image_title_correction'
    assert result['seller_title_conflicts'] == ['年份冲突']
    assert '球员' in result['seller_title_missing']


@pytest.mark.parametrize('change', [
    {'source': 'client'}, {'evidence_binding': {}}, {'match_evidence': []},
    {'confidence': '.89'}, {'verdict': 'compatible'},
    {'reasons': ['geometry_same_template:matches=8,inliers=7,ratio=0.875']},
    {'finish_match': 'unknown'}, {'conflict_evidence': [{'kind': 'printed_color'}]},
])
def test_similarity_or_unbound_decision_cannot_override_wrong_title(change):
    match = score_match(now_card(), {'title': 'Darryn Peterson 2025-26 Topps Now Base'})
    assert not apply_inference(now_card(), match, {**image_correction(), **change})['automatic']


@pytest.mark.parametrize('field,value', [('serial_total', 50), ('parallel_name', 'Gold'),
                                       ('grade_company', 'PSA'), ('is_numbered', True), ('is_autograph', True),
                                       ('is_memorabilia', True)])
def test_title_correction_never_overrides_specific_physical_variant(field, value):
    card = {**now_card(), field: value}
    result = apply_inference(card, score_match(card, {'title': 'Darryn Peterson 2025-26 Topps Now Base'}), image_correction())
    assert not result['automatic']


def test_topps_now_calendar_marker_cannot_be_a_catalog_number():
    card = CardRecognitionResult(player_name='Darius Acuff Jr.', brand_name='Topps', product_name='Topps Now',
                                 season_start=2026, card_number='JUN 23', confidence=Decimal('.90'))
    result = sanitize_identity_field_roles(card)
    assert result.card_number is None
    assert result.recognition_status == 'research_required'


def test_alphanumeric_topps_now_catalog_number_is_preserved():
    card = CardRecognitionResult(player_name='Darius Acuff Jr.', brand_name='Topps', product_name='Topps Now',
                                 season_start=2026, card_number='DACU', confidence=Decimal('.90'))
    assert sanitize_identity_field_roles(card).card_number == 'DACU'

def test_two_full_roster_subjects_can_anchor_third_reviewed_surname():
    # Published catalogs intentionally keep the bare surname out of identity aliases.
    snapshot = {'entities': [
        {'id':'sc','entity_type':'player','canonical_name':'Stephen Curry','aliases':['斯蒂芬库里']},
        {'id':'lj','entity_type':'player','canonical_name':'LeBron James','aliases':['勒布朗詹姆斯']},
        {'id':'kd','entity_type':'player','canonical_name':'Kevin Durant','aliases':['凯文杜兰特']},
        {'id':'jh','entity_type':'player','canonical_name':'James Harden','aliases':['哈登']},
    ]}
    card = {**now_card(),'player_name':'Curry / James / Durant','season_start':2024}
    match = score_match(card, {'title':'2024 Topps Now Stephen Curry LeBron James 杜兰特 Base'},KnowledgeResolver(snapshot))
    assert match['automatic'], match
    other = score_match(card, {'title':'2024 Topps Now Stephen Curry James Harden Kevin Durant Base'},KnowledgeResolver(snapshot))
    assert not other['automatic'] and other['conflicts']

def test_topps_now_numeric_month_day_is_not_a_catalog_number():
    result = sanitize_identity_field_roles(CardRecognitionResult(player_name='Curry / James / Durant',
        brand_name='Topps',product_name='Topps Now',season_start=2024,card_number='8.10',confidence=Decimal('.90')))
    assert result.card_number is None
    assert 'card_number_is_calendar_date_not_catalog_identifier' in result.recognition_quality_issues


def test_image_title_correction_is_invalid_after_target_identity_or_asset_change():
    original = now_card()
    match = score_match(original, {'title':'Darryn Peterson 2025 Topps Now Base'})
    for changed in ({**original,'season_start':2027},
                    {**original,'image_content_hashes':{'front':'f'*64,'back':'e'*64}}):
        assert not apply_inference(changed,match,image_correction())['automatic']

@pytest.mark.parametrize('target', [now_card(), {**now_card(),'serial_number':7,'serial_total':50}])
def test_pure_numeric_fraction_after_condition_is_never_stripped_as_stock_reference(target):
    match = score_match(target, {'title':'2026 Topps Now Darius Acuff Jr Base/瑕疵见详图/8/50'})
    assert not match['automatic']
    assert '限编' in match['missing'] or '限编分子冲突' in match['conflicts']
