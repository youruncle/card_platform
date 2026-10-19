"""Public semantic controls; original private image and payload evidence is external."""
from decimal import Decimal

import pytest

from card_platform.domain.serial_evidence import unique_visible_serial
from card_platform.image_service import sanitize_identity_field_roles
from card_platform.schemas import CardRecognitionResult


@pytest.mark.parametrize(('token', 'evidence'), [
    ('9', 'FIRST APPEARANCE: Avenging Spider-Man (2011) #9'),
    ('9', '背面 FIRST APPEARANCE: Avenging Spider-Man (2011) #9；右上独立卡号49'),
    ('130', '卡套左上外部手写130；卡背独立印刷No.8'),
    ('30', 'Back external handwritten 30 on sleeve; printed card No.8'),
])
def test_comic_issue_and_external_handwriting_cannot_be_catalog_anchors(token, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number=token,
        field_evidence={'card_number': [evidence]}))
    assert result.card_number is None


def test_first_appearance_year_is_not_a_release_year():
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), season_start=2011,
        field_evidence={'season_start': ['FIRST APPEARANCE: Avenging Spider-Man (2011) #9']}))
    assert result.season_start is None


@pytest.mark.parametrize(('token', 'evidence'), [
    ('49', '右上独立卡号49；FIRST APPEARANCE: Avenging Spider-Man (2011) #9'),
    ('8', '背面No.8；卡套左上外部手写130'),
    ('86', "背面No.86；生产串码METAL/2/'97-98"),
    ('RA-JSM', '背面右上独立卡号RA-JSM；版权生产码CODE#CMP144370'),
    ('FRA-CD', '背面NO.FRA-CD；左侧机器流水66/99'),
    ('19 of 20 N', '背面套组编号19 of 20 N'),
])
def test_unrelated_numbers_preserve_real_catalog_number(token, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number=token,
        field_evidence={'card_number': [evidence]}))
    assert result.card_number == token


def test_set_sequence_cannot_be_promoted_to_a_unique_serial():
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number='19 of 20 N',
        serial_number=19, serial_total=20, is_numbered=True,
        field_evidence={'serial_number': ['背面19 of 20 N，套组编号']}))
    assert result.card_number == '19 of 20 N'
    assert (result.serial_number, result.serial_total, result.is_numbered) == (None, None, False)
    assert unique_visible_serial(['Back set position 19 of 20 N'], source_is_physical=True) is None


@pytest.mark.parametrize(('number', 'total'), [(65, 125), (77, 299), (66, 99), (33, 75)])
def test_explicit_physical_foil_serial_is_kept(number, total):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), serial_number=number, serial_total=total, is_numbered=True,
        field_evidence={'serial_number': [f'背面机器流水{number:03d}/{total}']}))
    assert (result.serial_number, result.serial_total, result.is_numbered) == (number, total, True)


@pytest.mark.parametrize(('token', 'evidence'), [
    ('9', 'FIRST APPEARANCE: Example Comic (2011) #9；背面独立印刷卡号9'),
    ('8', '外部手写8；背面独立印刷No.8'),
    ('30', '30不是外部手写，是背面独立卡号30'),
    ('9', '9不是漫画期号，是背面No.9'),
])
def test_same_value_independent_catalog_or_negated_role_is_preserved(token, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number=token,
        field_evidence={'card_number': [evidence]}))
    assert result.card_number == token


@pytest.mark.parametrize(('year', 'evidence'), [
    (2022, ['产品行2022 Upper Deck Marvel', 'FIRST APPEARANCE: Example Comic (2011) #9']),
    (2011, ['产品行2011 Upper Deck Marvel', 'FIRST APPEARANCE: Example Comic (2011) #9']),
    (2011, ['2011不是首次漫画出场年，产品行2011 Upper Deck Marvel']),
])
def test_independent_release_line_wins_over_first_appearance_reference(year, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), season_start=year,
        field_evidence={'season_start': evidence}))
    assert result.season_start == year


@pytest.mark.parametrize(('token', 'evidence'), [
    ('9', 'Printed card No.9. FIRST APPEARANCE: Example Comic (2011) #9'),
    ('8', '背面独立印刷No.8 / 外部手写8'),
    ('9', 'Printed card No.9 and FIRST APPEARANCE: Example Comic (2011) #9'),
    ('8', 'Back printed No.8 and external handwritten 8'),
    ('8', '外部手写8 / 背面独立印刷No.8'),
])
def test_independent_catalog_occurrence_survives_other_role_with_same_digits(token, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number=token,
        field_evidence={'card_number': [evidence]}))
    assert result.card_number == token


@pytest.mark.parametrize('suffix', ['为漫画期号', '是首次出场漫画'])
def test_comic_year_and_issue_bind_before_chinese_explanatory_prose(suffix):
    evidence = f'FIRST APPEARANCE: Example Comic (2011) #9{suffix}'
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number='9', season_start=2011,
        field_evidence={'card_number': [evidence], 'season_start': [evidence]}))
    assert result.card_number is None
    assert result.season_start is None


@pytest.mark.parametrize('evidence', [
    '外部手写No.8',
    '卡套外部手写卡号8',
    'No.8 is external handwritten',
])
def test_catalog_label_inside_the_same_handwritten_occurrence_does_not_override_role(evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number='8',
        field_evidence={'card_number': [evidence]}))
    assert result.card_number is None


def test_handwritten_catalog_label_without_dot_binds_the_same_occurrence():
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number='8',
        field_evidence={'card_number': ['外部手写No8']}))
    assert result.card_number is None
