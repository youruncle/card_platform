"""Shared capture persistence, idempotency and atomic human confirmation."""

import asyncio
import hashlib
import json
from uuid import uuid4

import aiofiles
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from card_platform.image_service import (
    ALLOWED_IMAGE_TYPES,
    ImageStorageService,
    detect_image_content_type,
)
from card_platform.image_rectification import rectify_card_image, RectifiedImage
from card_platform.scanner_queue import ScannerQueueService
from card_platform.repository import PortfolioRepository
from card_platform.pair_integrity import validate_pair_for_confirmation, retained_recognition_result, derive_pair_validation
from card_platform.schemas import (
    CaptureJob,
    CollectionItemCreate,
    CreatedResource,
    CardIdentityInput,
)


IDENTITY_ACCEPTANCE_REASON = (
    "此卡已有身份审核、市场绑定、估值记录或正在估值，请在身份审核入口修改；本次仍可仅补拍照片"
)
CAPTURE_TARGET_SQL = """SELECT ci.id,ci.updated_at,ci.image_front_url,ci.image_back_url,
            NOT EXISTS(SELECT 1 FROM card_identity_version iv WHERE iv.collection_item_id=ci.id)
            AND NOT EXISTS(SELECT 1 FROM market_identity_binding mib WHERE mib.collection_item_id=ci.id)
            AND NOT EXISTS(SELECT 1 FROM valuation_snapshot vs WHERE vs.collection_item_id=ci.id)
            AND NOT EXISTS(SELECT 1 FROM card_analysis_snapshot cas WHERE cas.collection_item_id=ci.id)
            AND NOT EXISTS(SELECT 1 FROM valuation_job vj WHERE vj.collection_item_id=ci.id AND vj.status IN ('identifying','searching','calculating')) AS can_accept_identity,
            p.full_name AS player_name,p.birth_date AS player_birth_date,
            b.name AS brand_name,b.manufacturer,b.tier AS brand_tier,
            cs.season_start,cs.season_end,cs.product_name,cs.set_name,
            cd.card_number,cd.insert_name,cd.parallel_name,cd.is_rookie,
            cd.is_autograph,cd.autograph_type,cd.has_inscription,cd.inscription_text,
            cd.is_memorabilia,cd.memorabilia_types,cd.memorabilia_usage,
            cd.memorabilia_player,cd.is_patch,cd.is_case_hit,cd.print_run
            FROM collection_item ci
            JOIN card_definition cd ON cd.id=ci.card_definition_id
            JOIN player p ON p.id=cd.player_id
            JOIN card_set cs ON cs.id=cd.card_set_id
            JOIN brand b ON b.id=cs.brand_id
            WHERE ci.id=%s AND ci.owner_id=%s AND ci.deleted_at IS NULL"""

SUPPORTED_IDENTITY_FIELDS = {
    "player_name",
    "brand_name",
    "season_start",
    "season_end",
    "product_name",
    "set_name",
    "card_number",
    "insert_name",
    "parallel_name",
}


def prepare_capture_image(content, content_type, preprocessed):
    """One bounded decode/transform operation shared with crop and variant generation."""
    from card_platform.media_validation import normalize_media_content
    from card_platform.image_variants import generation_slot

    try:
        with generation_slot():
            content, _width, _height = normalize_media_content(content)
            content_type = detect_image_content_type(content) or content_type
            if preprocessed:
                return RectifiedImage(content, content_type, False, 1.0)
            return rectify_card_image(
                content, content_type, standardize_ratio=None, orient_portrait=False
            )
    except OSError as error:
        raise HTTPException(503, "图片处理繁忙，请重试", headers={"Retry-After": "2"}) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def capture_job_view(row):
    values = dict(row)
    revision = str(row.get("revision", 1))
    images = {}
    for side in ("front", "back"):
        url = f"/v1/capture/jobs/{row['id']}/images/{side}?revision={revision}"
        exists = bool(row.get(f"{side}_image_path"))
        values[f"{side}_image_url"] = url if exists else None
        images[side] = (
            {
                "url": url,
                "revision": revision,
                "source_url": url + "&source=true",
                "crop_recipe": (row.get("crop_recipe") or {}).get(side),
                "source_hash": (row.get("capture_metadata") or {}).get(side, {}).get("source_hash"),
                "thumb_url": url + "&variant=thumb",
                "preview_url": url + "&variant=preview",
                "original_url": url + "&variant=original",
            }
            if exists
            else None
        )
    return CaptureJob(
        **values,
        available_sides=[s for s in images if images[s]],
        images=images,
        source=row.get("capture_source", "scanner"),
    )


