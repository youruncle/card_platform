"""Completion timestamps and short database leases use a disposable database."""

import asyncio
import os
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

from test_mobile_postgres import account, photos
from test_mobile_postgres import client as client

from card_platform.card_orientation import OrientationDecision, rotate_image_content
from card_platform.config import get_settings
from card_platform.image_service import CardVisionService
from card_platform.main import app
from card_platform.scanner_queue import ScannerRecognitionWorker
from card_platform.schemas import CardRecognitionResult


pytestmark = pytest.mark.skipif(
    "TEST_DATABASE_URL" not in os.environ, reason="disposable migrated PostgreSQL required"
)


def test_import_finishes_at_real_wall_time_without_idle_transaction_during_recognition(client, monkeypatch):
    owner, headers = account(client)
    response = client.post("/v1/scanner/jobs", headers=headers, files=photos())
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("UPDATE scanner_recognition_job SET status='running',started_at=clock_timestamp() WHERE id=%s", (job_id,))

    async def orient(self, original, display, content_type, vision):
        await asyncio.sleep(.03)
        display.write_bytes(await asyncio.to_thread(rotate_image_content, original.read_bytes(), 0))
        return OrientationDecision(0, .99, "vision_model")

    async def recognize(*args):
        async with app.state.database.connection() as db:
            row = await (await db.execute(
                """SELECT count(*) AS count FROM pg_stat_activity
                WHERE datname=current_database() AND state='idle in transaction'"""
            )).fetchone()
            assert row["count"] == 0, "recognition keeps a database transaction open"
        await asyncio.sleep(.08)
        return CardRecognitionResult(player_name="Timing Fixture", brand_name="Panini",
                                     product_name="Prizm", season_start=2024, season_end=2025,
                                     card_number="T1", confidence=".99")

    monkeypatch.setattr(ScannerRecognitionWorker, "_orient_side", orient)
    monkeypatch.setattr(CardVisionService, "recognize_pair_contents", recognize)
    client.portal.call(ScannerRecognitionWorker(app.state.database, get_settings())._process, job_id)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as db:
        row = db.execute("SELECT * FROM scanner_recognition_job WHERE id=%s", (job_id,)).fetchone()
        assert row["status"] == "succeeded", row["error_message"]
        assert (row["finished_at"] - row["started_at"]).total_seconds() >= .1
        assert row["imported_at"] <= row["finished_at"]
        item = db.execute("SELECT * FROM collection_item WHERE id=%s", (row["collection_item_id"],)).fetchone()
        assert item["owner_id"] == UUID(owner["id"])
        assert (item["created_at"] - row["started_at"]).total_seconds() >= .1
        pipeline = next(t for t in row["result"]["recognition_route"] if t["tier"] == "scanner_pipeline")
        assert pipeline["stages"]["orientation"]["concurrent"] is True


@pytest.mark.parametrize("race", ["retry_then_success", "retry_then_failure", "succeeded_then_failure"])
def test_stale_attempt_cannot_publish_or_overwrite_a_new_attempt(client, monkeypatch, race):
    _, headers = account(client)
    response = client.post("/v1/scanner/jobs", headers=headers, files=photos())
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
        db.execute("""UPDATE scanner_recognition_job SET require_confirmation=true,status='running',
                   attempt_count=1,started_at=clock_timestamp() WHERE id=%s""", (job_id,))

    async def orient(self, original, display, content_type, vision):
        display.write_bytes(await asyncio.to_thread(rotate_image_content, original.read_bytes(), 0))
        return OrientationDecision(0, .99, "vision_model")

    async def recognize(*args):
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as db:
            if race == "succeeded_then_failure":
                db.execute("UPDATE scanner_recognition_job SET status='succeeded' WHERE id=%s", (job_id,))
            else:
                db.execute("""UPDATE scanner_recognition_job SET attempt_count=2,
                           started_at=clock_timestamp() WHERE id=%s""", (job_id,))
        if race.endswith("failure"):
            raise RuntimeError("late failure from obsolete attempt")
        return CardRecognitionResult(player_name="Obsolete result", confidence=".99")

    monkeypatch.setattr(ScannerRecognitionWorker, "_orient_side", orient)
    monkeypatch.setattr(CardVisionService, "recognize_pair_contents", recognize)
    client.portal.call(ScannerRecognitionWorker(app.state.database, get_settings())._process, job_id)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as db:
        row = db.execute("SELECT * FROM scanner_recognition_job WHERE id=%s", (job_id,)).fetchone()
        assert row["status"] == ("succeeded" if race == "succeeded_then_failure" else "running")
        assert row["result"] is None
        assert row["collection_item_id"] is None
        assert row["error_message"] is None
