import pytest
from card_platform.valuation_worker import ComparableSearchResult, empty_valuation_recall_diagnostics


@pytest.mark.parametrize('raw,accepted,code',[(0,0,'no_eligible_sales_returned_for_queries'),(20,0,'recalled_rows_rejected_by_identity'),(20,3,'recalled_candidates_not_retained')])
def test_recall_loss_is_not_reported_as_no_market(raw,accepted,code):
    search=ComparableSearchResult(search_queries=[f'卡淘已售接口[x]：Example (召回{raw}/通过{accepted};accepted)'])
    result=empty_valuation_recall_diagnostics(search)
    assert result['root_cause_codes']==[code]
    assert result['raw_result_occurrences']==raw
    assert result['accepted_result_occurrences']==accepted
    assert '50%' not in result['reason']


def test_outage_is_not_zero_market():
    result=empty_valuation_recall_diagnostics(ComparableSearchResult(notes=['卡淘定向检索失败：timeout']))
    assert result['root_cause_codes']==['market_source_unavailable']


def test_rejected_history_without_live_query_is_not_no_search_results():
    result=empty_valuation_recall_diagnostics(ComparableSearchResult(rejected_candidates=[{'reason_codes':['serial_total_conflict']}]))
    assert result['root_cause_codes']==['cached_candidates_rejected_by_current_identity']
    assert result['observed_query_count']==0
