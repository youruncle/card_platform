import asyncio
import sys

import pytest

from card_platform import local_vision_process as module


@pytest.mark.asyncio
async def test_engine_timeout_stops_other_scripts_from_repeating_the_wait():
    with pytest.raises(TimeoutError):
        await module.run_local_vision(
            sys.executable, "-c", "import time; time.sleep(60)", timeout_seconds=.03
        )
    with pytest.raises(TimeoutError, match="cooldown"):
        await module.run_local_vision(sys.executable, "-c", "print('should not start')")


@pytest.mark.asyncio
async def test_expired_circuit_allows_only_one_probe_then_recovers(monkeypatch):
    monkeypatch.setattr(module, "_COOLDOWN_SECONDS", .04, raising=False)
    with pytest.raises(TimeoutError):
        await module.run_local_vision(
            sys.executable, "-c", "import time; time.sleep(60)", timeout_seconds=.02
        )
    await asyncio.sleep(.06)
    probe = asyncio.create_task(module.run_local_vision(
        sys.executable, "-c", "import time; time.sleep(.1); print('healthy')"
    ))
    await asyncio.sleep(.02)
    with pytest.raises(TimeoutError, match="probe"):
        await module.run_local_vision(sys.executable, "-c", "print('duplicate probe')")
    stdout, _, code = await probe
    assert code == 0 and stdout.strip() == b"healthy"
    stdout, _, code = await module.run_local_vision(sys.executable, "-c", "print('recovered')")
    assert code == 0 and stdout.strip() == b"recovered"


@pytest.mark.asyncio
async def test_user_cancellation_does_not_disable_the_local_engine():
    running = asyncio.create_task(module.run_local_vision(
        sys.executable, "-c", "import time; time.sleep(60)"
    ))
    await asyncio.sleep(.04)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    stdout, _, code = await module.run_local_vision(sys.executable, "-c", "print('healthy')")
    assert code == 0 and stdout.strip() == b"healthy"


@pytest.mark.asyncio
async def test_waiting_for_capacity_is_not_an_engine_failure():
    tasks = [asyncio.create_task(module.run_local_vision(
        sys.executable, "-c", "import time; time.sleep(.15)"
    )) for _ in range(2)]
    await asyncio.sleep(.04)
    try:
        with pytest.raises(TimeoutError):
            await module.run_local_vision(sys.executable, "-c", "print('queued')", timeout_seconds=.03)
        await asyncio.gather(*tasks)
        stdout, _, code = await module.run_local_vision(sys.executable, "-c", "print('healthy')")
        assert code == 0 and stdout.strip() == b"healthy"
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)
