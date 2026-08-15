from card_platform.evidence_reasoning import EvidenceReview, enforce_conservative_rejections


def test_only_high_confidence_allowlisted_deepseek_rejections_are_enforced() -> None:
    review = EvidenceReview.model_validate({"decisions": [
        {"evidence_id": "lot", "decision": "reject", "confidence": "0.96",
         "reason_codes": ["multi_card_lot"]},
        {"evidence_id": "weak", "decision": "reject", "confidence": "0.70",
         "reason_codes": ["different_parallel"]},
        {"evidence_id": "missing", "decision": "uncertain", "confidence": "0.99",
         "reason_codes": ["insufficient_title_detail"]},
        {"evidence_id": "alias", "decision": "reject", "confidence": "0.99",
         "reason_codes": ["different_product"]},
    ]})
    assert enforce_conservative_rejections(review) == {
        "lot": ["deepseek_multi_card_lot"]
    }
