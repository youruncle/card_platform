"""Batch 02 semantic observations; no private inventory or market captures."""
from decimal import Decimal
import pytest
from card_platform.image_service import sanitize_identity_field_roles
from card_platform.schemas import CardRecognitionResult


@pytest.mark.parametrize(('token', 'evidence'), [
    ('132874', '132874是底部生产码；背面独立卡号BH-9'),
    ('194-27/H', '背面制造串码194-27/H；ROW1 SEAT45'),
    ('2', '厂印串码2；背面卡号86'),
    ('29', '29是壳贴号码；背面卡号127'),
    ('4.20.26', '背面DPOY；4.20.26为事件日期'),
])
def test_explicit_non_catalog_role_stays_out_of_card_number(token, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number=token,
        field_evidence={'card_number': [evidence]}))
    assert result.card_number is None


@pytest.mark.parametrize(('token', 'evidence'), [
    ('BH-9', '背面卡号BH-9；132874是生产码'),
    ('45', 'SEAT45为卡号；制造串码194-27/H'),
    ('86', '背面No.86；厂印串码2'),
    ('127', '背面卡号127；29是壳贴号码'),
    ('DPOY', '卡号DPOY；4.20.26为事件日期'),
    ('132874', '132874不是生产码，是实际卡号'),
    ('29', '29不是壳贴号码，是卡背No.29'),
])
def test_unrelated_or_negated_roles_preserve_actual_catalog_token(token, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal('.95'), card_number=token,
        field_evidence={'card_number': [evidence]}))
    assert result.card_number == token
