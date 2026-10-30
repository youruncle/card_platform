"""Authenticated auction drafts. There is deliberately no listing publication API."""
import asyncio
from hashlib import sha256
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
import httpx
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from card_platform.api import get_connection
from card_platform.auction_drafts import (
    AuctionMediaStore, DraftConflict, EXTERNAL, IDENTITY_FIELDS, MAX_PHOTOS,
    apply_edit, create_draft, generate_copy, load_draft, owned_item, persist,
    public_draft, validate_selection, change_photo,
)
from card_platform.auth import AuthenticatedUser
from card_platform.capture_media_api import media_user
from card_platform.cardhobby_account import AccountError
from card_platform.cardhobby_account_api import account_service
from card_platform.config import get_settings
from card_platform.image_service import read_validated_image
from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url
from card_platform.private_uploads import PRIVATE_HEADERS
from card_platform.repository import NotFoundError

router = APIRouter(prefix='/v1/auction', tags=['auction drafts'])


class DraftEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    condition_notes: str | None = Field(default=None, max_length=2000)
    selected_photo_ids: list[str] | None = Field(default=None, max_length=MAX_PHOTOS)


class DraftGenerate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    selected_photo_ids: list[str] = Field(min_length=1, max_length=MAX_PHOTOS)
    condition_notes: str = Field(default='', max_length=2000)
    use_order_examples: bool = True
    automatic: bool = False
    edited_title: str | None = Field(default=None, max_length=200)
    edited_description: str | None = Field(default=None, max_length=8000)


class DraftPhotoEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    selected_photo_ids: list[str] | None = Field(default=None, max_length=MAX_PHOTOS)


def media_store():
    # Existing upload volume persists these files; flat /uploads cannot serve subdirectories.
    return AuctionMediaStore(get_settings().resolved_upload_dir / '.auction-media')


def error_response(error):
    if isinstance(error, NotFoundError) or isinstance(error, FileNotFoundError):
        return HTTPException(404, '拍卖草稿或图片不存在', headers=PRIVATE_HEADERS)
    if isinstance(error, DraftConflict):
        return HTTPException(409, str(error), headers=PRIVATE_HEADERS)
    if isinstance(error, OSError):
        return HTTPException(503, '拍卖照片暂不可用，请稍后重试', headers=PRIVATE_HEADERS)
    return HTTPException(422, str(error), headers=PRIVATE_HEADERS)


async def load_order_examples(connection, owner, identity):
    """Read only the current owner's orders; unrelated seller claims never enter copy."""
    try:
        service = account_service()
        async with asyncio.timeout(35):
            status = await service.status(connection, owner)
            if status['state'] != 'connected':
                return [], 'unavailable'
            orders = await service.archive_orders(connection, owner)
            if orders is None:
                keyword = ' '.join(str(identity.get('player_name') or '').split())[:100]
                if not keyword:
                    return [], 'unavailable'
                result = await service.search(connection, owner, [keyword], prefer_live=True)
                orders = result.get('orders', [])
            examples, seen = [], set()
            for order in orders:
                for line in order.get('lines', []):
                    item_url = canonical_public_item_url(line.get('item_url'))
                    title = ' '.join(str(line.get('title') or '').split())[:200]
                    if title and item_url and item_url not in seen:
                        examples.append(dict(title=title, item_url=item_url))
                        seen.add(item_url)
                    if len(examples) >= 3:
                        return examples, 'used'
            return examples, 'used' if examples else 'unavailable'
    except (AccountError, ValueError, OSError, httpx.HTTPError, TimeoutError):
        # Preserve explicit unavailable provenance, but do not swallow DB/programming errors.
        return [], 'unavailable'


@router.post('/items/{item_id}/draft')
async def open_draft(item_id: UUID, response: Response, user: AuthenticatedUser = Depends(media_user),
                     connection: AsyncConnection = Depends(get_connection)):
    try:
        row = await create_draft(connection, user.id, item_id, get_settings().resolved_upload_dir, media_store())
    except (NotFoundError, ValueError, OSError) as error:
        raise error_response(error) from error
    response.headers.update(PRIVATE_HEADERS)
    return public_draft(row)


