"""Durable photo intake. Original bytes stay private; writes use CaptureService."""

import asyncio
import hashlib
from io import BytesIO
import json
import logging
import shutil
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException, UploadFile
from PIL import Image
from psycopg.types.json import Jsonb
from pydantic import ConfigDict, Field, model_validator
from starlette.datastructures import Headers

from card_platform.capture_service import CaptureService
from card_platform.domain.card_identity import is_placeholder_identity_value
from card_platform.image_service import ALLOWED_IMAGE_TYPES, CardVisionService, detect_image_content_type
from card_platform.media_validation import normalize_media_content
from card_platform.mcp.media import AttachmentDownloader, CardImportFilesRequest
from card_platform.mcp.operations import OperationHandler
from card_platform.mcp.portfolio import public_card
from card_platform.mcp.schemas import McpFailure, McpPrincipal, StrictModel
from card_platform.pair_integrity import derive_pair_validation
from card_platform.repository import PortfolioRepository
from card_platform.schemas import CardIdentityInput, CollectionItemCreate, PurchaseInput, SheetCardRecognitionResult

logger = logging.getLogger(__name__)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                      default=str, ensure_ascii=False).encode()).hexdigest()


def domain_error(error):
    code = {404: "NOT_FOUND", 409: "REVISION_CONFLICT", 422: "INVALID_INPUT"}.get(error.status_code, "SOURCE_BLOCKED")
    return McpFailure(code, "照片草稿校验失败，请核对照片、身份和配对", "重新查看任务并校正后准备确认")


class ImportIdentity(CardIdentityInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ImportPurchase(PurchaseInput):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def explicit_date_currency(self):
        if self.transacted_at.tzinfo is None or self.transacted_at.utcoffset() is None:
            raise ValueError("采购日期必须有时区")
        if len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isalpha():
            raise ValueError("采购币种必须是三字母代码")
        self.currency = self.currency.upper()
        return self


class ImportFields(StrictModel):
    card: ImportIdentity
    serial_number: int | None = Field(default=None, gt=0)
    serial_total: int | None = Field(default=None, gt=0)
    is_numbered: bool = False
    grade_company: str | None = Field(default=None, max_length=40)
    grade_value: str | None = None
    grade_cert_number: str | None = Field(default=None, max_length=80)
    strategy_tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=4000)
    purchase: ImportPurchase | None = None

    @model_validator(mode="after")
    def validate_collection(self):
        if any(is_placeholder_identity_value(getattr(self.card, field))
               for field in ("player_name", "brand_name", "product_name")):
            raise ValueError("必要身份未知时请先人工校正，不能以占位身份入库")
        CollectionItemCreate.model_validate({"owner_id": UUID(int=0), **self.model_dump(mode="json")})
        return self


class PrepareCaptureConfirmationRequest(StrictModel):
    job_id: UUID
    corrected_fields: ImportFields
    idempotency_key: UUID


class GetJobStatusRequest(StrictModel):
    job_id: UUID | None = None
    batch_id: UUID | None = None

    @model_validator(mode="after")
    def one_identifier(self):
        if (self.job_id is None) == (self.batch_id is None):
            raise ValueError("job_id和batch_id必须二选一")
        return self


