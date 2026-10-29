"""Exercise real worker/confirmation branches with storage and database boundaries."""
from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from card_platform.capture_service import CaptureService
from card_platform.config import Settings
from card_platform.scanner_queue import ScannerRecognitionWorker
from card_platform.schemas import CardRecognitionResult


class Result:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Database:
    def __init__(self, row):
        self.row, self.queries = row, []

    @asynccontextmanager
    async def connection(self):
        yield self

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if not sql.lstrip().startswith(("SELECT", "UPDATE scanner_recognition_job")):
            raise AssertionError("draft must not write inventory, cost or valuation")
        return Result(self.row)


@pytest.mark.asyncio
async def test_automatic_conflict_remains_succeeded_draft_without_inventory_writes(tmp_path, monkeypatch):
    validation = {"status": "conflict", "reason_codes": ["different_cards"]}
    job = {"id": uuid4(), "owner_id": uuid4(), "status": "running", "attempt_count": 1,
           "started_at": None, "front_image_path": "front", "back_image_path": "back",
           "front_original_image_path": "front", "back_original_image_path": "back",
           "front_content_type": "image/jpeg", "back_content_type": "image/jpeg",
           "require_confirmation": False, "crop_recipe": {s: {"confirmed": True} for s in ("front", "back")}}
    for side in ("front", "back"):
        (tmp_path / side).write_bytes(side.encode())
    db = Database(job)

    class Vision:
        def __init__(self, settings):
            pass

        async def recognize_pair_contents(self, *args):
            return CardRecognitionResult(confidence=".99", player_name="LeBron James", pair_validation=validation)

    monkeypatch.setattr("card_platform.scanner_queue.CardVisionService", Vision)
    worker = ScannerRecognitionWorker(db, Settings())
    worker.queue = SimpleNamespace(image_path=lambda path: tmp_path / path)
    await worker._process(job["id"])
    writes = [(sql, params) for sql, params in db.queries if "status = 'succeeded'" in sql]
    assert writes, db.queries[-1]
    assert "require_confirmation" in writes[-1][0]
    payload = writes[-1][1][0].obj
    assert payload["pair_validation"]["status"] == "conflict"
    assert payload["pair_validation"]["image_content_hashes"]["front"] == sha256(b"front").hexdigest()


@pytest.mark.asyncio
async def test_automatic_unknown_pair_imports_blank_identity_and_preserves_pending_evidence(tmp_path, monkeypatch):
    item_id, definition_id = uuid4(), uuid4()
    job = {"id": uuid4(), "owner_id": uuid4(), "status": "running", "attempt_count": 1,
           "started_at": None, "front_image_path": "front", "back_image_path": "back",
           "front_original_image_path": "front", "back_original_image_path": "back",
           "front_content_type": "image/jpeg", "back_content_type": "image/jpeg",
           "require_confirmation": False,
           "crop_recipe": {side: {"confirmed": True} for side in ("front", "back")}}
    for side in ("front", "back"):
        (tmp_path / side).write_bytes(side.encode())
    created = {}

    class ImportDatabase(Database):
        async def execute(self, sql, params=None):
            self.queries.append((sql, params))
            if "SELECT ci.card_definition_id" in sql:
                return Result({"card_definition_id": definition_id,
                               **created["payload"].card.model_dump(),
                               "serial_number": None, "serial_total": None, "is_numbered": None})
            return Result(self.row)

    class Repository:
        async def create_item(self, connection, payload):
            created["payload"] = payload
            return item_id

    class Storage:
        def __init__(self, settings):
            pass

        async def save_preprocessed_item_image_content(self, connection, saved_id, owner_id, side, content, content_type):
            (tmp_path / f"stored-{side}").write_bytes(content)

    class Vision:
        def __init__(self, settings):
            pass

        async def recognize_pair_contents(self, *args):
            return CardRecognitionResult(confidence=".99")

    monkeypatch.setattr("card_platform.scanner_queue.CardVisionService", Vision)
    monkeypatch.setattr("card_platform.scanner_queue.PortfolioRepository", Repository)
    monkeypatch.setattr("card_platform.scanner_queue.ImageStorageService", Storage)
    db = ImportDatabase(job)
    worker = ScannerRecognitionWorker(db, Settings())
    worker.queue = SimpleNamespace(image_path=lambda path: tmp_path / path)
    await worker._process(job["id"])
    writes = [(sql, params) for sql, params in db.queries if "collection_item_id = %s" in sql]
    assert len(writes) == 1, db.queries[-1]
    sql, params = writes[0]
    assert "imported_at = completed.at" in sql and params[1] == item_id
    saved = params[0].obj
    assert all(saved[field] is None for field in ("player_name", "brand_name", "product_name", "season_start"))
    assert saved["pair_validation"]["status"] == "unknown"
    assert saved["pair_validation"]["front"]["evidence"] == []
    assert saved["pair_validation"]["back"]["evidence"] == []
    assert "pair_validation_pending" in saved["recognition_quality_issues"]
    assert "pair_review" not in saved
    assert saved["pair_validation"]["image_content_hashes"] == {
        side: sha256(side.encode()).hexdigest() for side in ("front", "back")}
    assert created["payload"].card.season_start is None
    assert (tmp_path / "stored-front").read_bytes() == b"front"
    assert (tmp_path / "stored-back").read_bytes() == b"back"


