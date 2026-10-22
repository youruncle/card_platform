"""Semantic extracts from batch 01; no inventory IDs, images or account data."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from card_platform.domain.card_identity import CardIdentityResolver, is_placeholder_identity_value
from card_platform.domain.serial_evidence import unique_visible_serial
from card_platform.image_service import sanitize_identity_field_roles
from card_platform.schemas import CardRecognitionResult
from card_platform.ingestion.cardhobby import CardHobbyProvider, CardHobbySale, assess_cardhobby_candidate
from card_platform.valuation_worker import cached_candidate_needs_image_review, DiscoveredComparable
from card_platform.domain.comparables import ComparableClassifier, ComparableInput


@pytest.mark.parametrize('value', ['无法识别球员', '未知球员', '未识别产品', 'TOPRIGHT', 'TOPLEFT'])
def test_placeholder_and_layout_tokens_do_not_become_facts(value):
    if value.startswith('TOP'):
        assert CardIdentityResolver().resolve({'card_number': value}).card_number is None
    else:
        assert is_placeholder_identity_value(value)


@pytest.mark.parametrize(('number', 'evidence'), [
    ('89', '卡套外贴纸89，不是卡号'),
    ('4', '外部价格贴纸4；卡背No41'),
    ('CMP12345678', '背面版权生产码CMP12345678'),
    ('19', '正面球衣号19；背面独立RR-5'),
])
def test_non_catalog_roles_are_removed_without_guessing_replacement(number, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal(".95"),         card_number=number, field_evidence={'card_number': [evidence]}))
    assert result.card_number is None


def test_set_position_does_not_create_a_machine_serial_or_print_run():
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal(".95"),         serial_number=4, serial_total=10, is_numbered=True,
        field_evidence={'serial_number': ['背面4 of 10，套组内第4张，共10张，并非机器限编']}))
    assert result.serial_number is None
    assert result.serial_total is None
    assert result.is_numbered is False
    assert unique_visible_serial(['Back printed 4 of 10, set position within ten cards'],
                                 source_is_physical=True) is None


@pytest.mark.parametrize(('number', 'total'), [(33, 75), (227, 275), (20, 20)])
def test_real_foil_serial_stamps_survive(number, total):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal(".95"),         serial_number=number, serial_total=total, is_numbered=True,
        card_number='FAN-16', field_evidence={'serial_number': [f'foil serial stamp {number}/{total}']}))
    assert (result.serial_number, result.serial_total, result.card_number) == (number, total, 'FAN-16')


def test_print_quantity_is_not_a_unique_copy_number():
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal(".95"),         serial_number=1, serial_total=11941, is_numbered=True,
        field_evidence={'serial_number': ['背面1 of 11941，印量说明，每张同样印字；不是逐张机器流水']}))
    assert result.serial_number is None
    assert result.serial_total is None
    assert result.is_numbered is False


@pytest.mark.parametrize('text', ['机器流水33/75，不是套组序号',
                                 'front foil serial stamp 33/75; back set position 4 of 10'])
def test_unrelated_or_negated_set_position_does_not_erase_real_serial(text):
    result = sanitize_identity_field_roles(CardRecognitionResult(confidence=Decimal('.95'),
        serial_number=33, serial_total=75, is_numbered=True,
        field_evidence={'serial_number':[text]}))
    assert (result.serial_number, result.serial_total) == (33,75)


def test_scene_query_and_discovery_use_work_anchors_without_inventing_a_player():
    identity = dict(player_name='无法识别球员', brand_name='Topps', product_name='Topps Chrome',
                    season_start=2025, season_end=2026, insert_name='Karate Island', card_number='169')
    plan = CardHobbyProvider.build_query_plan(identity)
    assert plan and all('无法识别' not in query for _, query in plan)
    assert any('Karate Island' in query and '169' in query for _, query in plan)
    sale = CardHobbySale(item_id='123', source_url='https://www.cardhobby.com.cn/market/item/123',
        title='2025 Topps Chrome Karate Island #169', sale_price=Decimal(10), currency='CNY',
        occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc), bid_count=2,
        evidence_level='ended_auction', sale_type='auction', sold_status_evidence='ended with 2 bids', match_confidence=Decimal('.7'), image_url='https://example.com/card.jpg')
    _, reasons, tier = assess_cardhobby_candidate(identity, sale)
    assert '标题未命中目标球员' not in reasons
    assert tier == 'image_review_candidate'
    classification = ComparableClassifier().classify(CardIdentityResolver().resolve(identity),
                                                     ComparableInput(title=sale.title, source_code='cardhobby'))
    comp = DiscoveredComparable(title=sale.title, source_url=sale.source_url, image_url=sale.image_url,
        occurred_at=sale.occurred_at, sale_price=sale.sale_price, currency='CNY',
        match_confidence=Decimal('.45'), fx_rate_to_base=Decimal(1), source_code='cardhobby', provider_payload=sale.model_dump(mode='json'))
    assert cached_candidate_needs_image_review(identity, comp, classification.hard_conflicts, tier)


def test_missing_sports_player_does_not_bypass_identity_gate():
    identity = dict(player_name='无法识别球员', product_name='Prizm', season_start=2024, card_number='1')
    resolved = CardIdentityResolver().resolve(identity)
    assert resolved.player_name == ''
