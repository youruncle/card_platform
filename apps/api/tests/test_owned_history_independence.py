"""Ownership is provenance; only other copies are independent market evidence."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace

import pytest

from card_platform.comparable_image_verifier import VisualDecision
from card_platform.domain.sale_independence import unique_physical_rows
from card_platform.valuation_worker import AutomatedValuationWorker, ComparableSearchResult, DiscoveredComparable
from test_audit_batch02_admission import fixture, observed


def row(key, title='2025 Topps Bowman Chrome Example Player #7 Refractor', **fields):
    return dict(id=key, listing_title=title, source_code='cardhobby',
                source_url=f'https://cardhobby.com.cn/Market/Item/{key}', raw_payload={}, **fields)


def owned_identity(source_url):
    return dict(owner_id='11111111-1111-4111-8111-111111111111',
        collection_item_id='22222222-2222-4222-8222-222222222222', purchase_sources=[dict(
            contract='owned-purchase-source-v1', owner_id='11111111-1111-4111-8111-111111111111',
            collection_item_id='22222222-2222-4222-8222-222222222222',
            assignment_id='33333333-3333-4333-8333-333333333333', evidence_hash='a'*64,
            source_url=source_url)])


def test_bound_own_purchase_is_history_even_without_full_title_serial():
    source = row('123')
    selected, excluded = unique_physical_rows([source], owned_identity(source['source_url']))
    assert selected == []
    assert [item['reason_code'] for item in excluded] == ['owned_copy_history']


@pytest.mark.parametrize('change', ('owner_id', 'collection_item_id', 'evidence_hash', 'assignment_id', 'contract'))
def test_unvalidated_purchase_binding_cannot_claim_ownership(change):
    source = row('123')
    identity = owned_identity(source['source_url'])
    identity['purchase_sources'][0][change] = 'invalid'
    selected, excluded = unique_physical_rows([source], identity)
    assert [item['id'] for item in selected] == ['123']
    assert excluded == []


def test_arbitrary_url_and_stale_owned_boolean_do_not_claim_ownership():
    source = row('123', classification={'field_matches': {'owned_public_auction_history': True}})
    selected, excluded = unique_physical_rows([source], dict(notes=source['source_url'],
        public_source_urls=[source['source_url']], purchase_sources=[]))
    assert [item['id'] for item in selected] == ['123']
    assert excluded == []


def test_actual_candidate_photo_relisting_of_owned_source_is_history():
    first, relisted = row('123'), row('456')
    for source in (first, relisted):
        source['raw_payload'] = dict(visual_verdict='same', visual_confidence='.99',
            visual_match_evidence=[{'kind': 'layout'}], visual_evidence_binding={'candidate_sha256': 'b'*64})
    selected, excluded = unique_physical_rows([relisted, first], owned_identity(first['source_url']))
    assert selected == []
    assert [item['reason_code'] for item in excluded] == ['owned_copy_history', 'owned_copy_history']


@pytest.mark.parametrize('key,other', [('brand_name', 'Panini'), ('product_name', 'Prizm'),
    ('parallel_name', 'Purple Wave'), ('season_start', 2024), ('card_number', '8')])
def test_same_full_serial_in_different_printed_version_is_not_one_copy(key, other):
    common = dict(brand_name='Topps', product_name='Bowman Chrome', parallel_name='Refractor',
                  season_start=2025, card_number='7')
    a = row('1', 'Example Player serial 13/50', **common)
    b = row('2', 'Example Player serial 13/50', **{**common, key: other})
    selected, excluded = unique_physical_rows([a, b])
    assert [item['id'] for item in selected] == ['1', '2']
    assert excluded == []


def test_same_explicit_certificate_is_one_other_copy():
    selected, excluded = unique_physical_rows([
        row('1', grade_company='PSA', grade_cert_number='12345678'),
        row('2', grade_company='PSA', grade_cert_number='12345678')])
    assert [item['id'] for item in selected] == ['1']
    assert [item['reason_code'] for item in excluded] == ['same_physical_card_certificate']


def test_same_explicit_owned_certificate_is_history():
    selected, excluded = unique_physical_rows([
        row('1', grade_company='PSA', grade_cert_number='12345678')],
        dict(grade_company='PSA', grade_cert_number='12345678'))
    assert selected == []
    assert [item['reason_code'] for item in excluded] == ['owned_copy_history']


@pytest.mark.parametrize('other', [('PSA', '87654321'), ('BGS', '12345678'), (None, '12345678')])
def test_different_or_unscoped_certificate_is_not_one_copy(other):
    selected, excluded = unique_physical_rows([
        row('1', grade_company='PSA', grade_cert_number='12345678'),
        row('2', grade_company=other[0], grade_cert_number=other[1])])
    assert [item['id'] for item in selected] == ['1', '2']
    assert excluded == []


def test_explicit_other_version_same_certificate_is_not_owned_history():
    selected, excluded = unique_physical_rows([
        row('1', grade_company='PSA', grade_cert_number='12345678', parallel_name='Purple Wave')],
        dict(grade_company='PSA', grade_cert_number='12345678', parallel_name='Purple'))
    assert [item['id'] for item in selected] == ['1']
    assert excluded == []


def test_other_serial_is_independent_and_gallery_advertisement_is_not_a_key():
    sources = [row('1', 'Example Player serial 13/50'), row('2', 'Example Player serial 20/50')]
    for source in sources:
        source['raw_payload'] = dict(gallery_image_hashes=['c'*64], image_role='terms',
                                    background_sha256='d'*64)
    selected, excluded = unique_physical_rows(sources, dict(serial_number=13, serial_total=50))
    assert [item['id'] for item in selected] == ['2']
    assert [item['reason_code'] for item in excluded] == ['owned_copy_history']


def visual_fixture():
    identity, sale = fixture()
    identity.update(owned_identity(sale.source_url))
    payload = sale.model_dump(mode='json')
    comp = DiscoveredComparable.model_validate({**payload, 'currency': 'CNY', 'source_code': 'cardhobby',
                                                'provider_payload': payload})
    raw = observed(identity, sale)
    decision = VisualDecision(candidate_id=sale.source_url, verdict='same', confidence='.99',
        design_match='same', finish_match='same', reasons=raw['visual_reasons'],
        evidence_binding=raw['visual_evidence_binding'], match_evidence=raw['visual_match_evidence'])
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = None
    worker.evidence_reviewer = None
    worker.settings = SimpleNamespace(valuation_visual_stage_timeout_seconds=5)
    return identity, comp, decision, worker


def test_bound_owned_source_cannot_stop_even_when_same_card_gate_passes():
    identity, comp, decision, worker = visual_fixture()
    assert worker._visual_candidate_can_stop(identity, comp, decision) is False


def test_same_card_independent_source_still_can_stop():
    identity, comp, decision, worker = visual_fixture()
    identity['purchase_sources'] = []
    assert worker._visual_candidate_can_stop(identity, comp, decision) is True


class FrozenProvider:
    def __init__(self, decisions):
        self.decisions = decisions
    def order_candidates(self, front, candidates, **kwargs):
        return candidates
    async def verify(self, front, candidates, **kwargs):
        return {candidate.id: self.decisions[candidate.id] for candidate in candidates}


@pytest.mark.asyncio
@pytest.mark.parametrize('same_photo', [False, True])
async def test_visual_loop_checks_next_source_after_owned_history(same_photo):
    identity, comp, decision, worker = visual_fixture()
    url = 'https://cardhobby.com.cn/Market/Item/456'
    payload = {**comp.provider_payload, 'source_url': url, 'item_id': '456',
               'image_url': 'https://images.cardhobby.com/other-front.jpg'}
    other = comp.model_copy(update={'source_url': url, 'image_url': payload['image_url'], 'provider_payload': payload})
    other_decision = deepcopy(decision)
    other_decision.candidate_id = url
    binding = other_decision.evidence_binding
    binding['source_url'] = url
    binding['candidate_image_url'] = other.image_url
    if not same_photo:
        binding['candidate_sha256'] = sha256(b'other physical copy photo').hexdigest()
    worker.image_verifier = FrozenProvider({comp.source_url: decision, url: other_decision})
    result = await worker._verify_comparable_images(identity, ComparableSearchResult(comparables=[comp, other]))
    assert result.visual_progress['attempted_count'] == 2
    assert result.visual_progress['observed_count'] == 2
    assert result.visual_progress['stop_reason'] == ('candidate_pool_exhausted' if same_photo else 'same_card_candidate_confirmed')


def test_current_t2_unknown_version_with_equal_title_serial_is_not_owned():
    # Exact _calculate input shape: SQL does not provide parallel_name on a row.
    # This real classifier branch admits contextual T2 without a hard conflict;
    # equality of 12/75 cannot fill its missing finish or make it this held copy.
    identity = dict(player_name='Cameron Johnson', brand_name='Topps', product_name='Chrome Update',
        season_start=2025, season_end=2026, card_number='69', parallel_name=None,
        serial_number=12, serial_total=75)
    source = row('1', '2026 Topps Chrome Update Cameron Johnson #69 12/75 serial')
    source.update(source_code='ebay', source_url='https://www.ebay.com/itm/123456789012')
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = None
    worker.image_verifier = None
    visual, facts = worker._current_visual_evidence(identity, source)
    classified = worker._classify_pricing_source(identity, source, visual, facts)
    assert classified.tier.value == 'T2' and classified.hard_conflicts == []
    source['comparable_tier'] = classified.tier.value
    source['classification'] = {'field_matches': classified.field_matches, 'hard_conflicts': []}
    selected, excluded = unique_physical_rows([source], identity)
    assert [item['id'] for item in selected] == ['1']
    assert excluded == []


def test_t2_unknown_version_does_not_bridge_a_same_serial_owned_t0_cluster():
    identity = dict(serial_number=12, serial_total=75)
    exact = row('1', 'Known same-card serial 12/75', comparable_tier='T0')
    analogy = row('2', 'Unknown finish serial 12/75', comparable_tier='T2')
    selected, excluded = unique_physical_rows([exact, analogy], identity)
    assert [item['id'] for item in selected] == ['2']
    assert [(item['dropped_id'], item['reason_code']) for item in excluded] == [('1', 'owned_copy_history')]


@pytest.mark.parametrize('title', [
    '2026 Topps Chrome Update Cameron Johnson TCU #69 12/75编 紫波纹折',
    '2025-26 Panini Prizm Cameron Johnson #69 Purple 12/75 serial',
])
def test_real_formal_admission_rejects_other_process_before_copy_clustering(title):
    from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity
    from card_platform.ingestion.cardhobby import CardHobbySale
    identity = dict(player_name='Cameron Johnson', brand_name='Topps', product_name='Chrome Update',
        season_start=2025, season_end=2026, card_number='69', parallel_name='Purple Refractor',
        serial_number=12, serial_total=75)
    source = row('1', title)
    sale = CardHobbySale(item_id='1', title=title, source_url=source['source_url'],
        image_url='https://images.cardhobby.com/process-front.jpg', occurred_at='2026-10-01T00:00:00Z',
        sale_price='10', bid_count=2, sale_type='auction', evidence_level='ended_auction',
        sold_status_evidence='Status=-2, 2 bids', match_confidence='.7')
    source['provider_raw_payload'] = sale.model_dump(mode='json')
    source['raw_payload']['image_url'] = sale.image_url
    worker = AutomatedValuationWorker.__new__(AutomatedValuationWorker)
    worker.knowledge_runtime = None
    worker.image_verifier = None
    visual, facts = worker._current_visual_evidence(identity, source)
    preliminary = worker._classify_pricing_source(identity, source, visual, facts)
    final = enforce_formal_cardhobby_identity(identity, source['provider_raw_payload'], preliminary,
        visual_verdict=visual.get('visual_verdict'), visual_facts=facts, visual_observation=visual)
    assert final.tier.value == 'rejected'
    assert ('parallel_conflict' in final.hard_conflicts if '波纹' in title
            else 'product_conflict' in final.hard_conflicts)
    # The real worker's next boundary only forwards rows without hard conflicts.
    selected, excluded = unique_physical_rows([] if final.hard_conflicts else [source], identity)
    assert selected == [] and excluded == []


def test_bound_owned_t2_source_still_taints_its_same_photo_t0_relisting():
    # T2 cannot borrow the target serial. Its actual purchase assignment is
    # stronger provenance, however, and must still mark the very same photo's
    # relisting as non-independent rather than publishing the relisting price.
    first = row('123', comparable_tier='T2')
    relisted = row('456', comparable_tier='T0')
    for source in (first, relisted):
        source['raw_payload'] = dict(visual_verdict='same', visual_confidence='.99',
            visual_match_evidence=[{'kind': 'layout'}], visual_evidence_binding={'candidate_sha256': 'b'*64})
    selected, excluded = unique_physical_rows([first, relisted], owned_identity(first['source_url']))
    assert selected == []
    assert [item['reason_code'] for item in excluded] == ['owned_copy_history', 'owned_copy_history']


def test_bound_owned_t2_cannot_bridge_a_t0_with_same_unproven_serial():
    # Real _calculate row shape has the fresh tier but no version-bearing fields.
    # The assignment owns only source123; its T2 title cannot lend a 12/75 key
    # to source456 when the held target's actual full serial is10/75.
    owned = row('123', 'Unknown version serial 12/75', comparable_tier='T2')
    exact = row('456', 'Known target version serial 12/75', comparable_tier='T0')
    identity = {**owned_identity(owned['source_url']), 'serial_number': 10, 'serial_total': 75,
                'parallel_name': 'Purple Refractor'}
    selected, excluded = unique_physical_rows([owned, exact], identity)
    assert [item['id'] for item in selected] == ['456']
    assert [(item['dropped_id'], item['reason_code']) for item in excluded] == [('123', 'owned_copy_history')]


def test_bound_owned_t2_cannot_bridge_a_t0_with_same_unproven_certificate():
    owned = row('123', 'Unknown version', comparable_tier='T2')
    exact = row('456', 'Known target version', comparable_tier='T0')
    for source in (owned, exact):
        source['provider_raw_payload'] = {'grade_company': 'PSA', 'grade_cert_number': '12345678'}
    identity = {**owned_identity(owned['source_url']), 'parallel_name': 'Purple Refractor'}
    selected, excluded = unique_physical_rows([owned, exact], identity)
    assert [item['id'] for item in selected] == ['456']
    assert [(item['dropped_id'], item['reason_code']) for item in excluded] == [('123', 'owned_copy_history')]
