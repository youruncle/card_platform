import math

import pytest
from fastapi import HTTPException

from card_platform.flatbed_api import _manual_layout_detections


def _payload(points):
    return {"regions": [{"points": [{"x": x, "y": y} for x, y in points]}]}


def _assert_unprocessable(payload) -> None:
    with pytest.raises(HTTPException) as raised:
        _manual_layout_detections(payload)
    assert raised.value.status_code == 422


def test_valid_rotated_manual_quad_preserves_the_user_corners() -> None:
    points = ((0.50, 0.08), (0.91, 0.50), (0.50, 0.92), (0.09, 0.50))

    detections = _manual_layout_detections(_payload(points))

    assert len(detections) == 1
    assert detections[0].points == points
    assert detections[0].evidence == ("manual_override",)
    assert (detections[0].x, detections[0].y) == (0.09, 0.08)
    assert detections[0].x + detections[0].width == pytest.approx(0.91)
    assert detections[0].y + detections[0].height == pytest.approx(0.92)


def test_crossed_input_order_is_valid_when_the_four_corners_form_a_convex_quad() -> None:
    points = ((0.10, 0.10), (0.90, 0.90), (0.90, 0.10), (0.10, 0.90))

    detection = _manual_layout_detections(_payload(points))[0]

    # Crop rectification canonicalizes the order later. The API only rejects
    # invalid corner sets, not harmless UI point ordering.
    assert detection.points == points


@pytest.mark.parametrize(
    "points",
    (
        pytest.param(
            ((0.10, 0.10), (0.90, 0.10), (0.90, 0.90), (0.90, 0.90)),
            id="duplicate_corner",
        ),
        pytest.param(
            ((0.10, 0.10), (0.35, 0.35), (0.60, 0.60), (0.90, 0.90)),
            id="collinear_corners",
        ),
        pytest.param(
            ((0.10, 0.10), (0.90, 0.10), (0.40, 0.40), (0.10, 0.90)),
            id="one_corner_inside_triangle",
        ),
    ),
)
def test_non_quadrilateral_corner_sets_are_rejected_before_store(points) -> None:
    _assert_unprocessable(_payload(points))


@pytest.mark.parametrize(
    "payload",
    (
        pytest.param([], id="payload_not_an_object"),
        pytest.param({"regions": "not-a-list"}, id="regions_not_a_list"),
        pytest.param({"regions": [{}]}, id="points_missing"),
        pytest.param({"regions": ["not-an-object"]}, id="region_not_an_object"),
        pytest.param(
            {"regions": [{"points": [{"x": 0.1, "y": 0.1}] * 3}]},
            id="wrong_point_count",
        ),
        pytest.param(
            {
                "regions": [
                    {
                        "points": [
                            {"x": 0.1, "y": 0.1},
                            {"x": 0.9, "y": 0.1},
                            {"x": 0.9, "y": 0.9},
                            {"x": math.nan, "y": 0.9},
                        ]
                    }
                ]
            },
            id="nonfinite_coordinate",
        ),
        pytest.param(
            _payload(((0.1, 0.1), (1.1, 0.1), (0.9, 0.9), (0.1, 0.9))),
            id="coordinate_outside_source",
        ),
        pytest.param(
            {
                "regions": [
                    {
                        "points": [
                            {"x": False, "y": 0.1},
                            {"x": 0.9, "y": 0.1},
                            {"x": 0.9, "y": 0.9},
                            {"x": 0.1, "y": 0.9},
                        ]
                    }
                ]
            },
            id="boolean_coordinate",
        ),
    ),
)
def test_malformed_manual_geometry_returns_422(payload) -> None:
    _assert_unprocessable(payload)


def test_axis_extent_floor_still_rejects_impractically_small_regions() -> None:
    _assert_unprocessable(_payload(((0.20, 0.20), (0.21, 0.20), (0.21, 0.80), (0.20, 0.80))))
