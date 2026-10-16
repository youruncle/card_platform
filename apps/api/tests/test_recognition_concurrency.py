import asyncio
import sys
from types import SimpleNamespace

import pytest

from card_platform.card_orientation import AppleVisionOrientationClassifier
from card_platform.config import Settings
from card_platform.flatbed_service import FlatbedService
from card_platform.image_service import CardVisionService
from card_platform.schemas import CardRecognitionResult


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["orientation_timeout", "orientation_cancel", "crop_ocr_timeout"])
async def test_stalled_local_vision_worker_is_reaped(tmp_path, monkeypatch, kind):
    import card_platform.card_orientation as orientation

    script = tmp_path / "worker.swift"
    script.write_text("placeholder")
    real_spawn = asyncio.create_subprocess_exec
    real_wait = asyncio.wait_for
    processes = []

    async def spawn(*args, **kwargs):
        process = await real_spawn(
            sys.executable, "-c", "import time; time.sleep(60)", **kwargs
        )
        processes.append(process)
        return process

    async def short_wait(awaitable, timeout):
        return await real_wait(awaitable, min(timeout, .05))

    monkeypatch.setattr(orientation.sys, "platform", "darwin")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "wait_for", short_wait)
    if kind == "crop_ocr_timeout":
        action = FlatbedService(str(tmp_path), str(script), str(script)).ocr_signals(script)
    else:
        action = AppleVisionOrientationClassifier(str(script)).classify(script)
    task = asyncio.create_task(action)
    try:
        if kind == "orientation_cancel":
            while not processes:
                await asyncio.sleep(.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        assert processes[0].returncode is not None, "timed-out Vision process is still alive"
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
            await process.communicate()


def identity(**updates):
    return CardRecognitionResult(
        player_name="Kobe Bryant", brand_name="Topps", product_name="Chrome",
        season_start=1996, season_end=1997, card_number="138", confidence="0.97",
    ).model_copy(update=updates)


@pytest.mark.asyncio
async def test_serial_recovery_overlaps_model_call_and_preserves_evidence(monkeypatch):
    from card_platform import local_ocr, serial_ocr
    serial_started = asyncio.Event()
    model_started = asyncio.Event()

    async def ocr(*args):
        return []

    async def serial(*args):
        serial_started.set()
        await model_started.wait()
        return {"status": "succeeded", "observations": []}

    class Provider:
        async def recognize_images(self, images, prompt):
            model_started.set()
            await asyncio.wait_for(serial_started.wait(), .1)
            return identity()

    monkeypatch.setattr(local_ocr, "recognize_images", ocr)
    monkeypatch.setattr(serial_ocr, "recognize_serial_images", serial)
    service = CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    result = await service.recognize_pair_contents(b"front", "image/jpeg", b"back", "image/jpeg")
    assert result.card_number == "138"
    assert result.recognition_route[-1]["tier"] == "local_serial_ocr"


@pytest.mark.asyncio
async def test_ocr_card_number_prevents_unnecessary_model_upgrade(monkeypatch):
    from card_platform import local_ocr

    async def ocr(*args):
        return ["NO. 138"]

    class Provider:
        async def recognize_images(self, *args):
            return identity(card_number=None)

        async def recognize_images_with_usage(self, *args, **kwargs):
            return identity(card_number=None), {}

    class UnnecessaryUpgrade(Provider):
        async def recognize_images_with_usage(self, *args, **kwargs):
            raise AssertionError("OCR already read the missing card number")

    monkeypatch.setattr(local_ocr, "recognize_images", ocr)
    service = CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    service.tiered_providers = {"flash": Provider(), "plus": UnnecessaryUpgrade(), "max": None}
    # A valid local serial avoids a second OCR pass, which is unrelated to this test.
    monkeypatch.setattr(local_ocr, "extract_anchors", lambda _: {
        "card_number": "138", "card_number_evidence": "NO. 138",
        "serial_number": 1, "serial_total": 50, "serial_evidence": "1/50",
    })
    result = await service.recognize_pair_contents(b"front", "image/jpeg", b"back", "image/jpeg")
    assert result.card_number == "138"
    assert [row["tier"] for row in result.recognition_route].count("plus") == 0
    assert result.field_evidence["card_number"] == ["Apple Vision OCR：NO. 138"]


@pytest.mark.asyncio
async def test_flatbed_crops_process_concurrently_before_locking_session(tmp_path):
    import cv2
    import numpy as np
    from uuid import uuid4
    from card_platform.flatbed_detection import Detection

    owner, session = uuid4(), uuid4()
    queries = []

    class Result:
        async def fetchone(self):
            return {"id": session, "owner_id": owner, "dpi": 300,
                    "revision": 1, "submitted_at": None, "status": "front_scanned"}

        async def fetchall(self):
            return []

    async def execute(sql, params=None):
        queries.append((sql, params))
        return Result()

    service = FlatbedService(str(tmp_path), "missing-bridge.swift")
    started = []
    both_started = asyncio.Event()

    async def classify(path):
        assert not any("FOR UPDATE" in sql for sql, _ in queries), "slow Vision runs under row lock"
        started.append(path)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), .1)
        return None

    service.orientation_classifier.classify = classify
    image = np.full((600, 600, 3), 180, np.uint8)
    _, content = cv2.imencode(".png", image)
    regions = [Detection(.05, .1, .4, .7, 0, .9), Detection(.55, .1, .4, .7, 0, .9)]
    result = await service.store_sheet(
        SimpleNamespace(execute=execute), owner, session, "back", content.tobytes(),
        "image/png", detections_override=regions,
    )
    assert len(result["regions"]) == 2
    assert [r["ordinal"] for r in result["regions"]] == [0, 1]
    assert len(list(tmp_path.rglob("region-*.jpg"))) == 2
    inserts = [params for sql, params in queries if "INSERT INTO flatbed_scan_region" in sql]
    assert [p[3] for p in inserts] == [0, 1]


