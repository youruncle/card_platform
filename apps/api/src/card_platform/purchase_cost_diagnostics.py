"""Describe the failed layer without weakening eligibility or writing costs."""


def summarize_candidates(candidates, rejected):
    diagnostics = dict(
        ready=sum(bool(c['automatic']) for c in candidates),
        identity_incomplete=sum(not c['automatic'] for c in candidates),
        identity_conflict=sum(bool(r.get('identity_conflicts')) for r in rejected),
        amount_blocked=sum(bool(r.get('automatic_identity') and r.get('order_errors')) for r in rejected),
        allocation_blocked=sum(bool(r.get('automatic_identity') and r.get('allocation_blocked')) for r in rejected),
        scope='synced_order_evidence',
    )
    if candidates:
        reason = '已找到购买记录，系统将继续核对同款身份'
    else:
        blockers = []
        if diagnostics['amount_blocked']:
            blockers.append('金额或整单运费依据未通过核验')
        if diagnostics['allocation_blocked']:
            blockers.append('订单件已被其他库存卡占用')
        if blockers:
            reason = '已发现同款购买记录，但' + '；'.join(blockers) + '，未录入成本'
        elif rejected:
            reason = '已找到相关购买记录，身份或订单证据不足，已保留空成本'
        else:
            reason = '当前已同步订单中未找到可用候选；不代表完整购买记录中没有交易'
    return dict(reason=reason, diagnostics=diagnostics)
