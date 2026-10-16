from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from card_platform.schemas import ExpertAnalysisSnapshot


def snapshot(evidence):
    return {
        "id": uuid4(), "job_id": uuid4(), "collection_item_id": uuid4(),
        "analysis_version": 1, "source": "codex_multimodal", "model_name": "test",
        "identity_version_id": None, "identity_summary": "Visual identity",
        "identity_confidence": 0.8, "currency": "CNY", "fair_value_low": 10,
        "fair_value": 20, "fair_value_high": 30, "valuation_confidence": 0.4,
        "evidence_summary": evidence, "value_drivers": [], "risks_and_conflicts": [],
        "investment_conclusion": None, "recommended_actions": [],
        "raw_result": {"evidence": deepcopy(evidence)}, "status": "succeeded",
        "error_message": None, "created_at": "2026-09-21T00:00:00Z",
    }


def test_legacy_text_evidence_is_readable_without_mutating_snapshot():
    # A legacy string must not prevent the history endpoint from returning newer versions.
    evidence = ["正反面编号一致", {"type": "visual", "detail": "5/8"}]
    row = snapshot(evidence)
    original = deepcopy(row)
    result = ExpertAnalysisSnapshot(**row)
    assert result.evidence_summary == [
        {"type": "legacy_text", "detail": "正反面编号一致"},
        {"type": "visual", "detail": "5/8"},
    ]
    assert result.raw_result == {"evidence": evidence}
    assert row == original


@pytest.mark.parametrize("evidence", [[], [{"type": "visual", "detail": "5/8"}]])
def test_structured_evidence_is_preserved(evidence):
    assert ExpertAnalysisSnapshot(**snapshot(evidence)).evidence_summary == evidence


def test_invalid_non_text_evidence_still_fails_validation():
    with pytest.raises(ValidationError):
        ExpertAnalysisSnapshot(**snapshot([123]))
