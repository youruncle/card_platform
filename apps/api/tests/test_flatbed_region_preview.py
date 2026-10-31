from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from card_platform import flatbed_api
from card_platform.auth import current_user
from card_platform.flatbed_service import FlatbedService


@pytest.fixture
def preview_api(tmp_path, monkeypatch):
    owner, region = uuid4(), uuid4()
    image = np.full((120, 160, 3), 245, np.uint8)
    image[30:90, 40:120] = (30, 80, 150)
    cv2.imwrite(str(tmp_path / "original.png"), image)
    row = {
        "crop_path": None,
        "original_path": "original.png",
        "bounds": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5},
        "signals": {"orientation_rotation_degrees": 90},
    }
    db = AsyncMock()

    async def execute(sql, params):
        assert params == (region, owner)
        result = AsyncMock()
        result.fetchone.return_value = row
        return result

    db.execute.side_effect = execute
    service = FlatbedService(str(tmp_path), "unused.swift")
    monkeypatch.setattr(flatbed_api, "service", lambda: service)
    app = FastAPI()
    app.include_router(flatbed_api.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=owner)

    async def connection():
        yield db

    app.dependency_overrides[flatbed_api.connection] = connection
    return app, f"/v1/flatbed/regions/{region}/image", row, tmp_path, db


@pytest.mark.asyncio
@pytest.mark.parametrize("crop_path", [None, "missing.jpg"])
async def test_missing_crop_returns_rotated_reference_preview_without_promoting_it(preview_api, crop_path):
    app, url, row, root, db = preview_api
    row["crop_path"] = crop_path
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-flatbed-preview"] == "uncorrected-region"
    assert response.headers["cache-control"] == "private, no-store"
    preview = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    assert preview.shape[:2] == (80, 60)
    assert np.max(np.abs(preview[40, 30].astype(int) - (30, 80, 150))) <= 3
    assert row["crop_path"] == crop_path
    assert sorted(p.name for p in root.iterdir()) == ["original.png"]
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_existing_crop_is_served_unchanged(preview_api):
    app, url, row, root, _ = preview_api
    content = (root / "original.png").read_bytes()
    (root / "region.jpg").write_bytes(content)
    row["crop_path"] = "region.jpg"
    row["original_path"] = "missing.png"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url)
    assert response.status_code == 200
    assert response.content == content
    assert "x-flatbed-preview" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["missing_original", "empty_original", "corrupt_original", "unsafe_original", "invalid_bounds", "outside_bounds"])
async def test_unavailable_preview_returns_404_instead_of_500(preview_api, problem):
    app, url, row, root, _ = preview_api
    if problem == "missing_original":
        row["original_path"] = "missing.png"
    elif problem == "empty_original":
        (root / "original.png").write_bytes(b"")
    elif problem == "corrupt_original":
        (root / "original.png").write_bytes(b"not an image")
    elif problem == "unsafe_original":
        row["original_path"] = "../private.png"
    elif problem == "invalid_bounds":
        row["bounds"]["width"] = None
    else:
        row["bounds"]["x"] = 1.5
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_foreign_or_unknown_region_returns_404(preview_api):
    app, url, _, _, db = preview_api
    result = AsyncMock()
    result.fetchone.return_value = None
    db.execute.side_effect = None
    db.execute.return_value = result
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url)
    assert response.status_code == 404
