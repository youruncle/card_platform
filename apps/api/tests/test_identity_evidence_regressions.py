"""Fixed real failures; this is a small regression set, not a population benchmark."""

import json
from pathlib import Path
import pytest
from card_platform.image_service import sanitize_identity_field_roles
from card_platform.vision_routing import finalize_recognition_status
from card_platform.schemas import CardRecognitionResult
from card_platform.purchase_cost_search import build_purchase_cost_search_plan
from card_platform.purchase_cost import score_match
from card_platform.knowledge import KnowledgeResolver

CASES = json.loads(
    (Path(__file__).parent / "fixtures/identity-evidence-regressions.json").read_text()
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda x: x["case_id"])
def test_reported_case_field_roles_and_bounded_discovery(case):
    result = finalize_recognition_status(
        sanitize_identity_field_roles(CardRecognitionResult.model_validate(case["recognition"]))
    )
    for field, expected in case["expected_fields"].items():
        assert getattr(result, field) == expected
        if expected is None:
            assert result.field_assessments[field].status in {"unknown", "conflicted"}
    plan = build_purchase_cost_search_plan(
        {**result.model_dump(mode="json"), "image_front_url": "/uploads/fixture.jpg"}
    )
    assert plan["status"] == "ready"
    assert len(plan["keywords"]) <= 3
    assert any(term in plan["keywords"] for term in case["expected_query_any"])
    if case.get("seller_title_error"):
        assert plan["fallback_keywords"] == ["Topps Now"]


@pytest.mark.parametrize("case", CASES, ids=lambda x: x["case_id"])
def test_reported_case_wrong_title_is_never_automatic_identity_proof(case):
    decision = score_match(
        case["corrected_identity"],
        {"title": case["negative_title"], "image_urls": []},
        KnowledgeResolver(),
    )
    assert not decision["automatic"], decision


@pytest.mark.parametrize(
    "case", [c for c in CASES if not c.get("seller_title_error")], ids=lambda x: x["case_id"]
)
def test_reported_case_known_correct_subject_still_reaches_confirmation(case):
    decision = score_match(
        case["corrected_identity"],
        {"title": case["positive_title"], "image_urls": []},
        KnowledgeResolver(),
    )
    assert decision["recalled"], decision