class CaptureService:
    def __init__(self, settings):
        self.settings = settings
        self.queue = ScannerQueueService(settings)

    async def enqueue(
        self,
        db,
        owner_id,
        key,
        images,
        *,
        sources=None,
        crop_recipe=None,
        target_item_id=None,
        expected_revision=None,
        legacy_digest=None,
    ):
        sources = sources or {}
        recipe = crop_recipe or {}
        digest = hashlib.sha256(
            json.dumps(
                {
                    "images": {s: hashlib.sha256(v[0]).hexdigest() for s, v in images.items()},
                    "sources": {s: hashlib.sha256(v[0]).hexdigest() for s, v in sources.items()},
                    "crop_recipe": recipe,
                    "target": str(target_item_id) if target_item_id else None,
                    "expected_revision": expected_revision.isoformat()
                    if expected_revision
                    else None,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        digest = legacy_digest or digest
        job_id = uuid4()
        directory = self.queue.root / str(owner_id) / str(job_id)
        written = []
        try:
            async with db.transaction():
                await db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"capture:{owner_id}:{key}",),
                )
                prior = await (
                    await db.execute(
                        "SELECT * FROM scanner_recognition_job WHERE owner_id=%s AND client_request_id=%s",
                        (owner_id, key),
                    )
                ).fetchone()
                if prior:
                    if prior["request_digest"] != digest:
                        raise HTTPException(409, "该上传编号已用于不同内容")
                    return capture_job_view(prior)
                if target_item_id:
                    await self.target(db, owner_id, target_item_id, expected_revision)
                directory.mkdir(parents=True, exist_ok=False)
                columns = [
                    "id",
                    "owner_id",
                    "require_confirmation",
                    "client_request_id",
                    "request_digest",
                    "capture_source",
                    "target_item_id",
                    "expected_revision",
                    "crop_recipe",
                    "capture_metadata",
                ]
                values = [
                    job_id,
                    owner_id,
                    True,
                    key,
                    digest,
                    "capture",
                    target_item_id,
                    expected_revision,
                    Jsonb(recipe),
                    Jsonb(
                        {
                            s: {"source_hash": hashlib.sha256(sources.get(s, v)[0]).hexdigest()}
                            for s, v in images.items()
                        }
                    ),
                ]
                for side, (content, content_type) in images.items():
                    raw, raw_type = sources.get(side, (content, content_type))
                    processed = await asyncio.to_thread(
                        prepare_capture_image, content, content_type, side in recipe
                    )
                    output = processed.content
                    output_type = processed.content_type
                    for suffix, data in [
                        ("raw" + ALLOWED_IMAGE_TYPES[raw_type], raw),
                        ("original" + ALLOWED_IMAGE_TYPES[output_type], output),
                        ("display" + ALLOWED_IMAGE_TYPES[output_type], output),
                    ]:
                        path = directory / f"{side}-{suffix}"
                        async with aiofiles.open(path, "wb") as handle:
                            await handle.write(data)
                        written.append(path)
                    columns.extend(
                        [
                            f"{side}_raw_image_path",
                            f"{side}_original_image_path",
                            f"{side}_image_path",
                            f"{side}_content_type",
                            f"{side}_crop_confidence",
                            f"{side}_transformed",
                        ]
                    )
                    values.extend([str(p.relative_to(self.queue.root)) for p in written[-3:]])
                    values.extend(
                        [
                            output_type,
                            processed.confidence,
                            processed.transformed,
                        ]
                    )
                row = await (
                    await db.execute(
                        f"INSERT INTO scanner_recognition_job ({','.join(columns)}) VALUES ({','.join(['%s'] * len(values))}) RETURNING *",
                        values,
                    )
                ).fetchone()
                return capture_job_view(row)
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            if directory.exists():
                directory.rmdir()
            raise

    async def target(self, db, owner_id, item_id, expected_revision):
        row = await (
            await db.execute(
                "SELECT * FROM collection_item WHERE id=%s AND owner_id=%s AND deleted_at IS NULL FOR UPDATE",
                (item_id, owner_id),
            )
        ).fetchone()
        if not row:
            raise HTTPException(404, "目标卡片不存在")
        if row["updated_at"] != expected_revision:
            raise HTTPException(409, "卡片已更新，请刷新后重新补拍")
        return row

    async def accept_identity(self, db, owner_id, item_id, payload):
        """Rebind only this collection row; never modify a shared catalog record."""
        # Lock before checking: the valuation worker claims queued rows with SKIP LOCKED.
        valuation_jobs = await (
            await db.execute(
                "SELECT id,status FROM valuation_job WHERE collection_item_id=%s FOR UPDATE",
                (item_id,),
            )
        ).fetchall()
        if any(
            job["status"] in {"identifying", "searching", "calculating"} for job in valuation_jobs
        ):
            raise HTTPException(409, IDENTITY_ACCEPTANCE_REASON)
        current_row = await (await db.execute(CAPTURE_TARGET_SQL, (item_id, owner_id))).fetchone()
        if not current_row["can_accept_identity"]:
            raise HTTPException(409, IDENTITY_ACCEPTANCE_REASON)
        current = CardIdentityInput.model_validate(dict(current_row)).model_dump()
        accepted = {**current, **payload.card.model_dump(exclude_unset=True)}
        payload = payload.model_copy(update={"card": CardIdentityInput.model_validate(accepted)})
        accepted = payload.card.model_dump()
        if any(
            current[key] != accepted[key] for key in current if key not in SUPPORTED_IDENTITY_FIELDS
        ):
            raise HTTPException(
                422,
                "补拍仅可接受球员、品牌、赛季、产品、系列、卡号、特卡和平行；其余身份字段必须保留",
            )
        conflict = HTTPException(
            409, "目标身份已有不同的人工字段，请保留原身份；补拍不会修改其他卡片"
        )
        # Serialize capture rebindings; concurrent legacy insertions are still handled
        # by conflict-safe inserts and exact-value checks below.
        await db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("capture-identity-rebind",)
        )
        if not all(accepted.get(key) for key in ("player_name", "brand_name", "product_name", "season_start")):
            repository = PortfolioRepository()
            player = await repository._get_or_create_player(db, payload)
            brand = await repository._get_or_create_brand(db, payload)
            card_set = await repository._get_or_create_set(db, brand, payload)
            return await repository._get_or_create_definition(db, player, card_set, payload)
        player = await PortfolioRepository()._get_or_create_player(db, payload)
        player_row = await (
            await db.execute("SELECT full_name FROM player WHERE id=%s", (player,))
        ).fetchone()
        if player_row["full_name"] != accepted["player_name"]:
            raise conflict
        await db.execute(
            "INSERT INTO brand(name,manufacturer,tier) VALUES (%s,%s,%s) ON CONFLICT(name) DO NOTHING",
            (accepted["brand_name"], accepted["manufacturer"], accepted["brand_tier"]),
        )
        brand = await (
            await db.execute("SELECT * FROM brand WHERE name=%s", (accepted["brand_name"],))
        ).fetchone()
        if (
            brand["manufacturer"] != accepted["manufacturer"]
            or brand["tier"] != accepted["brand_tier"]
        ):
            raise conflict
        parameters = (
            brand["id"],
            accepted["season_start"],
            accepted["product_name"],
            accepted["set_name"],
        )
        sets = await (
            await db.execute(
                "SELECT * FROM card_set WHERE brand_id=%s AND season_start=%s AND product_name=%s AND set_name IS NOT DISTINCT FROM %s",
                parameters,
            )
        ).fetchall()
        card_set = next((row for row in sets if row["season_end"] == accepted["season_end"]), None)
        if sets and not card_set:
            raise conflict
        if card_set is None:
            await db.execute(
                "INSERT INTO card_set(brand_id,season_start,product_name,set_name,season_end) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (*parameters, accepted["season_end"]),
            )
            card_set = await (
                await db.execute(
                    "SELECT * FROM card_set WHERE brand_id=%s AND season_start=%s AND product_name=%s AND set_name IS NOT DISTINCT FROM %s AND season_end IS NOT DISTINCT FROM %s",
                    (*parameters, accepted["season_end"]),
                )
            ).fetchone()
            if card_set is None:
                raise conflict
        columns = [
            "card_number",
            "insert_name",
            "parallel_name",
            "is_rookie",
            "is_autograph",
            "autograph_type",
            "has_inscription",
            "inscription_text",
            "is_memorabilia",
            "memorabilia_types",
            "memorabilia_usage",
            "memorabilia_player",
            "is_patch",
            "is_case_hit",
            "print_run",
        ]
        await db.execute(
            f"INSERT INTO card_definition(card_set_id,player_id,{','.join(columns)}) VALUES ({','.join(['%s'] * (len(columns) + 2))}) ON CONFLICT DO NOTHING",
            (card_set["id"], player, *[accepted[key] for key in columns]),
        )
        definition = await (
            await db.execute(
                "SELECT * FROM card_definition WHERE card_set_id=%s AND player_id=%s AND COALESCE(card_number,'')=COALESCE(%s,'') AND COALESCE(insert_name,'')=COALESCE(%s,'') AND COALESCE(parallel_name,'')=COALESCE(%s,'')",
                (
                    card_set["id"],
                    player,
                    accepted["card_number"],
                    accepted["insert_name"],
                    accepted["parallel_name"],
                ),
            )
        ).fetchone()
        if definition is None or any(definition[key] != accepted[key] for key in columns):
            raise conflict
        return definition["id"]

    async def confirm(self, db, owner_id, job_id, body):
        saved = []
        try:
            async with db.transaction():
                job = await (
                    await db.execute(
                        "SELECT * FROM scanner_recognition_job WHERE id=%s AND owner_id=%s FOR UPDATE",
                        (job_id, owner_id),
                    )
                ).fetchone()
                if not job:
                    raise HTTPException(404, "任务不存在")
                if not job["require_confirmation"]:
                    raise HTTPException(409, "任务不需要人工确认")
                if job["collection_item_id"]:
                    return CreatedResource(id=job["collection_item_id"])
                if job.get("imported_at") or (
                    job.get("expected_revision") and not job.get("target_item_id")
                ):
                    raise HTTPException(409, "原卡片已删除，任务不能再次创建卡片")
                if job["status"] != "succeeded":
                    raise HTTPException(409, "识别尚未完成")
                body = dict(body or {})
                pair_review = body.pop("pair_review", None)
                image_hashes = {}
                processed_contents = {}
                for side in ("front", "back"):
                    if job.get(f"{side}_image_path"):
                        path = self.queue.image_path(job[f"{side}_image_path"])
                        if not path.is_file():
                            raise HTTPException(409, "任务照片缺失，请重新上传")
                        processed_contents[side] = path.read_bytes()
                        image_hashes[side] = hashlib.sha256(processed_contents[side]).hexdigest()
                validate_pair_for_confirmation(retained_recognition_result(job) or {}, image_hashes, pair_review)
                decision = derive_pair_validation(retained_recognition_result(job) or {}, image_hashes)
                if len(image_hashes) == 2 and decision["status"] == "unknown" and not (
                    isinstance(pair_review, dict) and pair_review.get("confirmed") is True
                    and pair_review.get("image_content_hashes") == image_hashes
                ):
                    raw = dict(job.get("result") or {"confidence": 0})
                    raw["recognition_quality_issues"] = list(dict.fromkeys([
                        *raw.get("recognition_quality_issues", []), "pair_validation_pending",
                    ]))
                    await db.execute("UPDATE scanner_recognition_job SET result=%s WHERE id=%s", (Jsonb(raw), job_id))
                if job.get("target_item_id"):
                    await self.target(db, owner_id, job["target_item_id"], job["expected_revision"])
                    item_id = job["target_item_id"]
                    # Explicit acceptance is deliberately separate from ordinary creation fields.
                    if body:
                        if set(body) != {"accepted_identity"}:
                            raise HTTPException(
                                422, "补拍确认仅接受 {} 或 {accepted_identity: 完整卡片身份}"
                            )
                        payload = CollectionItemCreate.model_validate(
                            {"owner_id": owner_id, "card": body["accepted_identity"]}
                        )
                        definition = await self.accept_identity(db, owner_id, item_id, payload)
                        await db.execute(
                            "UPDATE scanner_recognition_job SET capture_metadata=capture_metadata || %s WHERE id=%s",
                            (
                                Jsonb({"accepted_identity": payload.card.model_dump(mode="json")}),
                                job_id,
                            ),
                        )
                        await db.execute(
                            "UPDATE collection_item SET card_definition_id=%s,updated_at=now() WHERE id=%s",
                            (definition, item_id),
                        )
                        stored_identity = await (
                            await db.execute(CAPTURE_TARGET_SQL, (item_id, owner_id))
                        ).fetchone()
                        if stored_identity and all(stored_identity[key] for key in (
                            "player_name", "brand_name", "product_name", "season_start"
                        )):
                            await db.execute(
                                """INSERT INTO automatic_purchase_cost(item_id,owner_id)
                                   SELECT %s,%s WHERE NOT EXISTS(
                                     SELECT 1 FROM card_transaction WHERE collection_item_id=%s AND transaction_type='buy')
                                   ON CONFLICT(item_id) DO NOTHING""",
                                (item_id, owner_id, item_id),
                            )
                            await db.execute(
                                """INSERT INTO valuation_job(collection_item_id,status)
                                   SELECT %s,'queued' WHERE NOT EXISTS(
                                     SELECT 1 FROM valuation_job WHERE collection_item_id=%s
                                       AND (status IN ('queued','identifying','searching','calculating')
                                         OR result_summary->>'identity_resolution_status' IS DISTINCT FROM 'pending'))
                                   ON CONFLICT(collection_item_id)
                                     WHERE status IN ('queued','identifying','searching','calculating') DO NOTHING""",
                                (item_id, item_id),
                            )
                else:
                    payload = CollectionItemCreate.model_validate({**body, "owner_id": owner_id})
                    item_id = await PortfolioRepository().create_item(db, payload)
                for side in ("front", "back"):
                    if not job[f"{side}_image_path"]:
                        continue
                    path = self.queue.image_path(job[f"{side}_image_path"])
                    if not path.is_file():
                        raise HTTPException(409, "任务照片缺失，请重新上传")
                    image = await ImageStorageService(
                        self.settings
                    ).save_preprocessed_item_image_content(
                        db, item_id, owner_id, side, processed_contents[side], job[f"{side}_content_type"]
                    )
                    saved.append(
                        self.settings.resolved_upload_dir / image.image_url.rsplit("/", 1)[-1]
                    )
                await db.execute(
                    "UPDATE scanner_recognition_job SET collection_item_id=%s, imported_at=now(),updated_at=now() WHERE id=%s",
                    (item_id, job_id),
                )
                return CreatedResource(id=item_id)
        except Exception as error:
            for path in saved:
                path.unlink(missing_ok=True)
            if isinstance(error, ValidationError):
                raise HTTPException(422, str(error)) from error
            raise
