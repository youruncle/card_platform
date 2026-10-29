from uuid import uuid4

import pytest
from card_platform.schemas import CardRecognitionResult
from card_platform.scanner_queue import _auto_import_payload


def test_unknown_year_is_not_replaced_with_copyright_or_current_year():
    with pytest.raises(ValueError, match="发行"):
        _auto_import_payload(
            uuid4(),
            CardRecognitionResult(
                confidence=".9",
                player_name="Test",
                brand_name="Panini",
                product_name="Prizm",
                copyright_year=2024,
            ),
        )


def test_auto_import_preserves_unknown_flags():
    p = _auto_import_payload(
        uuid4(),
        CardRecognitionResult(
            confidence=".9",
            player_name="Test",
            brand_name="Panini",
            product_name="Prizm",
            season_start=2024,
        ),
    )
    assert p.card.is_autograph is None and p.card.is_rookie is None
    assert p.card.is_memorabilia is None and p.card.is_patch is None
    assert p.card.has_inscription is None and p.card.autograph_type == "unknown"


def test_task_business_status_and_safe_errors():
    from card_platform.capture_tasks import task_state, failure_message

    assert task_state({"status": "succeeded", "collection_item_id": None}) == "needs_review"
    assert task_state({"status": "succeeded", "collection_item_id": "item"}) == "imported"
    assert task_state({"status": "failed", "require_confirmation": False}) == "failed"
    assert "已保存" in failure_message("recognition: all configured vision model tiers failed")
    assert "/secret" not in failure_message("[Errno 2] /secret/private.jpg")
