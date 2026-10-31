import asyncio
from io import BytesIO
import json
from uuid import UUID, uuid4

import httpx
from PIL import Image
import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from card_platform.config import Settings
from card_platform.mcp.media import AttachmentDownloader, CardImportFilesRequest
from card_platform.mcp.operations import OperationService
from card_platform.mcp.schemas import McpFailure
from card_platform.schemas import CardRecognitionResult, SheetCardRecognitionResult
from test_mcp_capture_files import AttachmentStream

pytestmark = pytest.mark.asyncio


def photo(color):
    output = BytesIO()
    Image.new("RGB", (80, 120), color).save(output, "PNG")
    return output.getvalue()


def observed():
    return CardRecognitionResult(player_name="Stephen Curry", brand_name="Panini", product_name="Prizm",
                                 season_start=2023, season_end=2024, confidence="0.7",
                                 caveats=["Synthetic external-model result for integration test"])


def correction():
    return {"card": {"player_name": "Stephen Curry", "brand_name": "Panini", "product_name": "Prizm",
                     "season_start": 2023, "season_end": 2024}}


@pytest_asyncio.fixture
async def captures_case(mcp_database, mcp_principal, tmp_path):
    from card_platform.mcp.captures import CaptureTools

    contents = {"/0": photo("red"), "/1": photo("blue"), "/2": photo("green")}
    def network(request):
        return httpx.Response(200, stream=AttachmentStream(contents[request.url.path]))
    settings = Settings(scanner_job_dir=str(tmp_path / "scanner"), upload_dir=str(tmp_path / "uploads"))
    operations = OperationService(mcp_database, "https://cardsp.cn")
    captures = CaptureTools(mcp_database, settings, operations,
                           downloader=AttachmentDownloader(transport=httpx.MockTransport(network)))
    return captures, mcp_principal, contents


def upload(count=1, sides=None, key=None):
    return CardImportFilesRequest(files=[{"download_url": f"https://8.8.8.8/{i}?signed=private", "file_id": f"file-{i}"}
                                        for i in range(count)],
        sides=sides or [{"side": "front", "group_id": f"card-{i}"} for i in range(count)], idempotency_key=key or uuid4())


async def process(captures, request_id, vision=None):
    from card_platform.mcp.captures import CaptureIntakeWorker
    worker = CaptureIntakeWorker(captures, vision=vision)
    assert await worker.process_one(UUID(request_id)) is True


async def successful_job(captures, principal, job_id, result=None):
    async with captures.database.connection() as conn:
        await conn.execute("""UPDATE scanner_recognition_job SET status='succeeded',result=%s,
                           finished_at=now(),updated_at=now() WHERE id=%s AND owner_id=%s""",
                           (Jsonb(result or observed().model_dump(mode="json")), UUID(job_id), principal.owner_id))


async def test_single_paired_and_three_files_persist_originals_and_explicit_groups(captures_case):
    from card_platform.mcp.captures import GetJobStatusRequest
    captures, principal, contents = captures_case
    for request, expected in (
        (upload(), [("front",)]),
        (upload(2, [{"side": "front", "group_id": "pair"}, {"side": "back", "group_id": "pair"}]), [("front", "back")]),
        (upload(3), [("front",), ("front",), ("front",)]),
    ):
        prepared = await captures.prepare_card_import(principal, request)
        assert prepared["status"] == "queued"
        await process(captures, prepared["job_id"])
        status = await captures.get_job_status(principal, GetJobStatusRequest(job_id=prepared["job_id"]))
        assert len(status["jobs"]) == len(expected)
        assert [tuple(job["available_sides"]) for job in status["jobs"]] == expected
        assert "https://8.8.8.8" not in json.dumps(status)
        async with captures.database.connection() as conn:
            row = await (await conn.execute("SELECT manifest FROM chatgpt_capture_intake WHERE id=%s",
                                            (UUID(prepared["job_id"]),))).fetchone()
            assert "download_url" not in json.dumps(row)
            assert "signed=" not in json.dumps(row)
            for entry in row["manifest"]:
                assert captures.source_path(entry["source_ref"]).read_bytes() == contents[f"/{entry['index']}"]


async def test_same_request_three_retries_return_original_but_changed_photo_conflicts(captures_case):
    captures, principal, contents = captures_case
    request = upload()
    result = await captures.prepare_card_import(principal, request)
    for _ in range(3):
        assert (await captures.prepare_card_import(principal, request))["job_id"] == result["job_id"]
    contents["/0"] = photo("yellow")
    with pytest.raises(McpFailure) as error:
        await captures.prepare_card_import(principal, request)
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    independent = await captures.prepare_card_import(principal, upload())
    assert independent["job_id"] != result["job_id"]


