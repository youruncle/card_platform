import pytest
from card_platform.catalog_evidence import normalize_catalog_evidence,complete_visible_roster
from card_platform.schemas import CardRecognitionResult


@pytest.mark.parametrize('text',['Rewind: first rookie to win this award after a long season','Skills: Can score handle pass and defend at either forward spot'])
def test_biography_is_not_an_insert(text):
    before=CardRecognitionResult(confidence='0.8',insert_name=text,product_name='Example',season_start=2005,field_evidence={'insert_name':['physical back prose: '+text]})
    after=normalize_catalog_evidence(before)
    assert after.insert_name is None
    assert after.product_name==before.product_name and after.season_start==before.season_start
    assert set(before.field_evidence['insert_name']).issubset(after.field_evidence['insert_name'])
    assert any(text in evidence for evidence in after.field_evidence['insert_name'])


def test_cleared_biography_is_retained_even_without_prior_evidence():
    text = 'Rewind: first rookie to win this award after a long season'
    result = normalize_catalog_evidence(CardRecognitionResult(confidence='0.8', insert_name=text))
    assert result.field_evidence['insert_name'] == ['原始识别正文（非特卡名）：' + text]


@pytest.mark.parametrize('name',['Rewind','Next Level','Career Highlights','Draft Picks & Prospects','Rookie Patch Autographs'])
def test_short_real_catalog_names_are_not_prose(name):
    before=CardRecognitionResult(confidence='0.8',insert_name=name)
    assert normalize_catalog_evidence(before)==before


def test_all_front_subjects_retained():
    assert complete_visible_roster(['Alpha Smith',' Beta Jones ','Gamma Brown','Alpha Smith'])=='Alpha Smith, Beta Jones, Gamma Brown'


@pytest.mark.parametrize('value',['TOP-LEFT','upper right','右上角'])
def test_position_instruction_is_not_a_card_number(value):
    result=normalize_catalog_evidence(CardRecognitionResult(confidence='.8',card_number=value))
    assert result.card_number is None
    assert value in result.field_evidence['card_number'][0]


def test_real_alphanumeric_card_number_is_unchanged():
    result=CardRecognitionResult(confidence='.8',card_number='CG-10')
    assert normalize_catalog_evidence(result)==result
