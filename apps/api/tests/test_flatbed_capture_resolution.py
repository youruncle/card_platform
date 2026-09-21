from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform import flatbed_api
from card_platform.flatbed_capture_resolution import resolve_source_resolution
from card_platform.flatbed_detection import Detection, LayoutHypothesis
from card_platform.flatbed_service import FlatbedService


def native_metadata(width=1000, height=1400, dpi=200):
    return {
        "capture_kind": "preview",
        "pixel_width": width,
        "pixel_height": height,
        "actual_dpi": dpi,
        "source": "image_capture_core_overview_resolution",
    }


def test_native_overview_resolution_survives_derived_revision():
    result = resolve_source_resolution(1700, 2338, 300, native_metadata(1700, 2338))
    assert result["effective_dpi"] == 200
    assert result["requested_dpi"] == 300
    assert result["metadata_status"] == "verified_native_dimensions"
    assert resolve_source_resolution(1700, 2338, 300, result) == result


@pytest.mark.parametrize(
    "metadata, reason",
    [
        (None, "unavailable"),
        ({"effective_dpi": 200}, "unavailable"),
        (native_metadata(width=999), "pixel_dimensions_mismatch"),
        (native_metadata(dpi=True), "invalid_resolution"),
        (native_metadata(dpi=float("nan")), "invalid_resolution"),
        (native_metadata(dpi=float("inf")), "invalid_resolution"),
        (native_metadata(dpi=10**400), "invalid_resolution"),
        ({**native_metadata(), "source": {}}, "invalid_resolution"),
        ({**native_metadata(), "source": "assumed_a4"}, "invalid_resolution"),
    ],
)
def test_unknown_or_invalid_resolution_is_explicit_fallback(metadata, reason):
    result = resolve_source_resolution(1000, 1400, 600, metadata)
    assert result["effective_dpi"] == 600
    assert result["source"] == "session_fallback"
    assert result["metadata_status"] == reason
    assert "actual_dpi" not in result


