"""Bound local Vision work and reap the entire Swift process on interruption."""

import asyncio
import os
import signal
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary


_limits = WeakKeyDictionary()
_COOLDOWN_SECONDS = 60


class LocalVisionCircuitOpen(TimeoutError):
    """The shared engine is cooling down or another call is probing recovery."""


@dataclass
class _VisionState:
    limit: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    open_until: float = 0
    probing: bool = False
    generation: int = 0

    def check(self, now: float) -> None:
        if self.probing:
            raise LocalVisionCircuitOpen("local Vision recovery probe already running")
        if now < self.open_until:
            raise LocalVisionCircuitOpen("local Vision cooldown after engine timeout")


def local_vision_health() -> dict:
    loop = asyncio.get_running_loop()
    state = _limits.get(loop)
    if state is None:
        return {"state": "ready", "retry_after_seconds": 0}
    remaining = max(0, state.open_until - loop.time())
    return {
        "state": "probing" if state.probing else "cooldown" if remaining else "ready",
        "retry_after_seconds": round(remaining, 2),
    }


async def run_local_vision(*arguments: str, timeout_seconds: float = 20) -> tuple[bytes, bytes, int]:
    loop = asyncio.get_running_loop()
    state = _limits.setdefault(loop, _VisionState())
    state.check(loop.time())
    process = None
    probe = False
    generation = state.generation
    try:
        # Include capacity waiting and process startup in the caller's budget.
        async with asyncio.timeout(timeout_seconds):
            async with state.limit:
                # The engine may have failed while this call waited for capacity.
                state.check(loop.time())
                if state.open_until:
                    state.probing = probe = True
                generation = state.generation
                process = await asyncio.create_subprocess_exec(
                    *arguments,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=os.name == "posix",
                )
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
                if probe and generation == state.generation:
                    if process.returncode == 0:
                        state.open_until = 0
                    else:
                        state.open_until = loop.time() + _COOLDOWN_SECONDS
                return stdout, stderr, process.returncode
    except (TimeoutError, asyncio.CancelledError) as error:
        # Capacity waits and user cancellation say nothing about engine health.
        if isinstance(error, TimeoutError) and process is not None:
            state.generation += 1
            state.open_until = loop.time() + _COOLDOWN_SECONDS
        if process is not None:
            try:
                # A completed parent can still have children holding its pipes.
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.returncode is None:
                    process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
        raise
    finally:
        if probe:
            state.probing = False