@router.get('/drafts/{draft_id}')
async def get_draft(draft_id: UUID, response: Response, user: AuthenticatedUser = Depends(media_user),
                    connection: AsyncConnection = Depends(get_connection)):
    try:
        row = await load_draft(connection, user.id, draft_id)
    except NotFoundError as error:
        raise error_response(error) from error
    response.headers.update(PRIVATE_HEADERS)
    return public_draft(row)


@router.patch('/drafts/{draft_id}')
async def edit_draft(draft_id: UUID, payload: DraftEdit, response: Response,
                     user: AuthenticatedUser = Depends(media_user), connection: AsyncConnection = Depends(get_connection)):
    try:
        row = await load_draft(connection, user.id, draft_id, lock=True)
        changes = payload.model_dump(exclude_unset=True, exclude={'revision'})
        if any(value is None for value in changes.values()):
            raise ValueError('草稿字段不能为null，请使用空文本或空选图列表')
        updated = await persist(connection, apply_edit(row, payload.revision, changes))
    except (NotFoundError, ValueError) as error:
        raise error_response(error) from error
    response.headers.update(PRIVATE_HEADERS)
    return public_draft(updated)


@router.post('/drafts/{draft_id}/images')
async def upload_photo(draft_id: UUID, response: Response, revision: int = Form(ge=1),
                       image: UploadFile = File(...), side: Literal['front', 'back', 'detail'] = Form('detail'),
                       source: Literal['upload', 'camera', 'redmi'] = Form('upload'),
                       request_id: UUID | None = Form(default=None),
                       user: AuthenticatedUser = Depends(media_user), connection: AsyncConnection = Depends(get_connection)):
    try:
        row = await load_draft(connection, user.id, draft_id, lock=True)
        if request_id and any(photo.get('request_id') == str(request_id) for photo in row.get('removed_photos', [])):
            raise DraftConflict('这张照片已从草稿移除，请读取最新版本；如需恢复请使用撤销移除')
        prior = next((photo for photo in row['photos'] if request_id and photo.get('request_id') == str(request_id)), None)
        if prior:
            content = await read_validated_image(image, get_settings().max_image_bytes)
            if prior['sha256'] != sha256(content).hexdigest() or prior['side'] != side or prior['source'] != source:
                raise ValueError('照片重试标识已用于其他内容，请重新选择照片')
            if row['revision'] != prior['uploaded_revision'] and revision != row['revision']:
                raise DraftConflict('照片已上传，但草稿随后在其他窗口更新；请读取最新版本核对后继续')
            response.headers.update(PRIVATE_HEADERS)
            return public_draft(row)
        edited = apply_edit(row, revision, {})
        if len(row['photos']) >= MAX_PHOTOS:
            raise ValueError('每个草稿最多保存12张拍卖照片')
        content = await read_validated_image(image, get_settings().max_image_bytes)
        photo = await run_in_threadpool(media_store().save, user.id, draft_id, content, side, source)
        if request_id:
            photo.update(request_id=str(request_id), uploaded_revision=edited['revision'])
        edited['photos'].append(photo)
        edited['selected_photo_ids'].append(photo['id'])
        updated = await persist(connection, edited)
    except (NotFoundError, ValueError, OSError) as error:
        raise error_response(error) from error
    response.headers.update(PRIVATE_HEADERS)
    return public_draft(updated)


