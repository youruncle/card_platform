"""Sampling invariants; invented identities only."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('cost_audit', Path(__file__).resolve().parents[3] / 'scripts/audit_missing_purchase_cost.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_sample_is_stable_unique_and_covers_rare_strata():
    cards = [dict(id=str(i),player_name='Common') for i in range(150)]
    cards += [dict(id='rare',player_name='Rare',grade_company='PSA',serial_total=10)]
    selected = audit.freeze_sample(cards)
    assert len(selected) == len({c['id'] for c in selected}) == 100
    assert 'rare' in {c['id'] for c in selected}
    assert selected == audit.freeze_sample(list(reversed(cards)))


def test_sample_does_not_pad_short_population():
    assert audit.freeze_sample([dict(id='a')]) == [dict(id='a')]
    assert audit.freeze_sample([]) == []


def test_private_evidence_never_overwrites_or_follows_symlink(tmp_path):
    import pytest
    target=tmp_path/'private'/'evidence.json'
    audit.save_private(target, {'private': True})
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        audit.save_private(target, {'private': False})
    link=tmp_path/'link.json'
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        audit.save_private(link, {})


def test_inventory_pagination_preserves_scope_and_handles_blank_search(monkeypatch):
    import pytest
    import importlib
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]/'scripts'))
    inventory=importlib.import_module('audit_purchase_order_inventory')
    from card_platform.cardhobby_order_html import Document
    current='https://cardhobby.com.cn/order/myorders?status=0&searchTxt=&beginDate=2000&endDate=2026'
    same=Document('<a href="/order/myorders?status=0&amp;beginDate=2000&amp;endDate=2026&amp;pageindex=2">»</a>')
    assert inventory.next_page(same,current).endswith('pageindex=2')
    changed=Document('<a href="/order/myorders?status=0&amp;beginDate=2025&amp;endDate=2026&amp;pageindex=2">»</a>')
    with pytest.raises(ValueError,match='scope_changed'):
        inventory.next_page(changed,current)
    disabled=Document('<span class="disabled"><a href="/order/myorders">»</a></span>')
    assert inventory.next_page(disabled,current) is None


def test_replay_distinguishes_missing_identity_money_and_occupied_units(monkeypatch):
    import importlib
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]/'scripts'))
    replay=importlib.import_module('replay_purchase_cost_audit')
    row=dict(match=dict(automatic=True,conflicts=[]),errors=[],assigned=[],quantity=1)
    assert replay.classify([row])=='text_candidate_ready'
    assert replay.classify([row,row])=='allocation_ambiguous'
    assert replay.classify([{**row,'assigned':['other-card']}])=='purchase_already_allocated'
    assert replay.classify([{**row,'errors':['运费未确认']}])=='amount_or_order_unverified'
    assert replay.classify([{**row,'match':dict(automatic=False,conflicts=[])}])=='identity_incomplete'
    assert replay.classify([])=='no_identity_anchor_in_scope'


def test_replay_rejects_cross_account_or_changed_manifest_before_matching(monkeypatch):
    import importlib
    import pytest
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]/'scripts'))
    replay=importlib.import_module('replay_purchase_cost_audit')
    with pytest.raises(ValueError,match='different accounts'):
        replay.replay(dict(owner_id='a'),dict(owner_id='b'))
    with pytest.raises(ValueError,match='ordering changed'):
        replay.replay(dict(owner_id='a',samples=[dict(id='changed')],sample_ids=['original'],manifest_sha256='bad'),dict(owner_id='a'))
