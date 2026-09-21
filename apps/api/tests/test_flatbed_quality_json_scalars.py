"""Image-derived NumPy coordinates must not break API/JSONB serialization."""

import json

import cv2
import numpy as np

from card_platform.flatbed_crop_quality import (
    CropQualityReport,
    CropQualityViolation,
    assess_sheet_crop_quality,
)
from card_platform.flatbed_detection import Detection


def test_numpy_object_coordinates_produce_native_quality_and_summary_scalars():
    image = np.full((600, 500, 3), 240, dtype=np.uint8)
    cv2.rectangle(image, (0, 180), (250, 450), (40, 100, 170), -1)
    cv2.putText(image, "CARD", (35, 310), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (250, 250, 250), 2)
    points = tuple(
        tuple(point)
        for point in np.array([[0, 0.3], [0.5, 0.3], [0.5, 0.75], [0, 0.75]], dtype=np.float64)
    )
    detection = Detection(
        np.float64(0),
        np.float64(0.3),
        np.float64(0.5),
        np.float64(0.45),
        0,
        0.9,
        points=points,
        evidence=("physical_object_envelope",),
    )
    reports = assess_sheet_crop_quality(image, [detection])
    report = reports[0]
    assert type(report.usable) is bool
    assert type(report.needs_review) is bool
    assert type(report.usability_score) is float
    assert type(report.metrics["source_edge_count"]) is int
    assert report.metrics["source_edge_count"] == 1
    payload = {
        "usable_count": sum(item.usable for item in reports),
        "review_count": sum(item.needs_review for item in reports),
        "minimum_usability_score": min(item.usability_score for item in reports),
        "reports": [item.as_dict() for item in reports],
    }
    # Uses standard json.dumps just like psycopg's default JSONB dumper: no
    # custom NumPy encoder should be necessary in production persistence.
    decoded = json.loads(json.dumps(payload))
    assert type(decoded["usable_count"]) is int
    assert type(decoded["reports"][0]["usable"]) is bool
    warning = next(
        item
        for item in decoded["reports"][0]["violations"]
        if item["code"] == "source_edge_contact"
    )
    assert type(warning["evidence"]["edge_count"]) is int


def test_public_quality_dict_normalizes_scalar_metrics_without_changing_types():
    report = CropQualityReport(
        np.float32(0.5),
        np.float64(0.75),
        np.float32(1),
        np.float64(0.8),
        np.bool_(True),
        np.bool_(False),
        (
            CropQualityViolation(
                "example", "warning", "message", {"count": np.int64(2), "ratio": np.float32(0.5)}
            ),
        ),
        {"count": np.int64(2), "ratio": np.float32(0.5)},
    )
    decoded = json.loads(json.dumps(report.as_dict()))
    assert decoded["metrics"] == {"count": 2, "ratio": 0.5}
    assert decoded["violations"][0]["evidence"] == {"count": 2, "ratio": 0.5}
    assert decoded["usable"] is True
    assert decoded["needs_review"] is False
    assert type(decoded["metrics"]["count"]) is int
    assert type(decoded["metrics"]["ratio"]) is float
