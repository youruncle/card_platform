"""Shared capture proposals and private, revision-bound collection media."""
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.config import get_settings
from card_platform.crop_proposals import CropProposalStore
from card_platform.image_service import InvalidImageError, detect_image_content_type, read_validated_image
from card_platform.image_variants import ImageVariant, get_image_variant, image_revision
from card_platform.private_uploads import PRIVATE_HEADERS

router = APIRouter(tags=['capture media'])


async def media_user(request: Request) -> AuthenticatedUser:
    try:
        return await current_user(request)
    except HTTPException as error:
        error.headers = {**(error.headers or {}), **PRIVATE_HEADERS}
        raise


def store() -> CropProposalStore:
    return CropProposalStore(get_settings().resolved_upload_dir / '.crop-proposals')


def media_error(error: Exception) -> HTTPException:
    if isinstance(error, FileNotFoundError):
        return HTTPException(404, '图片不存在或已过期', headers=PRIVATE_HEADERS)
    if isinstance(error, TimeoutError):
        return HTTPException(503, '图片处理中，请稍后重试', headers={**PRIVATE_HEADERS, 'Retry-After': '2'})
    if isinstance(error, OSError):
        return HTTPException(503, '图片暂时不可用，请稍后重试', headers={**PRIVATE_HEADERS, 'Retry-After': '2'})
    return HTTPException(422, str(error), headers=PRIVATE_HEADERS)


class ApplyCrop(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quad: list[tuple[float, float]] | None


@router.post('/v1/capture/crop-proposals', status_code=201)
async def create_crop_proposal(
    response: Response, image: UploadFile = File(...), target: Literal['card', 'slab'] = Form('card'),
    user: AuthenticatedUser = Depends(media_user),
):
    try:
        content = await read_validated_image(image, get_settings().max_image_bytes)
        result = await run_in_threadpool(store().create, user.id, content, image.content_type or '', target)
    except (InvalidImageError, ValueError, OSError) as error:
        raise media_error(error) from error
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post('/v1/capture/crop-proposals/{proposal_id}/apply')
async def apply_crop_proposal(
    proposal_id: UUID, payload: ApplyCrop, response: Response,
    user: AuthenticatedUser = Depends(media_user),
):
    try:
        result = await run_in_threadpool(store().apply, user.id, proposal_id, payload.quad)
    except (ValueError, OSError) as error:
        raise media_error(error) from error
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.api_route('/v1/capture/crop-proposals/{proposal_id}/images/{resource}', methods=['GET', 'HEAD'])
async def crop_resource(
    proposal_id: UUID, resource: str, user: AuthenticatedUser = Depends(media_user),
):
    try:
        path = await run_in_threadpool(store().resource, user.id, proposal_id, resource)
        with path.open('rb') as handle:
            content_type = detect_image_content_type(handle.read(16))
    except (ValueError, OSError) as error:
        raise media_error(error) from error
    return FileResponse(path, media_type=content_type, headers=PRIVATE_HEADERS)


@router.api_route('/v1/portfolio/items/{item_id}/images/{side}', methods=['GET', 'HEAD'])
async def portfolio_variant(
    item_id: UUID, side: Literal['front', 'back'], request: Request,
    variant: ImageVariant = 'preview', revision: str | None = None,
    user: AuthenticatedUser = Depends(media_user),
):
    path = await portfolio_image_path(request.app.state.database, user.id, item_id, side, variant, revision)
    return FileResponse(path, headers=PRIVATE_HEADERS)


async def portfolio_image_path(database, owner_id: UUID, item_id: UUID, side: Literal['front', 'back'],
                               variant: ImageVariant = 'preview', revision: str | None = None) -> Path:
    """Shared owner/revision checks for HTTP media and authenticated MCP images."""
    column = 'image_front_url' if side == 'front' else 'image_back_url'
    async with database.connection() as connection:
        result = await connection.execute(
            f'SELECT {column} AS image_url FROM collection_item WHERE id = %s AND owner_id = %s AND deleted_at IS NULL',
            (item_id, owner_id),
        )
        row = await result.fetchone()
    current_revision = image_revision(row['image_url']) if row else None
    if current_revision is None:
        raise HTTPException(404, '图片不存在', headers=PRIVATE_HEADERS)
    if revision is not None and revision != current_revision:
        raise HTTPException(409, '图片版本已更新', headers=PRIVATE_HEADERS)
    root = get_settings().resolved_upload_dir
    source = root / current_revision
    if source.is_symlink() or source.resolve().parent != root.resolve() or Path(current_revision).suffix.lower() not in {'.jpg','.jpeg','.png','.webp'}:
        raise HTTPException(404, '图片不存在', headers=PRIVATE_HEADERS)
    try:
        path = await run_in_threadpool(get_image_variant, source, root / '.image-variants', f'{owner_id}:{item_id}:{side}:{current_revision}', variant)
    except (ValueError, OSError) as error:
        raise media_error(error) from error
    return path