async def test_auto_multi_card_uses_sheet_recognition_and_keeps_separate_crops(captures_case):
    from card_platform.mcp.captures import GetJobStatusRequest
    captures, principal, _ = captures_case
    class ExternalSheetVision:
        async def recognize_sheet(self, image):
            return SheetCardRecognitionResult(detected_count=2, cards=[
                {"result": observed(), "card_image_bbox": [0, 0, 1000, 500], "purchase_price": "100", "currency": "CNY"},
                {"result": observed(), "card_image_bbox": [0, 500, 1000, 1000]},
            ])
    result = await captures.prepare_card_import(principal, upload(sides=[{"side": "auto", "group_id": "sheet"}]))
    await process(captures, result["job_id"], ExternalSheetVision())
    status = await captures.get_job_status(principal, GetJobStatusRequest(job_id=result["job_id"]))
    assert len(status["jobs"]) == 2
    assert all(job["status"] == "review_required" for job in status["jobs"])
    assert status["jobs"][0]["purchase_draft"]["purchase_price"] == "100"
    assert status["jobs"][1]["purchase_draft"]["purchase_price"] is None
    async with captures.database.connection() as conn:
        assert (await (await conn.execute("SELECT count(*) AS n FROM collection_item WHERE owner_id=%s",
                                          (principal.owner_id,))).fetchone())["n"] == 0


async def test_confirm_import_is_approved_atomic_and_idempotent(captures_case):
    from card_platform.mcp.captures import GetJobStatusRequest, PrepareCaptureConfirmationRequest
    captures, principal, _ = captures_case
    result = await captures.prepare_card_import(principal, upload())
    await process(captures, result["job_id"])
    status = await captures.get_job_status(principal, GetJobStatusRequest(job_id=result["job_id"]))
    job_id = status["jobs"][0]["job_id"]
    await successful_job(captures, principal, job_id)
    request = PrepareCaptureConfirmationRequest(job_id=job_id, corrected_fields=correction(), idempotency_key=uuid4())
    operation = await captures.prepare_capture_confirmation(principal, request)
    with pytest.raises(McpFailure) as error:
        await captures.operations.execute(principal, operation["operation_id"])
    assert error.value.code == "CONFIRMATION_REQUIRED"
    await captures.operations.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    results = await asyncio.gather(*(captures.operations.execute(principal, operation["operation_id"]) for _ in range(3)))
    assert len({r["readback"]["item_id"] for r in results}) == 1
    assert sum(not r["already_executed"] for r in results) == 1
    assert results[0]["readback"]["item"]["player_name"] == "Stephen Curry"
    assert (await captures.prepare_capture_confirmation(principal, request))["operation_id"] == operation["operation_id"]


async def test_changed_capture_revision_or_missing_photo_prevents_confirm(captures_case):
    from card_platform.mcp.captures import GetJobStatusRequest, PrepareCaptureConfirmationRequest
    captures, principal, _ = captures_case
    result = await captures.prepare_card_import(principal, upload())
    await process(captures, result["job_id"])
    status = await captures.get_job_status(principal, GetJobStatusRequest(job_id=result["job_id"]))
    job_id = status["jobs"][0]["job_id"]
    await successful_job(captures, principal, job_id)
    operation = await captures.prepare_capture_confirmation(principal, PrepareCaptureConfirmationRequest(
        job_id=job_id, corrected_fields=correction(), idempotency_key=uuid4()))
    async with captures.database.connection() as conn:
        await conn.execute("UPDATE scanner_recognition_job SET revision=revision+1 WHERE id=%s", (UUID(job_id),))
    with pytest.raises(McpFailure) as error:
        await captures.operations.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    assert error.value.code == "REVISION_CONFLICT"
    async with captures.database.connection() as conn:
        row = await (await conn.execute("SELECT front_image_path FROM scanner_recognition_job WHERE id=%s", (UUID(job_id),))).fetchone()
    captures.capture.queue.image_path(row["front_image_path"]).unlink()
    with pytest.raises(McpFailure):
        await captures.prepare_capture_confirmation(principal, PrepareCaptureConfirmationRequest(
            job_id=job_id, corrected_fields=correction(), idempotency_key=uuid4()))


async def test_explicit_front_back_conflict_cannot_be_imported(captures_case):
    from card_platform.mcp.captures import GetJobStatusRequest, PrepareCaptureConfirmationRequest
    captures, principal, _ = captures_case
    result = await captures.prepare_card_import(principal, upload(2, [
        {"side": "front", "group_id": "pair"}, {"side": "back", "group_id": "pair"}]))
    await process(captures, result["job_id"])
    status = await captures.get_job_status(principal, GetJobStatusRequest(job_id=result["job_id"]))
    job_id = status["jobs"][0]["job_id"]
    conflicting = observed().model_dump(mode="json")
    conflicting["pair_validation"] = {"status": "consistent", "front": {"role": "front", "player_names": ["Stephen Curry"]},
                                     "back": {"role": "back", "player_names": ["LeBron James"]}}
    await successful_job(captures, principal, job_id, conflicting)
    with pytest.raises(McpFailure):
        await captures.prepare_capture_confirmation(principal, PrepareCaptureConfirmationRequest(
            job_id=job_id, corrected_fields=correction(), idempotency_key=uuid4()))


