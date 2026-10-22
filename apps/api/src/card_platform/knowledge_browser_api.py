"""Read-only browsing of one immutable published knowledge version.

Mounted beneath the existing knowledge router and its administrator permission.
Slow, rebuildable indexes run in worker threads and survive request cancellation.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError

from card_platform.knowledge_browser import KnowledgeBrowser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/browse", tags=["knowledge-browser"])
ROOT = Path(__file__).resolve().parents[4]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
Search = Annotated[str | None, Query(max_length=200)]


def _paths(request: Request) -> dict[str, Path]:
    defaults = {
        "data_root": ROOT / "data",
        "artifact_root": ROOT / "data/official-checklists/artifacts",
        "cache_root": Path(os.environ.get("KNOWLEDGE_BROWSER_CACHE_DIR", ROOT / ".runtime/knowledge-browser")),
    }
    defaults.update(getattr(request.app.state, "knowledge_browser_paths", {}))
    return {key: Path(value) for key, value in defaults.items()}


async def published_browser(
    request: Request, version_id: UUID | None = None,
) -> KnowledgeBrowser:
    """Pin an explicitly selected version; otherwise resolve the current publication."""
    connection = request.state.connection
    selected = str(version_id) if version_id else None
    if version_id is None:
        result = await connection.execute(
            "SELECT published_version_id FROM knowledge_workspace WHERE name = 'Card Platform'"
        )
        workspace = await result.fetchone()
        selected = str(workspace["published_version_id"]) if workspace and workspace["published_version_id"] else None
    cache = getattr(request.app.state, "knowledge_browser_cache", None)
    if cache is None:
        cache = OrderedDict()
        request.app.state.knowledge_browser_cache = cache
    key = selected or "unpublished"
    task = cache.get(key)
    if task is None:
        snapshot = {}
        if selected:
            result = await connection.execute(
                "SELECT snapshot, version_number FROM knowledge_version WHERE id = %s",
                (UUID(selected),),
            )
            row = await result.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="知识版本不存在")
            snapshot = dict(row["snapshot"] or {})
            snapshot.setdefault("version", row["version_number"])
        # Another request may have started the same build while the DB read yielded.
        task = cache.get(key)
        if task is None:
            task = asyncio.create_task(asyncio.to_thread(
                KnowledgeBrowser, snapshot, version_id=selected, **_paths(request),
            ))
            cache[key] = task
    cache.move_to_end(key)
    for old_key in list(cache):
        if len(cache) <= 2:
            break
        if old_key != key and cache[old_key].done():
            del cache[old_key]
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=25)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503, detail="知识目录索引正在准备，请稍后重试",
            headers={"Retry-After": "3"},
        ) from exc
    except Exception as exc:
        cache.pop(key, None)
        logger.exception("Unable to build knowledge browser for version %s", selected)
        raise HTTPException(status_code=503, detail="知识目录准备失败，请重试") from exc


Browser = Annotated[KnowledgeBrowser, Depends(published_browser)]


async def _read(browser: KnowledgeBrowser, method: str, *args, **kwargs):
    try:
        return await asyncio.to_thread(getattr(browser, method), *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="未找到该知识条目") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="知识查询参数无效") from exc


@router.get("/summary")
async def summary(browser: Browser):
    return await _read(browser, "summary")


@router.get("/products")
async def products(
    browser: Browser, manufacturer: Search = None, season: int | None = None,
    q: Search = None, has_images: bool | None = None, offset: Offset = 0, limit: Limit = 24,
):
    return await _read(browser, "products", manufacturer=manufacturer, season=season, q=q,
                       has_images=has_images, offset=offset, limit=limit)


@router.get("/products/{product_id:path}")
async def product(product_id: str, browser: Browser):
    return await _read(browser, "product", product_id)


@router.get("/cards")
async def cards(
    browser: Browser, product_id: Search = None, manufacturer: Search = None,
    season: int | None = None, q: Search = None, section: Search = None,
    parallel: Search = None,
    image_state: Literal["any", "has_image", "missing_front", "missing_back"] = "any",
    offset: Offset = 0, limit: Limit = 40,
):
    return await _read(browser, "cards", product_id=product_id, manufacturer=manufacturer,
                       season=season, q=q, section=section, parallel=parallel,
                       image_state=image_state, offset=offset, limit=limit)


@router.get("/cards/{card_id:path}")
async def card(card_id: str, browser: Browser):
    return await _read(browser, "card", card_id)


@router.get("/media")
async def media(
    browser: Browser, product_id: Search = None, card_id: Search = None,
    kind: Literal["all", "design", "specimen"] = "all",
    offset: Offset = 0, limit: Limit = 24,
):
    return await _read(browser, "media", product_id=product_id, card_id=card_id,
                       kind=kind, offset=offset, limit=limit)


@router.get("/entities")
async def entities(
    browser: Browser, entity_type: Search = None, q: Search = None,
    offset: Offset = 0, limit: Limit = 40,
):
    return await _read(browser, "entities", entity_type=entity_type, q=q,
                       offset=offset, limit=limit)


def _image_bytes(browser: KnowledgeBrowser, media_id: str, data_root: Path, thumbnail: bool):
    try:
        path, expected = browser.media_file(media_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="参考图片不存在") from exc
    path = Path(path).resolve()
    if not path.is_relative_to(data_root.resolve()):
        raise HTTPException(status_code=404, detail="参考图片不可访问")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=404, detail="参考图片文件缺失") from exc
    if len(raw) > 40 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
        raise HTTPException(status_code=409, detail="参考图片校验失败")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > 60_000_000:
                raise ValueError("Image size limit")
            content_type = Image.MIME.get(image.format)
            native_format = content_type in {"image/jpeg", "image/png", "image/webp", "image/gif"}
            if not native_format and image.format not in {"JPEG2000", "TIFF", "BMP", "MPO"}:
                raise ValueError("Unsupported image format")
            # Captured sell-sheet images include CMYK JPEG2000. Browsers cannot
            # reliably decode those bytes; render a display derivative after
            # verifying the original hash, without changing the source asset.
            if thumbnail or not native_format:
                image = ImageOps.exif_transpose(image)
                if thumbnail:
                    image.thumbnail((320, 480))
                image = image.convert("RGB")
                buffer = BytesIO()
                image.save(buffer, "JPEG", quality=82 if thumbnail else 94)
                return buffer.getvalue(), "image/jpeg"
            image.verify()
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=409, detail="参考图片内容无效") from exc
    return raw, content_type


@router.get("/media/{media_id:path}/image")
async def image(
    media_id: str, request: Request, browser: Browser, thumbnail: bool = False,
):
    raw, content_type = await asyncio.to_thread(
        _image_bytes, browser, media_id, _paths(request)["data_root"], thumbnail,
    )
    return Response(raw, media_type=content_type, headers={
        "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff",
    })
