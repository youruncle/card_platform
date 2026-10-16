"""Original-image content evidence, kept separate from chosen crop geometry.

A rotated crop need not retain an axis-aligned envelope's empty background.
It must retain visible card content, including spatially separate label panels.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

import cv2
import numpy as np

from card_platform.flatbed_detection import Detection

MIN_CONTENT_COVERAGE = 0.98


@dataclass(frozen=True)
class ContentReference:
    source: Detection
    image_width: int
    image_height: int
    left: int
    top: int
    features: np.ndarray
    partitions: tuple[tuple[int, int, int, int], ...]
    role: str = "independent_object"
    panels: tuple[tuple[int, int, int, int], ...] = ()


def _points(item: Detection) -> np.ndarray:
    return cv2.convexHull(np.asarray(item.points or (
        (item.x, item.y), (item.x + item.width, item.y),
        (item.x + item.width, item.y + item.height), (item.x, item.y + item.height),
    ), np.float32)).reshape(-1, 2)


def build_content_references(
    image: np.ndarray, source_objects: Sequence[Detection], *, search_only: bool = False,
    scene_regions: Sequence[Detection] = (),
) -> tuple[ContentReference, ...]:
    """Extract immutable evidence from PRE-resolution source regions only.

    Search regions can audit unexplained content but cannot provide crop corners
    or certify a physical object. Final quads are never independent evidence.
    """
    sources = [d for d in source_objects if not set(d.evidence).intersection(
        {"manual_override", "user_confirmed_quad", "user_confirmed_quads", "front_to_back_prior"}
    ) and ("current_pixel_quad" not in d.evidence or "independent_source_object" in d.evidence)
        and (search_only or any(e in d.evidence for e in (
            "independent_source_object", "multi_route_instance_corroboration", "physical_object_envelope",
        ))) ]
    if not sources:
        return ()
    scale = min(1.0, 1200 / max(image.shape[:2]))
    small = cv2.resize(image, None, fx=scale, fy=scale)
    height, width = small.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    saturation = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 1]
    source_masks = []
    for source in sources:
        mask = np.zeros(gray.shape, np.uint8)
        cv2.fillConvexPoly(mask, np.rint(_points(source) * [width, height]).astype(np.int32), 1)
        source_masks.append(mask > 0)
    background_mask = np.logical_or.reduce(source_masks)
    for region in scene_regions:
        scene_mask = np.zeros(gray.shape, np.uint8)
        cv2.fillConvexPoly(scene_mask, np.rint(_points(region) * [width, height]).astype(np.int32), 1)
        background_mask |= scene_mask > 0
    outside = ~background_mask
    samples = gray[outside & (saturation < 25)]
    background = float(np.median(samples)) if samples.size > 100 else float(np.percentile(gray, 85))
    # Scanner-bed brightness is not card ink. Contrast is relative to the
    # observed bed; edge evidence also preserves pale monochrome printed backs.
    edges = cv2.Canny(gray, 35, 100) > 0
    local_mean = cv2.blur(gray.astype(np.float32), (9, 9))
    variance = cv2.blur(gray.astype(np.float32) ** 2, (9, 9)) - local_mean ** 2
    printed_structure = (variance > 64) | (cv2.dilate(edges.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0)
    dark_ink = (gray.astype(float) < background - 35) & (printed_structure if search_only else True)
    light_object_on_dark_bed = (gray.astype(float) > background + 35) if background < 120 and samples.size > 100 else False
    dense_features = (saturation > 30) | dark_ink | light_object_on_dark_bed
    if search_only:
        # Capacity regions audit printed content, not scanner seams, shadows or
        # transparent-holder edges belonging to a neighbouring region.
        near_ink = cv2.dilate(dense_features.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        features = dense_features | (edges & near_ink)
    else:
        features = dense_features | edges
    # Every source pixel has one owner. Overlapping reference envelopes must
    # never delete shared label content from ALL references simultaneously.
    yy, xx = np.ogrid[:height, :width]
    distance = np.full(gray.shape, np.inf, np.float32)
    owner = np.full(gray.shape, -1, np.int32)
    for index, (source, mask) in enumerate(zip(sources, source_masks, strict=True)):
        current = ((xx / width - source.x - source.width / 2) / max(source.width, 1e-9)) ** 2
        current = current + ((yy / height - source.y - source.height / 2) / max(source.height, 1e-9)) ** 2
        closer = mask & (current < distance)
        distance[closer] = current[closer]
        owner[closer] = index
    references = []
    for index, (source, mask) in enumerate(zip(sources, source_masks, strict=True)):
        mask = mask & (owner == index)
        left, top = max(0, int(source.x * width)), max(0, int(source.y * height))
        right = min(width, int(np.ceil((source.x + source.width) * width)))
        bottom = min(height, int(np.ceil((source.y + source.height) * height)))
        content = (features & mask)[top:bottom, left:right]
        total = int(content.sum())
        if total < 30:
            continue
        partitions = []
        for row in range(4):
            for column in range(4):
                x1, x2 = column * content.shape[1] // 4, (column + 1) * content.shape[1] // 4
                y1, y2 = row * content.shape[0] // 4, (row + 1) * content.shape[0] // 4
                count = int(content[y1:y2, x1:x2].sum())
                if count >= max(20, total * 0.01):
                    partitions.append((x1, y1, x2, y2))
        panels = []
        if "grading_label_association" in source.evidence:
            dense = ((dense_features & mask)[top:bottom, left:right]).astype(np.uint8)
            closed = cv2.morphologyEx(dense, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
            _, _, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
            components = [v for v in stats[1:] if v[cv2.CC_STAT_AREA] >= 20]
            if components:
                main = max(components, key=lambda v: v[cv2.CC_STAT_AREA])
                mx, my, mw, mh = main[:4]
                for component in components:
                    x, y, cw, ch = component[:4]
                    separated = (x + cw < mx - 3 or x > mx + mw + 3
                                 or y + ch < my - 3 or y > my + mh + 3)
                    if separated and content[y:y+ch, x:x+cw].sum() >= 20:
                        # A separately observed label never disappears into
                        # the whole-card 2% allowance, however small it is.
                        panels.append((int(x), int(y), int(x+cw), int(y+ch)))
        references.append(ContentReference(source, width, height, left, top, content,
                            tuple(partitions), "search_residual" if search_only else "independent_object",
                            tuple(panels)))
    return tuple(references)


def content_preservation(reference: ContentReference, points: np.ndarray, *,
                         explained_points: Sequence[np.ndarray] = ()) -> dict:
    points = np.asarray(points, np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        return {"preserved": False, "content_coverage": 0.0, "minimum_partition_coverage": 0.0}
    mask = np.zeros(reference.features.shape, np.uint8)
    local = points * [reference.image_width, reference.image_height] - [reference.left, reference.top]
    cv2.fillConvexPoly(mask, np.rint(local).astype(np.int32), 1)
    # Three preview pixels account for contour rasterisation/edge blur only;
    # this cannot cover a missing panel or a macroscopic card corner.
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8))
    features = reference.features
    if reference.role == "search_residual" and explained_points:
        explained = np.zeros(features.shape, np.uint8)
        for peer in explained_points:
            peer_local = np.asarray(peer) * [reference.image_width, reference.image_height] - [reference.left, reference.top]
            cv2.fillConvexPoly(explained, np.rint(peer_local).astype(np.int32), 1)
        features = features & (explained == 0)
    retained = features & (mask > 0)
    count = int(features.sum())
    coverage = float(retained.sum() / count) if count else 1.0
    partitions = [float(retained[y1:y2, x1:x2].sum() / features[y1:y2, x1:x2].sum())
                  for x1, y1, x2, y2 in reference.partitions
                  if features[y1:y2, x1:x2].sum() >= 20]
    minimum = min(partitions, default=coverage)
    panels = [float(retained[y1:y2, x1:x2].sum() / features[y1:y2, x1:x2].sum())
              for x1, y1, x2, y2 in reference.panels if features[y1:y2, x1:x2].sum() >= 20]
    minimum_panel = min(panels, default=1.0)
    return {"preserved": (coverage >= MIN_CONTENT_COVERAGE and minimum_panel >= MIN_CONTENT_COVERAGE),
            "minimum_panel_coverage": round(minimum_panel, 6), "panel_count": len(panels),
            "content_coverage": round(coverage, 6),
            "minimum_partition_coverage": round(minimum, 6),
            "partition_count": len(partitions), "content_pixels": count,
            "reference_role": reference.role}
