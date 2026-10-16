"""Mobile sessions and idempotent, human-confirmed captures. No public registration."""

from datetime import datetime
import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile
from psycopg import AsyncConnection
from pydantic import BaseModel

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.auth_api import authenticate_login, connection
from card_platform.config import get_settings
from card_platform.image_service import InvalidImageError, read_validated_image
from card_platform.scanner_queue import scanner_job_view
from card_platform.schemas import (
    AuthLogin,
    AuthUser,
    CollectionItemCreate,
    CreatedResource,
    ScannerRecognitionJob,
)

router = APIRouter(prefix="/v1/mobile", tags=["mobile"])


class MobileSession(BaseModel):
    user: AuthUser
    token: str
    expires_at: datetime


@router.post("/auth/login", response_model=MobileSession)
async def mobile_login(
    payload: AuthLogin,
    request: Request,
    response: Response,
    db: AsyncConnection = Depends(connection),
) -> MobileSession:
    user, token, expires_at = await authenticate_login(payload, request, db)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return MobileSession(user=user, token=token, expires_at=expires_at)


@router.post("/jobs", response_model=ScannerRecognitionJob, status_code=202)
async def upload_capture(
    front: UploadFile = File(...),
    back: UploadFile = File(...),
    idempotency_key: UUID = Header(...),
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> ScannerRecognitionJob:
    settings = get_settings()
    try:
        front_bytes = await read_validated_image(front, settings.max_image_bytes)
        back_bytes = await read_validated_image(back, settings.max_image_bytes)
    except InvalidImageError as error:
        raise HTTPException(422, str(error)) from error
    digest = hashlib.sha256(
        hashlib.sha256(front_bytes).digest() + hashlib.sha256(back_bytes).digest()
    ).hexdigest()
    from card_platform.capture_service import CaptureService
    job = await CaptureService(settings).enqueue(
        db,user.id,idempotency_key,
        {"front":(front_bytes,front.content_type),"back":(back_bytes,back.content_type)},
        legacy_digest=digest,
    )
    return ScannerRecognitionJob.model_validate(job.model_dump())


@router.get("/jobs", response_model=list[ScannerRecognitionJob])
async def list_captures(
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> list[ScannerRecognitionJob]:
    rows = await db.execute(
        """SELECT * FROM scanner_recognition_job WHERE owner_id=%s AND require_confirmation AND front_image_path IS NOT NULL AND back_image_path IS NOT NULL
        ORDER BY created_at DESC LIMIT 100""",
        (user.id,),
    )
    return [scanner_job_view(dict(row)) for row in await rows.fetchall()]


@router.post("/jobs/{job_id}/confirm", response_model=CreatedResource, status_code=201)
async def confirm_capture(
    job_id: UUID,
    payload: CollectionItemCreate,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> CreatedResource:
    from card_platform.capture_service import CaptureService
    return await CaptureService(get_settings()).confirm(db,user.id,job_id,payload.model_dump(mode="json"))
