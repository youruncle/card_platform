"""Scanner previews must not transfer full-resolution scans into small UI tiles."""
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
def image_api(tmp_path, monkeypatch):
    owner, sheet, region = uuid4(), uuid4(), uuid4()
    pixels = np.random.default_rng(7).integers(0, 256, (1800, 1200, 3), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "original.png"), pixels)
    cv2.imwrite(str(tmp_path / "crop.jpg"), pixels)
    row = {"original_path": "original.png", "content_type": "image/png",
           "crop_path": "crop.jpg", "bounds": {"x": 0, "y": 0, "width": 1, "height": 1},
           "signals": {"orientation_rotation_degrees": 90}}
    db = AsyncMock()

    async def execute(sql, params):
        assert params in {(sheet, owner), (region, owner)}
        result = AsyncMock()
        result.fetchone.return_value = row
        return result

    db.execute.side_effect = execute
    monkeypatch.setattr(flatbed_api, "service", lambda: FlatbedService(str(tmp_path), "unused"))
    app = FastAPI()
    app.include_router(flatbed_api.router)
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=owner)
    app.dependency_overrides[flatbed_api.connection] = lambda: db
    return app, f"/v1/flatbed/sheets/{sheet}/image", f"/v1/flatbed/regions/{region}/image", row, tmp_path, db


@pytest.mark.asyncio
@pytest.mark.parametrize("resource,variant,edge", [(1, "preview", 960), (2, "thumb", 320)])
async def test_small_variants_preserve_ratio_and_original_bytes(image_api, resource, variant, edge):
    app, _, _, _, root, _ = image_api
    source = root / ("original.png" if resource == 1 else "crop.jpg")
    original = source.read_bytes()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        small = await client.get(image_api[resource], params={"variant": variant})
        full = await client.get(image_api[resource])
    assert small.status_code == 200
    decoded = cv2.imdecode(np.frombuffer(small.content, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (edge, round(edge * 2 / 3))
    assert len(small.content) < len(original) / 5
    assert full.content == original == source.read_bytes()
    assert small.headers["content-type"] == "image/jpeg"
    assert "private" in small.headers["cache-control"]


@pytest.mark.asyncio
async def test_thumbnail_revalidates_after_rotation_and_authorizes_cache_hits(image_api):
    app, _, url, row, root, db = image_api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(url, params={"variant": "thumb"})
        headers = {"If-None-Match": first.headers["etag"]}
        cached = await client.get(url, params={"variant": "thumb"}, headers=headers)
        assert cached.status_code == 304
        assert not cached.content
        pixels = cv2.imread(str(root / "crop.jpg"))
        cv2.imwrite(str(root / "crop.jpg"), cv2.rotate(pixels, cv2.ROTATE_90_CLOCKWISE))
        rotated = await client.get(url, params={"variant": "thumb"}, headers=headers)
        assert rotated.status_code == 200
        assert rotated.headers["etag"] != first.headers["etag"]
        decoded = cv2.imdecode(np.frombuffer(rotated.content, np.uint8), cv2.IMREAD_COLOR)
        assert decoded.shape[:2] == (213, 320)
        db.execute.side_effect = None
        db.execute.return_value.fetchone.return_value = None
        denied = await client.get(url, params={"variant": "thumb"}, headers=headers)
        assert denied.status_code == 404


@pytest.mark.asyncio
async def test_unresolved_region_thumbnail_stays_marked_and_cannot_become_a_crop(image_api):
    app, _, url, row, root, _ = image_api
    row["crop_path"] = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(url, params={"variant": "thumb"})
    assert response.status_code == 200
    assert response.headers["x-flatbed-preview"] == "uncorrected-region"
    decoded = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (213, 320)
    assert row["crop_path"] is None
    assert sorted(p.name for p in root.iterdir()) == ["crop.jpg", "original.png"]


@pytest.mark.asyncio
async def test_invalid_variant_and_missing_sources_do_not_serve_cached_images(image_api):
    app, sheet, _, _, root, _ = image_api
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(sheet, params={"variant": "huge"})).status_code == 422
        assert (await client.get(sheet, params={"variant": "preview"})).status_code == 200
        (root / "original.png").unlink()
        assert (await client.get(sheet, params={"variant": "preview"})).status_code == 404
