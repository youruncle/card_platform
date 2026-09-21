from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from psycopg import AsyncConnection

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.config import get_settings
from card_platform.flatbed_detection import Detection
from card_platform.flatbed_service import FlatbedBridgeError, FlatbedService, ordered_source_quad
from card_platform.image_service import InvalidImageError, read_validated_image
from card_platform.scanner_queue import ScannerQueueService

router = APIRouter(prefix="/v1/flatbed", tags=["flatbed"], dependencies=[Depends(current_user)])

_capture_progress: dict[UUID, dict] = {}
_CAPTURE_PROGRESS_TTL = timedelta(minutes=10)


def _manual_layout_detections(payload: dict) -> list[Detection]:
    """Validate editable normalized quads before creating a sheet revision."""
    if not isinstance(payload, dict):
        raise HTTPException(422, "人工布局请求格式无效")
    raw_regions = payload.get("regions") or []
    if not isinstance(raw_regions, list) or not 1 <= len(raw_regions) <= 30:
        raise HTTPException(422, "人工布局必须包含 1 至 30 个区域")

    detections: list[Detection] = []
    for raw in raw_regions:
        if not isinstance(raw, dict):
            raise HTTPException(422, "每个区域必须提供四个有效归一化角点")
        raw_points = raw.get("points")
        if not isinstance(raw_points, list) or len(raw_points) != 4:
            raise HTTPException(422, "每个区域必须提供四个有效归一化角点")
        try:
            points = tuple(
                (float(point["x"]), float(point["y"]))
                for point in raw_points
                if isinstance(point, dict)
                and not isinstance(point.get("x"), bool)
                and not isinstance(point.get("y"), bool)
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise HTTPException(422, "每个区域必须提供四个有效归一化角点") from None
        coordinates = np.asarray(points, dtype=np.float64)
        if (
            coordinates.shape != (4, 2)
            or not np.isfinite(coordinates).all()
            or (coordinates < 0).any()
            or (coordinates > 1).any()
        ):
            raise HTTPException(422, "每个区域必须提供四个有效归一化角点")

        xs, ys = coordinates[:, 0], coordinates[:, 1]
        if float(np.ptp(xs)) < 0.02 or float(np.ptp(ys)) < 0.02:
            raise HTTPException(422, "人工区域过小")
        try:
            # ``ordered_source_quad`` uses a one-pixel area floor. Scale the
            # normalized geometry only for validation so a valid small quad is
            # not mistaken for a degenerate pixel polygon.
            ordered_source_quad((coordinates * 10_000).astype(np.float32))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

        normalized_points = tuple((float(x), float(y)) for x, y in coordinates)
        detections.append(
            Detection(
                float(xs.min()),
                float(ys.min()),
                float(np.ptp(xs)),
                float(np.ptp(ys)),
                0,
                1,
                points=normalized_points,
                evidence=("manual_override",),
            )
        )
    return detections


def _stored_crop_confidence(row: dict, side: str) -> float:
    """Keep detector/validator uncertainty when a flatbed crop enters the queue."""
    confidence = float(row.get(f"{side}_confidence") or 0.0)
    signals = row.get(f"{side}_signals") or {}
    quality = signals.get("crop_quality") if isinstance(signals, dict) else None
    if isinstance(quality, dict) and quality.get("usability_score") is not None:
        confidence = min(confidence, float(quality["usability_score"]))
    return round(max(0.0, min(1.0, confidence)), 4)


def _publish_capture_progress(
    capture_id: UUID,
    *,
    owner_id: UUID,
    session_id: UUID,
    side: str,
    phase: str,
    detail: str | None = None,
) -> None:
    now = datetime.now(UTC)
    expired = [
        key
        for key, value in _capture_progress.items()
        if now - value["updated_at"] > _CAPTURE_PROGRESS_TTL
    ]
    for key in expired:
        _capture_progress.pop(key, None)
    _capture_progress[capture_id] = {
        "capture_id": capture_id,
        "owner_id": owner_id,
        "session_id": session_id,
        "side": side,
        "phase": phase,
        "detail": detail,
        "updated_at": now,
    }


@router.get("/captures/{capture_id}/status")
async def capture_status(
    capture_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
) -> dict:
    progress = _capture_progress.get(capture_id)
    if progress is None or progress["owner_id"] != user.id:
        raise HTTPException(404, "扫描进度不存在")
    return {key: value for key, value in progress.items() if key != "owner_id"}


async def connection(request: Request):
    async with request.app.state.database.connection() as value:
        yield value


def service() -> FlatbedService:
    settings = get_settings()
    return FlatbedService(
        settings.flatbed_scan_dir,
        settings.flatbed_bridge_script,
        settings.flatbed_ocr_script,
        ml_enabled=settings.flatbed_ml_enabled,
        ml_python=settings.flatbed_ml_python,
        ml_worker_script=settings.flatbed_ml_worker_script,
        ml_checkpoint=settings.flatbed_ml_checkpoint,
        ml_timeout_seconds=settings.flatbed_ml_timeout_seconds,
    )


@router.get("/device")
async def device() -> dict:
    try:
        events = await service().bridge("capabilities", timeout=15)
        return {
            "status": "ready",
            "name": "Canon CanoScan LiDE 400",
            "serial": "4706E5",
            "capabilities": events[-1],
        }
    except FlatbedBridgeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: dict,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    dpi = int(payload.get("dpi", 300))
    if dpi not in {300, 600}:
        raise HTTPException(422, "DPI 仅支持 300 或 600")
    return await service().create_session(db, user.id, dpi)


@router.get("/sessions")
async def sessions(
    user: AuthenticatedUser = Depends(current_user), db: AsyncConnection = Depends(connection)
) -> list[dict]:
    result = await db.execute(
        "SELECT * FROM flatbed_scan_session WHERE owner_id=%s ORDER BY created_at DESC LIMIT 30",
        (user.id,),
    )
    return [dict(row) for row in await result.fetchall()]


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    result = await db.execute(
        "SELECT * FROM flatbed_scan_session WHERE id=%s AND owner_id=%s", (session_id, user.id)
    )
    row = await result.fetchone()
    if row is None:
        raise HTTPException(404, "扫描会话不存在")
    pairs = await db.execute(
        "SELECT * FROM flatbed_pair_proposal WHERE session_id=%s AND owner_id=%s ORDER BY created_at",
        (session_id, user.id),
    )
    regions = await db.execute(
        """SELECT r.*,s.side,s.revision FROM flatbed_scan_region r
           JOIN flatbed_scan_sheet s ON s.id=r.sheet_id
           WHERE s.session_id=%s AND r.owner_id=%s
             AND s.revision=(SELECT max(latest.revision) FROM flatbed_scan_sheet latest
                             WHERE latest.session_id=s.session_id AND latest.side=s.side)
           ORDER BY s.side,r.ordinal""",
        (session_id, user.id),
    )
    sheets = await db.execute(
        """SELECT id,side,revision,content_type,created_at,detector_version,layout_hypothesis,front_to_back_prior,requires_review,derived_version FROM flatbed_scan_sheet s
           WHERE session_id=%s AND owner_id=%s
             AND revision=(SELECT max(latest.revision) FROM flatbed_scan_sheet latest
                           WHERE latest.session_id=s.session_id AND latest.side=s.side)
           ORDER BY side""",
        (session_id, user.id),
    )
    return {
        **dict(row),
        "pairs": [dict(item) for item in await pairs.fetchall()],
        "regions": [dict(item) for item in await regions.fetchall()],
        "sheets": [dict(item) for item in await sheets.fetchall()],
    }


@router.post("/sessions/{session_id}/sheets/{side}", status_code=201)
async def upload_sheet(
    session_id: UUID,
    side: str,
    image: UploadFile = File(...),
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    if side not in {"front", "back"}:
        raise HTTPException(404, "扫描面不存在")
    try:
        content = await read_validated_image(image, get_settings().max_image_bytes * 8)
        return await service().store_sheet(
            db, user.id, session_id, side, content, image.content_type or "image/jpeg"
        )
    except (InvalidImageError, ValueError) as error:
        raise HTTPException(422, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.put("/sheets/{sheet_id}/layout", status_code=201)
async def override_layout(
    sheet_id: UUID,
    payload: dict,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    """Create a derived revision from editable quads without changing source bytes."""
    source = await db.execute(
        "SELECT session_id,side,original_path,content_type,layout_hypothesis FROM flatbed_scan_sheet WHERE id=%s AND owner_id=%s",
        (sheet_id, user.id),
    )
    row = await source.fetchone()
    if row is None:
        raise HTTPException(404, "整版扫描图片不存在")
    detections = _manual_layout_detections(payload)
    flatbed = service()
    path = flatbed.resolve_path(row["original_path"])
    return await flatbed.store_sheet(
        db,
        user.id,
        row["session_id"],
        row["side"],
        path.read_bytes(),
        row["content_type"],
        detections_override=detections,
        source_resolution=(row.get("layout_hypothesis") or {}).get("source_resolution"),
    )


@router.post("/sheets/{sheet_id}/reprocess", status_code=201)
async def reprocess_sheet(
    sheet_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    """Create a new detector revision from immutable source bytes, without rescanning."""
    source = await db.execute(
        "SELECT session_id,side,original_path,content_type,layout_hypothesis FROM flatbed_scan_sheet WHERE id=%s AND owner_id=%s",
        (sheet_id, user.id),
    )
    row = await source.fetchone()
    if row is None:
        raise HTTPException(404, "整版扫描图片不存在")
    flatbed = service()
    path = flatbed.resolve_path(row["original_path"])
    return await flatbed.store_sheet(
        db,
        user.id,
        row["session_id"],
        row["side"],
        path.read_bytes(),
        row["content_type"],
        source_resolution=(row.get("layout_hypothesis") or {}).get("source_resolution"),
    )


@router.post("/sessions/{session_id}/capture/{side}", status_code=201)
async def capture_sheet(
    session_id: UUID,
    side: str,
    preview: bool = False,
    capture_id: UUID | None = None,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    """Scan from LiDE 400 directly; the CLI process owns and always closes the session."""
    if side not in {"front", "back"}:
        raise HTTPException(404, "扫描面不存在")
    session_result = await db.execute(
        "SELECT dpi FROM flatbed_scan_session WHERE id=%s AND owner_id=%s",
        (session_id, user.id),
    )
    session = await session_result.fetchone()
    if session is None:
        raise HTTPException(404, "扫描会话不存在")
    progress_id = capture_id or uuid4()
    _publish_capture_progress(
        progress_id,
        owner_id=user.id,
        session_id=session_id,
        side=side,
        phase="scanning",
    )
    capture_dir = service().root / str(user.id) / str(session_id) / "captures"
    requested_path = capture_dir / f"{side}-{uuid4()}.png"
    try:
        events = await service().bridge(
            "preview" if preview else "scan",
            output=requested_path,
            dpi=session["dpi"],
            timeout=90 if preview else 240,
        )
        completed = next(
            (item for item in reversed(events) if item.get("event") == "scan_complete"), None
        )
        path = (Path(completed["path"]) if completed else requested_path).resolve()
        if not path.is_relative_to(capture_dir.resolve()):
            raise FlatbedBridgeError("扫描仪返回了不安全的输出路径")
        if not path.is_file():
            raise FlatbedBridgeError("扫描完成但未生成图像")
        _publish_capture_progress(
            progress_id,
            owner_id=user.id,
            session_id=session_id,
            side=side,
            phase="processing",
        )
        native_resolution = (completed or {}).get("source_resolution")
        stored = await service().store_sheet(
            db, user.id, session_id, side, path.read_bytes(), "image/png",
            source_resolution={
                **(native_resolution if isinstance(native_resolution, dict) else {}),
                "capture_kind": "preview" if preview else "scan",
            },
        )
        path.unlink()
        try:
            capture_dir.rmdir()
        except OSError:
            pass
        _publish_capture_progress(
            progress_id,
            owner_id=user.id,
            session_id=session_id,
            side=side,
            phase="completed",
        )
        return stored
    except FlatbedBridgeError as error:
        detail = str(error)
        _publish_capture_progress(
            progress_id,
            owner_id=user.id,
            session_id=session_id,
            side=side,
            phase="failed",
            detail=detail,
        )
        code = 409 if "busy" in detail.lower() or "in use" in detail.lower() else 503
        raise HTTPException(code, detail) from error
    except Exception as error:
        _publish_capture_progress(
            progress_id,
            owner_id=user.id,
            session_id=session_id,
            side=side,
            phase="failed",
            detail=str(error),
        )
        raise


@router.post("/sessions/{session_id}/pair")
async def pair(
    session_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    locked = await db.execute(
        "SELECT status,submitted_at FROM flatbed_scan_session WHERE id=%s AND owner_id=%s FOR UPDATE",
        (session_id, user.id),
    )
    session = await locked.fetchone()
    if session is None:
        raise HTTPException(404, "扫描会话不存在")
    if session["submitted_at"] is not None or session["status"] == "submitted":
        raise HTTPException(409, "扫描会话已经录入，不能重新生成配对")
    return await service().pair(db, user.id, session_id)


@router.put("/sessions/{session_id}/pairs")
async def override_pairs(
    session_id: UUID,
    payload: dict,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    locked = await db.execute(
        "SELECT status,submitted_at FROM flatbed_scan_session WHERE id=%s AND owner_id=%s FOR UPDATE",
        (session_id, user.id),
    )
    session = await locked.fetchone()
    if session is None:
        raise HTTPException(404, "扫描会话不存在")
    if session["submitted_at"] is not None or session["status"] == "submitted":
        raise HTTPException(409, "扫描会话已经录入，不能重新确认配对")
    pairs = payload.get("pairs") or []
    if not pairs:
        raise HTTPException(422, "至少确认一对正反面后才能录入")
    try:
        front_ids = [UUID(item["front_region_id"]) for item in pairs]
        back_ids = [UUID(item["back_region_id"]) for item in pairs]
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, "配对数据格式不正确") from error
    if len(front_ids) != len(set(front_ids)) or len(back_ids) != len(set(back_ids)):
        raise HTTPException(409, "正反面必须一一对应")
    owned = await db.execute(
        """SELECT r.id,s.side FROM flatbed_scan_region r
           JOIN flatbed_scan_sheet s ON s.id=r.sheet_id
           WHERE r.owner_id=%s AND r.id=ANY(%s) AND s.session_id=%s
             AND s.revision=(SELECT max(latest.revision) FROM flatbed_scan_sheet latest
                             WHERE latest.session_id=s.session_id AND latest.side=s.side)""",
        (user.id, front_ids + back_ids, session_id),
    )
    owned_rows = await owned.fetchall()
    if len(owned_rows) != len(front_ids) + len(back_ids):
        raise HTTPException(404, "配对区域不存在")
    sides = {row["id"]: row["side"] for row in owned_rows}
    if any(sides.get(region_id) != "front" for region_id in front_ids) or any(
        sides.get(region_id) != "back" for region_id in back_ids
    ):
        raise HTTPException(409, "配对必须由正面和背面组成")
    await db.execute(
        "UPDATE flatbed_pair_proposal SET selected=false WHERE session_id=%s AND owner_id=%s",
        (session_id, user.id),
    )
    for front_id, back_id in zip(front_ids, back_ids, strict=True):
        updated = await db.execute(
            """UPDATE flatbed_pair_proposal SET selected=true,manual_override=true
               WHERE id=(SELECT id FROM flatbed_pair_proposal
                         WHERE session_id=%s AND owner_id=%s
                           AND front_region_id=%s AND back_region_id=%s
                         ORDER BY manual_override ASC,created_at DESC LIMIT 1)
               RETURNING id""",
            (session_id, user.id, front_id, back_id),
        )
        if await updated.fetchone() is None:
            raise HTTPException(409, "配对候选已过期，请重新生成配对")
    await db.execute(
        "UPDATE flatbed_scan_session SET pairing_requires_review=false,status='paired',updated_at=now() WHERE id=%s AND owner_id=%s",
        (session_id, user.id),
    )
    skipped = await db.execute(
        """WITH latest_regions AS (
               SELECT r.id,s.side FROM flatbed_scan_region r
               JOIN flatbed_scan_sheet s ON s.id=r.sheet_id
               WHERE s.session_id=%s AND r.owner_id=%s
                 AND s.revision=(SELECT max(latest.revision) FROM flatbed_scan_sheet latest
                                 WHERE latest.session_id=s.session_id AND latest.side=s.side)
           )
           SELECT count(*) FILTER (WHERE side='front' AND NOT (id=ANY(%s))) AS front,
                  count(*) FILTER (WHERE side='back' AND NOT (id=ANY(%s))) AS back
           FROM latest_regions""",
        (session_id, user.id, front_ids, back_ids),
    )
    skipped_row = await skipped.fetchone()
    return {
        "status": "paired",
        "count": len(pairs),
        "skipped": {"front": skipped_row["front"], "back": skipped_row["back"]},
    }


@router.post("/sessions/{session_id}/submit")
async def submit(
    session_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> dict:
    locked = await db.execute(
        "SELECT * FROM flatbed_scan_session WHERE id=%s AND owner_id=%s FOR UPDATE",
        (session_id, user.id),
    )
    session = await locked.fetchone()
    if session is None:
        raise HTTPException(404, "扫描会话不存在")
    if session["submitted_at"]:
        existing = await db.execute(
            "SELECT scanner_job_id FROM flatbed_pair_proposal WHERE session_id=%s AND owner_id=%s AND selected AND scanner_job_id IS NOT NULL ORDER BY created_at",
            (session_id, user.id),
        )
        return {
            "status": "submitted",
            "job_ids": [str(row["scanner_job_id"]) for row in await existing.fetchall()],
        }
    # Confidence and quality warnings are advisory. The explicit pair
    # confirmation moves the session to `paired`; they must never block ingest.
    if session["status"] != "paired":
        raise HTTPException(409, "请先确认至少一对正反面")
    rows = await db.execute(
        """SELECT p.id pair_id,p.scanner_job_id,
                  f.crop_path front_path,f.confidence front_confidence,f.signals front_signals,
                  b.crop_path back_path,b.confidence back_confidence,b.signals back_signals
           FROM flatbed_pair_proposal p
           JOIN flatbed_scan_region f ON f.id=p.front_region_id
           JOIN flatbed_scan_region b ON b.id=p.back_region_id
           WHERE p.session_id=%s AND p.owner_id=%s AND p.selected
           ORDER BY p.created_at""",
        (session_id, user.id),
    )
    selected_rows = await rows.fetchall()
    if not selected_rows:
        raise HTTPException(409, "没有可录入的已确认配对")
    jobs = []
    queue = ScannerQueueService(get_settings())
    flatbed = service()
    for row in selected_rows:
        if row["scanner_job_id"]:
            jobs.append(str(row["scanner_job_id"]))
            continue
        if not row["front_path"] or not row["back_path"]:
            raise HTTPException(409, "已确认配对缺少可用裁图，请先校正布局")
        front = flatbed.resolve_path(row["front_path"]).read_bytes()
        back = flatbed.resolve_path(row["back_path"]).read_bytes()
        job = await queue.enqueue(
            db,
            user.id,
            front,
            "image/jpeg",
            back,
            "image/jpeg",
            front_raw_content=front,
            front_raw_content_type="image/jpeg",
            back_raw_content=back,
            back_raw_content_type="image/jpeg",
            preprocessed=True,
            front_crop_confidence=_stored_crop_confidence(row, "front"),
            back_crop_confidence=_stored_crop_confidence(row, "back"),
        )
        jobs.append(str(job.id))
        await db.execute(
            "UPDATE flatbed_pair_proposal SET scanner_job_id=%s WHERE id=%s AND owner_id=%s",
            (job.id, row["pair_id"], user.id),
        )
    await db.execute(
        "UPDATE flatbed_scan_session SET status='submitted',submitted_at=now(),updated_at=now() WHERE id=%s",
        (session_id,),
    )
    return {"status": "submitted", "job_ids": jobs}


@router.get("/regions/{region_id}/image")
async def region_image(
    region_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> FileResponse:
    result = await db.execute(
        "SELECT crop_path FROM flatbed_scan_region WHERE id=%s AND owner_id=%s",
        (region_id, user.id),
    )
    row = await result.fetchone()
    if row is None:
        raise HTTPException(404, "图片不存在")
    path = service().resolve_path(row["crop_path"])
    return FileResponse(path, media_type="image/jpeg")


@router.get("/sheets/{sheet_id}/image")
async def sheet_image(
    sheet_id: UUID,
    user: AuthenticatedUser = Depends(current_user),
    db: AsyncConnection = Depends(connection),
) -> FileResponse:
    result = await db.execute(
        "SELECT original_path,content_type FROM flatbed_scan_sheet WHERE id=%s AND owner_id=%s",
        (sheet_id, user.id),
    )
    row = await result.fetchone()
    if row is None:
        raise HTTPException(404, "整版扫描图片不存在")
    path = service().resolve_path(row["original_path"])
    return FileResponse(path, media_type=row["content_type"])
