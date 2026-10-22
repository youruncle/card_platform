from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from card_platform import flatbed_api
from card_platform.flatbed_service import FlatbedService


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["device", "capture"])
@pytest.mark.parametrize("bridge_kind", ["missing", "missing_interpreter", "not_executable"])
async def test_unavailable_native_bridge_returns_actionable_503(
    tmp_path, monkeypatch, operation, bridge_kind
):
    bridge = tmp_path / "bridge"
    if bridge_kind != "missing":
        bridge.write_text("#!/nonexistent/flatbed-runtime\n")
        bridge.chmod(0o700 if bridge_kind == "missing_interpreter" else 0o600)
    service = FlatbedService(str(tmp_path / "scans"), str(bridge))
    monkeypatch.setattr(flatbed_api, "service", lambda: service)
    owner_id, session_id, capture_id = uuid4(), uuid4(), uuid4()
    result = AsyncMock()
    result.fetchone.return_value = {"dpi": 300}
    db = AsyncMock()
    db.execute.return_value = result

    try:
        with pytest.raises(HTTPException) as raised:
            if operation == "device":
                await flatbed_api.device()
            else:
                await flatbed_api.capture_sheet(
                    session_id, "front", preview=False, capture_id=capture_id,
                    user=SimpleNamespace(id=owner_id), db=db,
                )
        assert raised.value.status_code == 503
        assert "Mac" in raised.value.detail
        assert "导入" in raised.value.detail
        assert "8000" not in raised.value.detail
        assert not list((tmp_path / "scans").rglob("*.png"))
        if operation == "capture":
            progress = await flatbed_api.capture_status(
                capture_id, user=SimpleNamespace(id=owner_id)
            )
            assert progress["phase"] == "failed"
    finally:
        flatbed_api._capture_progress.pop(capture_id, None)
