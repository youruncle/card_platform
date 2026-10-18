from datetime import datetime, timezone
from decimal import Decimal

from card_platform.domain.sale_independence import unique_physical_rows as deduplicate_market_rows


def row(key, **payload):
    return dict(id=key, external_id=key, source_code='cardhobby',
                occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                listing_title='2025 Topps Chrome #12', raw_payload=payload,
                source_reliability=Decimal('.7'))


def test_identical_verified_card_face_is_one_physical_card_even_after_relisting():
    face = dict(visual_verdict='same', visual_confidence='.99',
                visual_match_evidence=[dict(field='card_number', target='12', candidate='12')],
                visual_evidence_binding={'candidate_sha256': 'a' * 64})
    chosen, duplicate = deduplicate_market_rows([row('first', **face), row('relisted', **face)])
    assert len(chosen) == 1
    assert duplicate[0]['reason_code'] == 'same_physical_card_image'


def test_shared_terms_gallery_image_does_not_collapse_distinct_card_faces():
    shared_terms = dict(gallery_image_hashes=['b' * 64], image_role='seller_terms')
    chosen, duplicate = deduplicate_market_rows([row('one', **shared_terms), row('two', **shared_terms)])
    assert len(chosen) == 2 and not duplicate


def test_full_stamped_serial_identifies_one_copy_across_sellers():
    first, second = row('one'), row('two')
    first['listing_title'] = '2025 Topps Chrome #12 Gold 097/199'
    second['listing_title'] = '2025 Topps Chrome #12 Gold 97/199'
    chosen, duplicate = deduplicate_market_rows([first, second])
    assert len(chosen) == 1
    assert duplicate[0]['reason_code'] == 'same_physical_card_serial'


def test_common_total_is_not_the_same_copy():
    first, second = row('one'), row('two')
    first['listing_title'] = '2025 Topps Chrome #12 Gold 97/199'
    second['listing_title'] = '2025 Topps Chrome #12 Gold 98/199'
    assert len(deduplicate_market_rows([first, second])[0]) == 2


def test_owned_full_serial_is_history_only():
    candidate = row('history')
    candidate['listing_title'] = '2025 Topps Chrome #12 Gold 097/199'
    chosen, duplicate = deduplicate_market_rows([candidate], identity={'serial_number': 97, 'serial_total': 199})
    assert chosen == []
    assert duplicate[0]['reason_code'] == 'owned_copy_history'


def test_single_or_stale_exact_sale_does_not_receive_high_confidence():
    from card_platform.domain.comparables import Classification, ComparableTier
    from card_platform.domain.hierarchical_estimator import ClassifiedSale, HierarchicalEstimator
    c = Classification(ComparableTier.T0, Decimal('1'), Decimal('.95'))
    assert HierarchicalEstimator().estimate([
        ClassifiedSale('one', Decimal(10), Decimal(0), 1, c)]).confidence <= Decimal('.45')
    assert HierarchicalEstimator().estimate([
        ClassifiedSale(str(i), Decimal(10), Decimal(0), 400, c) for i in range(4)
    ]).confidence <= Decimal('.45')


def test_multiple_records_from_one_seller_are_not_high_confidence():
    from card_platform.domain.comparables import Classification, ComparableTier
    from card_platform.domain.hierarchical_estimator import ClassifiedSale, HierarchicalEstimator
    c = Classification(ComparableTier.T0, Decimal('1'), Decimal('.95'))
    assert HierarchicalEstimator().estimate([
        ClassifiedSale(str(i), Decimal(10), Decimal(0), 1, c, seller_key='seller-one')
        for i in range(3)]).confidence <= Decimal('.45')


def test_malformed_old_visual_confidence_does_not_crash_or_merge():
    payload = dict(visual_verdict='same', visual_confidence='unknown',
                   visual_evidence_binding={'candidate_sha256':'a'*64}, visual_match_evidence=[{}])
    assert len(deduplicate_market_rows([row('one', **payload), row('two', **payload)])[0]) == 2


def test_bridge_evidence_merges_the_whole_physical_copy_cluster():
    face = dict(visual_verdict='same', visual_confidence='.99', visual_match_evidence=[{}],
                visual_evidence_binding={'candidate_sha256':'a'*64})
    first, second, bridge = row('first', **face), row('second'), row('bridge', **face)
    second['listing_title'] = bridge['listing_title'] = 'Topps Gold 97/199'
    assert len(deduplicate_market_rows([first, second, bridge])[0]) == 1


def test_owned_status_propagates_to_same_photo_relisting_without_serial():
    face = dict(visual_verdict='same',visual_confidence='.99',visual_match_evidence=[{}],
                visual_evidence_binding={'candidate_sha256':'a'*64})
    own, relist = row('own',**face),row('relist',**face)
    own['listing_title']='Gold97/199'
    chosen, rejected=deduplicate_market_rows([own,relist],identity={'serial_number':97,'serial_total':199})
    assert chosen==[] and all(r['reason_code']=='owned_copy_history' for r in rejected)


def test_structured_public_seller_and_consignment_survive_provider_normalization():
    from card_platform.ingestion.cardhobby import CardHobbySale, CardHobbySearchResult
    from card_platform.market_data.cardhobby_provider import CardHobbyMarketDataProvider
    sale=CardHobbySale(item_id='123', title='2025 Chrome Card #12',
        source_url='https://cardhobby.com.cn/Market/Item/123',occurred_at='2026-10-01T00:00:00Z',
        sale_price='10',sale_type='auction',evidence_level='ended_auction',bid_count=2,
        sold_status_evidence='ended with bids',match_confidence='.7',
        seller_id='public-seller',consignment_code='public-code')
    payload=CardHobbyMarketDataProvider.normalize_result(CardHobbySearchResult(sales=[sale])).evidence[0].raw_payload
    assert payload['seller_id']=='public-seller' and payload['consignment_code']=='public-code'