async def test_unknown_pair_requires_platform_acknowledgement_of_current_images(captures_case):
    from card_platform.mcp.captures import GetJobStatusRequest, PrepareCaptureConfirmationRequest
    captures, principal, _ = captures_case
    result = await captures.prepare_card_import(principal, upload(2, [
        {"side": "front", "group_id": "pair"}, {"side": "back", "group_id": "pair"}]))
    await process(captures, result["job_id"])
    status = await captures.get_job_status(principal, GetJobStatusRequest(job_id=result["job_id"]))
    job_id = status["jobs"][0]["job_id"]
    await successful_job(captures, principal, job_id)
    operation = await captures.prepare_capture_confirmation(principal, PrepareCaptureConfirmationRequest(
        job_id=job_id, corrected_fields=correction(), idempotency_key=uuid4()))
    assert operation["payload"]["required_acknowledgements"] == ["pair_images_checked"]
    with pytest.raises(McpFailure) as error:
        await captures.operations.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"])
    assert error.value.code == "CONFIRMATION_REQUIRED"
    await captures.operations.approve(principal.owner_id, uuid4(), operation["operation_id"], operation["payload_hash"],
                                      acknowledgements={"pair_images_checked"})
    assert (await captures.operations.execute(principal, operation["operation_id"]))["readback"]["item_id"]


async def test_other_owner_cannot_read_intake_or_prepare_its_draft(captures_case):
    from card_platform.mcp.captures import GetJobStatusRequest
    captures, principal, _ = captures_case
    result = await captures.prepare_card_import(principal, upload())
    with pytest.raises(McpFailure):
        await captures.get_job_status(principal.model_copy(update={"owner_id": uuid4()}), GetJobStatusRequest(job_id=result["job_id"]))


async def test_revoked_connection_stops_worker_before_reading_or_sending_photos(captures_case):
    captures, principal, _ = captures_case
    class UnusedVision:
        async def recognize_sheet(self, image):
            pytest.fail("revoked intake reached external vision")
    result = await captures.prepare_card_import(principal, upload(sides=[{"side": "auto", "group_id": "one"}]))
    async with captures.database.connection() as conn:
        await conn.execute("UPDATE chatgpt_connection SET revoked_at=now() WHERE id=%s", (principal.connection_id,))
    await process(captures, result["job_id"], UnusedVision())
    async with captures.database.connection() as conn:
        row = await (await conn.execute("SELECT status,error_code FROM chatgpt_capture_intake WHERE id=%s", (UUID(result["job_id"]),))).fetchone()
    assert row == {"status": "failed", "error_code": "AUTH_REQUIRED"}


async def test_expired_attachment_retry_returns_previous_durable_intake(captures_case):
    captures, principal, _ = captures_case
    request = upload()
    first = await captures.prepare_card_import(principal, request)
    def expired(request):
        return httpx.Response(403)
    captures.downloader = AttachmentDownloader(transport=httpx.MockTransport(expired))
    assert (await captures.prepare_card_import(principal, request))["job_id"] == first["job_id"]


async def test_worker_restart_reuses_completed_analysis_and_does_not_repeat_successful_crops(captures_case, monkeypatch):
    from card_platform.mcp.captures import CaptureIntakeWorker, GetJobStatusRequest
    captures, principal, _ = captures_case
    class ExternalSheetVision:
        async def recognize_sheet(self, image):
            return SheetCardRecognitionResult(detected_count=2, cards=[
                {"result": observed(), "card_image_bbox": [0, 0, 1000, 500]},
                {"result": observed(), "card_image_bbox": [0, 500, 1000, 1000]},
            ])
    result = await captures.prepare_card_import(principal, upload(sides=[{"side": "auto", "group_id": "sheet"}]))
    worker = CaptureIntakeWorker(captures, vision=ExternalSheetVision())
    original = worker._enqueue
    async def cancelled_after_first(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(worker, "_enqueue", cancelled_after_first)
    with pytest.raises(asyncio.CancelledError):
        await worker.process_one(UUID(result["job_id"]))
    class NoSecondRecognition:
        async def recognize_sheet(self, image):
            pytest.fail("completed sheet recognition was invoked again after restart")
    resumed = CaptureIntakeWorker(captures, vision=NoSecondRecognition())
    assert await resumed.process_one(UUID(result["job_id"]))
    status = await captures.get_job_status(principal, GetJobStatusRequest(job_id=result["job_id"]))
    assert len(status["jobs"]) == 2
    assert len({job["job_id"] for job in status["jobs"]}) == 2
    assert status["status"] == "review_required"


async def test_exhausted_abandoned_intake_is_failed_instead_of_running_forever(captures_case):
    from card_platform.mcp.captures import CaptureIntakeWorker, GetJobStatusRequest
    captures, principal, _ = captures_case
    result = await captures.prepare_card_import(principal, upload())
    async with captures.database.connection() as conn:
        await conn.execute("""UPDATE chatgpt_capture_intake SET status='running',attempt_count=3,
                           started_at=now()-interval '11 minutes' WHERE id=%s""", (UUID(result["job_id"]),))
    assert await CaptureIntakeWorker(captures).process_one(UUID(result["job_id"])) is False
    status = await captures.get_job_status(principal, GetJobStatusRequest(job_id=result["job_id"]))
    assert status["status"] == "failed"
    assert status["error_code"] == "RETRY_EXHAUSTED"
