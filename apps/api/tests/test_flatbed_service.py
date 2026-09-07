import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import numpy as np
import pytest

from card_platform.flatbed_detection import Detection
from card_platform.flatbed_service import (
    CROP_LONG_AXIS_PADDING_RATIO,
    CROP_OUTPUT_BORDER_RATIO,
    CROP_SHORT_AXIS_PADDING_RATIO,
    FlatbedBridgeError,
    FlatbedService,
    add_output_safety_border,
    back_refinement_prompts,
    padded_source_quad,
    stored_region_as_prior,
)


@pytest.mark.asyncio
async def test_create_session_reuses_unfinished_session() -> None:
    owner_id = uuid4()
    existing = {"id": uuid4(), "owner_id": owner_id, "dpi": 300, "submitted_at": None}
    lock_result = AsyncMock()
    existing_result = AsyncMock()
    existing_result.fetchone.return_value = existing
    connection = AsyncMock()
    connection.execute.side_effect = [lock_result, existing_result]

    result = await FlatbedService("data/flatbed-scans", "bridge.swift").create_session(
        connection, owner_id, 300
    )

    assert result == existing
    assert connection.execute.await_count == 2


def test_crop_quad_pads_short_axis_without_crossing_long_axis_neighbors() -> None:
    points = np.array([[100, 100], [500, 100], [500, 300], [100, 300]], dtype=np.float32)

    expanded = padded_source_quad(points, 1000, 800)

    assert CROP_SHORT_AXIS_PADDING_RATIO == 0.03
    assert CROP_LONG_AXIS_PADDING_RATIO == 0
    assert expanded.tolist() == [
        [100.0, 94.0],
        [500.0, 94.0],
        [500.0, 306.0],
        [100.0, 306.0],
    ]


def test_crop_quad_clips_padding_at_scanner_bed_edges() -> None:
    points = np.array([[0, 0], [500, 0], [500, 300], [0, 300]], dtype=np.float32)

    expanded = padded_source_quad(points, 1000, 800)

    assert expanded[:, 0].min() == 0
    assert expanded[:, 1].min() == 0
    assert expanded[:, 0].max() == 500
    assert expanded[:, 1].max() == 309


def test_crop_quad_pads_horizontal_axis_for_portrait_card() -> None:
    points = np.array([[100, 100], [300, 100], [300, 500], [100, 500]], dtype=np.float32)

    expanded = padded_source_quad(points, 1000, 800)

    assert expanded.tolist() == [
        [94.0, 100.0],
        [306.0, 100.0],
        [306.0, 500.0],
        [94.0, 500.0],
    ]


def test_output_safety_border_is_narrow_and_uniform() -> None:
    image = np.full((400, 300, 3), 20, dtype=np.uint8)

    bordered = add_output_safety_border(image)

    expected = round(300 * CROP_OUTPUT_BORDER_RATIO)
    assert bordered.shape == (400 + expected * 2, 300 + expected * 2, 3)
    assert np.all(bordered[:expected] == 245)
    assert np.all(bordered[-expected:] == 245)
    assert np.all(bordered[:, :expected] == 245)
    assert np.all(bordered[:, -expected:] == 245)


def test_stored_front_region_keeps_grading_label_evidence_for_back_scan() -> None:
    detection = stored_region_as_prior(
        {
            "bounds": {
                "x": 0.0,
                "y": 0.25,
                "width": 0.62,
                "height": 0.26,
                "angle": -89.5,
                "points": [
                    {"x": 0.0, "y": 0.25},
                    {"x": 0.62, "y": 0.25},
                    {"x": 0.62, "y": 0.51},
                    {"x": 0.0, "y": 0.51},
                ],
            },
            "signals": {
                "detection_evidence": [
                    "mixed_format_instance",
                    "grading_label_association",
                ]
            },
        }
    )

    assert "grading_label_association" in detection.evidence
    assert "front_to_back_prior" in detection.evidence
    assert detection.points is not None


def test_back_refinement_uses_compatible_prior_as_prompt_only() -> None:
    current = [
        Detection(
            0.5,
            0.5,
            0.5,
            0.25,
            0,
            0.46,
            "card_outline_unresolved",
        )
    ]
    prior = [Detection(0.54, 0.483, 0.46, 0.269, 0, 1.0)]

    prompts = back_refinement_prompts(current, prior)

    assert prompts is not None
    assert prompts[0].x == prior[0].x
    assert prompts[0].y == prior[0].y
    assert prompts[0].failure == current[0].failure
    assert current[0].x == 0.5


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
    ready = tmp_path / "ready"
    bridge = tmp_path / "fake-bridge"
    bridge.write_text(
        "#!/usr/bin/env python3\n"
        "import os, signal, sys, time\n"
        "def release(signum, frame):\n"
        "    open(os.environ['FLATBED_RELEASE_MARKER'], 'w').close()\n"
        "    sys.exit(130)\n"
        "signal.signal(signal.SIGINT, release)\n"
        "open(os.environ['FLATBED_READY_MARKER'], 'w').close()\n"
        "while True: time.sleep(1)\n"
    )
    bridge.chmod(0o700)
    monkeypatch.setenv("FLATBED_RELEASE_MARKER", str(marker))
    monkeypatch.setenv("FLATBED_READY_MARKER", str(ready))
    service = FlatbedService(str(tmp_path / "root"), str(bridge))
    task = asyncio.create_task(service.bridge("capabilities", timeout=30))
    for _ in range(40):
        if ready.is_file():
            break
        await asyncio.sleep(0.05)
    assert ready.is_file()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert marker.is_file()
