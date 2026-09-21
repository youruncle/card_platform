import pytest
from scripts import audit_cardhobby_valuation as audit


def test_reference_metrics_exclude_unknown_and_measure_distance_to_interval():
    rows=[{'display_id':1,'fair':'90'},{'display_id':2,'fair':'260'}, {'display_id':3,'fair':'1'}]
    labels=[{'display_id':1,'reference_interval':['100','120'],'reference_status':'sufficient'},
            {'display_id':2,'reference_interval':['150','200'],'reference_status':'sufficient'},
            {'display_id':3,'reference_interval':None,'reference_status':'unknown'}]
    result=audit.reference_metrics(rows,labels)
    assert result['D']==2 and result['U']==1
    assert result['reasonable_coverage']==0
    assert result['interval_distance_median']==pytest.approx(.2)
    assert result['interval_distance_p90']==pytest.approx(.3)
    assert result['price_acceptance_evaluable'] is False


def test_missing_price_counts_against_coverage_but_not_numeric_error():
    result=audit.reference_metrics([{'display_id':1,'fair':None}],
        [{'display_id':1,'reference_status':'sufficient','reference_interval':[10,20]}])
    assert result['reasonable_coverage']==0
    assert result['priced_reference_count']==0
    assert result['interval_distance_median'] is None


def test_unknown_reference_never_implies_zero_error_or_full_coverage():
    result=audit.reference_metrics([{'display_id':1,'fair':1}],[])
    assert result['D']==0 and result['U']==1
    assert result['reasonable_coverage'] is None
    assert result['interval_distance_p90'] is None


def test_unreviewed_interval_and_duplicate_labels_are_not_gold():
    assert audit.reference_metrics([{'display_id':1,'fair':1}],
        [{'display_id':1,'reference_interval':[1,1]}])['D']==0
    with pytest.raises(ValueError):
        audit.reference_metrics([], [{'display_id':1},{'display_id':1}])


def test_interval_overlap_and_width_are_reported_separately_from_fair_error():
    result=audit.reference_metrics([
        {'display_id':1,'fair':'110','low':'90','high':'130'},
        {'display_id':2,'fair':'10','low':'10','high':'10'},
        {'display_id':3,'fair':'50','low':None,'high':None}], [
        {'display_id':1,'reference_status':'sufficient','reference_interval':[100,120]},
        {'display_id':2,'reference_status':'sufficient','reference_interval':[10,10]},
        {'display_id':3,'reference_status':'unknown','reference_interval':None}])
    assert result['interval_compared_count']==2
    assert result['interval_overlap_mean']==pytest.approx(.75)
    assert result['normalized_width_mean']==pytest.approx((40/110)/2)


def test_invalid_system_interval_cannot_masquerade_as_zero_width():
    with pytest.raises(ValueError):
        audit.reference_metrics([{'display_id':1,'fair':10,'low':20,'high':10}],
            [{'display_id':1,'reference_status':'sufficient','reference_interval':[10,20]}])
