import cv2
import numpy as np
import pytest

from card_platform.flatbed_ml_geometry import (
    consensus_edge_quad,
    strong_axis_aligned_edge_quad,
)


def _card_image(angle: float) -> tuple[np.ndarray, dict[str, float]]:
    image = np.full((800, 800, 3), 255, dtype=np.uint8)
    rectangle = ((400.0, 400.0), (320.0, 230.0), angle)
    points = cv2.boxPoints(rectangle).astype(np.int32)
    cv2.fillConvexPoly(image, points, (25, 25, 25))
    return image, {"x": 0.30, "y": 0.30, "width": 0.40, "height": 0.40}


def test_edge_consensus_recovers_rotated_card_quad() -> None:
    image, coarse = _card_image(5.0)

    result = consensus_edge_quad(image, coarse)

    assert result is not None
    assert abs(float(result["angle"])) == pytest.approx(5.0, abs=0.5)
    assert int(result["agreement"]) >= 2
    assert len(result["points"]) == 4


def test_edge_consensus_does_not_override_axis_aligned_card() -> None:
    image, coarse = _card_image(0.0)

    assert consensus_edge_quad(image, coarse) is None


def test_strong_axis_edge_recovers_materially_smaller_grid_object() -> None:
    image = np.full((800, 800, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (450, 550), (792, 792), (25, 25, 25), -1)
    coarse = {"x": 0.5, "y": 0.70, "width": 0.5, "height": 0.30}

    result = strong_axis_aligned_edge_quad(image, coarse)

    assert result is not None
    points = np.asarray(result["points"])
    assert points[:, 0].min() > coarse["x"]
    assert points[:, 0].max() >= 0.988
    assert points[:, 1].max() >= 0.988
