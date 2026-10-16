import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from card_platform.config import Settings
from card_platform.image_service import ImageStorageService


@pytest.mark.asyncio
async def test_rectification_runs_off_the_event_loop_and_preserves_arguments(tmp_path, monkeypatch):
    event_loop_thread = threading.get_ident()
    seen = []

    def rectify(content, content_type, *, standardize_ratio):
        assert threading.get_ident() != event_loop_thread
        seen.append((content, content_type, standardize_ratio))
        return SimpleNamespace(content=b"corrected-image", content_type="image/jpeg")

    monkeypatch.setattr("card_platform.image_service.rectify_card_image", rectify)
    connection = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value={"id": uuid4()}))))
    service = ImageStorageService(Settings(upload_dir=str(tmp_path)))
    result = await service._save_content(connection, uuid4(), "front", b"input", "image/jpeg", standardize_ratio=False)
    assert seen == [(b"input", "image/jpeg", False)]
    assert (tmp_path / result.image_url.rsplit("/", 1)[1]).read_bytes() == b"corrected-image"


@pytest.mark.asyncio
async def test_rectification_failure_does_not_write_files_or_update_database(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ValueError("invalid geometry")
    monkeypatch.setattr("card_platform.image_service.rectify_card_image", fail)
    connection = SimpleNamespace(execute=AsyncMock())
    service = ImageStorageService(Settings(upload_dir=str(tmp_path)))
    with pytest.raises(ValueError, match="invalid geometry"):
        await service._save_content(connection, uuid4(), "front", b"input", "image/jpeg")
    connection.execute.assert_not_awaited()
    assert not list(tmp_path.iterdir())
