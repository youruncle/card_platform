import asyncio
import logging
from uuid import UUID

from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.db import Database
from card_platform.recognition_benchmark_predictor import RecognitionBenchmarkPredictor

logger = logging.getLogger(__name__)
MAX_JOB_ATTEMPTS = 5


class RecognitionBenchmarkWorker:
    """Durable single-job worker for long-running vision benchmark predictions."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self._stop = asyncio.Event()

    async def run(self) -> None:
        await self._recover_stale()
        while not self._stop.is_set():
            try:
                job_id = await self._claim()
                if job_id is None:
                    await asyncio.wait_for(self._stop.wait(), timeout=2)
                    continue
                await self._process(job_id)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("recognition benchmark worker loop failed")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._stop.set()

    async def _recover_stale(self) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                """
                UPDATE recognition_prediction_job
                SET status = 'queued', updated_at = now(),
                    error_message = 'worker restarted after stale running lease'
                WHERE status = 'running'
                  AND updated_at < now() - interval '30 minutes'
                  AND attempt_count < %s
                """
                ,
                (MAX_JOB_ATTEMPTS,),
            )

    async def _claim(self) -> UUID | None:
        async with self.database.connection() as connection, connection.transaction():
            result = await connection.execute(
                """
                SELECT id
                FROM recognition_prediction_job
                WHERE status = 'queued' AND attempt_count < %s
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """,
                (MAX_JOB_ATTEMPTS,),
            )
            row = await result.fetchone()
            if row is None:
                return None
            await connection.execute(
                """
                UPDATE recognition_prediction_job
                SET status = 'running', started_at = COALESCE(started_at, now()),
                    attempt_count = attempt_count + 1, updated_at = now(),
                    error_message = NULL
                WHERE id = %s
                """,
                (row["id"],),
            )
            return row["id"]

    async def _process(self, job_id: UUID) -> None:
        heartbeat: asyncio.Task | None = None
        try:
            heartbeat = asyncio.create_task(self._heartbeat(job_id))
            async with self.database.connection() as connection:
                result = await connection.execute(
                    """
                    SELECT batch_id, requested_limit, retry_errors, replay_all,
                           knowledge_version
                    FROM recognition_prediction_job
                    WHERE id = %s
                    """,
                    (job_id,),
                )
                job = await result.fetchone()
                if job is None:
                    return
                async def update_progress(
                    requested: int, predicted: int, failed: int
                ) -> None:
                    await connection.execute(
                        """
                        UPDATE recognition_prediction_job
                        SET requested_count = %s, predicted_count = %s,
                            failed_count = %s, updated_at = clock_timestamp()
                        WHERE id = %s
                        """,
                        (requested, predicted, failed, job_id),
                    )

                outcome = await RecognitionBenchmarkPredictor(
                    self.settings
                ).predict_batch(
                    connection,
                    job["batch_id"],
                    limit=job["requested_limit"],
                    retry_errors=job["retry_errors"],
                    replay_all=job["replay_all"],
                    knowledge_version=job["knowledge_version"],
                    job_id=job_id,
                    progress_callback=update_progress,
                )
                final_status = (
                    "succeeded"
                    if outcome["failed"] == 0
                    else "partial"
                    if outcome["predicted"] > 0
                    else "failed"
                )
                await connection.execute(
                    """
                    UPDATE recognition_prediction_job
                    SET status = %s, requested_count = %s, predicted_count = %s,
                        failed_count = %s, finished_at = clock_timestamp(),
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (
                        final_status,
                        outcome["requested"],
                        outcome["predicted"],
                        outcome["failed"],
                        job_id,
                    ),
                )
        except Exception as error:
            async with self.database.connection() as connection:
                await connection.execute(
                    """
                    UPDATE recognition_prediction_job
                    SET status = 'failed', error_message = %s,
                        finished_at = clock_timestamp(),
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (str(error)[:500], job_id),
                )
                await connection.execute(
                    """
                    UPDATE recognition_benchmark_batch
                    SET status = 'failed', metrics = metrics || %s, updated_at = now()
                    WHERE id = (
                        SELECT batch_id FROM recognition_prediction_job WHERE id = %s
                    )
                    """,
                    (Jsonb({"prediction_job_error": str(error)[:300]}), job_id),
                )
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass

    async def _heartbeat(self, job_id: UUID) -> None:
        """Keep the durable lease fresh while external vision/search calls run."""
        while True:
            await asyncio.sleep(15)
            async with self.database.connection() as connection:
                await connection.execute(
                    """
                    UPDATE recognition_prediction_job
                    SET updated_at = clock_timestamp()
                    WHERE id = %s AND status = 'running'
                    """,
                    (job_id,),
                )


async def stop_recognition_worker(
    task: asyncio.Task, worker: RecognitionBenchmarkWorker
) -> None:
    await worker.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
