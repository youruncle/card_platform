"""Local artwork support only; never proof of card identity or parallel finish."""
from decimal import Decimal

import cv2
import numpy as np


def geometric_feature_score(target_points, target_descriptors, target_shape,
                            candidate_points, candidate_descriptors, candidate_shape):
    """Count independent, spatially distributed correspondences.

    SIFT can assign several orientations to one physical point. Ratio matching
    also permits many query descriptors to select the same candidate point.
    Both inflate RANSAC's support unless coordinates are deduplicated first.
    Shapes must describe the same scaled coordinate frames as the keypoints.
    """
    if target_descriptors is None or candidate_descriptors is None:
        return 0, 0, Decimal('0')
    pairs = cv2.BFMatcher().knnMatch(target_descriptors, candidate_descriptors, k=2)
    matched = [p[0] for p in pairs if len(p) == 2 and p[0].distance < .72*p[1].distance]
    good, source_seen, destination_seen = [], set(), set()
    for match in sorted(matched, key=lambda m: m.distance):
        source = tuple(round(float(v)) for v in target_points[match.queryIdx].pt)
        destination = tuple(round(float(v)) for v in candidate_points[match.trainIdx].pt)
        if source in source_seen or destination in destination_seen:
            continue
        source_seen.add(source)
        destination_seen.add(destination)
        good.append(match)
    if len(good) < 8:
        return len(good), 0, Decimal('0')
    source = np.float32([target_points[m.queryIdx].pt for m in good])
    destination = np.float32([candidate_points[m.trainIdx].pt for m in good])
    homography, mask = cv2.findHomography(source, destination, cv2.RANSAC, 5.)
    if homography is None or not np.isfinite(homography).all() or mask is None:
        return len(good), 0, Decimal('0')
    supported = mask.ravel().astype(bool)
    inliers = int(supported.sum())
    if inliers < 8:
        return len(good), 0, Decimal('0')
    for points, shape in ((source, target_shape), (destination, candidate_shape)):
        area = cv2.contourArea(cv2.convexHull(points[supported]))
        if area < .01 * shape[0] * shape[1]:
            # Collinear/point-like fits and one small repeated patch cannot
            # support a whole-card template claim, regardless of vote count.
            return len(good), 0, Decimal('0')
    return len(good), inliers, Decimal(str(inliers / len(good)))
