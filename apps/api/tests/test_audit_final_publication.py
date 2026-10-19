"""Public semantic regressions; private frozen replays are separate receipts."""
from copy import deepcopy
from datetime import datetime

import pytest

from card_platform.domain.market_price_projection import apply_market_snapshot_read_contract
from card_platform.domain.parallel_valuation import _base_card_evidence
from card_platform.domain.sale_independence import unique_physical_rows
from test_audit_batch02_admission import fixture, observed
from test_codex_sales_research import research, NOW


@pytest.fixture
def frozen_research_date(monkeypatch):
    class ResearchDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr('card_platform.domain.codex_sales_research.datetime', ResearchDate)


def published(**changes):
    row = dict(id='item', valuation_status='succeeded', fair_value=25,
               shadow_fair_value=25, codex_fair_value=25)
    snapshot = dict(id='snapshot', method='ai_estimate', currency='CNY',
                    low_value=10, fair_value=25, high_value=50,
                    model_version='relative-multimodal-comps-1.0', evidence={})
    snapshot.update(changes)
    return row, snapshot


@pytest.mark.parametrize('model', ['codex-independent-fallback-v1', 'codex-researched-fallback-v2'])
def test_legacy_codex_reference_is_preserved_outside_independent_market_value(model):
    row, snapshot = published(model_version=model)
    before = deepcopy(snapshot)
    result, current = apply_market_snapshot_read_contract(row, snapshot)
    assert result['fair_value'] is None and result['shadow_fair_value'] is None
    assert result['codex_fair_value'] == 25
    assert current is None
    assert result['valuation_result_summary']['historical_reference']['current_job_verified'] is False
    assert snapshot == before


@pytest.mark.parametrize('entry', [
    {'visual_reasons': ['explicit_design_or_finish_conflict']},
    {'visual_conflict_evidence': [{'kind':'fixed_pattern', 'source':'images', 'both_visible':True,
                                 'target':'tile grid', 'candidate':'circle array'}]},
])
def test_conflicting_print_run_prior_is_withheld_without_rewriting_history(entry):
    row, snapshot = published(model_version='bounded-sqrt-print-run-prior-v1',
        evidence={'used_comparables':[{'source_url':'https://cardhobby.com.cn/Market/Item/123',
                                      'tier':'T2', **entry}]})
    before = deepcopy(snapshot)
    result, current = apply_market_snapshot_read_contract(row, snapshot)
    assert result['fair_value'] is None and current is None
    assert snapshot == before


@pytest.mark.parametrize('number_in_title', [True, False])
def test_explicit_fixed_finish_conflict_cannot_enter_print_run_prior(number_in_title):
    identity, sale = fixture()
    if not number_in_title:
        sale = sale.model_copy(update={'title':'2025 Bowman Chrome Ace Bailey Refractor'})
    raw = observed(identity, sale)
    raw.update(visual_verdict='different', visual_reasons=[*raw['visual_reasons'],
               'explicit_design_or_finish_conflict'])
    assert _base_card_evidence(identity, sale, raw) is None


def test_print_run_only_difference_retains_its_disclosed_analogue_basis():
    identity, sale = fixture()
    assert _base_card_evidence(identity, sale, observed(identity, sale)) is not None
    row, snapshot = published(evidence={'used_comparables':[{'tier':'T2',
        'visual_verdict':'different', 'visual_reasons':['serial_total_conflict']}]})
    assert apply_market_snapshot_read_contract(row, snapshot)[1] is snapshot


def test_stale_binding_does_not_erase_explicit_negative_finish_evidence():
    identity, sale = fixture()
    raw = observed(identity, sale)
    raw.pop('visual_evidence_binding')
    raw.update(visual_verdict='different', visual_reasons=['explicit_design_or_finish_conflict'])
    assert _base_card_evidence(identity, sale, raw) is None


@pytest.mark.parametrize('payload', [[], [1], {'searches':None}, {'sales':None},
                                   {'contract_version':'codex-sales-research-1', 'searches':None}])
def test_malformed_old_research_is_withheld_without_breaking_portfolio_read(payload):
    row, snapshot = published(model_version='codex-researched-fallback-v2',
        evidence={'pricing_basis':'codex_researched_observation', 'market_research':payload})
    assert apply_market_snapshot_read_contract(row, snapshot)[0]['fair_value'] is None


