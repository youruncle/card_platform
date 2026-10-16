import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis
from card_platform.flatbed_service import FlatbedService


@pytest.mark.asyncio
async def test_scan_detection_does_not_block_progress_or_timeout_callbacks(tmp_path, monkeypatch):
    # A real event-loop callback must run while the detector is busy.
    callback_served = threading.Event()
    asyncio.get_running_loop().call_later(.02, callback_served.set)
    layout = LayoutHypothesis((Detection(.1, .1, .6, .7, 0, .9),), .9, 1, 1)

    def detect(*args, **kwargs):
        assert callback_served.wait(.2), "image detection blocked the event loop"
        return layout

    monkeypatch.setattr("card_platform.flatbed_service.detect_layout", detect)
    monkeypatch.setattr("card_platform.flatbed_service.resolve_object_layout", lambda content, result: result)
    owner, session = uuid4(), uuid4()

    class Result:
        async def fetchone(self):
            return {"id": session, "owner_id": owner, "dpi": 300, "revision": 1}

    async def execute(*args):
        return Result()

    service = FlatbedService(str(tmp_path), "missing-bridge.swift")
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    _, encoded = cv2.imencode(".png", np.full((400, 400, 3), 180, np.uint8))
    result = await service.store_sheet(
        SimpleNamespace(execute=execute), owner, session, "front", encoded.tobytes(), "image/png"
    )
    assert result["side"] == "front"
    assert len(result["regions"]) == 1
