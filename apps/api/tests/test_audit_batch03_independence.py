"""Batch 03 independence boundaries; semantic fixtures, no private audit files."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from card_platform.domain.comparables import ComparableTier
from card_platform.domain.sale_independence import (
    independence_confidence_cap,
    unique_physical_rows,
)
from card_platform.domain.valuation_quality import ShadowSale, ShadowValuationEstimator
from card_platform.valuation_worker import AutomatedValuationWorker, deduplicate_market_rows


def market_row(key, *, title='2025 Chrome #12', digest=None, seller=None, consignment=None):
    raw = {}
    if digest:
        raw = dict(
            visual_verdict='same', visual_confidence='.99',
            visual_match_evidence=[dict(kind='layout', target='portrait', candidate='portrait')],
            visual_evidence_binding={'candidate_sha256': digest},
        )
    return dict(
        id=key, source_code='cardhobby', external_id=f'item:{key}', listing_title=title,
        source_url=f'https://cardhobby.com.cn/Market/Item/{key}',
        occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        source_reliability=Decimal('.7'), raw_payload=raw,
        provider_raw_payload={'seller_id': seller, 'consignment_code': consignment},
    )


def test_shared_verified_photo_is_a_reuse_risk_not_a_physical_identity_claim():
    chosen, dropped = unique_physical_rows([
        market_row('1', digest='a' * 64), market_row('2', digest='a' * 64),
    ])

    assert [row['id'] for row in chosen] == ['1']
    assert dropped[0]['reason_code'] == 'same_card_photo_reuse_risk'
    assert '独立性未证' in dropped[0]['reason']


def test_same_design_with_different_photos_does_not_prove_same_owned_copy():
    chosen, dropped = unique_physical_rows([
        market_row('1', digest='a' * 64), market_row('2', digest='b' * 64),
    ])

    assert [row['id'] for row in chosen] == ['1', '2']
    assert dropped == []


def test_shared_terms_image_does_not_bridge_different_card_photo_clusters():
    rows = [market_row('1', digest='a' * 64), market_row('2', digest='b' * 64)]
    for row in rows:
        row['raw_payload']['gallery_image_hashes'] = ['c' * 64]
        row['raw_payload']['image_role'] = 'seller_terms'

    chosen, dropped = unique_physical_rows(rows)

    assert len(chosen) == 2
    assert dropped == []


def test_unbound_legacy_same_flag_does_not_establish_a_photo_cluster():
    rows = [market_row('1'), market_row('2')]
    for row in rows:
        row['raw_payload'] = dict(visual_verdict='same', visual_confidence='.99')

    chosen, dropped = unique_physical_rows(rows)

    assert len(chosen) == 2
    assert dropped == []


def test_owned_full_serial_history_does_not_remove_a_different_numerator():
    chosen, dropped = unique_physical_rows([
        market_row('1', title='Gold serial 035/050'),
        market_row('2', title='Gold serial 004/050'),
    ], identity={'serial_number': 35, 'serial_total': 50})

    assert [row['id'] for row in chosen] == ['2']
    assert [(row['dropped_id'], row['reason_code']) for row in dropped] == [
        ('1', 'owned_copy_history'),
    ]


def test_three_urls_with_another_complete_serial_count_as_one_other_copy():
    chosen, dropped = unique_physical_rows([
        market_row(str(i), title='Gold stamped 023/025', seller='public-seller')
        for i in range(1, 4)
    ], identity={'serial_number': 22, 'serial_total': 25})

    assert [row['id'] for row in chosen] == ['1']
    assert [row['reason_code'] for row in dropped] == [
        'same_physical_card_serial', 'same_physical_card_serial',
    ]


def test_unknown_owned_numerator_is_not_invented_from_a_candidate_serial():
    chosen, dropped = unique_physical_rows([
        market_row('1', title='Blue serial 081/150'),
    ], identity={'serial_number': None, 'serial_total': 150})

    assert [row['id'] for row in chosen] == ['1']
    assert dropped == []
    # Identity/condition/independence admission still belongs to the caller.
    # This result does not certify that an unresolved held copy is distinct.


@pytest.mark.parametrize('title', ['Gold /50', 'Gold 50编', 'Set card 1 of 50'])
def test_total_or_set_position_without_full_copy_serial_does_not_claim_owned_history(title):
    chosen, dropped = unique_physical_rows([
        market_row('1', title=title), market_row('2', title=title),
    ], identity={'serial_number': 35, 'serial_total': 50})

    assert len(chosen) == 2
    assert dropped == []


@pytest.mark.parametrize(('first_seller', 'second_seller', 'expected_count'), [
    ('seller-one', 'seller-one', 1),
    ('seller-one', 'seller-two', 2),
    (None, None, 2),
])
def test_consignment_codes_only_link_records_with_the_same_public_seller(
    first_seller, second_seller, expected_count,
):
    chosen, dropped = unique_physical_rows([
        market_row('1', seller=first_seller, consignment='explicit-code'),
        market_row('2', seller=second_seller, consignment='explicit-code'),
    ])

    assert len(chosen) == expected_count
    assert len(dropped) == 2 - expected_count


@pytest.mark.parametrize('external_id', ['item:123', 'cardhobby:item:123'])
def test_one_source_sale_shared_by_two_held_cards_does_not_double_the_market_sample(external_id):
    first, second = market_row('123'), market_row('123')
    second['id'] = 'separate-held-card-market-row'
    second['external_id'] = external_id

    chosen, dropped = deduplicate_market_rows([first, second])

    assert len(chosen) == 1
    assert len(dropped) == 1
    assert dropped[0]['reason'] == 'same underlying marketplace sale'


@pytest.mark.parametrize(('sellers', 'age', 'expected_cap'), [
    (['one', 'one', 'one'], 1, Decimal('.45')),
    ([None, None, None], 1, Decimal('.65')),
    (['one', 'two', 'three'], 181, Decimal('.45')),
    (['one', 'two', 'three'], 1, Decimal('.90')),
])
def test_record_count_does_not_replace_recent_independent_seller_coverage(sellers, age, expected_cap):
    sales = [ShadowSale(str(i), 'cardhobby', Decimal('10'), Decimal('0'), age,
                        ComparableTier.T0, Decimal('1'), Decimal('.7'), 'ended_auction',
                        seller_key=seller) for i, seller in enumerate(sellers)]

    assert independence_confidence_cap(sales) == expected_cap


def test_disclosed_t2_proxy_cannot_supply_the_current_same_card_price():
    sale = ShadowSale('proxy', 'cardhobby', Decimal('10'), Decimal('0'), 1,
                      ComparableTier.T2, Decimal('.7'), Decimal('.7'), 'ended_auction')
    identity = dict(player_name='Jason Kidd', product_name='National Treasures',
                    season_start=2023, card_number='TTS-JKD', insert_name='Timeless Talents')

    estimate = ShadowValuationEstimator().estimate([sale], identity, require_same_card=True)

    assert estimate.fair is None
    assert estimate.used_ids == ()
    assert 'formal_price_requires_same_card_and_grade' in estimate.rejected[0]['reasons']


@pytest.mark.parametrize(('stored_tier', 'stored_verdict'), [
    ('T2', None), ('T2', 'same'), ('T2', 'uncertain'), ('T1', 'uncertain'),
])
def test_legacy_proxy_and_explicit_same_need_their_own_current_image_evidence(
    stored_tier, stored_verdict,
):
    identity = dict(player_name='Jason Kidd', product_name='National Treasures',
                    season_start=2023, season_end=2024, card_number='TTS-JKD',
                    insert_name='Timeless Talents', parallel_name='Base',
                    image_front_url='/uploads/semantic-front.jpg',
                    image_back_url='/uploads/semantic-back.jpg',
                    image_content_hashes={'front': 'a' * 64, 'back': 'b' * 64})
    row = market_row('1', title='2023-24 Panini National Treasures Jason Kidd TTS-JKD')
    row['comparable_tier'] = stored_tier
    row['raw_payload'] = dict(image_url='https://images.cardhobby.com/semantic.jpg',
                              visual_verdict=stored_verdict, visual_confidence='.95')
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = None
    worker.image_verifier = None

    visual, facts = worker._current_visual_evidence(identity, row)
    classification = worker._classify_pricing_source(identity, row, visual, facts)
    from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
    provider = dict(item_id='1', source_url=row['source_url'], title=row['listing_title'],
                    image_url=row['raw_payload']['image_url'], occurred_at='2026-10-01T00:00:00Z',
                    sale_price='10', bid_count=2, sale_type='auction',
                    evidence_level='ended_auction', sold_status_evidence='ended with bids',
                    match_confidence='.7')
    final = enforce_formal_cardhobby_identity(identity, provider, classification,
        visual_verdict=visual.get('visual_verdict'), visual_facts=facts,
        visual_observation=visual)

    assert facts is None
    assert final.tier == ComparableTier.REJECTED
    assert 'current_paired_card_image_confirmation_required' in final.hard_conflicts
