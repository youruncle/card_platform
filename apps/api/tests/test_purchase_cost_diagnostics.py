from card_platform.purchase_cost_diagnostics import summarize_candidates


def test_evidenced_purchase_is_not_reported_as_no_record_when_shipping_is_blocked():
    result=summarize_candidates([], [dict(automatic_identity=True,order_errors=['整单物理张数未确认'],
        allocation_blocked=False,identity_conflicts=[])])
    assert result['diagnostics']['amount_blocked']==1
    assert '同款购买记录' in result['reason'] and '运费' in result['reason']
    assert '未找到' not in result['reason']


def test_allocated_purchase_is_a_separate_layer_and_never_a_new_candidate():
    result=summarize_candidates([], [dict(automatic_identity=True,order_errors=[],allocation_blocked=True,identity_conflicts=[])])
    assert result['diagnostics']['allocation_blocked']==1
    assert result['diagnostics']['ready']==0
    assert '占用' in result['reason']


def test_empty_synced_scope_does_not_claim_no_purchases_exist():
    result=summarize_candidates([], [])
    assert '已同步' in result['reason'] and '不代表' in result['reason']
