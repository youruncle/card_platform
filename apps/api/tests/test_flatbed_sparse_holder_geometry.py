from pathlib import Path

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import detect_layout


def test_four_kobe_cards_are_whole_distinct_crops_without_blank_fifth():
    root = Path(__file__).parents[3]
    sources = list((root / 'data/flatbed-scans').glob('*/c8f1d7f1-34ba-40ce-9d78-8d414f46541d/eb670aba-74aa-47e7-81d8-38cca6a65695/original.png'))
    if not sources:
        pytest.skip('Local four-card original scan is not installed')
    # Hand-marked complete card borders, including their white margins, in
    # normalized original coordinates. Holder shadows are outside the labels.
    polygons = [
        np.array([(0.040, 0.038), (0.450, 0.036), (0.450, 0.257), (0.040, 0.258)], np.float32),
        np.array([(0.526, 0.041), (0.925, 0.027), (0.946, 0.252), (0.550, 0.266)], np.float32),
        np.array([(0.046, 0.325), (0.486, 0.330), (0.485, 0.550), (0.047, 0.546)], np.float32),
        np.array([(0.044, 0.600), (0.466, 0.597), (0.475, 0.823), (0.046, 0.827)], np.float32),
    ]
    layout = detect_layout(sources[0].read_bytes(), dpi=300)
    assert len(layout.detections) == 4
    owners = []
    for polygon in polygons:
        coverage = []
        for detection in layout.detections:
            crop = cv2.convexHull(np.asarray(detection.points, np.float32))
            intersection, _ = cv2.intersectConvexConvex(crop, polygon)
            coverage.append(intersection / cv2.contourArea(polygon))
        assert max(coverage) >= 0.98, coverage
        owners.append(int(np.argmax(coverage)))
    assert len(set(owners)) == 4
    # A crop may include clear holder margin, but cannot include another card.
    for owner, polygon in zip(owners, polygons):
        crop = cv2.convexHull(np.asarray(layout.detections[owner].points, np.float32))
        for peer in polygons:
            if peer is polygon:
                continue
            intersection, _ = cv2.intersectConvexConvex(crop, peer)
            assert intersection / cv2.contourArea(peer) <= 0.01
