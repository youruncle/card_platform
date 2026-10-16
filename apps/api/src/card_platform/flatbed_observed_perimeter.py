"""Protect observed source-image edge pixels, never a bounding box's area."""

import cv2
import numpy as np

from card_platform.flatbed_detection import Detection


def _quad(item: Detection, width: int, height: int) -> np.ndarray | None:
    if not item.points:
        return None
    points = np.asarray(item.points, np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        return None
    hull = cv2.convexHull(points).reshape(-1, 2)
    if len(hull) != 4 or cv2.contourArea(hull) <= 1e-6:
        return None
    return hull * [width, height]


def observed_perimeter_preservation(
    image: np.ndarray, sourcequad: Detection, refinedquad: Detection,
) -> dict:
    """Require four supported source edges before protecting their pixels.

    The source must already have passed independent content validation and
    have local contour corroboration. Six preview pixels accommodate the
    resolver's safety expansion. Gradient direction must match the edge normal
    within 15 degrees, in at least eight of ten along-edge bins. Refinement
    retains at least 98% of those actual pixels, with one pixel alias tolerance.
    This evidence establishes observed lines only, not whole-object identity.
    """
    routes = set()
    for signal in sourcequad.evidence:
        if signal.startswith('quad_routes:'):
            routes.update(signal.removeprefix('quad_routes:').split(','))
    local_routes = {route for route in routes if route.startswith(('edge_', 'threshold_')) or route == 'otsu'}
    base = {'status': 'not_applicable', 'evidence': 'observed_source_edge_pixels',
            'retained_coverage': None, 'edges': [], 'preview_long_edge': 1200,
            'edge_band_pixels': 6, 'alias_tolerance_pixels': 1,
            'gradient_tolerance_degrees': 15, 'minimum_retained_coverage': .98,
            'required_supported_bins': 8, 'total_edge_bins': 10}
    if ('original_content_verified' not in sourcequad.evidence
        or 'current_multiroute_outer_object' in routes or len(local_routes) < 2
        or set(sourcequad.evidence).intersection({'quad_unresolved','search_region_only'})):
        return {**base, 'reason': 'source_not_verified_local_contour'}
    scale = min(1.0, 1200 / max(image.shape[:2]))
    small = cv2.resize(image, None, fx=scale, fy=scale)
    height, width = small.shape[:2]
    source = _quad(sourcequad, width, height)
    final = _quad(refinedquad, width, height)
    if source is None or final is None:
        return {**base, 'reason': 'invalid_quad'}
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    edge_mask = cv2.Canny(gray, 35, 100)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    yy, xx = np.nonzero(edge_mask)
    pixels = np.column_stack((xx, yy)).astype(np.float32)
    gradients = np.column_stack((gx[yy, xx], gy[yy, xx]))
    magnitudes = np.linalg.norm(gradients, axis=1)
    retained_mask = np.zeros(gray.shape, np.uint8)
    cv2.fillConvexPoly(retained_mask, np.rint(final).astype(np.int32), 1)
    retained_mask = cv2.dilate(retained_mask, np.ones((3, 3), np.uint8))
    source_mask = np.zeros(gray.shape, np.uint8)
    cv2.fillConvexPoly(source_mask, np.rint(source).astype(np.int32), 1)
    source_mask = cv2.dilate(source_mask, np.ones((3, 3), np.uint8))
    all_supported = np.zeros(len(pixels), bool)
    details = []
    for index, start in enumerate(source):
        vector = source[(index + 1) % 4] - start
        length = float(np.linalg.norm(vector))
        if length < 20:
            return {**base, 'reason': 'source_edge_too_short'}
        tangent = vector / length
        normal = np.array([-tangent[1], tangent[0]])
        relative = pixels - start
        along = relative @ tangent
        perpendicular = np.abs(relative @ normal)
        alignment = np.abs(gradients @ normal) / np.maximum(magnitudes, 1e-9)
        supported = ((along >= 0) & (along <= length) & (perpendicular <= 6)
                     & (alignment >= np.cos(np.deg2rad(15))))
        bins = np.clip((along[supported] / length * 10).astype(int), 0, 9)
        bin_counts = np.bincount(bins, minlength=10)
        # Multiple isolated pixels in each bin establish distributed support;
        # one corner or an intersecting printed diagonal is insufficient.
        occupied = int(np.count_nonzero(bin_counts >= max(2, length / 100)))
        count = int(supported.sum())
        supported_edge = occupied >= 8 and count >= max(20, length * .2)
        # Protect pixels visible in the original crop, rather than pixels
        # outside its own boundary that even an unchanged crop cannot retain.
        tracked = supported & (source_mask[yy, xx] > 0)
        tracked_count = int(tracked.sum())
        retained = int(np.count_nonzero(retained_mask[yy[tracked], xx[tracked]]))
        details.append({'edge_index': index, 'length_pixels': round(length, 3),
                        'start_preview': tuple(round(float(v), 3) for v in start),
                        'end_preview': tuple(round(float(v), 3) for v in source[(index + 1) % 4]),
                        'supported_pixels': count, 'supported_bins': occupied,
                        'supported': supported_edge, 'tracked_pixels': tracked_count,
                        'retained_coverage': round(retained / tracked_count, 6) if tracked_count else 0.0})
        all_supported |= tracked
    if not all(edge['supported'] for edge in details):
        return {**base, 'edges': details, 'reason': 'four_edges_not_observed'}
    count = int(all_supported.sum())
    if not count or any(edge['tracked_pixels'] < 20 for edge in details):
        return {**base, 'edges': details, 'reason': 'no_retained_source_edge_pixels'}
    retained = int(np.count_nonzero(retained_mask[yy[all_supported], xx[all_supported]]))
    coverage = retained / count
    preserved = coverage >= .98 and all(edge['retained_coverage'] >= .98 for edge in details)
    return {**base, 'status': 'preserved' if preserved else 'needs_fallback',
            'reason': 'observed_perimeter_retained' if preserved else 'observed_perimeter_removed',
            'retained_coverage': round(coverage, 6), 'observed_pixels': count,
            'edges': details, 'source_perimeter_supported': True}