@pytest.mark.asyncio
async def test_failed_model_cancels_serial_worker_and_preserves_error_type(monkeypatch):
    from card_platform import local_ocr, serial_ocr
    serial_started, serial_stopped = asyncio.Event(), asyncio.Event()

    async def ocr(*args):
        return []

    async def serial(*args):
        serial_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            serial_stopped.set()

    class Provider:
        async def recognize_images(self, *args):
            await serial_started.wait()
            raise RuntimeError("vision unavailable")

    monkeypatch.setattr(local_ocr, "recognize_images", ocr)
    monkeypatch.setattr(serial_ocr, "recognize_serial_images", serial)
    service = CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    with pytest.raises(RuntimeError, match="vision unavailable"):
        await service.recognize_pair_contents(b"front", "image/jpeg", b"back", "image/jpeg")
    assert serial_stopped.is_set()


@pytest.mark.asyncio
async def test_local_vision_timeout_also_bounds_wait_for_capacity(monkeypatch):
    from card_platform.local_vision_process import run_local_vision
    real_spawn = asyncio.create_subprocess_exec
    started = 0
    full = asyncio.Event()

    async def spawn(*args, **kwargs):
        nonlocal started
        process = await real_spawn(*args, **kwargs)
        started += 1
        if started == 2:
            full.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    blockers = [asyncio.create_task(run_local_vision(
        sys.executable, "-c", "import time; time.sleep(60)", timeout_seconds=1
    )) for _ in range(2)]
    try:
        await full.wait()
        waiting = asyncio.create_task(run_local_vision(
            sys.executable, "-c", "print('done')", timeout_seconds=.05
        ))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(waiting, .2)
        assert not waiting.cancelled(), "capacity wait escaped its own time budget"
    finally:
        for task in blockers:
            task.cancel()
        await asyncio.gather(*blockers, return_exceptions=True)


@pytest.mark.asyncio
async def test_exited_parent_with_live_child_does_not_hold_vision_pipe_open(monkeypatch):
    import os
    import signal
    from card_platform.local_vision_process import run_local_vision
    if os.name != "posix":
        pytest.skip("process-group lifecycle is a POSIX contract")
    real_spawn = asyncio.create_subprocess_exec
    processes = []

    async def spawn(*args, **kwargs):
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    worker = asyncio.create_task(run_local_vision(
        sys.executable, "-c",
        "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])",
        timeout_seconds=.05,
    ))
    try:
        await asyncio.sleep(.15)
        assert worker.done(), "exited parent left a child holding the Vision pipes open"
        with pytest.raises(TimeoutError):
            await worker
    finally:
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await asyncio.gather(worker, return_exceptions=True)
