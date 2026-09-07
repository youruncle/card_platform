import asyncio
import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiofiles
from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.card_orientation import (
    AppleVisionOrientationClassifier,
    OrientationDecision,
    rotate_image_content,
)
from card_platform.db import Database
from card_platform.image_rectification import RectifiedImage, rectify_card_image
from card_platform.image_service import ALLOWED_IMAGE_TYPES, CardVisionService, ImageStorageService
from card_platform.repository import PortfolioRepository
from card_platform.recognition_benchmark_predictor import canonicalize_identity
from card_platform.vision_routing import finalize_recognition_status
from card_platform.schemas import (
    CardIdentityInput,
    CardRecognitionResult,
    CollectionItemCreate,
    ScannerRecognitionJob,
)


logger = logging.getLogger(__name__)

PAIR_QUAD_MEAN_DISTANCE = 0.07
PAIR_QUAD_MAX_DISTANCE = 0.12
PAIR_QUALITY_ADVANTAGE = 0.10
MIN_PROCESSED_QUAD_AREA = 0.13


def _quad_quality(quad: tuple[tuple[float, float], ...] | None) -> float:
    if quad is None:
        return 0
    points = [complex(x, y) for x, y in quad]
    horizontal = (abs(points[1] - points[0]), abs(points[2] - points[3]))
    vertical = (abs(points[3] - points[0]), abs(points[2] - points[1]))
    return min(min(edges) / max(edges) for edges in (horizontal, vertical))


def _quad_distance(
    first: tuple[tuple[float, float], ...],
    second: tuple[tuple[float, float], ...],
) -> tuple[float, float]:
    distances = [abs(complex(*left) - complex(*right)) for left, right in zip(first, second)]
    return sum(distances) / len(distances), max(distances)


def _preserved_image(content: bytes, content_type: str, confidence: float) -> RectifiedImage:
    return RectifiedImage(content, content_type, False, confidence)


def _quad_area(quad: tuple[tuple[float, float], ...] | None) -> float:
    if quad is None:
        return 0
    return abs(
        sum(
            quad[index][0] * quad[(index + 1) % 4][1]
            - quad[(index + 1) % 4][0] * quad[index][1]
            for index in range(4)
        )
        / 2
    )


def _rectify_pair(
    front_content: bytes,
    front_content_type: str,
    back_content: bytes,
    back_content_type: str,
) -> tuple[RectifiedImage, RectifiedImage]:
    front = rectify_card_image(
        front_content,
        front_content_type,
        standardize_ratio=None,
        orient_portrait=False,
    )
    back = rectify_card_image(
        back_content,
        back_content_type,
        standardize_ratio=None,
        orient_portrait=False,
    )
    if front.transformed and back.transformed and front.normalized_quad and back.normalized_quad:
        mean_distance, max_distance = _quad_distance(front.normalized_quad, back.normalized_quad)
        if mean_distance > PAIR_QUAD_MEAN_DISTANCE or max_distance > PAIR_QUAD_MAX_DISTANCE:
            front_quality = _quad_quality(front.normalized_quad)
            back_quality = _quad_quality(back.normalized_quad)
            if back_quality - front_quality >= PAIR_QUALITY_ADVANTAGE:
                front = rectify_card_image(
                    front_content,
                    front_content_type,
                    standardize_ratio=None,
                    orient_portrait=False,
                    fallback_normalized_quad=back.normalized_quad,
                    fallback_confidence=back.confidence,
                    prefer_fallback_quad=True,
                )
            elif front_quality - back_quality >= PAIR_QUALITY_ADVANTAGE:
                back = rectify_card_image(
                    back_content,
                    back_content_type,
                    standardize_ratio=None,
                    orient_portrait=False,
                    fallback_normalized_quad=front.normalized_quad,
                    fallback_confidence=front.confidence,
                    prefer_fallback_quad=True,
                )
            else:
                # Conflicting but similarly plausible outlines cannot safely
                # identify which face is right. Preserve both source images.
                front = _preserved_image(front_content, front_content_type, front.confidence)
                back = _preserved_image(back_content, back_content_type, back.confidence)
    if not front.transformed and back.transformed and back.normalized_quad:
        front = rectify_card_image(
            front_content,
            front_content_type,
            standardize_ratio=None,
            orient_portrait=False,
            fallback_normalized_quad=back.normalized_quad,
            fallback_confidence=back.confidence,
        )
    if not back.transformed and front.transformed and front.normalized_quad:
        back = rectify_card_image(
            back_content,
            back_content_type,
            standardize_ratio=None,
            orient_portrait=False,
            fallback_normalized_quad=front.normalized_quad,
            fallback_confidence=front.confidence,
        )
    return front, back


