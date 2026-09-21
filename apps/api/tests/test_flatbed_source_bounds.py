import cv2
import numpy as np

import card_platform.flatbed_detection as detection_module
from card_platform.flatbed_crop_quality import assess_crop_quality
from card_platform.flatbed_detection import Detection, _clamp_detection_to_source


def _encoded_blank_sheet() -> bytes:
    image = np.full((500, 400, 3), 245, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_public_layout_clamps_bounds_and_polygon_points_from_any_route(monkeypatch) -> None:
    candidate = Detection(
        -0.006,
        0.20,
        0.406,
        0.804,
        -2.0,
        0.91,
        points=((-0.006, 0.20), (0.40, 0.20), (0.40, 1.004), (-0.006, 1.004)),
        evidence=("raw_contour",),
    )
    monkeypatch.setattr(detection_module, "_local_candidates", lambda *_args, **_kwargs: [candidate])

    layout = detection_module.detect_layout(_encoded_blank_sheet())

    assert len(layout.detections) == 1
    result = layout.detections[0]
    assert (result.x, result.y, result.width, result.height) == (0.0, 0.20, 0.40, 0.80)
    assert result.points is not None
    assert all(0.0 <= coordinate <= 1.0 for point in result.points for coordinate in point)
    assert result.failure is None
    assert result.evidence == (
        "raw_contour",
        "source_bounds_clamped",
        "source_edge_contact",
    )


def test_material_overflow_remains_an_explicit_failure_after_clamping() -> None:
    candidate = Detection(
        -0.08,
        0.15,
        0.50,
        1.05,
        0,
        0.9,
        points=((-0.08, 0.15), (0.42, 0.15), (0.42, 1.20), (-0.08, 1.20)),
    )

    result = _clamp_detection_to_source(candidate)

    assert (result.x, result.y, result.width, result.height) == (0.0, 0.15, 0.42, 0.85)
    assert result.points is not None
    assert all(0.0 <= coordinate <= 1.0 for point in result.points for coordinate in point)
    assert result.failure == "geometry_outside_source"
    assert "source_bounds_severe_overflow" in result.evidence
    assert "source_edge_contact" in result.evidence

    image = np.full((500, 400, 3), 245, dtype=np.uint8)
    image[75:500, 0:168] = (30, 80, 160)
    report = assess_crop_quality(image, result)

    assert report.usable is False
    assert report.boundary_score <= 0.3
    assert any(
        violation.code == "geometry_outside_source" and violation.severity == "error"
        for violation in report.violations
    )
