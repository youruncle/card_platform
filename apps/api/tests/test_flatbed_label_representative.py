"""A route medoid is not allowed to discard a verified grading-label extent."""

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, _corroborated_current_instances


@pytest.mark.parametrize("prefer_outer_envelope", [False, True])
def test_shifted_larger_consensus_cannot_replace_a_complete_label_envelope(
    prefer_outer_envelope,
):
    image = np.full((1000, 1000, 3), 230, dtype=np.uint8)
    labelled = Detection(
        0.20,
        0.20,
        0.20,
        0.35,
        0,
        0.88,
        evidence=("grading_label_association", "saturation_content_island"),
    )
    # Two other routes agree on a larger rectangle, but both miss the label at
    # the top. Area growth and IoU alone must not launder this into completeness.
    shifted = [
        Detection(0.18, 0.26, 0.23, 0.34, 0, 0.92, evidence=(route,))
        for route in ("threshold_165", "adaptive_threshold")
    ]
    objects = _corroborated_current_instances(
        image,
        [],
        [labelled, *shifted],
        cell_area=0.10,
        prefer_outer_envelope=prefer_outer_envelope,
    )
    assert len(objects) == 1
    result = objects[0]
    source = np.float32([[0.20, 0.20], [0.40, 0.20], [0.40, 0.55], [0.20, 0.55]])
    polygon = np.float32(
        result.points
        or [
            (result.x, result.y),
            (result.x + result.width, result.y),
            (result.x + result.width, result.y + result.height),
            (result.x, result.y + result.height),
        ]
    )
    coverage = cv2.intersectConvexConvex(source, polygon)[0] / cv2.contourArea(source)
    assert coverage >= 0.95
    assert "grading_label_association" in result.evidence
