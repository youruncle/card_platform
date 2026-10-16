"""Versioned single/pair capture API; cookie and bearer share ownership checks."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from psycopg import AsyncConnection
from starlette.concurrency import run_in_threadpool

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.auth_api import connection
from card_platform.config import get_settings
from card_platform.capture_service import (
    CaptureService,
    capture_job_view,
    CAPTURE_TARGET_SQL,
    IDENTITY_ACCEPTANCE_REASON,
)
from card_platform.image_service import (
    read_validated_image,
    InvalidImageError,
    detect_image_content_type,
)
from card_platform.schemas import CaptureJob, CreatedResource, CardIdentityInput

router = APIRouter(prefix="/v1/capture", tags=["capture"])


@router.post("/jobs", response_model=CaptureJob, status_code=202)
async def upload_capture(
    front: UploadFile | None = File(None),
    back: UploadFile | None = File(None),
    front_source: UploadFile | None = File(None),
    back_source: UploadFile | None = File(None),
    crop_recipe: str | None = Form(None),
    target_item_id: UUID | None = Form(None),
    expected_revision: datetime | None = Form(None),
    idempotency_key: UUID = Header(...),
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
):
    if not front and not back:
        raise HTTPException(422, "至少提交正面或背面照片")
    if bool(target_item_id) != bool(expected_revision):
        raise HTTPException(422, "补拍需要 target_item_id 和 expected_revision")
    try:
        recipe = json.loads(crop_recipe) if crop_recipe else {}
        if not isinstance(recipe, dict) or set(recipe) - {"front", "back"}:
            raise ValueError("crop_recipe must contain only front/back recipes")
        images, sources = {}, {}
        for side, image, source in [("front", front, front_source), ("back", back, back_source)]:
            if (source or side in recipe) and not image:
                raise ValueError("source/recipe requires submitted side")
            if side in recipe and (not isinstance(recipe[side], dict) or not source):
                raise ValueError("crop_recipe requires an object and source image")
            if image:
                images[side] = (
                    await read_validated_image(image, get_settings().max_image_bytes),
                    image.content_type,
                )
            if source:
                sources[side] = (
                    await read_validated_image(source, get_settings().max_image_bytes),
                    source.content_type,
                )
        from card_platform.media_validation import validate_image_header

        for content, _content_type in [*images.values(), *sources.values()]:
            validate_image_header(content)
        from card_platform.crop_proposals import validate_quad

        for side, edit in recipe.items():
            actual_hash = hashlib.sha256(sources[side][0]).hexdigest()
            if edit.get("source_hash") is not None and edit["source_hash"] != actual_hash:
                raise ValueError("crop recipe source hash does not match source image")
            if edit.get("quad") is not None:
                validate_quad(edit["quad"])
            if edit.get("rotation", 0) not in (0, 90, 180, 270):
                raise ValueError("crop rotation must be 0, 90, 180 or 270")
    except (InvalidImageError, ValueError) as error:
        raise HTTPException(422, str(error)) from error
    return await CaptureService(get_settings()).enqueue(
        db,
        user.id,
        idempotency_key,
        images,
        sources=sources,
        crop_recipe=recipe,
        target_item_id=target_item_id,
        expected_revision=expected_revision,
    )


async def owned_job(db, owner_id, job_id):
    row = await (
        await db.execute(
            "SELECT * FROM scanner_recognition_job WHERE id=%s AND owner_id=%s", (job_id, owner_id)
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    return row


@router.get("/jobs", response_model=list[CaptureJob])
async def list_jobs(
    user: AuthenticatedUser = Depends(current_user), db: AsyncConnection = Depends(connection)
):
    rows = await (
        await db.execute(
            "SELECT * FROM scanner_recognition_job WHERE owner_id=%s AND require_confirmation ORDER BY created_at DESC LIMIT 100",
            (user.id,),
        )
    ).fetchall()
    return [capture_job_view(row) for row in rows]


@router.get("/jobs/{job_id}", response_model=CaptureJob)
async def get_job(
    job_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
):
    return capture_job_view(await owned_job(db, user.id, job_id))


@router.post("/jobs/{job_id}/confirm", response_model=CreatedResource, status_code=201)
async def confirm(
    job_id: UUID,
    payload: dict,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
):
    return await CaptureService(get_settings()).confirm(db, user.id, job_id, payload)


@router.post("/jobs/{job_id}/retry", response_model=CaptureJob)
async def retry(
    job_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
):
    await owned_job(db, user.id, job_id)
    row = await (
        await db.execute(
            """UPDATE scanner_recognition_job SET status='queued', result=NULL,error_message=NULL,
        started_at=NULL,finished_at=NULL,updated_at=now(),revision=revision+1
        WHERE id=%s AND owner_id=%s AND status='failed' AND collection_item_id IS NULL AND attempt_count<%s RETURNING *""",
            (job_id, user.id, get_settings().scanner_job_max_attempts),
        )
    ).fetchone()
    if not row:
        raise HTTPException(409, "该任务当前不能重试")
    return capture_job_view(row)


@router.api_route("/jobs/{job_id}/images/{side}", methods=["GET", "HEAD"])
async def image(
    job_id: UUID,
    side: Literal["front", "back"],
    variant: Literal["thumb", "preview", "original"] = "original",
    revision: str | None = None,
    source: bool = False,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
):
    row = await owned_job(db, user.id, job_id)
    if revision is not None and revision != str(row["revision"]):
        raise HTTPException(409, "图片版本已更新")
    relative = row[f"{side}_raw_image_path" if source else f"{side}_image_path"]
    if not relative:
        raise HTTPException(404, "该面照片不存在")
    settings = get_settings()
    path = CaptureService(settings).queue.image_path(relative)
    if not path.is_file():
        raise HTTPException(404, "照片不存在")
    content_type = (
        detect_image_content_type(await run_in_threadpool(path.read_bytes))
        or row[f"{side}_content_type"]
    )
    if variant != "original":
        from card_platform.image_variants import get_image_variant

        try:
            path = await run_in_threadpool(
                get_image_variant,
                path,
                settings.resolved_upload_dir / ".image-variants",
                f"capture-{job_id}-{side}-{row['revision']}-{source}",
                variant,
            )
        except FileNotFoundError as error:
            raise HTTPException(404, "照片不存在") from error
        except ValueError as error:
            raise HTTPException(422, "照片无法处理") from error
        except OSError as error:
            raise HTTPException(
                503, "图片处理繁忙，请重试", headers={"Retry-After": "2"}
            ) from error
        content_type = "image/jpeg"
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "Vary": "Cookie, Authorization"},
    )


@router.get("/items/{item_id}")
async def capture_target(
    item_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
):
    row = await (
        await db.execute(
            CAPTURE_TARGET_SQL,
            (item_id, user.id),
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, "卡片不存在")
    return {
        "id": str(row["id"]),
        "revision": row["updated_at"].isoformat(),
        "identity": CardIdentityInput.model_validate(dict(row)).model_dump(mode="json"),
        "can_accept_identity": row["can_accept_identity"],
        "identity_acceptance_reason": None
        if row["can_accept_identity"]
        else IDENTITY_ACCEPTANCE_REASON,
        "available_sides": [s for s in ("front", "back") if row[f"image_{s}_url"]],
    }