def _needs_raw_recovery(front: RectifiedImage, back: RectifiedImage) -> bool:
    """Detect a zoom crop that found an inner patch or could not agree on an outline."""
    return (
        not front.transformed
        or not back.transformed
        or _quad_area(front.normalized_quad) < MIN_PROCESSED_QUAD_AREA
        or _quad_area(back.normalized_quad) < MIN_PROCESSED_QUAD_AREA
    )


def _safe_raw_recovery(front: RectifiedImage, back: RectifiedImage) -> bool:
    return bool(
        front.transformed
        and back.transformed
        and front.normalized_quad
        and back.normalized_quad
    )


def scanner_job_view(row: dict) -> ScannerRecognitionJob:
    values = dict(row)
    job_id = row["id"]
    result = (
        CardRecognitionResult.model_validate(row["result"])
        if row.get("result") is not None
        else None
    )
    values["result"] = result
    version = row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else row["updated_at"]
    return ScannerRecognitionJob(
        **values,
        front_image_url=f"/v1/scanner/jobs/{job_id}/images/front?v={version}",
        back_image_url=f"/v1/scanner/jobs/{job_id}/images/back?v={version}",
    )


def _auto_import_payload(owner_id: UUID, result: CardRecognitionResult) -> CollectionItemCreate:
    season_start = result.season_start or result.copyright_year or datetime.now().year
    season_end = (
        result.season_end
        if result.season_end in {season_start, season_start + 1}
        else None
    )
    confidence_percent = round(float(result.confidence) * 100)
    issue_note = (
        f"；待核对：{', '.join(result.recognition_quality_issues)}"
        if result.recognition_quality_issues
        else ""
    )
    return CollectionItemCreate(
        owner_id=owner_id,
        card=CardIdentityInput(
            player_name=result.player_name or "无法识别球员",
            brand_name=result.brand_name or "无法识别品牌",
            season_start=season_start,
            season_end=season_end,
            product_name=result.product_name or "无法识别产品",
            card_number=result.card_number,
            insert_name=result.insert_name,
            parallel_name=result.parallel_name,
            is_rookie=bool(result.is_rookie),
            is_autograph=bool(result.is_autograph),
            autograph_type=result.autograph_type or "none",
            has_inscription=bool(result.has_inscription),
            inscription_text=result.inscription_text,
            is_memorabilia=bool(result.is_memorabilia),
            memorabilia_types=result.memorabilia_types,
            memorabilia_usage=result.memorabilia_usage or "none",
            memorabilia_player=result.memorabilia_player,
            is_patch=bool(result.is_patch),
            print_run=result.serial_total,
        ),
        serial_number=result.serial_number,
        serial_total=result.serial_total,
        is_numbered=bool(result.is_numbered),
        grade_company=result.grade_company,
        grade_value=result.grade_value,
        notes=(
            f"后台扫描自动录入；识别置信度 {confidence_percent}%{issue_note}。"
            "识别依据已保留，可编辑并参与估值。"
        ),
    )


