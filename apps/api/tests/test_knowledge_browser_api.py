"""Browser contracts: published data, bounded requests and protected image bytes."""

from contextlib import asynccontextmanager
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from card_platform.auth import current_user
from card_platform.knowledge_api import router


VERSION = UUID("15e775d8-35cd-46dc-90bc-710ba9b92aa1")


@pytest.fixture
def browser_client(tmp_path, monkeypatch, request):
    image_format = getattr(request, "param", "JPEG")
    filename = "example.jp2" if image_format == "JPEG2000" else "example.jpg"
    image_path = tmp_path / "batch" / "media" / filename
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (600, 850), "green").save(image_path, format=image_format)
    digest = sha256(image_path.read_bytes()).hexdigest()
    snapshot = {
        "version": 9,
        "entities": [
            {"id": "maker", "entity_type": "manufacturer", "canonical_name": "Panini"},
            {
                "id": "p", "entity_type": "product", "canonical_name": "2025-26 Court Kings",
                "aliases": ["油画"],
                "attributes": {
                    "manufacturer": "Panini", "product": "Court Kings",
                    "season_start": 2025, "season_end": 2026,
                    "reference_images": [{
                        "asset_id": "design-1", "asset_path": "batch/media/" + filename,
                        "sha256": digest, "side": "front", "image_type": "official_design",
                        "source_url": "https://example.com/design", "visibility": "public_reference",
                    }],
                },
            },
            {
                "id": "c", "entity_type": "card", "canonical_name": "Court Kings #1 Cade",
                "attributes": {
                    "manufacturer": "Panini", "brand": "Panini", "product": "Court Kings",
                    "season_start": 2025, "season_end": 2026,
                    "player": "Cade Cunningham", "card_number": "1", "section": "Art Nouveau",
                    "source_url": "https://example.com/checklist", "source_locator": "page 1 line 2",
                },
            },
        ],
    }

    class Connection:
        async def execute(self, sql, params=()):
            found = not ("FROM knowledge_version" in sql and params and str(params[0]) != str(VERSION))
            row = {
                "published_version_id": VERSION, "id": VERSION, "version_number": 9,
                "snapshot": snapshot,
            } if found else None

            async def fetchone():
                return row

            return SimpleNamespace(fetchone=fetchone)

    @asynccontextmanager
    async def connection():
        yield Connection()

    app = FastAPI()
    app.include_router(router)
    app.state.database = SimpleNamespace(connection=connection)
    app.state.knowledge_browser_paths = {
        "data_root": tmp_path, "artifact_root": tmp_path / "artifacts",
        "cache_root": tmp_path / "cache",
    }
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(role="admin")
    with TestClient(app) as client:
        yield app, client, image_path


def test_browse_requires_existing_admin_permission(browser_client):
    app, client, _ = browser_client
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(role="user")
    assert client.get("/v1/knowledge/browse/summary").status_code == 403
    app.dependency_overrides.clear()
    assert client.get("/v1/knowledge/browse/summary").status_code == 401


def test_summary_products_and_card_details_use_published_snapshot(browser_client):
    _, client, _ = browser_client
    summary = client.get("/v1/knowledge/browse/summary")
    assert summary.status_code == 200
    assert summary.json()["version"] == 9
    assert summary.json()["version_id"] == str(VERSION)
    assert summary.json()["summary"]["card_facts"] == 1
    products = client.get("/v1/knowledge/browse/products?q=油画&limit=1").json()
    assert products["total"] == 1
    product_id = products["items"][0]["id"]
    detail = client.get(f"/v1/knowledge/browse/products/{product_id}")
    assert detail.status_code == 200
    cards = client.get("/v1/knowledge/browse/cards", params={"product_id": product_id}).json()
    assert cards["items"][0]["card_number"] == "1"
    card = client.get("/v1/knowledge/browse/cards/c")
    assert card.status_code == 200
    assert card.json()["card"]["player"] == "Cade Cunningham"
    assert card.json()["images"] == []  # A product design is not a card photo.


@pytest.mark.parametrize("query", ["limit=101", "limit=0", "offset=-1", "season=abc", "image_state=bogus"])
def test_card_query_parameters_are_bounded(browser_client, query):
    _, client, _ = browser_client
    assert client.get("/v1/knowledge/browse/cards?" + query).status_code == 422


def test_version_pin_unknown_ids_and_media_paths_are_rejected(browser_client):
    _, client, _ = browser_client
    assert client.get("/v1/knowledge/browse/summary?version_id=bad").status_code == 422
    assert client.get("/v1/knowledge/browse/summary?version_id=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").status_code == 404
    assert client.get("/v1/knowledge/browse/products/not-found").status_code == 404
    assert client.get("/v1/knowledge/browse/cards/not-found").status_code == 404
    assert client.get("/v1/knowledge/browse/media/not-found/image").status_code == 404


def test_media_bytes_and_bounded_thumbnail_require_auth_and_checksum(browser_client):
    app, client, image_path = browser_client
    media = client.get("/v1/knowledge/browse/media?kind=design")
    assert media.status_code == 200
    item = media.json()["items"][0]
    original = client.get(item["image_url"])
    assert original.status_code == 200
    assert original.content == image_path.read_bytes()
    thumb = client.get(item["thumbnail_url"])
    assert thumb.status_code == 200
    with Image.open(BytesIO(thumb.content)) as image:
        assert max(image.size) <= 480
    image_path.write_bytes(b"corrupt")
    assert client.get(item["image_url"]).status_code == 409
    image_path.unlink()
    assert client.get(item["image_url"]).status_code == 404
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(role="user")
    assert client.get(item["image_url"]).status_code == 403


def test_glossary_is_paged_and_design_filter_does_not_imply_card_image(browser_client):
    _, client, _ = browser_client
    glossary = client.get("/v1/knowledge/browse/entities?entity_type=product&q=油画&limit=1")
    assert glossary.status_code == 200
    assert glossary.json()["total"] == 1
    cards = client.get("/v1/knowledge/browse/cards?image_state=has_image").json()
    assert cards["total"] == 0


@pytest.mark.parametrize("browser_client", ["JPEG2000"], indirect=True)
def test_captured_jpeg2000_is_displayable_without_changing_original(browser_client):
    _, client, image_path = browser_client
    original = image_path.read_bytes()
    item = client.get("/v1/knowledge/browse/media").json()["items"][0]
    response = client.get(item["image_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (600, 850)
        assert image.format == "JPEG"
    assert image_path.read_bytes() == original
    assert client.get(item["thumbnail_url"]).status_code == 200
