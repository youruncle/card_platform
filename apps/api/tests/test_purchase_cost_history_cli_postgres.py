"""Real ledger/checkpoint regression for bounded native CLI processes."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_purchase_cost_postgres import client as _cost_client, item, register

cost_client = _cost_client

pytestmark = pytest.mark.skipif('TEST_DATABASE_URL' not in os.environ, reason='requires PostgreSQL')


def runner():
    path=Path(__file__).resolve().parents[3]/'scripts'/'purchase_cost_history.py'
    spec=importlib.util.spec_from_file_location('bounded_history_runner',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('budget',[1,2])
def test_process_budget_checkpoints_without_claiming_completion_and_resumes_same_ledger(cost_client,tmp_path,monkeypatch,budget):
    from card_platform import cardhobby_account_api

    owner=register(cost_client)
    cards=[item(cost_client,owner,purchase=dict(transacted_at='2025-01-01T00:00:00Z',price='10',currency='CNY')) for _ in range(3)]
    cases=[dict(case_id=f'A00{i+1}',card_id=card) for i,card in enumerate(cards)]
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps(dict(owner_id=owner,cases=cases)))
    out=tmp_path/'results'
    out.mkdir()
    # An earlier completion artifact must not survive a partial new run.
    for name in ('summary.json','ledger-after.json'):
        (out/name).write_text(json.dumps(dict(total=3,attempted=3,stale=True)))

    class ExternalAccount:
        async def archive_orders(self,conn,owner_id):
            return []

    # Only the encrypted external account/history check is replaced. Native
    # Database, search_plan, transaction reads, checkpoints and resume are real.
    monkeypatch.setattr(cardhobby_account_api,'account_service',ExternalAccount)
    args=SimpleNamespace(phase='match',manifest=manifest,output=out,rerun=False,
                         max_rounds=1,max_cases=budget)
    module=runner()
    asyncio.run(module.execute(args))
    rows=json.loads((out/'results.json').read_text())
    assert [row['case_id'] for row in rows]==[f'A00{i+1}' for i in range(budget)]
    assert not (out/'summary.json').exists()
    assert not (out/'ledger-after.json').exists()
    before=(out/'ledger-before.json').read_bytes()
    assert len(json.loads(before)['transactions'])==3

    while len(rows)<3:
        previous=len(rows)
        asyncio.run(module.execute(args))
        rows=json.loads((out/'results.json').read_text())
        assert len(rows)==min(previous+budget,3)
        assert (out/'ledger-before.json').read_bytes()==before
        if len(rows)<3:
            assert not (out/'summary.json').exists()
            assert not (out/'ledger-after.json').exists()
    assert [row['case_id'] for row in rows]==['A001','A002','A003']
    assert all(row['result']['status']=='existing_cost' for row in rows)
    summary=json.loads((out/'summary.json').read_text())
    assert summary['total']==summary['attempted']==3
    assert summary['new_cost_cards']==0
    assert json.loads((out/'ledger-after.json').read_text())['transactions']==json.loads(before)['transactions']


@pytest.mark.parametrize('budget',['0','-1'])
def test_nonpositive_process_budget_rejected_before_manifest_or_database_access(monkeypatch,tmp_path,capsys,budget):
    module=runner()
    monkeypatch.setattr('sys.argv',['runner','--phase','match','--manifest',str(tmp_path/'missing.json'),
                                  '--output',str(tmp_path/'out'),'--max-cases',budget])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code==2
    assert 'invalid run budget' in capsys.readouterr().err