@pytest.mark.parametrize('entry', [{'visual_reasons':7}, {'visual_conflict_evidence':7}])
def test_malformed_old_visual_metadata_does_not_break_unrelated_price_read(entry):
    row, snapshot = published(evidence={'used_comparables':[entry]})
    assert apply_market_snapshot_read_contract(row, snapshot)[0]['fair_value'] == 25


@pytest.mark.parametrize('evidence', [[{'old':'experience'}], 'old', 7])
def test_malformed_outer_evidence_cannot_bypass_legacy_reference_guard(evidence):
    row, snapshot = published(model_version='codex-independent-fallback-v1', evidence=evidence)
    row['valuation_result_summary'] = 'old-summary'
    assert apply_market_snapshot_read_contract(row, snapshot)[0]['fair_value'] is None


def test_malformed_conflict_fields_do_not_break_the_whole_portfolio():
    row, snapshot = published(evidence={'used_comparables':[
        {'visual_conflict_evidence':[{'kind':[], 'source':'images', 'both_visible':True}]},
        {'source_url':[], 'visual_reasons':['explicit_design_or_finish_conflict']} ]})
    assert apply_market_snapshot_read_contract(row, snapshot)[0]['fair_value'] is None


@pytest.mark.parametrize('text', ['small square grid', ''])
def test_equal_or_empty_structured_observations_do_not_invent_a_finish_conflict(text):
    row, snapshot = published(evidence={'used_comparables':[{
        'visual_conflict_evidence':[{'kind':'fixed_pattern', 'source':'images',
            'both_visible':True, 'target':text, 'candidate':text}]}]})
    assert apply_market_snapshot_read_contract(row, snapshot)[0]['fair_value'] == 25


def test_unused_conflicting_context_and_manual_prices_keep_independent_value():
    for changes in [dict(method='manual'), dict(evidence={'used_comparables':[
        {'used_for_center':False, 'final_weight':0,
         'visual_reasons':['explicit_design_or_finish_conflict']}]})]:
        row, snapshot = published(**changes)
        assert apply_market_snapshot_read_contract(row, snapshot)[0]['fair_value'] == 25


def test_qualified_current_research_is_not_withdrawn_by_legacy_model_name(monkeypatch, frozen_research_date):
    data = research(); data['collection_item_id'] = 'item'
    row, snapshot = published(model_version='codex-researched-fallback-v2',
        low_value=34, fair_value=39, high_value=62,
        evidence={'pricing_basis':'codex_researched_observation', 'market_research':data,
                  'source_analysis_id':'analysis'})
    row.update(data['identity'])
    row['synthesis_codex_history'] = [dict(id='analysis', currency='CNY',
        fair_value_low=34, fair_value=39, fair_value_high=62, raw_result=dict(
            research_captures_verified=True, market_research=data))]
    monkeypatch.setattr('card_platform.codex_joint_context.current_image_hashes',
        lambda item: {'front':data['front_sha256'], 'back':data['back_sha256']})
    assert apply_market_snapshot_read_contract(row, snapshot)[1] is snapshot


def test_research_shaped_data_without_source_capture_attestation_is_not_market_price(monkeypatch, frozen_research_date):
    data = research(); data['collection_item_id'] = 'item'
    row, snapshot = published(model_version='codex-researched-fallback-v2',
        low_value=34, fair_value=39, high_value=62,
        evidence={'pricing_basis':'codex_researched_observation', 'market_research':data})
    row.update(data['identity'])
    monkeypatch.setattr('card_platform.codex_joint_context.current_image_hashes',
        lambda item: {'front':data['front_sha256'], 'back':data['back_sha256']})
    assert apply_market_snapshot_read_contract(row, snapshot)[0]['fair_value'] is None


def test_same_public_transaction_casing_is_one_even_without_external_ids_or_same_tier():
    rows = [dict(id='a', source_url='https://www.cardhobby.com.cn/Market/Item/123',
                 comparable_tier='T2'),
            dict(id='b', source_url='https://cardhobby.com.cn/market/item/123',
                 comparable_tier='T1')]
    kept, excluded = unique_physical_rows(rows)
    assert len(kept) == 1 and len(excluded) == 1


def test_shared_seller_different_public_transactions_remain_independent():
    rows = [dict(id=str(i), source_url=f'https://cardhobby.com.cn/market/item/{i}',
                 provider_raw_payload={'seller_id':'same-seller'}) for i in [123, 124]]
    assert len(unique_physical_rows(rows)[0]) == 2