class CaptureTools:
    def __init__(self, database, settings, operations, *, downloader=None):
        self.database, self.settings, self.operations = database, settings, operations
        self.downloader = downloader or AttachmentDownloader()
        self.capture = CaptureService(settings)
        self.root = self.capture.queue.root
        self.operations.register("import", OperationHandler(frozenset({"cards:write"}),
                                                           self._import_snapshot, self._import_execute))

    def source_path(self, reference):
        path = self.root / reference
        if path.is_symlink() or not path.resolve().is_relative_to(self.root) or not path.is_file():
            raise McpFailure("NOT_FOUND", "任务原图缺失", "重新选择照片")
        return path

    async def _prior_intake(self, principal, key):
        async with self.database.connection() as conn:
            await self.operations._grant(conn, principal, frozenset({"cards:write"}))
            return await (await conn.execute("SELECT * FROM chatgpt_capture_intake WHERE owner_id=%s AND idempotency_key=%s",
                                              (principal.owner_id, key))).fetchone()

    @staticmethod
    def _intake_response(row):
        return {"job_id": str(row["id"]), "status": row["status"], "file_count": len(row["manifest"]),
                "groups": [{"file_id": entry["file_id"], "side": entry["side"], "group_id": entry["group_id"]}
                           for entry in row["manifest"]]}

    async def prepare_card_import(self, principal, request: CardImportFilesRequest):
        request_hash = digest([{"file_id": file.file_id, **side.model_dump()}
                               for file, side in zip(request.files, request.sides, strict=True)])
        prior = await self._prior_intake(principal, request.idempotency_key)
        if prior and (prior["request_hash"] != request_hash or prior["connection_id"] != principal.connection_id):
            raise McpFailure("IDEMPOTENCY_CONFLICT", "此上传编号已用于不同附件或连接")
        request_id = uuid4()
        directory = self.root / ".mcp-intake" / str(principal.owner_id) / str(request_id)
        directory.mkdir(parents=True, mode=0o700)
        retained = False
        try:
            manifest = []
            for index, (file, side) in enumerate(zip(request.files, request.sides, strict=True)):
                try:
                    content = await self.downloader.download(file.download_url)
                except McpFailure as error:
                    if prior and error.code == "SOURCE_BLOCKED":
                        return self._intake_response(prior)
                    raise
                mime = detect_image_content_type(content)
                source = directory / f"{index}{ALLOWED_IMAGE_TYPES[mime]}"
                await asyncio.to_thread(source.write_bytes, content)
                source.chmod(0o600)
                manifest.append({"index": index, "file_id": file.file_id, **side.model_dump(),
                    "source_ref": str(source.relative_to(self.root)), "mime_type": mime,
                    "sha256": hashlib.sha256(content).hexdigest()})
            content_hash = digest([{k: entry[k] for k in ("file_id", "side", "group_id", "sha256")} for entry in manifest])
            async with self.database.connection() as conn:
                await self.operations._grant(conn, principal, frozenset({"cards:write"}))
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                   (f"mcp-intake:{principal.owner_id}:{request.idempotency_key}",))
                prior = await (await conn.execute("SELECT * FROM chatgpt_capture_intake WHERE owner_id=%s AND idempotency_key=%s",
                                                   (principal.owner_id, request.idempotency_key))).fetchone()
                if prior:
                    if prior["content_hash"] != content_hash or prior["connection_id"] != principal.connection_id:
                        raise McpFailure("IDEMPOTENCY_CONFLICT", "此上传编号已用于不同照片")
                    return self._intake_response(prior)
                row = await (await conn.execute("""INSERT INTO chatgpt_capture_intake
                    (id,owner_id,connection_id,idempotency_key,request_hash,content_hash,manifest)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (request_id, principal.owner_id, principal.connection_id, request.idempotency_key,
                     request_hash, content_hash, Jsonb(manifest)))).fetchone()
            retained = True
            return self._intake_response(row)
        finally:
            if not retained:
                await asyncio.to_thread(shutil.rmtree, directory, True)

    async def _owned_capture(self, conn, principal, job_id, *, lock=False):
        row = await (await conn.execute("""SELECT j.*,link.draft_metadata FROM scanner_recognition_job j
            JOIN chatgpt_capture_job link ON link.job_id=j.id
            WHERE j.id=%s AND j.owner_id=%s AND link.owner_id=%s""" + (" FOR UPDATE OF j" if lock else ""),
            (job_id, principal.owner_id, principal.owner_id))).fetchone()
        if row is None:
            raise McpFailure("NOT_FOUND", "照片任务不存在")
        return row

    def _image_hashes(self, job):
        values = {}
        for side in ("front", "back"):
            if job.get(f"{side}_image_path"):
                try:
                    values[side] = hashlib.sha256(self.capture.queue.image_path(job[f"{side}_image_path"]).read_bytes()).hexdigest()
                except (OSError, ValueError):
                    raise McpFailure("NOT_FOUND", "任务照片缺失", "重新上传照片") from None
        if not values:
            raise McpFailure("NOT_FOUND", "任务没有可确认照片")
        return values

    async def _job_view(self, conn, principal, row):
        result = row["result"] or {}
        hashes = await asyncio.to_thread(self._image_hashes, row)
        status = "succeeded" if row["collection_item_id"] else (
            "review_required" if row["status"] == "succeeded" else row["status"])
        return {"job_id": str(row["id"]), "status": status, "revision": str(row["revision"]),
                "available_sides": [side for side in ("front", "back") if side in hashes],
                "recognition": result, "purchase_draft": row.get("draft_metadata") or {},
                "pair_validation": derive_pair_validation(result, hashes) if len(hashes) == 2 else None,
                "item_id": str(row["collection_item_id"]) if row["collection_item_id"] else None,
                "error_code": "SOURCE_BLOCKED" if row.get("error_message") else None}

    async def get_job_status(self, principal, request: GetJobStatusRequest):
        identifier = request.job_id or request.batch_id
        async with self.database.connection() as conn:
            await self.operations._grant(conn, principal, frozenset({"cards:read"}))
            intake = await (await conn.execute("SELECT * FROM chatgpt_capture_intake WHERE id=%s AND owner_id=%s",
                                               (identifier, principal.owner_id))).fetchone()
            if intake is None:
                job = await self._owned_capture(conn, principal, identifier)
                return await self._job_view(conn, principal, job)
            rows = await (await conn.execute("""SELECT j.*,link.draft_metadata FROM scanner_recognition_job j
                JOIN chatgpt_capture_job link ON link.job_id=j.id WHERE link.intake_id=%s
                AND link.owner_id=%s AND j.owner_id=%s ORDER BY link.ordinal""",
                (identifier, principal.owner_id, principal.owner_id))).fetchall()
            jobs = [await self._job_view(conn, principal, row) for row in rows]
        state = intake["status"]
        if state == "succeeded":
            states = {job["status"] for job in jobs}
            state = ("running" if states & {"queued", "running"} else
                     "failed" if "failed" in states else "review_required" if "review_required" in states else "succeeded")
        return {"job_id": str(identifier), "status": state, "jobs": jobs, "error_code": intake["error_code"]}

    async def _import_snapshot(self, conn, principal, payload):
        job = await self._owned_capture(conn, principal, UUID(payload["job_id"]), lock=True)
        hashes = await asyncio.to_thread(self._image_hashes, job)
        if job["status"] != "succeeded":
            raise McpFailure("INVALID_INPUT", "识别尚未完成", "查询任务状态后重试")
        if len(hashes) == 2 and derive_pair_validation(job["result"] or {}, hashes)["status"] == "conflict":
            raise McpFailure("REVISION_CONFLICT", "正反面存在明确冲突", "重新配对或补拍")
        return digest({"revision": job["revision"], "result": job["result"], "hashes": hashes,
                       "item_id": job["collection_item_id"], "imported_at": job.get("imported_at")})

    async def prepare_capture_confirmation(self, principal, request: PrepareCaptureConfirmationRequest):
        fields = request.corrected_fields.model_dump(mode="json")
        async with self.database.connection() as conn:
            await self.operations._grant(conn, principal, frozenset({"cards:write"}))
            owner = await (await conn.execute("SELECT base_currency FROM app_user WHERE id=%s", (principal.owner_id,))).fetchone()
            purchase = request.corrected_fields.purchase
            if purchase and purchase.currency != owner["base_currency"] and "fx_rate_to_base" not in purchase.model_fields_set:
                raise McpFailure("INVALID_INPUT", "外币采购缺少折算依据", "补充实际汇率后重新准备")
            job = await self._owned_capture(conn, principal, request.job_id)
            hashes = await asyncio.to_thread(self._image_hashes, job)
            pair = derive_pair_validation(job["result"] or {}, hashes) if len(hashes) == 2 else None
            payload = {"job_id": str(request.job_id), "corrected_fields": fields,
                       "preview_images": [{"side": side, "url": f"/v1/capture/jobs/{request.job_id}/images/{side}?revision={job['revision']}"}
                                          for side in hashes],
                       "required_acknowledgements": ["pair_images_checked"] if pair and pair["status"] != "consistent" else []}
            prior = await (await conn.execute("""SELECT * FROM chatgpt_operation
                WHERE owner_id=%s AND kind='import' AND idempotency_key=%s""",
                (principal.owner_id, request.idempotency_key))).fetchone()
            if prior:
                if prior["payload"] != payload or prior["connection_id"] != principal.connection_id:
                    raise McpFailure("IDEMPOTENCY_CONFLICT", "该确认编号已用于其他草稿或参数")
                expected = prior["expected_revision"]
            else:
                expected = await self._import_snapshot(conn, principal, payload)
        return await self.operations.prepare(principal, "import", request.idempotency_key, payload, expected)

    async def _import_execute(self, conn, principal, payload):
        job = await self._owned_capture(conn, principal, UUID(payload["job_id"]), lock=True)
        hashes = await asyncio.to_thread(self._image_hashes, job)
        body = {**payload["corrected_fields"]}
        if len(hashes) == 2:
            body["pair_review"] = {"confirmed": True, "image_content_hashes": hashes}
        try:
            saved = await self.capture.confirm(conn, principal.owner_id, job["id"], body)
        except HTTPException as error:
            raise domain_error(error) from None
        detail = await PortfolioRepository().item_detail(conn, principal.owner_id, saved.id)
        return {"item_id": str(saved.id), "job_id": str(job["id"]), "item": public_card(detail.item)}

    def register(self, registry, *, include_job_status=True):
        from card_platform.mcp.server import ToolAdapter
        for name, model, handler, description in (
            ("prepare_card_import", CardImportFilesRequest, self.prepare_card_import, "提交照片建立云端识别草稿。files必须来自附件，sides逐个明确正面/背面/auto及配对；此步骤不创建收藏。"),
            ("get_job_status", GetJobStatusRequest, self.get_job_status, "按任务编号恢复照片处理进度、逐卡草稿、证据和不确定项。识别完成后仍须核对并准备确认。"),
            ("prepare_capture_confirmation", PrepareCaptureConfirmationRequest, self.prepare_capture_confirmation, "为已校正的单张照片草稿准备入库预览。完整必要身份和采购信息须由用户核对，平台批准后再执行。"),
        ):
            if name == "get_job_status" and not include_job_status:
                continue
            registry.register(ToolAdapter(name, description, model, frozenset({"cards:read"} if name == "get_job_status" else {"cards:write"}),
                              handler, read_only=name == "get_job_status",
                              meta={"openai/fileParams": ["files"]} if name == "prepare_card_import" else None))


class CaptureIntakeWorker:
    def __init__(self, captures: CaptureTools, *, vision=None):
        self.captures = captures
        self.vision = vision

    async def run(self):
        while True:
            try:
                if not await self.process_one():
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error("MCP intake loop failed: exception_type=%s", type(error).__name__)
                await asyncio.sleep(2)

    async def process_one(self, request_id=None):
        lease = uuid4()
        async with self.captures.database.connection() as conn:
            await conn.execute("""UPDATE chatgpt_capture_intake SET status='failed',error_code='RETRY_EXHAUSTED',
                finished_at=now() WHERE attempt_count>=3 AND
                (status='queued' OR (status='running' AND started_at<now()-interval '10 minutes'))""")
            row = await (await conn.execute("""SELECT * FROM chatgpt_capture_intake
                WHERE (status='queued' OR (status='running' AND started_at<now()-interval '10 minutes'))
                AND attempt_count<3 AND (%s::uuid IS NULL OR id=%s)
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""", (request_id, request_id))).fetchone()
            if row is None:
                return False
            await conn.execute("""UPDATE chatgpt_capture_intake SET status='running',lease_id=%s,
                started_at=now(),attempt_count=attempt_count+1 WHERE id=%s""", (lease, row["id"]))
        heartbeat = asyncio.create_task(self._heartbeat(row["id"], lease))
        try:
            await self._process(row, lease)
        except asyncio.CancelledError:
            async with self.captures.database.connection() as conn:
                await conn.execute("UPDATE chatgpt_capture_intake SET status='queued' WHERE id=%s AND lease_id=%s",
                                   (row["id"], lease))
            raise
        except Exception as error:
            code = error.code if isinstance(error, McpFailure) else "SOURCE_BLOCKED"
            async with self.captures.database.connection() as conn:
                await conn.execute("""UPDATE chatgpt_capture_intake SET status='failed',error_code=%s,
                    finished_at=now() WHERE id=%s AND lease_id=%s""", (code, row["id"], lease))
            logger.warning("MCP intake failed: exception_type=%s", type(error).__name__)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        return True

    async def _heartbeat(self, request_id, lease):
        while True:
            await asyncio.sleep(20)
            async with self.captures.database.connection() as conn:
                await conn.execute("UPDATE chatgpt_capture_intake SET started_at=now() WHERE id=%s AND lease_id=%s AND status='running'",
                                   (request_id, lease))

    async def _process(self, row, lease):
        principal = McpPrincipal(owner_id=row["owner_id"], connection_id=row["connection_id"], scopes={"cards:write"})
        async with self.captures.database.connection() as conn:
            await self.captures.operations._grant(conn, principal, frozenset({"cards:write"}))
        groups = {}
        for entry in row["manifest"]:
            groups.setdefault(entry["group_id"], []).append(entry)
        ordinal = 0
        for group_index, entries in enumerate(groups.values()):
            if entries[0]["side"] == "auto":
                entry = entries[0]
                raw = await asyncio.to_thread(self.captures.source_path(entry["source_ref"]).read_bytes)
                if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    raise McpFailure("REVISION_CONFLICT", "原图发生变化")
                processing, _, _ = await asyncio.to_thread(normalize_media_content, raw)
                mime = detect_image_content_type(processing)
                cached = row["analysis_results"].get(str(group_index))
                if cached is not None:
                    sheet = SheetCardRecognitionResult.model_validate(cached)
                else:
                    vision = self.vision or CardVisionService(self.captures.settings)
                    sheet = await vision.recognize_sheet(UploadFile(file=BytesIO(processing), filename="attachment" + ALLOWED_IMAGE_TYPES[mime],
                                                         headers=Headers({"content-type": mime})))
                    async with self.captures.database.connection() as conn:
                        await self.captures.operations._grant(conn, principal, frozenset({"cards:write"}))
                        await conn.execute("""UPDATE chatgpt_capture_intake SET analysis_results=analysis_results || %s
                            WHERE id=%s AND lease_id=%s AND status='running'""",
                            (Jsonb({str(group_index): sheet.model_dump(mode="json")}), row["id"], lease))
                if not sheet.cards or len(sheet.cards) + ordinal > 20:
                    raise McpFailure("INVALID_INPUT", "未识别到卡片或单次超过20张，请拆分照片")
                for index, card in enumerate(sheet.cards):
                    if len(sheet.cards) > 1 and card.card_image_bbox is None:
                        raise McpFailure("INVALID_INPUT", "多卡图缺少逐卡裁切位置，请单独拍摄")
                    content = await asyncio.to_thread(self._crop, processing, card.card_image_bbox)
                    await self._enqueue(row, lease, principal, ordinal, group_index, index,
                        {"front": (content, "image/png")}, {"front": (raw, entry["mime_type"])},
                        card.result, card.model_dump(mode="json", exclude={"result"}),
                        {"front": {"bbox": card.card_image_bbox, "source_hash": entry["sha256"], "source": "model_candidate"}})
                    ordinal += 1
            else:
                images = {}
                for entry in entries:
                    content = await asyncio.to_thread(self.captures.source_path(entry["source_ref"]).read_bytes)
                    if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                        raise McpFailure("REVISION_CONFLICT", "原图发生变化")
                    images[entry["side"]] = (content, entry["mime_type"])
                await self._enqueue(row, lease, principal, ordinal, group_index, 0, images, images, None, {}, {})
                ordinal += 1
        async with self.captures.database.connection() as conn:
            await conn.execute("""UPDATE chatgpt_capture_intake SET status='succeeded',finished_at=now(),error_code=NULL
                               WHERE id=%s AND lease_id=%s""", (row["id"], lease))

    async def _enqueue(self, row, lease, principal, ordinal, group, index, images, sources, result, metadata, recipe):
        async with self.captures.database.connection() as conn:
            await self.captures.operations._grant(conn, principal, frozenset({"cards:write"}))
            current = await (await conn.execute("SELECT lease_id FROM chatgpt_capture_intake WHERE id=%s FOR UPDATE", (row["id"],))).fetchone()
            if current["lease_id"] != lease:
                raise McpFailure("REVISION_CONFLICT", "任务已由其他工作进程接续")
            existing = await (await conn.execute("SELECT job_id FROM chatgpt_capture_job WHERE intake_id=%s AND ordinal=%s",
                                                  (row["id"], ordinal))).fetchone()
            if existing is not None:
                return
            key = uuid5(row["id"], f"group-{group}-card-{index}")
            job = await self.captures.capture.enqueue(conn, principal.owner_id, key, images, sources=sources, crop_recipe=recipe)
            await conn.execute("""INSERT INTO chatgpt_capture_job(job_id,intake_id,owner_id,ordinal,draft_metadata)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(job_id) DO NOTHING""",
                (job.id, row["id"], principal.owner_id, ordinal, Jsonb(metadata)))
            if result is not None:
                await conn.execute("""UPDATE scanner_recognition_job SET status='succeeded',result=%s,
                    finished_at=now(),updated_at=now() WHERE id=%s AND collection_item_id IS NULL""",
                    (Jsonb(result.model_dump(mode="json")), job.id))

    @staticmethod
    def _crop(content, box):
        with Image.open(BytesIO(content)) as image:
            if box:
                left, top, right, bottom = box
                image = image.crop((round(left * image.width / 1000), round(top * image.height / 1000),
                                    round(right * image.width / 1000), round(bottom * image.height / 1000)))
            if min(image.size) < 2:
                raise McpFailure("INVALID_INPUT", "裁切候选太小，请单独拍摄")
            output = BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
