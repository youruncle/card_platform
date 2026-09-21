from itertools import permutations
from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis
from card_platform.flatbed_service import (
    FlatbedService,
    ordered_source_quad,
    peer_safe_padding_ratio,
)


def test_diagonal_quad_has_four_unique_corners_for_every_input_order():
    diamond = np.array([[300, 100], [500, 300], [300, 500], [100, 300]], np.float32)
    for permutation in permutations(diamond):
        ordered = ordered_source_quad(np.asarray(permutation))
        assert len(np.unique(ordered, axis=0)) == 4
        assert cv2.contourArea(ordered) == 80000
        transform = cv2.getPerspectiveTransform(
            ordered, np.array([[0, 0], [299, 0], [299, 299], [0, 299]], np.float32)
        )
        assert abs(np.linalg.det(transform)) > 0.1


def test_generic_margin_cannot_expand_into_a_real_neighbour():
    points = np.array([[100, 100], [800, 100], [800, 600], [100, 600]], np.float32)
    cards = [Detection(0.1, 0.1, 0.7, 0.5, 0, 1), Detection(0.1, 0.605, 0.7, 0.3, 0, 1)]
    assert peer_safe_padding_ratio(points, cards, 0, 1000, 1000, 0.03) == 0


def test_isolated_card_keeps_its_source_margin():
    points = np.array([[100, 100], [800, 100], [800, 600], [100, 600]], np.float32)
    cards = [Detection(0.1, 0.1, 0.7, 0.5, 0, 1), Detection(0.1, 0.7, 0.7, 0.25, 0, 1)]
    assert peer_safe_padding_ratio(points, cards, 0, 1000, 1000, 0.03) == 0.03


def test_rotated_peer_bbox_is_not_treated_as_actual_peer_pixels():
    points = np.array([[100, 100], [250, 100], [250, 250], [100, 250]], np.float32)
    diamond = ((0.5, 0.2), (0.8, 0.5), (0.5, 0.8), (0.2, 0.5))
    cards = [
        Detection(0.1, 0.1, 0.15, 0.15, 0, 1),
        Detection(0.2, 0.2, 0.6, 0.6, 45, 1, points=diamond),
    ]
    assert peer_safe_padding_ratio(points, cards, 0, 1000, 1000, 0.03) == 0.03


@pytest.mark.asyncio
async def test_actual_generic_crop_does_not_copy_red_pixels_from_lower_peer(tmp_path, monkeypatch):
    image = np.full((1000, 1000, 3), 246, np.uint8)
    cv2.rectangle(image, (100, 100), (800, 600), (80, 140, 50), -1)
    cv2.rectangle(image, (100, 605), (800, 905), (20, 20, 230), -1)
    cards = [
        Detection(
            0.1, 0.1, 0.7, 0.5, 0, 1, points=((0.1, 0.1), (0.8, 0.1), (0.8, 0.6), (0.1, 0.6))
        ),
        Detection(
            0.1,
            0.605,
            0.7,
            0.3,
            0,
            1,
            points=((0.1, 0.605), (0.8, 0.605), (0.8, 0.905), (0.1, 0.905)),
        ),
    ]
    monkeypatch.setattr(
        "card_platform.flatbed_service.detect_layout",
        lambda *args, **kwargs: LayoutHypothesis(tuple(cards), 1, 2, 1),
    )
    owner, session = uuid4(), uuid4()

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session, "owner_id": owner, "dpi": 300}
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 1}
        return result

    connection = AsyncMock()
    connection.execute.side_effect = execute
    service = FlatbedService(str(tmp_path), "unused.swift", ml_enabled=False)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    ok, content = cv2.imencode(".png", image)
    assert ok
    output = await service.store_sheet(
        connection, owner, session, "front", content.tobytes(), "image/png"
    )
    cropped = cv2.imread(str(next(tmp_path.glob("*/*/*/region-0.jpg"))))
    assert np.mean((cropped[:, :, 2].astype(int) - cropped[:, :, 1]) > 100) < 0.001
    assert output["regions"][0]["bounds"]["crop_padding_ratio"] == 0
    assert len(output["regions"][0]["bounds"]["source_crop_points"]) == 4