def test_full_scan_resolution_and_malformed_capture_kind():
    raw = {
        **native_metadata(dpi=600),
        "capture_kind": "scan",
        "source": "image_capture_core_resolution",
    }
    result = resolve_source_resolution(1000, 1400, 600, raw)
    assert result["effective_dpi"] == 600
    assert result["capture_kind"] == "scan"
    assert (
        resolve_source_resolution(1000, 1400, 300, {"capture_kind": {}})["capture_kind"]
        == "unknown"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected_prior", [False, True])
async def test_store_uses_source_dpi_and_never_reapplies_rejected_front_slots(
    tmp_path, monkeypatch, rejected_prior
):
    owner_id, session_id = uuid4(), uuid4()
    current = [
        Detection(0.025 + i % 2 * 0.5, 0.01 + i // 2 * 0.25, 0.45, 0.23, 0, 0.9) for i in range(8)
    ]
    prior = (
        [Detection(0.025 + i % 2 * 0.5, 0.01 + i // 2 * 0.5, 0.45, 0.48, 0, 0.9) for i in range(4)]
        if rejected_prior
        else current
    )
    image = np.full((1400, 1000, 3), 245, np.uint8)
    for d in current:
        cv2.rectangle(
            image,
            (int(d.x * 1000), int(d.y * 1400)),
            (int((d.x + d.width) * 1000), int((d.y + d.height) * 1400)),
            (30, 80, 120),
            -1,
        )
    _, encoded = cv2.imencode(".png", image)

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session_id, "owner_id": owner_id, "dpi": 300}
        elif sql.startswith("SELECT r.bounds,r.signals"):
            result.fetchall.return_value = [
                {
                    "bounds": {
                        "x": d.x,
                        "y": d.y,
                        "width": d.width,
                        "height": d.height,
                        "angle": 0,
                    },
                    "signals": {},
                }
                for d in prior
            ]
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 2}
        return result

    connection = AsyncMock()
    connection.execute.side_effect = execute
    detect = Mock(
        return_value=LayoutHypothesis(
            tuple(current), 0.9, 4, 2, signals={"prior_topology_rejected": int(rejected_prior)}
        )
    )
    slots = AsyncMock(return_value=(current, {"status": "accepted", "geometry_fallback_count": 0}))
    general = AsyncMock(return_value=(current, {"status": "accepted", "refined_count": 8}))
    monkeypatch.setattr("card_platform.flatbed_service.detect_layout", detect)
    monkeypatch.setattr("card_platform.flatbed_slot_refinement.refine_back_slots", slots)
    monkeypatch.setattr("card_platform.flatbed_service.refine_with_sam2", general)
    service = FlatbedService(str(tmp_path), "bridge.swift", ml_enabled=True)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    output = await service.store_sheet(
        connection,
        owner_id,
        session_id,
        "back",
        encoded.tobytes(),
        "image/png",
        source_resolution=native_metadata(),
    )
    assert detect.call_args.kwargs["dpi"] == 200
    assert len(output["regions"]) == 8
    assert output["layout_hypothesis"]["source_resolution"]["effective_dpi"] == 200
    slots.assert_not_called()
    assert general.call_args.kwargs["prompt_detections"] is None
    assert ("front_back_count_mismatch" in output["failures"]) == rejected_prior
    assert "front_layout_merges_current_cards" not in output["failures"]
    assert output["layout_hypothesis"]["slot_refinement"]["reason"] == "independent_back_geometry"
    stored = next(
        call.args[1]
        for call in connection.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_scan_sheet")
    )
    assert stored[9].obj["source_resolution"] == output["layout_hypothesis"]["source_resolution"]
    assert stored[10].obj["expected_count"] == len(prior)
    assert stored[10].obj["prior_role"] == "count_and_pairing_advisory_only"


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [False, True])
async def test_derived_routes_preserve_resolution(tmp_path, monkeypatch, manual):
    source = tmp_path / "original.png"
    source.write_bytes(b"unchanged source bytes")
    metadata = native_metadata()
    row = {
        "session_id": uuid4(),
        "side": "front",
        "original_path": "original.png",
        "content_type": "image/png",
        "layout_hypothesis": {"source_resolution": metadata},
    }
    result = AsyncMock()
    result.fetchone.return_value = row
    db = AsyncMock()
    db.execute.return_value = result
    service = SimpleNamespace(resolve_path=lambda _: source, store_sheet=AsyncMock(return_value={}))
    monkeypatch.setattr(flatbed_api, "service", lambda: service)
    kwargs = {"sheet_id": uuid4(), "user": SimpleNamespace(id=uuid4()), "db": db}
    if manual:
        await flatbed_api.override_layout(
            **kwargs,
            payload={
                "regions": [
                    {
                        "points": [
                            {"x": 0.1, "y": 0.1},
                            {"x": 0.9, "y": 0.1},
                            {"x": 0.9, "y": 0.9},
                            {"x": 0.1, "y": 0.9},
                        ]
                    }
                ]
            },
        )
    else:
        await flatbed_api.reprocess_sheet(**kwargs)
    assert service.store_sheet.call_args.kwargs["source_resolution"] == metadata
    assert service.store_sheet.call_args.args[4] == source.read_bytes()


@pytest.mark.asyncio
async def test_capture_route_passes_native_overview_metadata(tmp_path, monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    metadata = native_metadata()

    async def bridge(command, *, output, dpi, timeout):
        assert command == "preview" and dpi == 300
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"captured source")
        return [{"event": "scan_complete", "path": str(output), "source_resolution": metadata}]

    service = SimpleNamespace(
        root=tmp_path, bridge=bridge, store_sheet=AsyncMock(return_value={"regions": []})
    )
    monkeypatch.setattr(flatbed_api, "service", lambda: service)
    result = AsyncMock()
    result.fetchone.return_value = {"dpi": 300}
    db = AsyncMock()
    db.execute.return_value = result
    await flatbed_api.capture_sheet(
        session_id, "back", preview=True, user=SimpleNamespace(id=owner_id), db=db
    )
    assert service.store_sheet.call_args.kwargs["source_resolution"] == metadata
    assert service.store_sheet.call_args.args[4] == b"captured source"
