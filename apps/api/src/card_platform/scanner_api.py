import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from psycopg import AsyncConnection

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.config import get_settings
from card_platform.image_service import InvalidImageError, read_validated_image
from card_platform.nssdk_camera import (
    NssdkCameraController,
    NssdkControlError,
    NssdkUnavailableError,
)
from card_platform.scanner_queue import ScannerQueueService, scanner_job_view
from card_platform.schemas import (
    ScannerCameraControls,
    ScannerCameraControlUpdate,
    ScannerRecognitionJob,
)


router = APIRouter(
    prefix="/v1/scanner",
    tags=["scanner"],
    dependencies=[Depends(current_user)],
)


async def connection(request: Request):
    async with request.app.state.database.connection() as value:
        yield value


@router.get("/camera-controls", response_model=ScannerCameraControls)
async def get_camera_controls() -> ScannerCameraControls:
    try:
        return await asyncio.to_thread(NssdkCameraController(get_settings()).read)
    except (NssdkUnavailableError, NssdkControlError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@router.put("/camera-controls", response_model=ScannerCameraControls)
async def update_camera_controls(values: ScannerCameraControlUpdate) -> ScannerCameraControls:
    try:
        return await asyncio.to_thread(NssdkCameraController(get_settings()).update, values)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    except (NssdkUnavailableError, NssdkControlError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@router.post(
    "/jobs",
    response_model=ScannerRecognitionJob,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_scanner_job(
    front: UploadFile = File(...),
    back: UploadFile = File(...),
    front_raw: UploadFile | None = File(None),
    back_raw: UploadFile | None = File(None),
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> ScannerRecognitionJob:
    settings = get_settings()
    try:
        front_content = await read_validated_image(front, settings.max_image_bytes)
        back_content = await read_validated_image(back, settings.max_image_bytes)
        front_raw_content = (
            await read_validated_image(front_raw, settings.max_image_bytes)
            if front_raw is not None
            else None
        )
        back_raw_content = (
            await read_validated_image(back_raw, settings.max_image_bytes)
            if back_raw is not None
            else None
        )
        return await ScannerQueueService(settings).enqueue(
            db,
            user.id,
            front_content,
            front.content_type or "image/jpeg",
            back_content,
            back.content_type or "image/jpeg",
            front_raw_content=front_raw_content,
            front_raw_content_type=(front_raw.content_type or "image/jpeg") if front_raw else None,
            back_raw_content=back_raw_content,
            back_raw_content_type=(back_raw.content_type or "image/jpeg") if back_raw else None,
        )
    except InvalidImageError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


@router.get("/jobs", response_model=list[ScannerRecognitionJob])
async def list_scanner_jobs(
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
    limit: int = 30,
) -> list[ScannerRecognitionJob]:
    limit = min(100, max(1, limit))
    result = await db.execute(
        """
        SELECT * FROM scanner_recognition_job
        WHERE owner_id = %s AND front_image_path IS NOT NULL AND back_image_path IS NOT NULL
        ORDER BY created_at DESC LIMIT %s
        """,
        (user.id, limit),
    )
    return [scanner_job_view(dict(row)) for row in await result.fetchall()]


@router.get("/jobs/{job_id}", response_model=ScannerRecognitionJob)
async def get_scanner_job(
    job_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> ScannerRecognitionJob:
    result = await db.execute(
        "SELECT * FROM scanner_recognition_job WHERE id = %s AND owner_id = %s",
        (job_id, user.id),
    )
    row = await result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    if not row["front_image_path"] or not row["back_image_path"]:
        raise HTTPException(409, "单面任务请升级客户端并使用 /v1/capture/jobs")
    return scanner_job_view(dict(row))


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scanner_job(
    job_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> Response:
    async with db.transaction():
        selected = await db.execute(
            """
            SELECT * FROM scanner_recognition_job
            WHERE id = %s AND owner_id = %s
            FOR UPDATE
            """,
            (job_id, user.id),
        )
        row = await selected.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="扫描任务不存在")
        await db.execute(
            "DELETE FROM scanner_recognition_job WHERE id = %s AND owner_id = %s",
            (job_id, user.id),
        )
    ScannerQueueService(get_settings()).delete_job_files(dict(row))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/jobs/{job_id}/images/{side}")
async def scanner_job_image(
    job_id: UUID,
    side: str,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> FileResponse:
    if side not in {"front", "back"}:
        raise HTTPException(status_code=404, detail="扫描图片不存在")
    result = await db.execute(
        f"""
        SELECT {side}_image_path AS image_path, {side}_content_type AS content_type
        FROM scanner_recognition_job WHERE id = %s AND owner_id = %s
        """,
        (job_id, user.id),
    )
    row = await result.fetchone()
    if row is None or not row["image_path"]:
        raise HTTPException(status_code=404, detail="扫描图片不存在")
    try:
        path = ScannerQueueService(get_settings()).image_path(row["image_path"])
    except ValueError as error:
        raise HTTPException(status_code=404, detail="扫描图片不存在") from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="扫描图片不存在")
    return FileResponse(path, media_type=row["content_type"], headers={
        "Cache-Control": "private, no-store", "Vary": "Cookie, Authorization",
    })


@router.post("/jobs/{job_id}/retry", response_model=ScannerRecognitionJob)
async def retry_scanner_job(
    job_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> ScannerRecognitionJob:
    result = await db.execute(
        """
        UPDATE scanner_recognition_job
        SET status = 'queued', error_message = NULL, result = NULL,
            started_at = NULL, finished_at = NULL, updated_at = now()
        WHERE id = %s AND owner_id = %s AND status = 'failed'
          AND attempt_count < %s AND front_image_path IS NOT NULL AND back_image_path IS NOT NULL
        RETURNING *
        """,
        (job_id, user.id, get_settings().scanner_job_max_attempts),
    )
    row = await result.fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="该任务当前不能重试")
    if not row["front_image_path"] or not row["back_image_path"]:
        raise HTTPException(409, "单面任务请升级客户端并使用 /v1/capture/jobs")
    return scanner_job_view(dict(row))
