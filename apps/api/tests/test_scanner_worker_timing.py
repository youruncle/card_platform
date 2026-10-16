import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from card_platform.card_orientation import OrientationDecision
from card_platform.config import Settings
from card_platform.scanner_queue import ScannerRecognitionWorker
from card_platform.schemas import CardRecognitionResult


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["none", "manual", "deleted", "retry", "imported_then_failed", "retry_then_failed"])
async def test_worker_orients_both_sides_concurrently_without_holding_database(
    tmp_path, monkeypatch, race
):
    job_id = uuid4()
    job = {"id": job_id, "owner_id": uuid4(), "front_image_path": "front.jpg",
           "back_image_path": "back.jpg", "front_original_image_path": "front-original.jpg",
           "back_original_image_path": "back-original.jpg", "front_content_type": "image/jpeg",
           "back_content_type": "image/jpeg", "crop_recipe": {}, "require_confirmation": True,
           "created_at": datetime.now(UTC), "started_at": datetime.now(UTC),
           "status": "running", "attempt_count": 1}
    if race == "manual":
        job["crop_recipe"] = {side: {"confirmed": True} for side in ("front", "back")}
        job.update(front_content_type="image/png", back_content_type="image/png")
    for name in ("front", "back"):
        (tmp_path / f"{name}.jpg").write_bytes(name.encode())
    queries = []

    class Result:
        async def fetchone(self):
            return None if database.deleted else job

    class DB:
        depth = 0
        deleted = False

        @asynccontextmanager
        async def connection(self):
            self.depth += 1
            try:
                yield self
            finally:
                self.depth -= 1

        @asynccontextmanager
        async def transaction(self):
            yield

        async def execute(self, sql, params=None):
            queries.append((sql, params))
            if "status = 'failed'" in sql:
                guarded = "attempt_count = %s" in sql and "collection_item_id IS NULL" in sql
                if not guarded or (job["status"] == "running" and job["attempt_count"] == params[-2]
                                   and job["started_at"] == params[-1] and not job.get("collection_item_id")):
                    job["status"] = "failed"
            return Result()

    database = DB()
    both_started = asyncio.Event()
    started = []

    class Vision:
        def __init__(self, settings):
            pass

        async def recognize_pair_contents(self, front, front_type, back, back_type):
            assert database.depth == 0, "network recognition holds a database connection"
            assert front == b"front" and back == b"back"
            expected_type = "image/png" if race == "manual" else "image/jpeg"
            assert front_type == back_type == expected_type
            database.deleted = race == "deleted"
            if race in {"retry", "retry_then_failed"}:
                job["attempt_count"] += 1
            if race == "imported_then_failed":
                job.update(status="succeeded", collection_item_id=uuid4())
            if race.endswith("failed"):
                raise RuntimeError("late recognition failure")
            return CardRecognitionResult(player_name="Kobe Bryant", confidence=".97")

    monkeypatch.setattr("card_platform.scanner_queue.CardVisionService", Vision)
    worker = ScannerRecognitionWorker(database, Settings())
    worker.queue = SimpleNamespace(image_path=lambda relative: tmp_path / relative)

    async def orient(original, display, content_type, vision):
        assert race != "manual", "manual crop must not be reoriented"
        assert database.depth == 0, "slow orientation holds a database connection"
        started.append(display.name)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), .1)
        return OrientationDecision(0, .99, "vision_model")

    worker._orient_side = orient
    await worker._process(job_id)
    successful = [(sql, params) for sql, params in queries if "status = 'succeeded'" in sql]
    if race not in {"none", "manual"}:
        assert job["status"] != "failed", "old failure must not overwrite a new attempt or imported job"
        assert not successful, "a deleted job must not publish a late recognition result"
        return
    assert successful, f"job failed: {queries[-1]}"
    sql, params = successful[0]
    assert "clock_timestamp()" in sql, "finished_at must represent completion, not transaction start"
    payload = params[0].obj
    assert payload["player_name"] == "Kobe Bryant"
    stages = next(row for row in payload["recognition_route"] if row["tier"] == "scanner_pipeline")
    assert stages["stages"]["orientation"]["concurrent"] is True
    assert set(stages["stages"]["orientation"]["sides"]) == {"front", "back"}
    if race == "manual":
        assert all(side["method"] == "manual" for side in stages["stages"]["orientation"]["sides"].values())
    assert database.depth == 0
