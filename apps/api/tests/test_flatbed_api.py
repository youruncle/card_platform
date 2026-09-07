from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from card_platform.flatbed_api import (
    _capture_progress,
    _publish_capture_progress,
    _stored_crop_confidence,
    capture_status,
)


@pytest.mark.asyncio
async def test_capture_progress_reports_phase_only_to_owner() -> None:
    capture_id = uuid4()
    owner_id = uuid4()
    session_id = uuid4()
    _capture_progress.clear()
    _publish_capture_progress(
        capture_id,
        owner_id=owner_id,
        session_id=session_id,
        side="front",
        phase="processing",
    )

    progress = await capture_status(capture_id, user=SimpleNamespace(id=owner_id))

    assert progress["phase"] == "processing"
    assert progress["session_id"] == session_id
    assert "owner_id" not in progress
    with pytest.raises(HTTPException, match="扫描进度不存在"):
        await capture_status(capture_id, user=SimpleNamespace(id=uuid4()))
    _capture_progress.clear()


def test_stored_crop_confidence_keeps_the_more_conservative_signal() -> None:
    assert _stored_crop_confidence(
        {
            "front_confidence": 0.91,
            "front_signals": {"crop_quality": {"usability_score": 0.74}},
        },
        "front",
    ) == 0.74
    assert _stored_crop_confidence(
        {"back_confidence": 0.63, "back_signals": {}}, "back"
    ) == 0.63
