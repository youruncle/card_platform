import asyncio
import logging
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
from card_platform.image_service import ALLOWED_IMAGE_TYPES, CardVisionService
from card_platform.schemas import CardRecognitionResult, ScannerRecognitionJob


logger = logging.getLogger(__name__)

PAIR_QUAD_MEAN_DISTANCE = 0.07
PAIR_QUAD_MAX_DISTANCE = 0.12
PAIR_QUALITY_ADVANTAGE = 0.10


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
    ) -> ScannerRecognitionJob:
        job_id = uuid4()
        job_dir = self.root / str(owner_id) / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=False)
        effective_front_raw = front_raw_content or front_content
        effective_back_raw = back_raw_content or back_content
        effective_front_raw_type = front_raw_content_type or front_content_type
        effective_back_raw_type = back_raw_content_type or back_content_type
        front_raw_path = job_dir / f"front-raw{ALLOWED_IMAGE_TYPES[effective_front_raw_type]}"
        back_raw_path = job_dir / f"back-raw{ALLOWED_IMAGE_TYPES[effective_back_raw_type]}"
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
        if (
            front.transformed
            and back.transformed
            and front.normalized_quad
            and back.normalized_quad
        ):
            mean_distance, max_distance = _quad_distance(
                front.normalized_quad, back.normalized_quad
            )
            if (
                mean_distance > PAIR_QUAD_MEAN_DISTANCE
                or max_distance > PAIR_QUAD_MAX_DISTANCE
            ):
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
                    front = _preserved_image(
                        front_content, front_content_type, front.confidence
                    )
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
                result = await vision.recognize_pair_contents(
                    front_content,
                    "image/jpeg",
                    back_content,
                    "image/jpeg",
                )
                await connection.execute(
                    """
                    UPDATE scanner_recognition_job
                    SET status = 'succeeded', result = %s, finished_at = now(),
                        updated_at = now(), error_message = NULL
                    WHERE id = %s
                    """,
                    (Jsonb(result.model_dump(mode="json")), job_id),
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
    task: asyncio.Task, worker: ScannerRecognitionWorker
) -> None:
    await worker.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
