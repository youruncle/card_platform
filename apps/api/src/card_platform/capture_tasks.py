"""Owner-scoped, paginated capture resolution and durable outcome history."""

import hashlib
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, UploadFile
from psycopg.types.json import Jsonb

from card_platform.auth import AuthenticatedUser, current_user
from card_platform.auth_api import connection
from card_platform.capture_service import CaptureService, capture_job_view
from card_platform.config import get_settings
from card_platform.image_service import read_validated_image, InvalidImageError
from card_platform.pair_integrity import validate_pair_for_confirmation, retained_recognition_result
from card_platform.schemas import CardRecognitionResult, CollectionItemCreate

router = APIRouter()
STATES = ("processing", "needs_review", "failed", "imported", "cancelled")
STATE_SQL = """CASE WHEN collection_item_id IS NOT NULL THEN 'imported'
 WHEN imported_at IS NOT NULL OR (expected_revision IS NOT NULL AND target_item_id IS NULL) THEN 'cancelled'
 WHEN status IN ('queued','running') THEN 'processing'
 WHEN status='succeeded' THEN 'needs_review' WHEN status='failed' THEN 'failed'
 ELSE 'cancelled' END"""
REASONS = {
    "subject_conflict": "两面主体不同，请核对或替换照片",
    "product_conflict": "两面产品不同，请核对或替换照片",
    "brand_conflict": "两面品牌不同，请核对或替换照片",
    "card_number_conflict": "两面卡号不同，请核对或替换照片",
    "insert_conflict": "两面特卡系列不同，请核对或替换照片",
    "duplicate_front": "两张都是正面，请补充真实背面",
    "duplicate_back": "两张都是背面，请补充真实正面",
    "identical_images": "两张照片重复，请补充另一面",
    "draft_identity_conflict": "两面观察与联合身份不一致，请重新识别",
    "reported_face_conflict": "识别发现配对冲突，请核对或替换照片",
}


def task_state(row):
    if row.get("collection_item_id"):
        return "imported"
    if row.get("imported_at") or (row.get("expected_revision") and not row.get("target_item_id")):
        return "cancelled"
    if row["status"] in {"queued", "running"}:
        return "processing"
    return {"succeeded": "needs_review", "failed": "failed"}.get(row["status"], "cancelled")


def failure_message(error):
    text = str(error or "").lower()
    if "no such file" in text or "not found" in text:
        return "原照片文件缺失，请重新上传照片；现有任务记录已保留。"
    if "timeout" in text or "timed out" in text:
        return "识别服务超时，照片已保存，可稍后重试或手工补充信息。"
    if "maximum attempts" in text:
        return "自动重试次数已用完，照片已保存，可主动重试或手工补充信息。"
    return "识别服务返回异常，照片已保存，可重试或手工补充信息。"


