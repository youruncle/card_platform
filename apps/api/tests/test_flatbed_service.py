import asyncio
from pathlib import Path

import numpy as np
import pytest

from card_platform.flatbed_service import (
    CROP_LONG_AXIS_PADDING_RATIO,
    CROP_SHORT_AXIS_LEADING_INSET_RATIO,
    CROP_SHORT_AXIS_PADDING_RATIO,
    FlatbedBridgeError,
    FlatbedService,
    padded_source_quad,
)


def test_crop_quad_pads_short_axis_without_crossing_long_axis_neighbors() -> None:
    points = np.array(
        [[100, 100], [500, 100], [500, 300], [100, 300]], dtype=np.float32
    )

    expanded = padded_source_quad(points, 1000, 800)

    assert CROP_SHORT_AXIS_PADDING_RATIO == 0.06
    assert CROP_SHORT_AXIS_LEADING_INSET_RATIO == 0.06
    assert CROP_LONG_AXIS_PADDING_RATIO == 0
    assert expanded.tolist() == [
        [100.0, 112.0],
        [500.0, 112.0],
        [500.0, 312.0],
        [100.0, 312.0],
    ]


def test_crop_quad_clips_padding_at_scanner_bed_edges() -> None:
    points = np.array(
        [[0, 0], [500, 0], [500, 300], [0, 300]], dtype=np.float32
    )

    expanded = padded_source_quad(points, 1000, 800)

    assert expanded[:, 0].min() == 0
    assert expanded[:, 1].min() == 0
    assert expanded[:, 0].max() == 500
    assert expanded[:, 1].max() == 318


def test_crop_quad_pads_horizontal_axis_for_portrait_card() -> None:
    points = np.array(
        [[100, 100], [300, 100], [300, 500], [100, 500]], dtype=np.float32
    )

    expanded = padded_source_quad(points, 1000, 800)

    assert expanded.tolist() == [
        [112.0, 100.0],
        [312.0, 100.0],
        [312.0, 500.0],
        [112.0, 500.0],
    ]


def test_flatbed_path_cannot_escape_private_root(tmp_path: Path) -> None:
    service = FlatbedService(str(tmp_path / "root"), "bridge.swift")
    with pytest.raises(ValueError, match="invalid flatbed image path"):
        service.resolve_path("../../private.jpg")


@pytest.mark.asyncio
async def test_bridge_refuses_to_overwrite_output(tmp_path: Path) -> None:
    output = tmp_path / "existing.png"
    output.write_bytes(b"keep")
    service = FlatbedService(str(tmp_path), str(tmp_path / "bridge.swift"))
    with pytest.raises(FlatbedBridgeError, match="拒绝覆盖"):
        await service.bridge("preview", output=output)
    assert output.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_cancelled_service_request_interrupts_and_releases_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "released"
    bridge = tmp_path / "fake-bridge"
    bridge.write_text(
        "#!/usr/bin/env python3\n"
        "import os, signal, sys, time\n"
        "def release(signum, frame):\n"
        "    open(os.environ['FLATBED_RELEASE_MARKER'], 'w').close()\n"
        "    sys.exit(130)\n"
        "signal.signal(signal.SIGINT, release)\n"
        "while True: time.sleep(1)\n"
    )
    bridge.chmod(0o700)
    monkeypatch.setenv("FLATBED_RELEASE_MARKER", str(marker))
    service = FlatbedService(str(tmp_path / "root"), str(bridge))
    task = asyncio.create_task(service.bridge("capabilities", timeout=30))
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert marker.is_file()