class ScannerQueueService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Scanner captures contain private collection data. Keep them outside
        # upload_dir, which is intentionally exposed through /uploads.
        self.root = Path(settings.scanner_job_dir).resolve()

    async def enqueue(
        self,
        connection,
        owner_id: UUID,
        front_content: bytes,
        front_content_type: str,
        back_content: bytes,
        back_content_type: str,
        *,
        front_raw_content: bytes | None = None,
        front_raw_content_type: str | None = None,
        back_raw_content: bytes | None = None,
        back_raw_content_type: str | None = None,
        preprocessed: bool = False,
        front_crop_confidence: float | None = None,
        back_crop_confidence: float | None = None,
    ) -> ScannerRecognitionJob:
        job_id = uuid4()
        job_dir = self.root / str(owner_id) / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        effective_front_raw = (
            front_raw_content if front_raw_content is not None else front_content
        )
        effective_back_raw = (
            back_raw_content if back_raw_content is not None else back_content
        )
        effective_front_raw_type = (
            front_raw_content_type if front_raw_content is not None else front_content_type
        )
        effective_back_raw_type = (
            back_raw_content_type if back_raw_content is not None else back_content_type
        )
        front_raw_path = job_dir / f"front-raw{ALLOWED_IMAGE_TYPES[effective_front_raw_type]}"
        back_raw_path = job_dir / f"back-raw{ALLOWED_IMAGE_TYPES[effective_back_raw_type]}"
        if preprocessed:
            front = _preserved_image(
                front_content,
                front_content_type,
                1.0 if front_crop_confidence is None else front_crop_confidence,
            )
            back = _preserved_image(
                back_content,
                back_content_type,
                1.0 if back_crop_confidence is None else back_crop_confidence,
            )
        else:
            front, back = _rectify_pair(
                front_content,
                front_content_type,
                back_content,
                back_content_type,
            )
        if (
            not preprocessed
            and front_raw_content is not None
            and back_raw_content is not None
            and _needs_raw_recovery(front, back)
        ):
            raw_front, raw_back = _rectify_pair(
                effective_front_raw,
                effective_front_raw_type,
                effective_back_raw,
                effective_back_raw_type,
            )
            if _safe_raw_recovery(raw_front, raw_back):
                front, back = raw_front, raw_back
        front_path = job_dir / "front.jpg"
        back_path = job_dir / "back.jpg"
        front_original_path = job_dir / "front-original.jpg"
        back_original_path = job_dir / "back-original.jpg"
        try:
            for path, content in (
                (front_raw_path, effective_front_raw),
                (back_raw_path, effective_back_raw),
                (front_original_path, front.content),
                (back_original_path, back.content),
                (front_path, front.content),
                (back_path, back.content),
            ):
                async with aiofiles.open(path, "wb") as output:
                    await output.write(content)
            inserted = await connection.execute(
                """
                INSERT INTO scanner_recognition_job (
                    id, owner_id, front_image_path, back_image_path,
                    front_original_image_path, back_original_image_path,
                    front_raw_image_path, back_raw_image_path,
                    front_content_type, back_content_type,
                    front_crop_confidence, back_crop_confidence,
                    front_transformed, back_transformed
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                RETURNING *
                """,
                (
                    job_id,
                    owner_id,
                    str(front_path.relative_to(self.root)),
                    str(back_path.relative_to(self.root)),
                    str(front_original_path.relative_to(self.root)),
                    str(back_original_path.relative_to(self.root)),
                    str(front_raw_path.relative_to(self.root)),
                    str(back_raw_path.relative_to(self.root)),
                    front.content_type,
                    back.content_type,
                    front.confidence,
                    back.confidence,
                    front.transformed,
                    back.transformed,
                ),
            )
            return scanner_job_view(dict(await inserted.fetchone()))
        except Exception:
            for path in (
                front_path,
                back_path,
                front_original_path,
                back_original_path,
                front_raw_path,
                back_raw_path,
            ):
                path.unlink(missing_ok=True)
            job_dir.rmdir()
            raise

    def image_path(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if self.root not in candidate.parents:
            raise ValueError("invalid scanner image path")
        return candidate

    def delete_job_files(self, row: dict) -> None:
        """Delete only files explicitly recorded for one already-deleted job."""
        directories: set[Path] = set()
        for column in (
            "front_image_path",
            "back_image_path",
            "front_original_image_path",
            "back_original_image_path",
            "front_raw_image_path",
            "back_raw_image_path",
        ):
            relative_path = row.get(column)
            if not relative_path:
                continue
            try:
                path = self.image_path(relative_path)
            except ValueError:
                logger.warning("ignored unsafe scanner image path during delete")
                continue
            path.unlink(missing_ok=True)
            directories.add(path.parent)
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                logger.warning("scanner job directory was not empty: %s", directory)


class ScannerRecognitionWorker:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.queue = ScannerQueueService(settings)
        self._stop = asyncio.Event()

    async def run(self) -> None:
        await self._recover_stale()
        while not self._stop.is_set():
            try:
                job_id = await self._claim()
                if job_id is None:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.settings.scanner_worker_poll_seconds
                    )
                    continue
                await self._process(job_id)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("scanner recognition worker loop failed")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._stop.set()

    async def _recover_stale(self) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                """
                UPDATE scanner_recognition_job
                SET status = 'queued', error_message = 'worker restarted', updated_at = now()
                WHERE status = 'running'
                  AND updated_at < now() - interval '15 minutes'
                  AND attempt_count < %s
                """,
                (self.settings.scanner_job_max_attempts,),
            )
            await connection.execute(
                """
                UPDATE scanner_recognition_job
                SET status = 'failed', error_message = 'maximum attempts reached',
                    finished_at = now(), updated_at = now()
                WHERE status = 'running' AND attempt_count >= %s
                """,
                (self.settings.scanner_job_max_attempts,),
            )

    async def _claim(self) -> UUID | None:
        async with self.database.connection() as connection, connection.transaction():
            result = await connection.execute(
                """
                SELECT id FROM scanner_recognition_job
                WHERE status = 'queued' AND attempt_count < %s
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
                """,
                (self.settings.scanner_job_max_attempts,),
            )
            row = await result.fetchone()
            if row is None:
                return None
            await connection.execute(
                """
                UPDATE scanner_recognition_job
                SET status = 'running', attempt_count = attempt_count + 1,
                    started_at = now(), finished_at = NULL,
                    error_message = NULL, updated_at = now()
                WHERE id = %s
                """,
                (row["id"],),
            )
            return row["id"]

    async def _process(self, job_id: UUID) -> None:
        try:
            async with self.database.connection() as connection:
                selected = await connection.execute(
                    "SELECT * FROM scanner_recognition_job WHERE id = %s",
                    (job_id,),
                )
                job = await selected.fetchone()
                if job is None:
                    return
                front_path = self.queue.image_path(job["front_image_path"])
                back_path = self.queue.image_path(job["back_image_path"])
                front_original_path = self.queue.image_path(job["front_original_image_path"])
                back_original_path = self.queue.image_path(job["back_original_image_path"])
                vision = CardVisionService(self.settings)
                front_decision, back_decision = await asyncio.gather(
                    self._orient_side(
                        front_original_path, front_path, job["front_content_type"], vision
                    ),
                    self._orient_side(
                        back_original_path, back_path, job["back_content_type"], vision
                    ),
                )
                await connection.execute(
                    """
                    UPDATE scanner_recognition_job
                    SET front_rotation_degrees = %s,
                        front_orientation_confidence = %s,
                        front_orientation_method = %s,
                        front_orientation_corrected_at = now(),
                        front_content_type = 'image/jpeg',
                        back_rotation_degrees = %s,
                        back_orientation_confidence = %s,
                        back_orientation_method = %s,
                        back_orientation_corrected_at = now(),
                        back_content_type = 'image/jpeg',
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        front_decision.rotation_degrees,
                        front_decision.confidence,
                        front_decision.method,
                        back_decision.rotation_degrees,
                        back_decision.confidence,
                        back_decision.method,
                        job_id,
                    ),
                )
                async with aiofiles.open(front_path, "rb") as source:
                    front_content = await source.read()
                async with aiofiles.open(back_path, "rb") as source:
                    back_content = await source.read()
                result = finalize_recognition_status(
                    canonicalize_identity(
                        await vision.recognize_pair_contents(
                            front_content,
                            "image/jpeg",
                            back_content,
                            "image/jpeg",
                        )
                    )
                )
                # Recognition quality is advisory. A successful scanner job is
                # always imported, queued for valuation, and remains editable;
                # uncertainty is exposed as confidence and issue metadata.
                async with connection.transaction():
                    item_id = await PortfolioRepository().create_item(
                        connection, _auto_import_payload(job["owner_id"], result)
                    )
                    storage = ImageStorageService(self.settings)
                    await storage.save_preprocessed_item_image_content(
                        connection, item_id, job["owner_id"], "front", front_content, "image/jpeg"
                    )
                    await storage.save_preprocessed_item_image_content(
                        connection, item_id, job["owner_id"], "back", back_content, "image/jpeg"
                    )
                    await connection.execute(
                        """
                        UPDATE scanner_recognition_job
                        SET status = 'succeeded', result = %s, collection_item_id = %s,
                            imported_at = now(), finished_at = now(), updated_at = now(),
                            error_message = NULL
                        WHERE id = %s
                        """,
                        (Jsonb(result.model_dump(mode="json")), item_id, job_id),
                    )
        except Exception as error:
            async with self.database.connection() as connection:
                await connection.execute(
                    """
                    UPDATE scanner_recognition_job
                    SET status = 'failed', error_message = %s,
                        finished_at = now(), updated_at = now()
                    WHERE id = %s
                    """,
                    (str(error)[:500], job_id),
                )

    async def _orient_side(
        self,
        original_path: Path,
        display_path: Path,
        content_type: str,
        vision: CardVisionService,
    ) -> OrientationDecision:
        classifier = AppleVisionOrientationClassifier(
            self.settings.scanner_orientation_script
        )
        decision = await classifier.classify(original_path)
        async with aiofiles.open(original_path, "rb") as source:
            original_content = await source.read()
        if decision is None or decision.confidence < self.settings.scanner_orientation_local_threshold:
            provider = vision.provider
            if provider is not None and hasattr(provider, "detect_orientation"):
                try:
                    decision = await provider.detect_orientation(original_content, content_type)
                except Exception as error:
                    logger.warning("vision orientation fallback failed: %s", error)
        if decision is None:
            decision = OrientationDecision(0, 0, "unresolved")
        normalized = rotate_image_content(original_content, decision.rotation_degrees)
        temporary_path = display_path.with_suffix(".tmp.jpg")
        async with aiofiles.open(temporary_path, "wb") as output:
            await output.write(normalized)
        temporary_path.replace(display_path)
        return decision


async def stop_scanner_worker(
    tasks: asyncio.Task | list[asyncio.Task], worker: ScannerRecognitionWorker
) -> None:
    await worker.stop()
    worker_tasks = [tasks] if isinstance(tasks, asyncio.Task) else tasks
    for task in worker_tasks:
        task.cancel()
    for task in worker_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