async def owned(db, owner, job_id, lock=False):
    row = await (
        await db.execute(
            "SELECT * FROM scanner_recognition_job WHERE id=%s AND owner_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (job_id, owner),
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, "录入任务不存在")
    return dict(row)


def task_view(row, *, hashes=None, history=None):
    row = dict(row)
    row["result"] = retained_recognition_result(row)
    values = capture_job_view(row).model_dump(mode="json")
    state = task_state(row)
    reasons = []
    raw = row.get("result") or {}
    if state == "failed":
        reasons.append(failure_message(row.get("error_message")))
    if state == "needs_review":
        for field, label in [
            ("player_name", "主体"),
            ("brand_name", "品牌"),
            ("product_name", "产品"),
            ("season_start", "发行赛季"),
        ]:
            if not raw.get(field):
                reasons.append(f"缺少{label}，请补充后确认")
        pair = raw.get("pair_validation") or {}
        reasons += [REASONS[code] for code in pair.get("reason_codes", []) if code in REASONS]
        if pair.get("status") != "consistent" and not reasons:
            reasons.append("请查看当前正反面，并确认属于同一张卡")
        if not reasons:
            reasons.append("识别已完成，尚未入库，请核对后确认")
    values.update(
        display_status=state,
        attention_reasons=reasons,
        can_retry=state in {"failed", "needs_review"},
        image_content_hashes=hashes
        or (raw.get("pair_validation") or {}).get("image_content_hashes", {}),
        history=history or [],
        replacement_job_id=(row.get("capture_metadata") or {}).get("replacement_job_id"),
    )
    if state == "failed":
        values["error_message"] = failure_message(row.get("error_message"))
    return values


async def detail(db, owner, job_id):
    row = await owned(db, owner, job_id)
    queue = CaptureService(get_settings()).queue
    hashes = {}
    for side in ("front", "back"):
        path = row.get(side + "_image_path")
        if path and queue.image_path(path).is_file():
            hashes[side] = hashlib.sha256(queue.image_path(path).read_bytes()).hexdigest()
    events = await (
        await db.execute(
            "SELECT * FROM scanner_job_event WHERE job_id=%s AND owner_id=%s ORDER BY occurred_at,id",
            (job_id, owner),
        )
    ).fetchall()
    history = []
    for event in events:
        state = task_state(event)
        message = (
            failure_message(event["error_message"])
            if state == "failed"
            else {
                "processing": "已进入识别队列",
                "needs_review": "识别完成，等待核对入库",
                "imported": "已确认入库",
                "cancelled": "任务已取消或更正",
            }[state]
        )
        if event["source"] == "manual":
            message = (
                "人工补充信息后确认入库"
                if state == "imported"
                else "人工补充信息，未宣称模型识别成功"
            )
        if event["source"] == "legacy_snapshot":
            message += "（旧任务现状快照，历史未完整保留）"
        if event["source"] == "recovered_audit":
            message += "（由修复前保留的现场记录恢复）"
        history.append(
            dict(
                id=str(event["id"]),
                occurred_at=event["occurred_at"].isoformat(),
                attempt_count=event["attempt_count"],
                display_status=state,
                message=message,
                source=event["source"],
            )
        )
    return task_view(row, hashes=hashes, history=history)


@router.get("")
async def list_tasks(
    state: Literal[
        "attention", "all", "processing", "needs_review", "failed", "imported", "cancelled"
    ] = "attention",
    page: int = 1,
    page_size: int = 20,
    user: AuthenticatedUser = Depends(current_user),
    db=Depends(connection),
):
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    count_rows = await (
        await db.execute(
            f"SELECT {STATE_SQL} state,count(*) n FROM scanner_recognition_job WHERE owner_id=%s GROUP BY 1",
            (user.id,),
        )
    ).fetchall()
    counts = {key: 0 for key in STATES}
    counts.update({r["state"]: r["n"] for r in count_rows})
    counts["total"] = sum(counts.values())
    wanted = (
        list(STATES)
        if state == "all"
        else ["processing", "needs_review", "failed"]
        if state == "attention"
        else [state]
    )
    total = sum(counts[s] for s in wanted)
    rows = await (
        await db.execute(
            f"""SELECT * FROM scanner_recognition_job WHERE owner_id=%s AND ({STATE_SQL})=ANY(%s)
        ORDER BY CASE WHEN status='failed' THEN 0 WHEN status='succeeded' AND collection_item_id IS NULL THEN 1 ELSE 2 END,
        created_at DESC,id DESC LIMIT %s OFFSET %s""",
            (user.id, wanted, page_size, (page - 1) * page_size),
        )
    ).fetchall()
    return dict(
        items=[task_view(r) for r in rows],
        counts=counts,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get("/{job_id}")
async def get_task(
    job_id: UUID, user: AuthenticatedUser = Depends(current_user), db=Depends(connection)
):
    return await detail(db, user.id, job_id)


@router.post("/{job_id}/retry")
async def retry_task(
    job_id: UUID, user: AuthenticatedUser = Depends(current_user), db=Depends(connection)
):
    row = await db.execute(
        """UPDATE scanner_recognition_job SET status='queued',capture_metadata=capture_metadata || jsonb_build_object('last_recognition_result',COALESCE(result,capture_metadata->'last_recognition_result')),result=NULL,
    error_message=NULL,started_at=NULL,finished_at=NULL,updated_at=clock_timestamp(),revision=revision+1,
    manual_retry_limit=GREATEST(manual_retry_limit,attempt_count+1)
    WHERE id=%s AND owner_id=%s AND collection_item_id IS NULL AND imported_at IS NULL
    AND NOT (expected_revision IS NOT NULL AND target_item_id IS NULL)
    AND status IN ('failed','succeeded') RETURNING id""",
        (job_id, user.id),
    )
    if not await row.fetchone():
        raise HTTPException(409, "该任务正在处理或已入库，请刷新任务")
    return await detail(db, user.id, job_id)


@router.post("/{job_id}/confirm", status_code=201)
async def confirm_task(
    job_id: UUID,
    body: dict = Body(...),
    user: AuthenticatedUser = Depends(current_user),
    db=Depends(connection),
):
    async with db.transaction():
        row = await owned(db, user.id, job_id, lock=True)
        if row.get("collection_item_id"):
            return {"id": str(row["collection_item_id"])}
        if row["status"] not in {"failed", "succeeded"}:
            raise HTTPException(409, "识别尚未完成，请稍后确认")
        if row["status"] == "failed":
            task = await detail(db, user.id, job_id)
            raw = retained_recognition_result(row) or CardRecognitionResult(
                confidence=0,
                pair_validation={
                    "status": "unknown",
                    "image_content_hashes": task["image_content_hashes"],
                },
            ).model_dump(mode="json")
            validate_pair_for_confirmation(
                raw, task["image_content_hashes"], body.get("pair_review")
            )
            # All original failed evidence remains in immutable history.
            await db.execute(
                "UPDATE scanner_recognition_job SET status='succeeded',require_confirmation=true,result=%s,error_message=NULL,capture_metadata=capture_metadata || jsonb_build_object('resolution_source','manual') WHERE id=%s",
                (Jsonb(raw), job_id),
            )
        elif not row["require_confirmation"]:
            await db.execute(
                "UPDATE scanner_recognition_job SET require_confirmation=true WHERE id=%s",
                (job_id,),
            )
        if body.get("card") and not row.get("target_item_id"):
            task = await detail(db, user.id, job_id)
            current = await owned(db, user.id, job_id)
            raw = retained_recognition_result(current) or {"confidence": 0}
            validate_pair_for_confirmation(
                raw, task["image_content_hashes"], body.get("pair_review")
            )
            try:
                payload = CollectionItemCreate.model_validate({**body, "owner_id": user.id})
            except ValueError as error:
                raise HTTPException(422, "请核对主体、品牌、产品、发行赛季和编号后确认") from error
            accepted = {
                **payload.card.model_dump(mode="json"),
                "serial_number": payload.serial_number,
                "serial_total": payload.serial_total,
                "is_numbered": payload.is_numbered,
            }
            evidence = dict(raw.get("field_evidence") or {})
            for field, value in accepted.items():
                if value != raw.get(field):
                    evidence[field] = ["用户查看当前照片后手工确认；非模型新识别结论"]
            raw = CardRecognitionResult.model_validate(
                {
                    **raw,
                    **accepted,
                    "field_evidence": evidence,
                    "field_assessments": {},
                    "recognition_model": "人工确认（原模型记录见任务历史）",
                    "recognition_quality_issues": [],
                }
            ).model_dump(mode="json")
            await db.execute(
                "UPDATE scanner_recognition_job SET result=%s,capture_metadata=capture_metadata || jsonb_build_object('resolution_source','manual','accepted_identity',%s::jsonb) WHERE id=%s",
                (Jsonb(raw), Jsonb(accepted), job_id),
            )
        created = await CaptureService(get_settings()).confirm(db, user.id, job_id, body)
        return {"id": str(created.id)}


@router.post("/{job_id}/images", status_code=202)
async def replace_images(
    job_id: UUID,
    front: UploadFile | None = File(None),
    back: UploadFile | None = File(None),
    idempotency_key: UUID = Header(...),
    user: AuthenticatedUser = Depends(current_user),
    db=Depends(connection),
):
    if not front and not back:
        raise HTTPException(422, "请选择需要更正的正面或背面照片")
    service = CaptureService(get_settings())
    replacements = {}
    for side, upload in [("front", front), ("back", back)]:
        if upload:
            try:
                content = await read_validated_image(upload, get_settings().max_image_bytes)
            except InvalidImageError as error:
                raise HTTPException(422, "照片格式无效，请选择有效的 JPEG、PNG 或 WebP") from error
            replacements[side] = (content, upload.content_type)
    async with db.transaction():
        old = await owned(db, user.id, job_id, lock=True)
        previous = (old.get("capture_metadata") or {}).get("replacement_job_id")
        if previous:
            prior = await owned(db, user.id, UUID(previous))
            if str(prior.get("client_request_id")) != str(idempotency_key):
                raise HTTPException(409, "照片已更正，请查看关联的新任务")
        if (
            old.get("collection_item_id")
            or old.get("imported_at")
            or (old.get("expected_revision") and not old.get("target_item_id"))
            or (not previous and old["status"] not in {"failed", "succeeded"})
        ):
            raise HTTPException(409, "只能更正未入库且已完成识别的任务")
        images = {}
        for side in ("front", "back"):
            if side in replacements:
                images[side] = replacements[side]
            elif old.get(side + "_image_path"):
                if not service.queue.image_path(old[side + "_image_path"]).is_file():
                    raise HTTPException(409, "保留的照片已缺失，请同时重新上传需要的正反面照片")
                images[side] = (
                    service.queue.image_path(old[side + "_image_path"]).read_bytes(),
                    old[side + "_content_type"],
                )
        if not previous and all(
            old.get(side + "_image_path")
            and service.queue.image_path(old[side + "_image_path"]).is_file()
            and content == service.queue.image_path(old[side + "_image_path"]).read_bytes()
            for side, (content, _) in images.items()
        ):
            raise HTTPException(409, "照片与原任务相同，请更换实际错配的一面或重新识别")
        job = await service.enqueue(
            db,
            user.id,
            idempotency_key,
            images,
            target_item_id=old.get("target_item_id"),
            expected_revision=old.get("expected_revision"),
        )
        if previous:
            return await detail(db, user.id, job.id)
        await db.execute(
            "UPDATE scanner_recognition_job SET capture_metadata=capture_metadata || %s WHERE id=%s",
            (Jsonb({"replaces_job_id": str(job_id)}), job.id),
        )
        await db.execute(
            "UPDATE scanner_recognition_job SET status='cancelled',capture_metadata=capture_metadata || %s,updated_at=clock_timestamp() WHERE id=%s",
            (Jsonb({"replacement_job_id": str(job.id)}), job_id),
        )
        return await detail(db, user.id, job.id)