@router.post('/drafts/{draft_id}/generate')
async def generate_draft(draft_id: UUID, payload: DraftGenerate, response: Response,
                         user: AuthenticatedUser = Depends(media_user), connection: AsyncConnection = Depends(get_connection)):
    try:
        original = await load_draft(connection, user.id, draft_id)
        # Check before order reads, then recheck under lock after the optional network wait.
        apply_edit(original, payload.revision, {})
        validate_selection(payload.selected_photo_ids, original['photos'])
        item = await owned_item(connection, user.id, original['item_id'])
        identity = {key: item.get(key) for key in IDENTITY_FIELDS}
        examples, examples_status = (await load_order_examples(connection, user.id, identity)
                                     if payload.use_order_examples else ([], 'not_requested'))
        row = await load_draft(connection, user.id, draft_id, lock=True)
        edited = apply_edit(row, payload.revision, dict(selected_photo_ids=payload.selected_photo_ids,
                                                       condition_notes=payload.condition_notes))
        copy = generate_copy(identity, len(payload.selected_photo_ids), payload.condition_notes, examples, examples_status)
        manual_fields = []
        for key in ('title', 'description'):
            override = getattr(payload, 'edited_' + key)
            if not payload.automatic and override is not None:
                raise ValueError('手工文本保留参数仅用于新照片的自动生成')
            if payload.automatic:
                if override is not None:
                    copy[key] = override
                    manual_fields.append(key)
                elif key in row['generation_basis'].get('manual_fields', []):
                    copy[key] = row[key]
                    manual_fields.append(key)
        copy['generation_basis']['manual_fields'] = manual_fields
        edited.update(copy)
        edited['identity'] = identity
        hashes = {photo['id']: photo['sha256'] for photo in row['photos']}
        edited['generation_basis']['photo_sha256'] = [hashes[id] for id in payload.selected_photo_ids]
        updated = await persist(connection, edited)
    except (NotFoundError, ValueError) as error:
        raise error_response(error) from error
    response.headers.update(PRIVATE_HEADERS)
    return public_draft(updated)


@router.api_route('/drafts/{draft_id}/images/{photo_id}', methods=['GET', 'HEAD'])
async def get_photo(draft_id: UUID, photo_id: UUID, user: AuthenticatedUser = Depends(media_user),
                    connection: AsyncConnection = Depends(get_connection)):
    try:
        row = await load_draft(connection, user.id, draft_id)
        photo = next((photo for photo in row['photos'] if photo['id'] == str(photo_id)), None)
        if photo is None:
            raise NotFoundError('拍卖图片不存在')
        path = media_store().resource(user.id, draft_id, photo)
    except (NotFoundError, OSError) as error:
        raise error_response(error) from error
    return FileResponse(path, headers=PRIVATE_HEADERS)


async def update_photo(draft_id, photo_id, payload, response, user, connection, *, restore):
    try:
        row = await load_draft(connection, user.id, draft_id, lock=True)
        edited = change_photo(row, payload.revision, photo_id, restore=restore,
                              selected_photo_ids=payload.selected_photo_ids)
        updated = await persist(connection, edited)
    except (NotFoundError, ValueError) as error:
        raise error_response(error) from error
    response.headers.update(PRIVATE_HEADERS)
    return public_draft(updated)


@router.delete('/drafts/{draft_id}/images/{photo_id}')
async def remove_photo(draft_id: UUID, photo_id: UUID, payload: DraftPhotoEdit, response: Response,
                       user: AuthenticatedUser = Depends(media_user), connection: AsyncConnection = Depends(get_connection)):
    return await update_photo(draft_id, photo_id, payload, response, user, connection, restore=False)


@router.post('/drafts/{draft_id}/images/{photo_id}/restore')
async def restore_photo(draft_id: UUID, photo_id: UUID, payload: DraftPhotoEdit, response: Response,
                        user: AuthenticatedUser = Depends(media_user), connection: AsyncConnection = Depends(get_connection)):
    return await update_photo(draft_id, photo_id, payload, response, user, connection, restore=True)


@router.post('/drafts/{draft_id}/cardhobby')
async def cardhobby_draft(draft_id: UUID, user: AuthenticatedUser = Depends(media_user),
                         connection: AsyncConnection = Depends(get_connection)):
    try:
        await load_draft(connection, user.id, draft_id)
    except NotFoundError as error:
        raise error_response(error) from error
    raise HTTPException(409, dict(code='CARDHOBBY_DRAFT_UNAVAILABLE', message=EXTERNAL['reason']), headers=PRIVATE_HEADERS)
