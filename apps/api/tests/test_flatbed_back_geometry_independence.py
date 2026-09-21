"""Back pixels, not front slots or their count, own back crop geometry."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis
from card_platform.flatbed_service import FlatbedService


def _objects(*, strong: bool = False) -> list[Detection]:
    # Three landscape objects next to two taller holders. Their row-major order
    # may change when the last landscape card moves down after flipping over.
    boxes = (
        (0.06, 0.03, 0.42, 0.20),
        (0.64, 0.03, 0.26, 0.39),
        (0.06, 0.28, 0.42, 0.20),
        (0.64, 0.50, 0.26, 0.39),
        (0.06, 0.68, 0.42, 0.20),
    )
    evidence = ("current_candidate_inward_edges",)
    if strong:
        evidence += ("physical_object_envelope",)
    return [
        Detection(
            x, y, width, height, 0, 0.90 if strong else 0.60,
            points=((x, y), (x + width, y), (x + width, y + height), (x, y + height)),
            evidence=evidence,
        )
        for x, y, width, height in boxes
    ]


def _source(objects: list[Detection]) -> bytes:
    image = np.full((1120, 800, 3), 245, np.uint8)
    for index, obj in enumerate(objects):
        x0, x1 = round(obj.x * 800), round((obj.x + obj.width) * 800)
        y0, y1 = round(obj.y * 1120), round((obj.y + obj.height) * 1120)
        cv2.rectangle(image, (x0, y0), (x1, y1), (30 + index * 20, 65, 110), -1)
        for y in range(y0 + 12, y1 - 8, 16):
            cv2.line(image, (x0 + 10, y), (x1 - 10, y), (170, 180, 190), 2)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _front_rows(detections: list[Detection]) -> list[dict]:
    return [
        {
            "bounds": {
                "x": obj.x, "y": obj.y, "width": obj.width, "height": obj.height,
                "angle": obj.angle,
                "points": [{"x": x, "y": y} for x, y in obj.points or ()],
            },
            "signals": {"detection_evidence": list(obj.evidence)},
        }
        for obj in detections
    ]


def _radically_different_fronts() -> list[list[dict]]:
    return [
        [],
        _front_rows([Detection(0.01, 0.01, 0.98, 0.98, 0, 1)]),
        _front_rows(list(reversed(_objects(strong=True)))),
        _front_rows([
            Detection(0.02 + index * 0.19, 0.12, 0.16, 0.15, 45, 1)
            for index in range(5)
        ]),
        _front_rows([
            Detection(
                0.01 + col * 0.5, 0.005 + row * 0.25, 0.48, 0.24, 0, 1,
                evidence=("physical_object_envelope", "grading_label_association"),
            )
            for row in range(4) for col in range(2)
        ]),
        # Front geometry is not needed even to report the count. An incomplete
        # historical row must not prevent processing valid current back bytes.
        [{"bounds": None, "signals": {}}, {"bounds": {}, "signals": {}}],
    ]


def _db(owner_id, session_id, fronts: list[dict]) -> AsyncMock:
    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session_id, "owner_id": owner_id, "dpi": 300}
        elif sql.startswith("SELECT r.bounds,r.signals"):
            result.fetchall.return_value = fronts
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 2}
        return result

    connection = AsyncMock()
    connection.execute.side_effect = execute
    return connection


def _stored_sheet(connection: AsyncMock):
    return next(
        call.args[1] for call in connection.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_scan_sheet")
    )


def _crop_fingerprints(connection: AsyncMock, root: Path) -> list[str]:
    return [
        sha256((root / call.args[1][7]).read_bytes()).hexdigest()
        for call in connection.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_scan_region")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("ml_enabled", [False, True])
@pytest.mark.parametrize("strong", [False, True], ids=["weak_current", "strong_current"])
async def test_same_back_bytes_ignore_front_count_geometry_and_evidence(
    tmp_path, monkeypatch, ml_enabled, strong
):
    owner_id, session_id = uuid4(), uuid4()
    current = _objects(strong=strong)
    content = _source(current)
    detector_calls, ml_calls = [], []

    def detect_current(data, *, dpi, **kwargs):
        # Strict dependency boundary: even a count is a geometric constraint.
        assert kwargs.get("expected_count") is None
        assert not kwargs.get("prior")
        assert not kwargs.get("stationary_prior", False)
        detector_calls.append((data, dpi))
        return LayoutHypothesis(tuple(current), 0.90, 0, 0, signals={})

    async def refine_current(data, detections, **kwargs):
        assert data == content
        assert detections == current
        assert kwargs.get("prompt_detections") is None
        ml_calls.append(tuple(detections))
        return list(detections), {"status": "accepted", "refined_count": len(detections)}

    def no_front_prompts(*args, **kwargs):
        raise AssertionError("A front-side envelope must not become a back-side ML prompt")

    monkeypatch.setattr("card_platform.flatbed_service.detect_layout", detect_current)
    monkeypatch.setattr("card_platform.flatbed_service.refine_with_sam2", refine_current)
    monkeypatch.setattr("card_platform.flatbed_service.back_refinement_prompts", no_front_prompts)
    monkeypatch.setattr(
        "card_platform.flatbed_slot_refinement.refine_back_slots",
        AsyncMock(side_effect=AssertionError("Front slot localization is not back detection")),
    )
    snapshots = []
    front_variants = _radically_different_fronts()
    for index, fronts in enumerate(front_variants):
        connection = _db(owner_id, session_id, fronts)
        service = FlatbedService(str(tmp_path / str(index)), "bridge.swift", ml_enabled=ml_enabled)
        service.orientation_classifier.classify = AsyncMock(return_value=None)
        result = await service.store_sheet(
            connection, owner_id, session_id, "back", content, "image/png"
        )
        snapshots.append((
            [region["bounds"] for region in result["regions"]],
            _crop_fingerprints(connection, service.root),
            result["layout_hypothesis"],
        ))
        stored = _stored_sheet(connection)
        assert (service.root / stored[5]).read_bytes() == content
        assert stored[10].obj["expected_count"] == (len(fronts) or None)
        assert stored[10].obj["prior_role"] == "count_and_pairing_advisory_only"
        assert result["layout_hypothesis"]["slot_refinement"]["reason"] == "independent_back_geometry"
        assert ("front_back_count_mismatch" in result["failures"]) == (
            bool(fronts) and len(fronts) != len(current)
        )
        assert not any(code.startswith("front_layout_") for code in result["failures"])
        assert any(
            call.args[0].startswith("DELETE FROM flatbed_pair_proposal")
            for call in connection.execute.call_args_list
        )
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    assert detector_calls == [(content, 300)] * len(front_variants)
    assert len(ml_calls) == (len(front_variants) if ml_enabled else 0)


@pytest.mark.asyncio
async def test_manual_back_quads_remain_authoritative_for_every_front_layout(tmp_path, monkeypatch):
    owner_id, session_id = uuid4(), uuid4()
    objects = _objects()
    manual = [replace(obj, evidence=("manual_override",)) for obj in objects[:3]]
    content = _source(objects)

    def no_automatic_geometry(*args, **kwargs):
        raise AssertionError("Explicit manual back geometry must not be re-detected or prompted")

    monkeypatch.setattr("card_platform.flatbed_service.detect_layout", no_automatic_geometry)
    monkeypatch.setattr("card_platform.flatbed_service.back_refinement_prompts", no_automatic_geometry)
    monkeypatch.setattr("card_platform.flatbed_service.refine_with_sam2", no_automatic_geometry)
    snapshots = []
    for index, fronts in enumerate(_radically_different_fronts()):
        connection = _db(owner_id, session_id, fronts)
        service = FlatbedService(str(tmp_path / str(index)), "bridge.swift", ml_enabled=True)
        service.orientation_classifier.classify = AsyncMock(return_value=None)
        result = await service.store_sheet(
            connection, owner_id, session_id, "back", content, "image/png",
            detections_override=manual,
        )
        assert result["layout_hypothesis"]["manual_override"] is True
        assert len(result["regions"]) == len(manual)
        for actual, expected in zip(result["regions"], manual, strict=True):
            assert actual["bounds"]["points"] == [{"x": x, "y": y} for x, y in expected.points]
        assert any(
            call.args[0].startswith("INSERT INTO flatbed_layout_override")
            for call in connection.execute.call_args_list
        )
        snapshots.append((
            [region["bounds"] for region in result["regions"]],
            _crop_fingerprints(connection, service.root),
        ))
    assert all(snapshot == snapshots[0] for snapshot in snapshots)


@pytest.mark.asyncio
@pytest.mark.parametrize("back_order", [(0, 1, 2, 3, 4), (3, 2, 1, 4, 0)])
async def test_pairing_uses_region_geometry_not_changed_scan_ordinals(tmp_path, back_order):
    owner_id, session_id = uuid4(), uuid4()
    objects = _objects()
    front_ids, back_ids = [uuid4() for _ in objects], [uuid4() for _ in objects]
    front_order = (0, 1, 2, 4, 3)

    def row(index, ordinal, side):
        obj = objects[index]
        # The lower left card is slightly higher in the front scan, hence its
        # earlier ordinal. Keep all matching signals local and identity-free.
        obj = replace(obj, y=obj.y - 0.15) if side == "front" and index == 4 else obj
        return {
            "id": (front_ids if side == "front" else back_ids)[index],
            "side": side, "ordinal": ordinal,
            "bounds": {"x": obj.x, "y": obj.y, "width": obj.width, "height": obj.height},
            "confidence": 0.9, "failure_reason": None, "signals": {},
        }

    rows = sorted(
        [row(index, ordinal, "front") for ordinal, index in enumerate(front_order)]
        + [row(index, ordinal, "back") for ordinal, index in enumerate(back_order)],
        key=lambda item: item["ordinal"],
    )

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT r.*,s.side"):
            result.fetchall.return_value = rows
        return result

    connection = AsyncMock()
    connection.execute.side_effect = execute
    result = await FlatbedService(str(tmp_path), "bridge.swift").pair(
        connection, owner_id, session_id
    )
    expected = {(str(front), str(back)) for front, back in zip(front_ids, back_ids, strict=True)}
    assert {(pair["front_id"], pair["back_id"]) for pair in result["pairs"]} == expected
    persisted = {
        (str(call.args[1][2]), str(call.args[1][3]))
        for call in connection.execute.call_args_list
        if call.args[0].startswith("INSERT INTO flatbed_pair_proposal") and call.args[1][9]
    }
    assert persisted == expected
