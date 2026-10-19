import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest


def test_runner_rejects_duplicate_cards_before_any_database_access(tmp_path):
    path = Path(__file__).resolve().parents[3]/'scripts'/'purchase_cost_history.py'
    assert path.exists(), 'missing resumable full-sample runner'
    spec = importlib.util.spec_from_file_location('history_runner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    item = str(uuid4())
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'owner_id':str(uuid4()), 'cases':[
        {'case_id':'A001','card_id':item}, {'case_id':'A002','card_id':item}]}))
    with pytest.raises(ValueError, match='duplicate'):
        module.load_targets(manifest)