def job_files(tmp_path, validation=None):
    for side in ("front", "back"):
        (tmp_path / side).write_bytes(side.encode())
    return {"id": uuid4(), "owner_id": uuid4(), "require_confirmation": True,
            "collection_item_id": None, "status": "succeeded", "result": {"pair_validation": validation},
            "front_image_path": "front", "back_image_path": "back",
            "front_content_type": "image/jpeg", "back_content_type": "image/jpeg"}


@pytest.mark.asyncio
@pytest.mark.parametrize("validation,review", [
    ({"status": "conflict"}, {"confirmed": True}),
    ({"status": "unknown", "image_content_hashes": {"front": "a" * 64, "back": "b" * 64}}, None),
])
async def test_capture_rejects_before_inventory_or_identity_writes(tmp_path, validation, review):
    row = job_files(tmp_path, validation)
    service = CaptureService(Settings(scanner_job_dir=str(tmp_path)))
    service.queue = SimpleNamespace(image_path=lambda path: tmp_path / path)
    db = Database(row)
    with pytest.raises(HTTPException) as caught:
        await service.confirm(db, row["owner_id"], row["id"], {"pair_review": review})
    assert caught.value.status_code == 409
    assert len(db.queries) == 1


@pytest.mark.asyncio
async def test_target_retake_removes_pair_review_before_exact_body_validation(tmp_path, monkeypatch):
    row = job_files(tmp_path)
    row.update(target_item_id=uuid4(), expected_revision="revision")
    db = Database(row)
    service = CaptureService(Settings(scanner_job_dir=str(tmp_path), upload_dir=str(tmp_path)))
    service.queue = SimpleNamespace(image_path=lambda path: tmp_path / path)

    async def target(*args):
        return {"id": row["target_item_id"]}

    class Storage:
        def __init__(self, settings):
            pass

        async def save_preprocessed_item_image_content(self, db, item_id, owner_id, side, content, content_type):
            assert item_id == row["target_item_id"]
            return SimpleNamespace(image_url="/uploads/" + side)

    service.target = target
    monkeypatch.setattr("card_platform.capture_service.ImageStorageService", Storage)
    review = {"confirmed": True, "image_content_hashes": {s: sha256(s.encode()).hexdigest() for s in ("front", "back")}}
    body = {"pair_review": review}
    result = await service.confirm(db, row["owner_id"], row["id"], body)
    assert result.id == row["target_item_id"]
    assert body == {"pair_review": review}, "caller body must not be mutated"
    row["collection_item_id"] = result.id
    assert (await service.confirm(db, row["owner_id"], row["id"], {})).id == result.id
