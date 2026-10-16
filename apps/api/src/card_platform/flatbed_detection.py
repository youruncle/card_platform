from __future__ import annotations

from dataclasses import dataclass, field, replace
import cv2
import numpy as np


@dataclass(frozen=True)
class Detection:
    x: float
    y: float
    width: float
    height: float
    angle: float
    confidence: float
    failure: str | None = None
    points: tuple[tuple[float, float], ...] | None = None
    evidence: tuple[str, ...] = ()


DETECTOR_VERSION = "object-quad-layout-v30"

GRID_INK_LOW_QUANTILE = 0.008
GRID_INK_HIGH_QUANTILE = 0.992
GRID_INK_MARGIN_RATIO = 0.035
GRID_MIN_REFINEMENT_INSET_RATIO = 0.018
GRID_MAX_REFINED_AREA_RATIO = 0.96
GRID_OCCUPANCY_CORE_INSET_RATIO = 0.10
GRID_EMPTY_MIN_MEAN_LUMA = 210.0
GRID_EMPTY_MAX_INK_RATIO = 0.035
GRID_EMPTY_MAX_EDGE_RATIO = 0.005
GRID_EMPTY_MAX_LUMA_STDDEV = 20.0
GRID_TRAILING_SLIVER_MAX_INK_RATIO = 0.12
GRID_TRAILING_SLIVER_MAX_EDGE_RATIO = 0.015
GRID_TRAILING_SLIVER_MAX_AXIS_COVERAGE = 0.16
GRID_MIN_PROJECTION_SPAN_REGULARITY = 0.62
GRID_INSTANCE_MIN_OBSERVATION_CELL_AREA_RATIO = 0.20
GRID_INSTANCE_MIN_CELL_AREA_RATIO = 0.55
GRID_INSTANCE_MAX_CELL_AREA_RATIO = 1.45
GRID_INSTANCE_MIN_ROUTE_FAMILIES = 2
GRID_INSTANCE_MIN_CORROBORATION_IOU = 0.58
GRID_INSTANCE_MIN_CELL_EXPLANATION = 0.075
GRID_INSTANCE_RESIDUAL_MAX_INK_RATIO = 0.18
GRID_INSTANCE_RESIDUAL_MAX_EDGE_RATIO = 0.02
GRID_INSTANCE_RESIDUAL_MAX_AXIS_COVERAGE = 0.35
GRID_INSTANCE_MIN_GRID_COVERAGE = 0.68
GRID_INSTANCE_MAX_PEER_OVERLAP = 0.15
GRID_INSTANCE_SINGLE_CELL_COMPLETE_COVERAGE = 0.94
GRID_INSTANCE_SINGLE_CELL_MAX_AREA_RATIO = 1.28
GRID_SHAPE_INSTANCE_MIN_AXIS_RATIO = 0.65
GRID_SHAPE_INSTANCE_MAX_AXIS_RATIO = 1.20
GRID_SHAPE_INSTANCE_MIN_SUPPORTED_CELLS = 3
GRID_SHAPE_INSTANCE_MIN_COVERAGE = 0.50
GRID_SHAPE_INSTANCE_SCORE_WEIGHT = 0.36
OBJECT_LAYOUT_MIN_GRID_CELL_EXPLANATION = 0.25
OBJECT_ENVELOPE_MARGIN_RATIO = 0.06
PRIOR_TOPOLOGY_MIN_CURRENT_SUPPORT_RATIO = 0.50
PRIOR_TOPOLOGY_MIN_CHILD_CONTAINMENT = 0.78
PRIOR_TOPOLOGY_MIN_CHILD_AREA_RATIO = 0.25
PRIOR_TOPOLOGY_MAX_CHILD_AREA_RATIO = 0.72
PRIOR_TOPOLOGY_MIN_PRIOR_FILL_RATIO = 0.68
PRIOR_TOPOLOGY_MIN_SPLIT_PRIOR_RATIO = 0.50
SOURCE_BOUNDS_SEVERE_OVERFLOW_RATIO = 0.01

IRREGULAR_PRIMARY_MIN_AREA_RATIO = 0.045
IRREGULAR_PRIMARY_MAX_AREA_RATIO = 0.16
IRREGULAR_SATELLITE_MIN_AREA_RATIO = 0.006
IRREGULAR_SATELLITE_MAX_AREA_RATIO = 0.06
IRREGULAR_COMPONENT_MIN_FILL_RATIO = 0.12
IRREGULAR_PRIMARY_MIN_FILL_RATIO = 0.20
IRREGULAR_COMPONENT_MARGIN_RATIO = 0.06
IRREGULAR_SATELLITE_MIN_PRIMARY_AXIS_RATIO = 0.15
STRUCTURAL_ENVELOPE_MIN_AREA_RATIO = 0.035
STRUCTURAL_ENVELOPE_MAX_AREA_RATIO = 0.22
STRUCTURAL_ENVELOPE_MIN_FILL_RATIO = 0.04


@dataclass(frozen=True)
class LayoutHypothesis:
    detections: tuple[Detection, ...]
    score: float
    rows: int
    columns: int
    transform: str = "independent"
    signals: dict[str, float | int | str | list[float]] = field(default_factory=dict)
    rejected_reasons: tuple[str, ...] = ()
    object_references: tuple[Detection, ...] = ()
    search_regions: tuple[Detection, ...] = ()

    def as_dict(self) -> dict:
        return {
            "detector_version": DETECTOR_VERSION,
            "score": self.score,
            "rows": self.rows,
            "columns": self.columns,
            "transform": self.transform,
            "signals": self.signals,
            "rejected_reasons": list(self.rejected_reasons),
            "independent_object_references": [
                {"bounds": [float(d.x), float(d.y), float(d.width), float(d.height)],
                 "points": d.points, "evidence": list(d.evidence)} for d in self.object_references
            ],
        }


def _clamp_detection_to_source(item: Detection) -> Detection:
    """Clamp public bounds and polygon points without hiding material overflow."""
    original_right = item.x + item.width
    original_bottom = item.y + item.height
    left = min(1.0, max(0.0, item.x))
    top = min(1.0, max(0.0, item.y))
    right = min(1.0, max(0.0, original_right))
    bottom = min(1.0, max(0.0, original_bottom))
    clamped_points = (
        tuple(
            (
                min(1.0, max(0.0, point_x)),
                min(1.0, max(0.0, point_y)),
            )
            for point_x, point_y in item.points
        )
        if item.points is not None
        else None
    )
    coordinates = [item.x, item.y, original_right, original_bottom]
    if item.points is not None:
        coordinates.extend(coordinate for point in item.points for coordinate in point)
    outside_distance = max(
        0.0,
        *(-coordinate for coordinate in coordinates),
        *(coordinate - 1.0 for coordinate in coordinates),
    )
    changed = (
        left != item.x
        or top != item.y
        or right != original_right
        or bottom != original_bottom
        or clamped_points != item.points
    )
    if not changed:
        return item

    severe_overflow = outside_distance > SOURCE_BOUNDS_SEVERE_OVERFLOW_RATIO
    return replace(
        item,
        x=left,
        y=top,
        width=max(0.0, right - left),
        height=max(0.0, bottom - top),
        failure=item.failure or ("geometry_outside_source" if severe_overflow else None),
        points=clamped_points,
        evidence=tuple(
            dict.fromkeys(
                (
                    *item.evidence,
                    "source_bounds_clamped",
                    "source_edge_contact",
                    *(("source_bounds_severe_overflow",) if severe_overflow else ()),
                )
            )
        ),
    )


def _clamp_layout_to_source(layout: LayoutHypothesis) -> LayoutHypothesis:
    detections = tuple(_clamp_detection_to_source(item) for item in layout.detections)
    return layout if detections == layout.detections else replace(layout, detections=detections)


def _intersection_over_union(left: Detection, right: Detection) -> float:
    intersection_width = max(
        0.0, min(left.x + left.width, right.x + right.width) - max(left.x, right.x)
    )
    intersection_height = max(
        0.0, min(left.y + left.height, right.y + right.height) - max(left.y, right.y)
    )
    intersection = intersection_width * intersection_height
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union else 0.0


def _intersection_over_smaller(left: Detection, right: Detection) -> float:
    intersection_width = max(
        0.0, min(left.x + left.width, right.x + right.width) - max(left.x, right.x)
    )
    intersection_height = max(
        0.0, min(left.y + left.height, right.y + right.height) - max(left.y, right.y)
    )
    smaller = min(left.width * left.height, right.width * right.height)
    return intersection_width * intersection_height / smaller if smaller else 0.0


def _foreground_detections(image: np.ndarray) -> list[Detection]:
    """Recover sleeved cards whose pale/reflective borders do not form closed edges."""
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray < 205).astype(np.uint8) * 255
    kernel_size = max(9, round(min(width, height) * 0.004))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width * box_height >= width * height * 0.015:
            boxes.append((x, y, box_width, box_height))
    if not boxes:
        return []
    # Large connected components are commonly several clear holders touching
    # edge-to-edge.  Including those components in the median inflates the
    # expected card height and prevents them from ever being split.  Establish
    # the single-card dimensions from the smallest half of components instead;
    # genuine single cards are the lower-area observations while joined stacks
    # are necessarily larger.  Width is retained in scanner coordinates so a
    # portrait component is not accidentally used to split a landscape row.
    reference_count = max(1, (len(boxes) + 1) // 2)
    reference_boxes = sorted(boxes, key=lambda box: box[2] * box[3])[:reference_count]
    typical_width = float(np.median([box[2] for box in reference_boxes]))
    typical_height = float(np.median([box[3] for box in reference_boxes]))
    split_boxes: list[tuple[int, int, int, int]] = []
    for x, y, box_width, box_height in boxes:
        if box_height > typical_height * 1.6 and 0.65 <= box_width / typical_width <= 1.4:
            count = max(2, round(box_height / typical_height))
            boundaries = np.linspace(y, y + box_height, count + 1).astype(int)
            split_boxes.extend(
                (
                    x,
                    int(boundaries[index]),
                    box_width,
                    int(boundaries[index + 1] - boundaries[index]),
                )
                for index in range(count)
            )
        elif box_width > typical_width * 1.6 and 0.65 <= box_height / typical_height <= 1.4:
            count = max(2, round(box_width / typical_width))
            boundaries = np.linspace(x, x + box_width, count + 1).astype(int)
            split_boxes.extend(
                (
                    int(boundaries[index]),
                    y,
                    int(boundaries[index + 1] - boundaries[index]),
                    box_height,
                )
                for index in range(count)
            )
        else:
            split_boxes.append((x, y, box_width, box_height))
    found = []
    for x, y, box_width, box_height in split_boxes:
        ratio = max(box_width, box_height) / max(1, min(box_width, box_height))
        if not 1.2 <= ratio <= 1.85:
            continue
        clipped = x <= 2 or y <= 2 or x + box_width >= width - 2 or y + box_height >= height - 2
        found.append(
            Detection(
                x / width,
                y / height,
                box_width / width,
                box_height / height,
                0.0,
                0.88,
                "severely_clipped" if clipped else None,
                (
                    (x / width, y / height),
                    ((x + box_width) / width, y / height),
                    ((x + box_width) / width, (y + box_height) / height),
                    (x / width, (y + box_height) / height),
                ),
            )
        )
    return found


def _foreground_layout_is_complete(detections: list[Detection]) -> bool:
    """Recognize a complete irregular sheet before considering a grid fallback.

    A global grid is useful when touching holders collapse into one component,
    but it must not replace a full set of separated card-sized components. Mixed
    portrait/landscape sheets do not share row and column boundaries, so forcing
    them into equal cells necessarily cuts cards in half.
    """
    if not 4 <= len(detections) <= 20:
        return False
    areas = [item.width * item.height for item in detections]
    if sum(areas) < 0.45 or min(areas) <= 0:
        return False
    if max(areas) / min(areas) > 2.6:
        return False
    if any(
        _intersection_over_smaller(left, right) > 0.18
        for index, left in enumerate(detections)
        for right in detections[index + 1 :]
    ):
        return False
    return True


def _saturation_content_recall(
    image: np.ndarray,
    detections: list[Detection],
) -> float:
    """Measure how much independently visible colour content the boxes cover."""
    saturation = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1]
    content = saturation > 20
    content_count = int(np.count_nonzero(content))
    if content_count < image.shape[0] * image.shape[1] * 0.01:
        return 0.0
    covered = np.zeros(content.shape, dtype=bool)
    height, width = content.shape
    for item in detections:
        left = max(0, min(width, round(item.x * width)))
        top = max(0, min(height, round(item.y * height)))
        right = max(left, min(width, round((item.x + item.width) * width)))
        bottom = max(top, min(height, round((item.y + item.height) * height)))
        covered[top:bottom, left:right] = True
    return float(np.count_nonzero(content & covered) / content_count)


def _single_slab_mixed_layout_is_complete(
    image: np.ndarray,
    detections: list[Detection],
) -> bool:
    """Recognize one rotated graded slab among uniformly oriented raw cards.

    A full-bed capacity grid is unsafe for this layout: the slab commonly spans
    two raw-card rows, so both halves look occupied and become separate cards.
    A unique label association alone is not sufficient because ordinary cards
    can contain label-shaped artwork. Require complete, conflict-free content,
    at least three consistently oriented peers, and comparable physical area.
    """
    if not _foreground_layout_is_complete(detections):
        return False
    labelled = [item for item in detections if "grading_label_association" in item.evidence]
    peers = [item for item in detections if "grading_label_association" not in item.evidence]
    if len(labelled) != 1 or len(peers) < 3:
        return False

    image_height, image_width = image.shape[:2]

    def horizontal(item: Detection) -> bool:
        return item.width * image_width > item.height * image_height

    slab = labelled[0]
    if not all(horizontal(peer) != horizontal(slab) for peer in peers):
        return False
    slab_width = slab.width * image_width
    slab_height = slab.height * image_height
    slab_aspect = max(slab_width, slab_height) / max(1.0, min(slab_width, slab_height))
    if not 1.30 <= slab_aspect <= 2.10:
        return False
    peer_median_area = float(
        np.median([peer.width * image_width * peer.height * image_height for peer in peers])
    )
    slab_area = slab_width * slab_height
    area_ratio = slab_area / max(1.0, peer_median_area)
    return 0.65 <= area_ratio <= 1.65 and _saturation_content_recall(image, detections) >= 0.92


def _grid_fragments_detection(
    candidate: Detection,
    grid: list[Detection],
) -> bool:
    """Return true when a grid divides one candidate into balanced fragments."""
    candidate_area = candidate.width * candidate.height
    if candidate_area <= 0:
        return False
    shares = []
    for cell in grid:
        intersection_width = max(
            0.0,
            min(candidate.x + candidate.width, cell.x + cell.width) - max(candidate.x, cell.x),
        )
        intersection_height = max(
            0.0,
            min(candidate.y + candidate.height, cell.y + cell.height) - max(candidate.y, cell.y),
        )
        share = intersection_width * intersection_height / candidate_area
        if share >= 0.28:
            shares.append(share)
    return len(shares) == 2 and sum(shares) >= 0.85 and max(shares) <= 0.72


def _instance_route_family(item: Detection, *, foreground: bool = False) -> str:
    if foreground:
        return "foreground_component"
    if "saturation_content_island" in item.evidence:
        return "saturation_component"
    if "adaptive_threshold" in item.evidence:
        return "adaptive_threshold_component"
    if "otsu_threshold" in item.evidence:
        return "global_threshold_component"
    if "structural_edge_envelope" in item.evidence:
        return "structural_edge_component"
    if any(signal.startswith("threshold_") for signal in item.evidence):
        return "global_threshold_component"
    return "gradient_contour"


def _corroborated_current_instances(
    image: np.ndarray,
    foreground: list[Detection],
    references: list[Detection],
    *,
    cell_area: float,
    prefer_outer_envelope: bool = False,
) -> list[Detection]:
    """Build complete current-image instances before applying grid boundaries.

    Grid cells describe scanner capacity, while these observations come from
    independent two-dimensional pixel routes. A connected component is not
    trusted by itself: at least one other route must agree, and the resulting
    envelope must remain comparable to one physical grid cell. The cell-area
    gate rejects a component made by two touching cards without assuming a
    particular row, column, orientation, or final card count.
    """
    if cell_area <= 0:
        return []
    image_height, image_width = image.shape[:2]
    observations: list[tuple[Detection, str]] = [
        (item, _instance_route_family(item, foreground=True)) for item in foreground
    ]
    observations.extend((item, _instance_route_family(item)) for item in references)

    plausible: list[tuple[Detection, str]] = []
    for item, route in observations:
        area_ratio = item.width * item.height / cell_area
        pixel_width = item.width * image_width
        pixel_height = item.height * image_height
        aspect = max(pixel_width, pixel_height) / max(1.0, min(pixel_width, pixel_height))
        if (
            GRID_INSTANCE_MIN_OBSERVATION_CELL_AREA_RATIO
            <= area_ratio
            <= GRID_INSTANCE_MAX_CELL_AREA_RATIO
            and 1.12 <= aspect <= 1.95
        ):
            plausible.append((item, route))

    clusters: list[list[tuple[Detection, str]]] = []
    remaining = set(range(len(plausible)))
    while remaining:
        pending = [remaining.pop()]
        cluster_indices: set[int] = set()
        while pending:
            index = pending.pop()
            if index in cluster_indices:
                continue
            cluster_indices.add(index)
            candidate = plausible[index][0]
            neighbours = [
                other_index
                for other_index in remaining
                if _intersection_over_union(candidate, plausible[other_index][0])
                >= GRID_INSTANCE_MIN_CORROBORATION_IOU
            ]
            for other_index in neighbours:
                remaining.remove(other_index)
                pending.append(other_index)
        clusters.append([plausible[index] for index in cluster_indices])

    instances: list[Detection] = []
    for cluster in clusters:
        routes = sorted({route for _, route in cluster})
        if len(routes) < GRID_INSTANCE_MIN_ROUTE_FAMILIES:
            continue
        representative_candidates = [
            item
            for item, _ in cluster
            if item.width * item.height / cell_area >= GRID_INSTANCE_MIN_CELL_AREA_RATIO
        ]
        if not representative_candidates:
            continue

        # Select a route medoid near one physical cell, never the largest
        # envelope or a union. Intersection-over-smaller lets one two-card
        # component claim every nested single-card observation as support;
        # bidirectional IoU and per-route agreement explicitly prevent that.
        by_route = {
            route: [item for item, item_route in cluster if item_route == route] for route in routes
        }

        def representative_score(item: Detection) -> float:
            route_agreement = float(
                np.mean(
                    [
                        max(_intersection_over_union(item, peer) for peer in peers)
                        for peers in by_route.values()
                    ]
                )
            )
            cell_area_ratio = item.width * item.height / cell_area
            return route_agreement - 0.08 * abs(cell_area_ratio - 1.0)

        representative = max(
            representative_candidates,
            key=lambda item: (representative_score(item), item.width * item.height),
        )
        labelled_envelopes = [
            item
            for item in representative_candidates
            if "grading_label_association" in item.evidence
            and _intersection_over_union(representative, item) >= 0.55
        ]
        retained_labelled_envelope: Detection | None = None
        if labelled_envelopes:
            # A slab label is part of the physical object, not optional inner
            # artwork. Once an extent-consistent, independently corroborated
            # label association exists, retain its complete envelope even when
            # a smaller card-body contour is the cluster medoid.
            representative = max(
                labelled_envelopes,
                key=lambda item: item.width * item.height,
            )
            retained_labelled_envelope = representative

        def retains_label(candidate: Detection) -> bool:
            if retained_labelled_envelope is None:
                return True
            intersection_width = max(
                0.0,
                min(
                    candidate.x + candidate.width,
                    retained_labelled_envelope.x + retained_labelled_envelope.width,
                )
                - max(candidate.x, retained_labelled_envelope.x),
            )
            intersection_height = max(
                0.0,
                min(
                    candidate.y + candidate.height,
                    retained_labelled_envelope.y + retained_labelled_envelope.height,
                )
                - max(candidate.y, retained_labelled_envelope.y),
            )
            labelled_area = retained_labelled_envelope.width * retained_labelled_envelope.height
            return intersection_width * intersection_height / max(labelled_area, 1e-9) >= 0.95

        if prefer_outer_envelope:
            representative_area = representative.width * representative.height
            extent_consistent = [
                item
                for item in representative_candidates
                if _intersection_over_union(representative, item)
                >= GRID_INSTANCE_MIN_CORROBORATION_IOU
                and item.width * item.height <= representative_area * 1.22
                and 0.82 <= item.width / max(representative.width, 1e-9) <= 1.18
                and 0.82 <= item.height / max(representative.height, 1e-9) <= 1.18
                and retains_label(item)
            ]
            if extent_consistent:
                # The ordinary grid reconciliation deliberately uses a medoid
                # to avoid broad two-card components. Object-first output needs
                # the outer physical/content contour instead. Only a tightly
                # extent-consistent member may widen the envelope, preserving
                # the anti-merge guarantee from the original cluster.
                representative = max(
                    extent_consistent,
                    key=lambda item: item.width * item.height,
                )
        direct_routes = {
            route
            for item, route in cluster
            if _intersection_over_union(representative, item) >= GRID_INSTANCE_MIN_CORROBORATION_IOU
        }
        if len(direct_routes) < GRID_INSTANCE_MIN_ROUTE_FAMILIES:
            continue
        confidence = min(
            0.94,
            max(
                representative.confidence,
                0.82 + 0.025 * len(direct_routes) + 0.005 * min(len(cluster), 6),
            ),
        )
        left = max(0.0, representative.x)
        top = max(0.0, representative.y)
        right = min(1.0, representative.x + representative.width)
        bottom = min(1.0, representative.y + representative.height)
        if right <= left or bottom <= top:
            continue
        clamped_points = (
            tuple(
                (
                    min(1.0, max(0.0, point_x)),
                    min(1.0, max(0.0, point_y)),
                )
                for point_x, point_y in representative.points
            )
            if representative.points
            else ((left, top), (right, top), (right, bottom), (left, bottom))
        )
        points_were_clamped = (
            representative.points is not None and clamped_points != representative.points
        )
        source_bounds_clamped = (
            left != representative.x
            or top != representative.y
            or right != representative.x + representative.width
            or bottom != representative.y + representative.height
            or points_were_clamped
        )
        inherited_cluster_evidence = {
            signal
            for item, _ in cluster
            for signal in item.evidence
            if signal in {"mixed_format_instance", "saturation_content_island"}
        }
        representative_retains_label = retained_labelled_envelope is not None and retains_label(
            representative
        )
        if "grading_label_association" in representative.evidence or representative_retains_label:
            # Geometry provenance must travel with the geometry that actually
            # contains it. A nearby inner/broader observation must never gain a
            # grading-label guarantee merely by belonging to the same cluster.
            inherited_cluster_evidence.add("grading_label_association")
        instances.append(
            Detection(
                left,
                top,
                right - left,
                bottom - top,
                representative.angle,
                round(confidence, 4),
                representative.failure,
                clamped_points,
                tuple(
                    dict.fromkeys(
                        (
                            *representative.evidence,
                            *sorted(inherited_cluster_evidence),
                            "current_image_instance",
                            "multi_route_instance_corroboration",
                            *(
                                ("source_bounds_clamped", "source_edge_contact")
                                if source_bounds_clamped
                                else ()
                            ),
                            f"instance_route_support:{'+'.join(sorted(direct_routes))}",
                            f"instance_observation_count:{len(cluster)}",
                        )
                    )
                ),
            )
        )

    # Do not allow broad, uncertain envelopes to turn two independently
    # observed neighbouring cards into one region. In the rare event of a
    # conflict, retain the more strongly corroborated instance and leave the
    # other position to the original grid candidate.
    selected: list[Detection] = []
    for candidate in sorted(instances, key=lambda item: item.confidence, reverse=True):
        if all(
            _intersection_over_smaller(candidate, peer) <= GRID_INSTANCE_MAX_PEER_OVERLAP
            for peer in selected
        ):
            selected.append(candidate)
    if prefer_outer_envelope:
        selected = _complete_multi_panel_object_envelopes(
            image,
            selected,
            observations,
            cell_area=cell_area,
        )
    return sorted(selected, key=lambda item: (item.y, item.x))


def _complete_multi_panel_object_envelopes(
    image: np.ndarray,
    instances: list[Detection],
    observations: list[tuple[Detection, str]],
    *,
    cell_area: float,
) -> list[Detection]:
    """Restore holder/label panels around independently found card bodies.

    Graded holders commonly expose a dark or colourful card-body rectangle and
    a paler grading label as separate threshold components. The crop must cover
    both, but containment alone is unsafe because one broad component can span
    two neighbouring cards. An outer envelope therefore needs either agreement
    from two route families, or a geometrically aligned satellite whose union
    is independently supported by a structural holder-edge envelope. Any
    candidate that also contains a peer instance is rejected.
    """
    if not instances:
        return instances
    image_height, image_width = image.shape[:2]

    def area(item: Detection) -> float:
        return item.width * item.height

    def intersection_area(left: Detection, right: Detection) -> float:
        return max(
            0.0,
            min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
        ) * max(
            0.0,
            min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
        )

    def contains_peer(candidate: Detection, own_index: int) -> bool:
        return any(
            index != own_index and intersection_area(candidate, peer) / max(area(peer), 1e-9) > 0.25
            for index, peer in enumerate(instances)
        )

    def physical_aspect(candidate: Detection) -> float:
        pixel_width = candidate.width * image_width
        pixel_height = candidate.height * image_height
        return max(pixel_width, pixel_height) / max(1.0, min(pixel_width, pixel_height))

    def envelope(
        item: Detection,
        left: float,
        top: float,
        right: float,
        bottom: float,
        *evidence: str,
        failure: str | None = None,
    ) -> Detection:
        return replace(
            item,
            x=max(0.0, left),
            y=max(0.0, top),
            width=min(1.0, right) - max(0.0, left),
            height=min(1.0, bottom) - max(0.0, top),
            failure=failure or item.failure,
            points=(
                (max(0.0, left), max(0.0, top)),
                (min(1.0, right), max(0.0, top)),
                (min(1.0, right), min(1.0, bottom)),
                (max(0.0, left), min(1.0, bottom)),
            ),
            evidence=tuple(dict.fromkeys((*item.evidence, *evidence))),
        )

    expanded: list[Detection] = []
    for index, item in enumerate(instances):
        item_area = area(item)
        if "grading_label_association" in item.evidence:
            # This geometry was already built from a body plus an explicit
            # label observation. A larger ordinary threshold/edge envelope is
            # not allowed to replace it: equal-area shifts can otherwise drop
            # the label while inheriting the label evidence from the cluster.
            expanded.append(item)
            continue
        direct: list[tuple[Detection, str]] = []
        for candidate, route in observations:
            candidate_area = area(candidate)
            containment = intersection_area(item, candidate) / max(item_area, 1e-9)
            if not (
                containment >= 0.84
                and 1.04 <= candidate_area / max(item_area, 1e-9) <= 1.85
                and candidate_area / max(cell_area, 1e-9) <= 1.65
                and 1.12 <= physical_aspect(candidate) <= 2.10
                and not contains_peer(candidate, index)
            ):
                continue
            has_independent_support = any(
                other_route != route
                and _intersection_over_union(candidate, other) >= 0.70
                and 0.80 <= candidate.width / max(other.width, 1e-9) <= 1.20
                and 0.80 <= candidate.height / max(other.height, 1e-9) <= 1.20
                for other, other_route in observations
            )
            if has_independent_support or "grading_label_association" in candidate.evidence:
                direct.append((candidate, route))

        completed = item
        if direct:
            outer, _ = max(direct, key=lambda pair: area(pair[0]))
            completed = envelope(
                item,
                min(item.x, outer.x),
                min(item.y, outer.y),
                max(item.x + item.width, outer.x + outer.width),
                max(item.y + item.height, outer.y + outer.height),
                "multi_panel_object_envelope",
                "independent_outer_envelope_support",
                *(
                    ("grading_label_association",)
                    if "grading_label_association" in outer.evidence
                    else ()
                ),
                failure=outer.failure,
            )

        # If no complete outer rectangle exists, join one aligned label panel
        # only when the proposed union is also visible in the structural edge
        # route. This recovers labels at either end without merging two cards
        # merely because a threshold component happens to touch both.
        satellite_proposals: list[tuple[float, Detection]] = []
        horizontal = item.width * image_width >= item.height * image_height
        short_pixels = min(item.width * image_width, item.height * image_height)
        for satellite, _ in observations:
            satellite_area = area(satellite)
            intersection = intersection_area(item, satellite)
            intersection_over_satellite = intersection / max(satellite_area, 1e-9)
            area_ratio = satellite_area / max(item_area, 1e-9)
            if (
                not 0.15 <= area_ratio <= 1.40
                or intersection_over_satellite > 0.82
                or (area_ratio > 0.90 and intersection_over_satellite < 0.45)
            ):
                continue
            if horizontal:
                perpendicular_overlap = max(
                    0.0,
                    min(item.y + item.height, satellite.y + satellite.height)
                    - max(item.y, satellite.y),
                ) / max(min(item.height, satellite.height), 1e-9)
                extends = (
                    satellite.x < item.x - item.width * 0.02
                    or satellite.x + satellite.width > item.x + item.width + item.width * 0.02
                )
                gap_pixels = (
                    max(
                        0.0,
                        max(item.x, satellite.x)
                        - min(item.x + item.width, satellite.x + satellite.width),
                    )
                    * image_width
                )
            else:
                perpendicular_overlap = max(
                    0.0,
                    min(item.x + item.width, satellite.x + satellite.width)
                    - max(item.x, satellite.x),
                ) / max(min(item.width, satellite.width), 1e-9)
                extends = (
                    satellite.y < item.y - item.height * 0.02
                    or satellite.y + satellite.height > item.y + item.height + item.height * 0.02
                )
                gap_pixels = (
                    max(
                        0.0,
                        max(item.y, satellite.y)
                        - min(item.y + item.height, satellite.y + satellite.height),
                    )
                    * image_height
                )
            if not (
                perpendicular_overlap >= 0.65 and extends and gap_pixels <= short_pixels * 0.35
            ):
                continue
            left = min(item.x, satellite.x)
            top = min(item.y, satellite.y)
            right = max(item.x + item.width, satellite.x + satellite.width)
            bottom = max(item.y + item.height, satellite.y + satellite.height)
            proposed = envelope(
                item,
                left,
                top,
                right,
                bottom,
                "multi_panel_object_envelope",
                "grading_label_association",
                "structural_satellite_association",
                failure=satellite.failure,
            )
            if (
                area(proposed) / max(cell_area, 1e-9) > 1.65
                or not 1.20 <= physical_aspect(proposed) <= 2.15
                or contains_peer(proposed, index)
            ):
                continue
            structural_support = max(
                (
                    _intersection_over_union(proposed, support)
                    for support, route in observations
                    if route == "structural_edge_component"
                    and intersection_area(item, support) / max(item_area, 1e-9) >= 0.75
                ),
                default=0.0,
            )
            if structural_support >= 0.50:
                satellite_proposals.append((structural_support, proposed))
        if satellite_proposals:
            _, satellite_envelope = max(
                satellite_proposals,
                key=lambda pair: (pair[0], area(pair[1])),
            )
            combined = envelope(
                completed,
                min(completed.x, satellite_envelope.x),
                min(completed.y, satellite_envelope.y),
                max(
                    completed.x + completed.width,
                    satellite_envelope.x + satellite_envelope.width,
                ),
                max(
                    completed.y + completed.height,
                    satellite_envelope.y + satellite_envelope.height,
                ),
                *satellite_envelope.evidence,
                failure=satellite_envelope.failure,
            )
            if (
                area(combined) > area(completed)
                and area(combined) / max(cell_area, 1e-9) <= 1.65
                and 1.20 <= physical_aspect(combined) <= 2.15
                and not contains_peer(combined, index)
            ):
                completed = combined
        expanded.append(completed)

    # Completion is an all-object transaction. If two proposed envelopes now
    # overlap materially, revert only the conflicting proposals to their
    # independently corroborated cores; the residual-content audit can then
    # decline object-first layout rather than silently losing a neighbour.
    result = list(expanded)
    for index, candidate in enumerate(expanded):
        if any(
            index != other_index
            and _intersection_over_smaller(candidate, other) > GRID_INSTANCE_MAX_PEER_OVERLAP
            for other_index, other in enumerate(expanded)
        ):
            result[index] = instances[index]
    return result


def _grid_cell_unexplained_metrics(
    image: np.ndarray,
    cell: Detection,
    instances: list[Detection],
) -> dict[str, float]:
    """Measure cell content that no corroborated object already explains.

    A shifted card can legitimately leave only a small fragment in a capacity
    cell, but that is not permission to remove a different card occupying the
    rest of the cell. Measure ink and edges after masking every independently
    corroborated instance. Keeping the denominator at the full cell size makes
    the thresholds describe a material residual body rather than dust or a
    clear-holder edge. Edges are computed before masking so rectangle masks do
    not manufacture artificial boundaries.
    """
    image_height, image_width = image.shape[:2]
    x1 = max(0, int(round(cell.x * image_width)))
    y1 = max(0, int(round(cell.y * image_height)))
    x2 = min(image_width, int(round((cell.x + cell.width) * image_width)))
    y2 = min(image_height, int(round((cell.y + cell.height) * image_height)))
    region = image[y1:y2, x1:x2]
    if region.size == 0:
        return {
            "ink_ratio": 0.0,
            "edge_ratio": 0.0,
            "ink_row_coverage": 0.0,
            "ink_column_coverage": 0.0,
        }

    inset_y = round(region.shape[0] * GRID_OCCUPANCY_CORE_INSET_RATIO)
    inset_x = round(region.shape[1] * GRID_OCCUPANCY_CORE_INSET_RATIO)
    core_x1 = x1 + inset_x
    core_y1 = y1 + inset_y
    core_x2 = x2 - inset_x
    core_y2 = y2 - inset_y
    core = image[core_y1:core_y2, core_x1:core_x2]
    if core.size == 0:
        return {
            "ink_ratio": 0.0,
            "edge_ratio": 0.0,
            "ink_row_coverage": 0.0,
            "ink_column_coverage": 0.0,
        }

    gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)[:, :, 1]
    ink = (saturation > 22) | (gray < 190)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 30, 90) > 0
    unexplained = np.ones(core.shape[:2], dtype=bool)
    for instance in instances:
        instance_x1 = max(core_x1, int(round(instance.x * image_width)))
        instance_y1 = max(core_y1, int(round(instance.y * image_height)))
        instance_x2 = min(
            core_x2,
            int(round((instance.x + instance.width) * image_width)),
        )
        instance_y2 = min(
            core_y2,
            int(round((instance.y + instance.height) * image_height)),
        )
        if instance_x2 <= instance_x1 or instance_y2 <= instance_y1:
            continue
        unexplained[
            instance_y1 - core_y1 : instance_y2 - core_y1,
            instance_x1 - core_x1 : instance_x2 - core_x1,
        ] = False

    ink &= unexplained
    edges &= unexplained
    return {
        "ink_ratio": float(np.mean(ink)),
        "edge_ratio": float(np.mean(edges)),
        "ink_row_coverage": float(np.mean(np.mean(ink, axis=1) > 0.02)),
        "ink_column_coverage": float(np.mean(np.mean(ink, axis=0) > 0.02)),
    }


def _reconcile_grid_with_current_instances(
    image: np.ndarray,
    grid: list[Detection],
    instances: list[Detection],
) -> list[Detection]:
    """Replace grid crops with corroborated current-image object boundaries.

    A single complete instance may explain one clipped grid region or several
    fragments when it crosses a capacity seam. Each grid region is assigned to
    at most one best-fitting instance, so a shifted card cannot consume a cell
    that is better explained by its neighbour. Unexplained grid regions remain
    available for sparse/low-contrast cards that lack multi-route evidence.
    """
    if not grid or not instances:
        return grid

    assignments: dict[int, list[int]] = {index: [] for index in range(len(instances))}
    for grid_index, cell in enumerate(grid):
        scores = sorted(
            (
                (_intersection_over_smaller(cell, instance), instance_index)
                for instance_index, instance in enumerate(instances)
            ),
            reverse=True,
        )
        best_score, best_index = scores[0]
        if best_score < GRID_INSTANCE_MIN_CELL_EXPLANATION:
            continue
        # An allocation region equally shared by two complete instances is a
        # true neighbour boundary, not permission for either one to delete it.
        if (
            len(scores) > 1
            and scores[1][0] >= GRID_INSTANCE_MIN_CELL_EXPLANATION
            and scores[1][0] / max(best_score, 1e-9) >= 0.70
        ):
            continue
        unexplained = _grid_cell_unexplained_metrics(image, cell, instances)
        # The overlap may be a genuine fragment entering otherwise blank
        # capacity, or it may cover only one end of a different card. Do not
        # delete the grid region while a card-sized residual remains after all
        # independently corroborated objects have been masked out.
        if (
            unexplained["ink_ratio"] >= GRID_INSTANCE_RESIDUAL_MAX_INK_RATIO
            or unexplained["edge_ratio"] >= GRID_INSTANCE_RESIDUAL_MAX_EDGE_RATIO
            or min(
                unexplained["ink_row_coverage"],
                unexplained["ink_column_coverage"],
            )
            >= GRID_INSTANCE_RESIDUAL_MAX_AXIS_COVERAGE
        ):
            continue
        assignments[best_index].append(grid_index)

    viable: dict[int, tuple[list[int], float]] = {}
    for instance_index, instance in enumerate(instances):
        grid_indices = assignments[instance_index]
        if not grid_indices:
            continue
        instance_area = instance.width * instance.height
        covered_area = 0.0
        for grid_index in grid_indices:
            cell = grid[grid_index]
            intersection_width = max(
                0.0,
                min(instance.x + instance.width, cell.x + cell.width) - max(instance.x, cell.x),
            )
            intersection_height = max(
                0.0,
                min(instance.y + instance.height, cell.y + cell.height) - max(instance.y, cell.y),
            )
            covered_area += intersection_width * intersection_height
        coverage = covered_area / max(instance_area, 1e-9)
        if coverage < GRID_INSTANCE_MIN_GRID_COVERAGE:
            continue
        if len(grid_indices) == 1:
            cell = grid[grid_indices[0]]
            cell_area_ratio = cell.width * cell.height / max(instance_area, 1e-9)
            strong_grid_envelope = any(
                signal in {"row_local_body_recovery", "ink_extent_refinement"}
                for signal in cell.evidence
            )
            # A complete row-local/grid envelope that already contains the
            # corroborated object is safer than a smaller inner artwork box.
            # Replace one region only when the instance proves clipping beyond
            # that region, or when the region is broad enough to absorb a peer.
            if coverage >= GRID_INSTANCE_SINGLE_CELL_COMPLETE_COVERAGE and (
                strong_grid_envelope or cell_area_ratio <= GRID_INSTANCE_SINGLE_CELL_MAX_AREA_RATIO
            ):
                continue
        viable[instance_index] = (grid_indices, coverage)

    # Reconciliation is transactional: replacing one or more grid regions is
    # allowed only when the resulting instance does not overlap any grid region
    # that will remain. Re-evaluate after every rejection so a failed instance
    # cannot make another replacement appear safe by temporarily claiming its
    # neighbour. This preserves a low-contrast grid card when an adjacent
    # envelope is too broad.
    accepted_indices = set(viable)
    while accepted_indices:
        claimed_grid_indices = {
            grid_index
            for instance_index in accepted_indices
            for grid_index in viable[instance_index][0]
        }
        rejected = {
            instance_index
            for instance_index in accepted_indices
            if any(
                grid_index not in claimed_grid_indices
                and _intersection_over_smaller(instances[instance_index], cell)
                > GRID_INSTANCE_MAX_PEER_OVERLAP
                for grid_index, cell in enumerate(grid)
            )
        }
        if not rejected:
            break
        accepted_indices -= rejected

    accepted_instances: list[Detection] = []
    replaced_grid_indices: set[int] = set()
    for instance_index in sorted(accepted_indices):
        instance = instances[instance_index]
        grid_indices, coverage = viable[instance_index]
        accepted_instances.append(
            Detection(
                instance.x,
                instance.y,
                instance.width,
                instance.height,
                instance.angle,
                instance.confidence,
                instance.failure,
                instance.points,
                (
                    *instance.evidence,
                    "current_instance_grid_reconciliation",
                    "grid_fragment_replacement",
                    f"replaced_grid_regions:{len(grid_indices)}",
                    f"grid_instance_coverage:{coverage:.3f}",
                ),
            )
        )
        replaced_grid_indices.update(grid_indices)

    reconciled = [item for index, item in enumerate(grid) if index not in replaced_grid_indices]
    reconciled.extend(accepted_instances)
    return sorted(reconciled, key=lambda item: (item.y, item.x))


def _component_intersection_over_smaller(left: dict, right: dict) -> float:
    intersection_width = max(
        0,
        min(left["x"] + left["width"], right["x"] + right["width"]) - max(left["x"], right["x"]),
    )
    intersection_height = max(
        0,
        min(left["y"] + left["height"], right["y"] + right["height"]) - max(left["y"], right["y"]),
    )
    smaller = min(left["width"] * left["height"], right["width"] * right["height"])
    return intersection_width * intersection_height / smaller if smaller else 0.0


def _structural_envelope_detections(image: np.ndarray) -> list[Detection]:
    """Find holder-sized rectangles by linking nearby physical edge segments.

    Transparent slabs often leave the card body and grading label as separate
    threshold islands, while the holder rim is visible only as short edge
    fragments. A very small, resolution-relative dilation reconnects those rim
    fragments without crossing the normal gap between neighbouring objects.
    Multiple scales are observations from one structural route, not votes from
    independent detectors; downstream corroboration still requires a second
    pixel route.
    """
    image_height, image_width = image.shape[:2]
    image_area = image_height * image_width
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 25, 75)
    short_side = min(image_height, image_width)
    kernel_sizes = {max(3, round(short_side * ratio)) | 1 for ratio in (0.002, 0.004, 0.006)}
    candidates: list[Detection] = []
    for kernel_size in sorted(kernel_sizes):
        connected_edges = cv2.dilate(
            edges,
            cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size)),
        )
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            (connected_edges > 0).astype(np.uint8), connectivity=8
        )
        for x, y, box_width, box_height, pixel_count in stats[1:count]:
            box_area = box_width * box_height
            area_ratio = box_area / image_area
            fill_ratio = pixel_count / max(1, box_area)
            aspect = max(box_width, box_height) / max(1, min(box_width, box_height))
            if not (
                STRUCTURAL_ENVELOPE_MIN_AREA_RATIO
                <= area_ratio
                <= STRUCTURAL_ENVELOPE_MAX_AREA_RATIO
                and STRUCTURAL_ENVELOPE_MIN_FILL_RATIO <= fill_ratio
                and 1.12 <= aspect <= 2.10
            ):
                continue
            left = x / image_width
            top = y / image_height
            right = (x + box_width) / image_width
            bottom = (y + box_height) / image_height
            clipped = (
                x <= 2
                or y <= 2
                or x + box_width >= image_width - 2
                or y + box_height >= image_height - 2
            )
            candidates.append(
                Detection(
                    left,
                    top,
                    right - left,
                    bottom - top,
                    0.0,
                    0.72,
                    "source_edge_contact" if clipped else None,
                    ((left, top), (right, top), (right, bottom), (left, bottom)),
                    (
                        "structural_edge_envelope",
                        f"structural_edge_scale:{kernel_size}",
                    ),
                )
            )
    return candidates


def _irregular_slab_detections(image: np.ndarray) -> list[Detection]:
    """Recover mixed raw-card and graded-slab layouts from content islands.

    Clear holders on a white scanner bed are often invisible while their card
    artwork and grading label remain strongly saturated. A global foreground
    close joins adjacent holders into one whole-bed component; instead, this
    route finds the large card-art islands independently and associates at most
    one nearby, collinear label island with each card. The one-label rule is
    important when vertically stacked slabs have labels facing each other.
    """
    height, width = image.shape[:2]
    saturation = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1]
    image_area = width * height
    components: list[dict] = []
    for threshold in (20, 35):
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            (saturation > threshold).astype(np.uint8), connectivity=8
        )
        for x, y, box_width, box_height, pixel_count in stats[1:count]:
            box_area = box_width * box_height
            area_ratio = box_area / image_area
            fill_ratio = pixel_count / max(1, box_area)
            if area_ratio < 0.002 or fill_ratio < IRREGULAR_COMPONENT_MIN_FILL_RATIO:
                continue
            components.append(
                {
                    "x": int(x),
                    "y": int(y),
                    "width": int(box_width),
                    "height": int(box_height),
                    "area_ratio": float(area_ratio),
                    "fill_ratio": float(fill_ratio),
                    "threshold": threshold,
                }
            )

    # The lower saturation pass recovers silver/monochrome cards; the higher
    # pass separates colourful cards from grey scanner rails. Consolidate the
    # near-identical observations before label association.
    unique: list[dict] = []
    for component in sorted(
        components,
        key=lambda item: (item["fill_ratio"], item["threshold"]),
        reverse=True,
    ):
        duplicate = any(
            _component_intersection_over_smaller(component, existing) >= 0.85
            and 0.72 <= component["width"] / existing["width"] <= 1.40
            and 0.72 <= component["height"] / existing["height"] <= 1.40
            for existing in unique
        )
        if not duplicate:
            unique.append(component)

    primary = []
    for component in unique:
        short, long = sorted((component["width"], component["height"]))
        ratio = long / max(1, short)
        if (
            IRREGULAR_PRIMARY_MIN_AREA_RATIO
            <= component["area_ratio"]
            <= IRREGULAR_PRIMARY_MAX_AREA_RATIO
            and IRREGULAR_PRIMARY_MIN_FILL_RATIO <= component["fill_ratio"]
            and 1.15 <= ratio <= 1.85
        ):
            primary.append(component)

    detections: list[Detection] = []
    for card in primary:
        horizontal = card["width"] > card["height"]
        compatible: list[tuple[float, float, dict]] = []
        for candidate in unique:
            if candidate is card or not (
                IRREGULAR_SATELLITE_MIN_AREA_RATIO
                <= candidate["area_ratio"]
                <= IRREGULAR_SATELLITE_MAX_AREA_RATIO
            ):
                continue
            # A label must extend the card envelope, rather than being a logo
            # or printed panel contained inside the artwork.
            if _component_intersection_over_smaller(card, candidate) > 0.35:
                continue
            if horizontal:
                overlap = max(
                    0,
                    min(
                        card["y"] + card["height"],
                        candidate["y"] + candidate["height"],
                    )
                    - max(card["y"], candidate["y"]),
                ) / min(card["height"], candidate["height"])
                gap = max(
                    0,
                    max(card["x"], candidate["x"])
                    - min(
                        card["x"] + card["width"],
                        candidate["x"] + candidate["width"],
                    ),
                )
                extends = (
                    candidate["x"] < card["x"] - card["width"] * 0.02
                    or candidate["x"] + candidate["width"] > card["x"] + card["width"] * 1.02
                )
                is_compatible = (
                    extends
                    and overlap >= 0.65
                    and gap <= card["height"] * 0.42
                    and candidate["width"]
                    >= card["width"] * IRREGULAR_SATELLITE_MIN_PRIMARY_AXIS_RATIO
                    and candidate["width"] <= card["width"] * 0.55
                    and candidate["height"] >= card["height"] * 0.60
                )
            else:
                overlap = max(
                    0,
                    min(
                        card["x"] + card["width"],
                        candidate["x"] + candidate["width"],
                    )
                    - max(card["x"], candidate["x"]),
                ) / min(card["width"], candidate["width"])
                gap = max(
                    0,
                    max(card["y"], candidate["y"])
                    - min(
                        card["y"] + card["height"],
                        candidate["y"] + candidate["height"],
                    ),
                )
                extends = (
                    candidate["y"] < card["y"] - card["height"] * 0.02
                    or candidate["y"] + candidate["height"] > card["y"] + card["height"] * 1.02
                )
                is_compatible = (
                    extends
                    and overlap >= 0.65
                    and gap <= card["width"] * 0.42
                    and candidate["height"] <= card["height"] * 0.55
                    and candidate["height"]
                    >= card["height"] * IRREGULAR_SATELLITE_MIN_PRIMARY_AXIS_RATIO
                    and candidate["width"] >= card["width"] * 0.60
                )
            if is_compatible:
                compatible.append((float(gap), -float(overlap), candidate))

        label = min(compatible, key=lambda item: (item[0], item[1]))[2] if compatible else None
        members = [card, *([label] if label is not None else [])]
        left = min(item["x"] for item in members)
        top = min(item["y"] for item in members)
        right = max(item["x"] + item["width"] for item in members)
        bottom = max(item["y"] + item["height"] for item in members)
        margin = round(min(card["width"], card["height"]) * IRREGULAR_COMPONENT_MARGIN_RATIO)
        left = max(0, left - margin)
        top = max(0, top - margin)
        right = min(width, right + margin)
        bottom = min(height, bottom + margin)
        clipped = left <= 2 or top <= 2 or right >= width - 2 or bottom >= height - 2
        evidence = ["saturation_content_island", "mixed_format_instance"]
        if label is not None:
            evidence.append("grading_label_association")
        detections.append(
            Detection(
                left / width,
                top / height,
                (right - left) / width,
                (bottom - top) / height,
                0.0,
                0.90 if label is not None else 0.84,
                "source_edge_contact" if clipped else None,
                (
                    (left / width, top / height),
                    (right / width, top / height),
                    (right / width, bottom / height),
                    (left / width, bottom / height),
                ),
                tuple(evidence),
            )
        )
    detections.sort(key=lambda item: (item.y, item.x))
    return _complete_aligned_slab_envelopes(detections)


def _complete_aligned_slab_envelopes(
    detections: list[Detection],
) -> list[Detection]:
    """Restore a pale grading label from neighbouring aligned slab envelopes.

    A white/grey label can disappear from the saturation mask even though the
    colourful card area remains visible. When at least two independently
    associated slabs establish a shared outer edge, extend only an unlabeled,
    collinear detection whose opposite edge already agrees with that envelope.
    """
    labelled = [item for item in detections if "grading_label_association" in item.evidence]
    if len(labelled) < 2:
        return detections

    horizontal = sum(item.width > item.height for item in labelled) >= len(labelled) / 2
    same_orientation = [item for item in labelled if (item.width > item.height) == horizontal]
    if len(same_orientation) < 2:
        return detections

    if horizontal:
        outer_start = float(np.median([item.x for item in same_orientation]))
        outer_end = float(np.median([item.x + item.width for item in same_orientation]))
    else:
        outer_start = float(np.median([item.y for item in same_orientation]))
        outer_end = float(np.median([item.y + item.height for item in same_orientation]))
    envelope_size = outer_end - outer_start
    if envelope_size <= 0:
        return detections

    completed: list[Detection] = []
    for item in detections:
        if "grading_label_association" in item.evidence or (item.width > item.height) != horizontal:
            completed.append(item)
            continue
        start = item.x if horizontal else item.y
        end = item.x + item.width if horizontal else item.y + item.height
        agrees_at_end = abs(end - outer_end) <= envelope_size * 0.08
        agrees_at_start = abs(start - outer_start) <= envelope_size * 0.08
        missing_start = start - outer_start >= envelope_size * 0.10
        missing_end = outer_end - end >= envelope_size * 0.10
        if not ((agrees_at_end and missing_start) or (agrees_at_start and missing_end)):
            completed.append(item)
            continue

        new_start = outer_start if agrees_at_end and missing_start else start
        new_end = outer_end if agrees_at_start and missing_end else end
        if horizontal:
            x, y = new_start, item.y
            width, height = new_end - new_start, item.height
        else:
            x, y = item.x, new_start
            width, height = item.width, new_end - new_start
        completed.append(
            Detection(
                x,
                y,
                width,
                height,
                item.angle,
                min(item.confidence, 0.82),
                item.failure,
                (
                    (x, y),
                    (x + width, y),
                    (x + width, y + height),
                    (x, y + height),
                ),
                (*item.evidence, "aligned_slab_envelope"),
            )
        )
    return completed


def _grid_cell_occupancy(
    image: np.ndarray,
    x: float,
    y: float,
    right: float,
    bottom: float,
) -> tuple[bool, dict[str, float]]:
    """Distinguish an occupied grid slot from exposed scanner-bed space.

    Grid boundaries can contain a narrow edge from the adjacent top-loader, so
    measurements use the central 80% of the slot. A slot is discarded only
    when four independent signals agree that it is bright, nearly uniform,
    almost ink-free, and structurally empty. This intentionally keeps pale card
    backs when their text, logos, or borders provide either ink or edge energy.
    """
    image_height, image_width = image.shape[:2]
    x1 = max(0, int(round(x * image_width)))
    y1 = max(0, int(round(y * image_height)))
    x2 = min(image_width, int(round(right * image_width)))
    y2 = min(image_height, int(round(bottom * image_height)))
    cell = image[y1:y2, x1:x2]
    if cell.size == 0:
        return False, {
            "ink_ratio": 0.0,
            "edge_ratio": 0.0,
            "mean_luma": 255.0,
            "luma_stddev": 0.0,
        }

    inset_y = round(cell.shape[0] * GRID_OCCUPANCY_CORE_INSET_RATIO)
    inset_x = round(cell.shape[1] * GRID_OCCUPANCY_CORE_INSET_RATIO)
    core = cell[
        inset_y : max(inset_y + 1, cell.shape[0] - inset_y),
        inset_x : max(inset_x + 1, cell.shape[1] - inset_x),
    ]
    gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)[:, :, 1]
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 30, 90)
    metrics = {
        "ink_ratio": float(np.mean((saturation > 22) | (gray < 190))),
        "edge_ratio": float(np.mean(edges > 0)),
        "mean_luma": float(np.mean(gray)),
        "luma_stddev": float(np.std(gray)),
        "mean_saturation": float(np.mean(saturation)),
        "saturation_stddev": float(np.std(saturation)),
    }
    ink = (saturation > 22) | (gray < 190)
    metrics["ink_row_coverage"] = float(np.mean(np.mean(ink, axis=1) > 0.02))
    metrics["ink_column_coverage"] = float(np.mean(np.mean(ink, axis=0) > 0.02))
    uniformly_empty = (
        metrics["mean_luma"] > GRID_EMPTY_MIN_MEAN_LUMA
        and metrics["ink_ratio"] < GRID_EMPTY_MAX_INK_RATIO
        and metrics["edge_ratio"] < GRID_EMPTY_MAX_EDGE_RATIO
        and metrics["luma_stddev"] < GRID_EMPTY_MAX_LUMA_STDDEV
    )
    # A card from the preceding row can protrude a few pixels into an otherwise
    # empty allocation cell. That produces enough ink and one strong horizontal
    # edge to defeat the uniform-empty rule, but the signal occupies only one
    # axis and has no card-sized body. Pale printed card backs remain occupied
    # because their text/borders create distributed structure or more edge
    # energy across the cell.
    trailing_neighbour_sliver = (
        metrics["mean_luma"] > 200
        and metrics["ink_ratio"] > GRID_EMPTY_MAX_INK_RATIO
        and metrics["ink_ratio"] < GRID_TRAILING_SLIVER_MAX_INK_RATIO
        and metrics["edge_ratio"] < GRID_TRAILING_SLIVER_MAX_EDGE_RATIO
        and min(metrics["ink_row_coverage"], metrics["ink_column_coverage"])
        < GRID_TRAILING_SLIVER_MAX_AXIS_COVERAGE
    )
    # Do not classify a darker, uniform region as empty in isolation: it may be
    # a pale card back or an unprinted holder. Exposure-aware empty-bed evidence
    # is safe only in the object-layout transaction, after complete envelopes
    # account for all surrounding structure.
    confidently_empty = uniformly_empty or trailing_neighbour_sliver
    return not confidently_empty, metrics


def _partial_grid_row_detections(
    image: np.ndarray,
    left: float,
    top: float,
    right: float,
    bottom: float,
    columns: int,
    references: list[Detection],
) -> list[Detection] | None:
    """Recover card-sized bodies that straddle slots in an incomplete row.

    Column divisions describe capacity, not object boundaries. Inspect the
    whole row before cropping either side of a division. Only replace slots
    when fewer, independently card-sized bodies explain the row and the rest
    is demonstrably blank. Ambiguous/full rows keep their existing detector.
    """
    if columns < 2:
        return None
    height, width = image.shape[:2]
    x1, y1 = max(0, round(left * width)), max(0, round(top * height))
    x2, y2 = min(width, round(right * width)), min(height, round(bottom * height))
    row = image[y1:y2, x1:x2]
    if row.size == 0 or min(row.shape[:2]) < 12:
        return None
    inset = max(1, round(row.shape[0] * 0.15))
    core = row[inset:-inset]
    gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)[:, :, 1]
    ink = (saturation > 22) | (gray < 190)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 30, 90) > 0
    cell_width = row.shape[1] / columns
    profile = (ink.mean(axis=0) > 0.16) | (edges.mean(axis=0) > 0.025)
    support = profile.astype(np.uint8)[None, :]
    support = cv2.morphologyEx(
        support, cv2.MORPH_CLOSE, np.ones((1, max(3, round(cell_width * 0.05))), np.uint8)
    )
    support = cv2.morphologyEx(
        support, cv2.MORPH_OPEN, np.ones((1, max(3, round(cell_width * 0.015))), np.uint8)
    )
    _, _, stats, _ = cv2.connectedComponentsWithStats(support)
    spans = [(int(s[0]), int(s[0] + s[2])) for s in stats[1:]]

    def plausible(spans: list[tuple[int, int]]) -> bool:
        return 0 < len(spans) < columns and all(
            0.65 <= (end - start) / cell_width <= 1.12 for start, end in spans
        )

    if not plausible(spans):
        # Pale backs have sparse ink, but may still have an independently
        # closed holder/card outline. Do not bridge arbitrary distant text:
        # require a card-sized two-dimensional rectangle from another route.
        outlines = [
            (
                max(0, round(ref.x * width) - x1),
                min(row.shape[1], round((ref.x + ref.width) * width) - x1),
            )
            for ref in references
            if top + (bottom - top) * 0.15 < ref.y + ref.height / 2 < bottom - (bottom - top) * 0.15
            and 0.72 <= ref.width * width / cell_width <= 1.12
            and 0.60 <= ref.height / (bottom - top) <= 1.25
        ]
        spans = []
        for start, end in sorted(outlines, key=lambda span: span[1] - span[0], reverse=True):
            if any(
                max(0, min(end, b) - max(start, a)) / max(1, min(end - start, b - a)) > 0.65
                for a, b in spans
            ):
                continue
            spans.append((start, end))
        spans.sort()
        if not plausible(spans):
            return None
    if not any(
        start + cell_width * 0.08 < boundary * cell_width < end - cell_width * 0.08
        for start, end in spans
        for boundary in range(1, columns)
    ):
        return None

    margin = round(cell_width * 0.035)
    windows = [(max(0, start - margin), min(row.shape[1], end + margin)) for start, end in spans]
    outside = np.ones(row.shape[1], dtype=bool)
    for start, end in windows:
        outside[start:end] = False
    # A sparse pale back must not be mistaken for unused bed. Require both
    # pixel evidence and any independent card-sized contour to agree.
    if not outside.any() or (
        float(gray[:, outside].mean()) < 205
        or float(gray[:, outside].std()) > 20
        or float(ink[:, outside].mean()) > 0.035
        or float(edges[:, outside].mean()) > 0.007
    ):
        return None
    candidates = []
    for start, end in windows:
        x, r = (x1 + start) / width, (x1 + end) / width
        refined = _refine_grid_cell_to_ink_extent(image, x, top, r, bottom)
        if refined is None:
            clipped = top <= 0.002 or bottom >= 0.998 or x <= 0.002 or r >= 0.998
            refined = Detection(
                x,
                top,
                r - x,
                bottom - top,
                0,
                0.80,
                "severely_clipped" if clipped else "boundary_evidence_weak",
                ((x, top), (r, top), (r, bottom), (x, bottom)),
            )
        candidates.append(
            Detection(
                refined.x,
                refined.y,
                refined.width,
                refined.height,
                refined.angle,
                refined.confidence,
                refined.failure,
                refined.points,
                (
                    *refined.evidence,
                    "full_bed_grid",
                    "row_local_body_recovery",
                    "outside_body_blank",
                ),
            )
        )
    for reference in references:
        center_y = reference.y + reference.height / 2
        if (
            top + (bottom - top) * 0.15 < center_y < bottom - (bottom - top) * 0.15
            and reference.width * reference.height
            >= (right - left) / columns * (bottom - top) * 0.45
            and max((_intersection_over_smaller(reference, c) for c in candidates), default=0)
            < 0.70
        ):
            return None
    return candidates


def _grid_shape_instance_support(
    image: np.ndarray,
    foreground: list[Detection],
    references: list[Detection],
    whole_bed: Detection,
    rows: int,
    columns: int,
) -> tuple[list[Detection], int, float]:
    """Measure distributed current-object support for a capacity shape.

    Scanner DPI is useful only when it describes the pixels being analysed. A
    preview can retain the device's nominal DPI while containing fewer pixels,
    making the physical-size gate prefer a coarser grid. Multi-route object
    instances provide a scale-independent correction, but only when they land
    in several distinct cells. Counting unique cells prevents logos and nested
    card artwork in one physical object from voting repeatedly.
    """
    capacity = rows * columns
    if capacity <= 0:
        return [], 0, 0.0
    cell_width = whole_bed.width / columns
    cell_height = whole_bed.height / rows
    instances = _corroborated_current_instances(
        image,
        foreground,
        references,
        cell_area=whole_bed.width * whole_bed.height / capacity,
    )
    supported_cells: set[tuple[int, int]] = set()
    for item in instances:
        width_ratio = item.width / max(cell_width, 1e-9)
        height_ratio = item.height / max(cell_height, 1e-9)
        center_x = item.x + item.width / 2
        center_y = item.y + item.height / 2
        if not (
            GRID_SHAPE_INSTANCE_MIN_AXIS_RATIO <= width_ratio <= GRID_SHAPE_INSTANCE_MAX_AXIS_RATIO
            and GRID_SHAPE_INSTANCE_MIN_AXIS_RATIO
            <= height_ratio
            <= GRID_SHAPE_INSTANCE_MAX_AXIS_RATIO
            and whole_bed.x <= center_x <= whole_bed.x + whole_bed.width
            and whole_bed.y <= center_y <= whole_bed.y + whole_bed.height
        ):
            continue
        column = min(
            columns - 1,
            max(0, int((center_x - whole_bed.x) / max(cell_width, 1e-9))),
        )
        row = min(
            rows - 1,
            max(0, int((center_y - whole_bed.y) / max(cell_height, 1e-9))),
        )
        supported_cells.add((row, column))
    support_count = len(supported_cells)
    return instances, support_count, support_count / capacity


def _physical_object_envelopes(
    image: np.ndarray,
    instances: list[Detection],
    *,
    margin_ratio: float = OBJECT_ENVELOPE_MARGIN_RATIO,
) -> list[Detection]:
    """Add a small, neighbour-isolated margin around complete object evidence."""
    image_height, image_width = image.shape[:2]

    completed: list[Detection] = []
    for item in instances:
        # Independent routes can agree on an inner frame rather than the card
        # border. Expand corroborated envelopes only when their safety collar
        # still contains unexplained structure; peer boundaries below constrain
        # expansion so the margin cannot enter another observed card.
        has_complete_boundary = "independent_outer_envelope_support" in item.evidence
        has_structural_boundary = (
            has_complete_boundary or "structural_edge_envelope" in item.evidence
        )
        pixel_margin = (
            min(item.width * image_width, item.height * image_height)
            * margin_ratio
        )
        collar_left = max(0.0, item.x - pixel_margin / image_width)
        collar_top = max(0.0, item.y - pixel_margin / image_height)
        collar_right = min(1.0, item.x + item.width + pixel_margin / image_width)
        collar_bottom = min(1.0, item.y + item.height + pixel_margin / image_height)
        collar = Detection(
            collar_left, collar_top, collar_right - collar_left,
            collar_bottom - collar_top, 0.0, 1.0,
        )
        if has_complete_boundary and not _cell_has_unexplained_object(image, collar, instances):
            pixel_margin = 0.0
        left = max(0.0, item.x - pixel_margin / image_width)
        right = min(1.0, item.x + item.width + pixel_margin / image_width)
        top = max(0.0, item.y - pixel_margin / image_height)
        bottom = min(1.0, item.y + item.height + pixel_margin / image_height)

        # Do not let a safety margin enter the next independently corroborated
        # object. A structural edge is already a meaningful boundary on the
        # neighbour-facing side, so keep that side fixed even when exposure
        # makes the two raw envelopes touch slightly. Weaker body contours may
        # share a real clear-holder gap and are limited to its midpoint.
        for peer in instances:
            if peer is item:
                continue
            y_overlap = max(
                0.0,
                min(item.y + item.height, peer.y + peer.height) - max(item.y, peer.y),
            ) / max(min(item.height, peer.height), 1e-9)
            x_overlap = max(
                0.0,
                min(item.x + item.width, peer.x + peer.width) - max(item.x, peer.x),
            ) / max(min(item.width, peer.width), 1e-9)
            if y_overlap >= 0.30:
                peer_is_right = peer.x + peer.width / 2 > item.x + item.width / 2
                if has_structural_boundary and peer_is_right and peer.x <= item.x + item.width:
                    right = min(right, item.x + item.width)
                elif (
                    has_structural_boundary and not peer_is_right and peer.x + peer.width >= item.x
                ):
                    left = max(left, item.x)
                elif peer.x >= item.x + item.width:
                    right = min(right, (item.x + item.width + peer.x) / 2)
                elif peer.x + peer.width <= item.x:
                    left = max(left, (peer.x + peer.width + item.x) / 2)
            if x_overlap >= 0.30:
                peer_is_below = peer.y + peer.height / 2 > item.y + item.height / 2
                if has_structural_boundary and peer_is_below and peer.y <= item.y + item.height:
                    bottom = min(bottom, item.y + item.height)
                elif (
                    has_structural_boundary and not peer_is_below and peer.y + peer.height >= item.y
                ):
                    top = max(top, item.y)
                elif peer.y >= item.y + item.height:
                    bottom = min(bottom, (item.y + item.height + peer.y) / 2)
                elif peer.y + peer.height <= item.y:
                    top = max(top, (peer.y + peer.height + item.y) / 2)
        if right <= left or bottom <= top:
            continue
        source_contact = left <= 0.002 or top <= 0.002 or right >= 0.998 or bottom >= 0.998
        completed.append(
            Detection(
                left,
                top,
                right - left,
                bottom - top,
                item.angle,
                item.confidence,
                (
                    "source_edge_contact"
                    if source_contact
                    and item.failure in {"severely_clipped", "source_edge_contact"}
                    else item.failure or ("source_edge_contact" if source_contact else None)
                ),
                ((left, top), (right, top), (right, bottom), (left, bottom)),
                tuple(
                    dict.fromkeys(
                        (
                            *item.evidence,
                            "physical_object_envelope",
                            "current_object_layout",
                            *(("source_edge_contact",) if source_contact else ()),
                        )
                    )
                ),
            )
        )
    return sorted(completed, key=lambda item: (item.y, item.x))


def _cell_has_unexplained_object(
    image: np.ndarray,
    cell: Detection,
    objects: list[Detection],
) -> bool:
    """Find distributed card structure outside every accepted object envelope."""
    image_height, image_width = image.shape[:2]
    x1 = max(0, int(round(cell.x * image_width)))
    y1 = max(0, int(round(cell.y * image_height)))
    x2 = min(image_width, int(round((cell.x + cell.width) * image_width)))
    y2 = min(image_height, int(round((cell.y + cell.height) * image_height)))
    region = image[y1:y2, x1:x2]
    if region.size == 0:
        return False

    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)[:, :, 1]
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 30, 90) > 0
    unexplained = np.ones(gray.shape, dtype=bool)
    mask_padding = max(3, round(min(region.shape[:2]) * 0.015))
    for item in objects:
        object_x1 = max(x1, int(round(item.x * image_width)))
        object_y1 = max(y1, int(round(item.y * image_height)))
        object_x2 = min(image_width, int(round((item.x + item.width) * image_width)))
        object_y2 = min(image_height, int(round((item.y + item.height) * image_height)))
        if object_x2 <= object_x1 or object_y2 <= object_y1:
            continue
        unexplained[
            max(0, object_y1 - y1 - mask_padding) : min(
                region.shape[0], object_y2 - y1 + mask_padding
            ),
            max(0, object_x1 - x1 - mask_padding) : min(
                region.shape[1], object_x2 - x1 + mask_padding
            ),
        ] = False
    if float(np.mean(unexplained)) < 0.06:
        return False

    saturated = (saturation > 22) & unexplained
    residual_edges = edges & unexplained

    def distributed(mask: np.ndarray, threshold: float) -> bool:
        return (
            float(np.mean(np.mean(mask, axis=0) > threshold)) >= 0.12
            and float(np.mean(np.mean(mask, axis=1) > threshold)) >= 0.12
        )

    saturation_ratio = float(np.mean(saturated))
    edge_ratio = float(np.mean(residual_edges))
    remaining_luma = gray[unexplained]
    luma_stddev = float(np.std(remaining_luma)) if remaining_luma.size else 0.0
    return (
        (saturation_ratio >= 0.045 and distributed(saturated, 0.02))
        or (edge_ratio >= 0.006 and distributed(residual_edges, 0.004))
        or (luma_stddev >= 20.0 and edge_ratio >= 0.002 and distributed(residual_edges, 0.002))
    )


def _object_layout_overrides_grid(
    image: np.ndarray,
    grid: list[Detection],
    instances: list[Detection],
) -> list[Detection] | None:
    """Prefer complete current objects when a capacity grid over-segments them.

    The grid remains a coverage audit rather than a cropping authority. Every
    occupied grid region must either be explained by a corroborated object or
    become structurally empty after those objects are masked. Any residual
    card-like structure vetoes the replacement, preserving low-contrast cards
    that lack a strong instance contour.
    """
    if not instances or len(grid) <= len(instances) or _layout_conflicts(instances):
        return None
    if not all("multi_route_instance_corroboration" in item.evidence for item in instances):
        return None

    objects = _physical_object_envelopes(image, instances)
    if _layout_conflicts(objects):
        # A protective collar is optional. Overlapping collars must not veto
        # independently observed, non-conflicting objects and restore grid cuts.
        # Keep actual object boundaries; the residual audit still checks recall.
        objects = _physical_object_envelopes(image, instances, margin_ratio=0.0)
    if len(objects) != len(instances) or _layout_conflicts(objects):
        return None
    # Scanner capacity includes empty bed. Completeness is established below
    # by residual object structure, never by the area of obsolete grid cells.

    explained_counts = [0] * len(objects)
    for cell in grid:
        cell_area = cell.width * cell.height
        explanations = [
            (
                max(
                    0.0,
                    min(cell.x + cell.width, item.x + item.width) - max(cell.x, item.x),
                )
                * max(
                    0.0,
                    min(cell.y + cell.height, item.y + item.height) - max(cell.y, item.y),
                )
                / max(cell_area, 1e-9),
                index,
            )
            for index, item in enumerate(objects)
        ]
        best_coverage, best_index = max(explanations, default=(0.0, -1))
        if best_coverage >= OBJECT_LAYOUT_MIN_GRID_CELL_EXPLANATION:
            explained_counts[best_index] += 1
        if _cell_has_unexplained_object(image, cell, objects):
            return None

    if not all(count >= 1 for count in explained_counts):
        return None
    # An offset card can lose a substantial corner in a neighbouring cell
    # without explaining 25% of that cell. Measure coverage of the object,
    # rather than requiring two large capacity-cell fragments.
    # Check the original instances here: a safety margin crossing a grid seam
    # alone does not prove that the grid cuts a card.
    object_cell_shares = [
        sorted(
            [
                max(0.0, min(item.x + item.width, cell.x + cell.width) - max(item.x, cell.x))
                * max(0.0, min(item.y + item.height, cell.y + cell.height) - max(item.y, cell.y))
                / max(item.width * item.height, 1e-9)
                for cell in grid
            ],
            reverse=True,
        )
        for item in instances
    ]
    has_cut_object = any(
        len(shares) >= 2 and shares[0] < 0.97 and shares[1] >= 0.05
        and sum(shares) >= 0.85
        for shares in object_cell_shares
    )
    if not any(count >= 2 for count in explained_counts) and not has_cut_object:
        return None

    mixed_orientation = (
        len({item.width * image.shape[1] >= item.height * image.shape[0] for item in objects}) > 1
    )
    return [
        replace(
            item,
            evidence=tuple(
                dict.fromkeys(
                    (
                        *item.evidence,
                        "current_instance_grid_reconciliation",
                        "grid_fragment_replacement",
                        f"replaced_grid_regions:{explained_counts[index]}",
                        "grid_oversegmentation_rejected",
                        *(("mixed_orientation_object_layout",) if mixed_orientation else ()),
                    )
                )
            ),
        )
        for index, item in enumerate(objects)
    ]


def _full_bed_grid_detections(
    image: np.ndarray, foreground: list[Detection], edge_references: list[Detection], dpi: int
) -> list[Detection] | None:
    """Split a tightly packed full-bed grid using visible card interiors as scale references."""
    whole_bed = max(
        (
            item
            for item in (*foreground, *edge_references)
            if item.width * item.height >= 0.55
            and item.x <= 0.04
            and item.y <= 0.04
            and item.x + item.width >= 0.96
            and item.y + item.height >= 0.96
        ),
        key=lambda item: item.width * item.height,
        default=None,
    )
    provisional_references = [
        item
        for item in edge_references
        if item.width * item.height < 0.20
        and 1.15
        <= max(item.width * image.shape[1], item.height * image.shape[0])
        / max(1.0, min(item.width * image.shape[1], item.height * image.shape[0]))
        <= 1.9
    ]
    if whole_bed is None and len(provisional_references) >= 2:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        border = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
        if float(np.mean(border)) < 235:
            whole_bed = Detection(0, 0, 1, 1, 0, 0.5, evidence=("bed_edge_coverage",))
    references = [
        item
        for item in edge_references
        if item is not whole_bed
        and item.width * item.height < 0.20
        and whole_bed is not None
        and _intersection_over_smaller(whole_bed, item) >= 0.90
        and 1.15
        <= max(item.width * image.shape[1], item.height * image.shape[0])
        / max(1.0, min(item.width * image.shape[1], item.height * image.shape[0]))
        <= 1.9
    ]
    if whole_bed is None or len(references) < 2:
        return None

    # Edge contours usually describe the printed card inside a clear holder,
    # so a grid cell is about 10-20% larger than the reference rectangle.
    # Printed artwork contours are often much smaller than their top-loader.
    # Inferring count directly from that size produced impossible 3x5/3x6
    # layouts. Select a sheet factorization using physical dimensions at the
    # scanner DPI and the normal trading-card/top-loader aspect instead.
    sheet_width_px = whole_bed.width * image.shape[1]
    sheet_height_px = whole_bed.height * image.shape[0]
    physical_or_supported_shapes: list[tuple[float, int, int, list[Detection], int, float]] = []
    scale_free_shapes: list[tuple[float, int, int, list[Detection], int, float]] = []
    for rows in range(2, 11):
        for columns in range(2, 7):
            count = rows * columns
            # Edge references are contours, not distinct cards. A single card
            # can contribute several artwork/holder rectangles, so using their
            # count as a lower bound rejects the correct 2x4 layout and can
            # split every landscape card into two 4x4 cells.
            if count < 4 or count > 20:
                continue
            cell_width_px = sheet_width_px / columns
            cell_height_px = sheet_height_px / rows
            short_px, long_px = sorted((cell_width_px, cell_height_px))
            ratio = long_px / max(short_px, 1.0)
            if not 1.20 <= ratio <= 1.85:
                continue
            instances, support_count, support_coverage = _grid_shape_instance_support(
                image,
                foreground,
                references,
                whole_bed,
                rows,
                columns,
            )
            base_score = abs(ratio - 1.40) + count * 0.001
            shape = (
                base_score - GRID_SHAPE_INSTANCE_SCORE_WEIGHT * support_coverage,
                rows,
                columns,
                instances,
                support_count,
                support_coverage,
            )
            scale_free_shapes.append(shape)
            physical_shape = short_px >= dpi * 2.15 and dpi * 3.0 <= long_px <= dpi * 5.5
            distributed_current_support = (
                support_count >= GRID_SHAPE_INSTANCE_MIN_SUPPORTED_CELLS
                and support_coverage >= GRID_SHAPE_INSTANCE_MIN_COVERAGE
            )
            if physical_shape or distributed_current_support:
                physical_or_supported_shapes.append(shape)
    shapes = physical_or_supported_shapes
    if not shapes:
        # Small synthetic/preview images do not carry real DPI scale. Keep the
        # same aspect/count objective as a scale-free fallback for those.
        shapes = scale_free_shapes
    if not shapes:
        return None
    _, rows, columns, current_instances, shape_support_count, shape_support_coverage = min(
        shapes,
        key=lambda shape: shape[:3],
    )
    cell_width = whole_bed.width / columns
    cell_height = whole_bed.height / rows
    pixel_ratio = max(cell_width * image.shape[1], cell_height * image.shape[0]) / max(
        1.0, min(cell_width * image.shape[1], cell_height * image.shape[0])
    )
    if not 1.15 <= pixel_ratio <= 1.9:
        return None

    grid = []
    for row in range(rows):
        row_top = whole_bed.y + row * cell_height
        recovered = _partial_grid_row_detections(
            image,
            whole_bed.x,
            row_top,
            whole_bed.x + whole_bed.width,
            row_top + cell_height,
            columns,
            references,
        )
        if recovered is not None:
            grid.extend(recovered)
            continue
        for column in range(columns):
            x = whole_bed.x + column * cell_width
            y = whole_bed.y + row * cell_height
            right = x + cell_width
            bottom = y + cell_height
            occupied, occupancy = _grid_cell_occupancy(image, x, y, right, bottom)
            if not occupied:
                # The inferred shape is scanner-bed capacity, not proof that
                # every slot contains a card. A neighbouring holder edge can
                # enter an otherwise blank slot, so central texture is the
                # deciding evidence.
                continue
            occupancy_evidence = (
                "cell_occupied:"
                f"ink={occupancy['ink_ratio']:.3f},"
                f"edge={occupancy['edge_ratio']:.3f},"
                f"luma={occupancy['mean_luma']:.1f}"
            )
            refined = _refine_grid_cell_to_ink_extent(image, x, y, right, bottom)
            if refined is not None:
                grid.append(
                    Detection(
                        refined.x,
                        refined.y,
                        refined.width,
                        refined.height,
                        refined.angle,
                        refined.confidence,
                        refined.failure,
                        refined.points,
                        (*refined.evidence, occupancy_evidence),
                    )
                )
                continue
            clipped = x <= 0.002 or y <= 0.002 or right >= 0.998 or bottom >= 0.998
            grid.append(
                Detection(
                    x,
                    y,
                    cell_width,
                    cell_height,
                    0.0,
                    0.46,
                    "card_outline_unresolved",
                    ((x, y), (right, y), (right, bottom), (x, bottom)),
                    (
                        "full_bed_grid",
                        "cell_extent_fallback",
                        "source_edge_contact" if clipped else "outline_evidence_weak",
                        occupancy_evidence,
                    ),
                )
            )
    # Capacity cells are only allocation suggestions. If independent current-
    # image routes instead agree on fewer complete physical envelopes and no
    # card-like residual remains outside them, the grid is demonstrably
    # over-segmenting those objects. Use a separately selected outer envelope
    # for this transaction so the ordinary fragment reconciler can retain its
    # conservative medoid behaviour on dense, regular sheets.
    object_instances = _corroborated_current_instances(
        image,
        foreground,
        edge_references,
        cell_area=whole_bed.width * whole_bed.height / (rows * columns),
        prefer_outer_envelope=True,
    )
    object_layout = _object_layout_overrides_grid(image, grid, object_instances)
    grid = (
        object_layout
        if object_layout is not None
        else _reconcile_grid_with_current_instances(image, grid, current_instances)
    )
    return [
        replace(
            item,
            evidence=(
                *item.evidence,
                f"grid_capacity:{rows}x{columns}",
                f"grid_shape_instance_support:{shape_support_count}/{rows * columns}",
                f"grid_shape_instance_coverage:{shape_support_coverage:.3f}",
            ),
        )
        for item in grid
    ]


def _refine_grid_cell_to_ink_extent(
    image: np.ndarray,
    x: float,
    y: float,
    right: float,
    bottom: float,
) -> Detection | None:
    """Tighten an inferred grid slot around its visible card/holder content.

    A full-bed grid is useful for cardinality but its row boundaries are only
    allocation boundaries. Treating them as crop edges clips one neighbour and
    includes part of the next. Robust colour/brightness extents recover the
    physical object while ignoring isolated dust, glare, and scanner noise.
    """
    image_height, image_width = image.shape[:2]
    x1 = max(0, int(round(x * image_width)))
    y1 = max(0, int(round(y * image_height)))
    x2 = min(image_width, int(round(right * image_width)))
    y2 = min(image_height, int(round(bottom * image_height)))
    cell = image[y1:y2, x1:x2]
    if cell.size == 0:
        return None

    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(cell, cv2.COLOR_BGR2HSV)[:, :, 1]
    mask = ((saturation > 22) | (gray < 190)).astype(np.uint8) * 255
    kernel_size = max(3, round(min(cell.shape[:2]) * 0.012))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    ink_y, ink_x = np.nonzero(mask)
    if len(ink_x) < cell.shape[0] * cell.shape[1] * 0.01:
        return None

    local_left, local_right = np.quantile(ink_x, (GRID_INK_LOW_QUANTILE, GRID_INK_HIGH_QUANTILE))
    local_top, local_bottom = np.quantile(ink_y, (GRID_INK_LOW_QUANTILE, GRID_INK_HIGH_QUANTILE))
    ink_width = max(1.0, float(local_right - local_left))
    ink_height = max(1.0, float(local_bottom - local_top))
    margin_x = ink_width * GRID_INK_MARGIN_RATIO
    margin_y = ink_height * GRID_INK_MARGIN_RATIO
    refined_x1 = max(x1, int(np.floor(x1 + local_left - margin_x)))
    refined_y1 = max(y1, int(np.floor(y1 + local_top - margin_y)))
    refined_x2 = min(x2, int(np.ceil(x1 + local_right + margin_x + 1)))
    refined_y2 = min(y2, int(np.ceil(y1 + local_bottom + margin_y + 1)))

    refined_width = refined_x2 - refined_x1
    refined_height = refined_y2 - refined_y1
    cell_width = x2 - x1
    cell_height = y2 - y1
    area_ratio = refined_width * refined_height / max(1, cell_width * cell_height)
    side_insets = (
        (refined_x1 - x1) / max(1, cell_width),
        (x2 - refined_x2) / max(1, cell_width),
        (refined_y1 - y1) / max(1, cell_height),
        (y2 - refined_y2) / max(1, cell_height),
    )
    if (
        refined_width < cell_width * 0.55
        or refined_height < cell_height * 0.55
        or area_ratio < 0.35
        # Merely reproducing the allocation cell is not a refinement. This was
        # the cause of oversized bottom-row crops: the dark/colour mask reached
        # every cell edge, yet the result was promoted as a precise outline.
        or (
            area_ratio > GRID_MAX_REFINED_AREA_RATIO
            and max(side_insets) < GRID_MIN_REFINEMENT_INSET_RATIO
        )
    ):
        return None

    normalized_x1 = refined_x1 / image_width
    normalized_y1 = refined_y1 / image_height
    normalized_x2 = refined_x2 / image_width
    normalized_y2 = refined_y2 / image_height
    clipped = (
        refined_x1 <= 2
        or refined_y1 <= 2
        or refined_x2 >= image_width - 2
        or refined_y2 >= image_height - 2
    )
    return Detection(
        normalized_x1,
        normalized_y1,
        normalized_x2 - normalized_x1,
        normalized_y2 - normalized_y1,
        0.0,
        0.86,
        "severely_clipped" if clipped else None,
        (
            (normalized_x1, normalized_y1),
            (normalized_x2, normalized_y1),
            (normalized_x2, normalized_y2),
            (normalized_x1, normalized_y2),
        ),
        ("full_bed_grid", "ink_extent_refinement"),
    )


def _separated_rotated_objects(image: np.ndarray) -> list[Detection]:
    """Keep stable, complete tilted components ahead of scanner capacity grids.

    Axis-aligned aspect ratios approach one when a card is tilted. Measure
    shape in the object's own axes, require two threshold observations and
    reject the entire route if any substantial foreground remains unexplained.
    Axis-aligned components may be touching rows with a card-like combined
    aspect ratio; leave those sheets to the existing structural/grid routes.
    """
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    passes: list[list[tuple[np.ndarray, float]]] = []
    for threshold in (185, 205):
        mask = (gray < threshold).astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        objects = []
        covered = np.zeros_like(mask)
        for contour in contours:
            rect = cv2.minAreaRect(contour)
            short, long = sorted(rect[1])
            area = short * long
            if area < gray.size * 0.015:
                continue
            if (
                area > gray.size * 0.35
                or not 1.25 <= long / max(short, 1) <= 1.85
                or cv2.contourArea(contour) / max(area, 1) < 0.90
            ):
                continue
            points = cv2.boxPoints(rect)
            # A fitted rectangle can extend slightly beyond a rounded holder
            # touching the scanner edge. Keep that whole object; the public
            # source clamp clips its corners and preserves the edge warning.
            # Reject material extrapolation rather than invent missing pixels.
            normalized = points / (width, height)
            if (normalized < -SOURCE_BOUNDS_SEVERE_OVERFLOW_RATIO).any() or (
                normalized > 1 + SOURCE_BOUNDS_SEVERE_OVERFLOW_RATIO
            ).any():
                continue
            objects.append((points, rect[2]))
            cv2.fillConvexPoly(covered, np.rint(points).astype(np.int32), 255)
        if not objects or np.count_nonzero(mask & ~covered) > max(
            gray.size * 0.002, np.count_nonzero(mask) * 0.02
        ):
            return []
        passes.append(objects)
    inner, outer = passes
    if len(inner) != len(outer) or not all(10 <= angle % 90 <= 80 for _, angle in outer):
        return []
    detections = []
    matched = set()
    for points, angle in outer:
        area = cv2.contourArea(points)
        matches = []
        for index, (reference, _) in enumerate(inner):
            intersection, _ = cv2.intersectConvexConvex(points, reference)
            union = area + cv2.contourArea(reference) - intersection
            if intersection / max(union, 1) >= 0.90:
                matches.append(index)
        if len(matches) != 1 or matches[0] in matched:
            return []
        matched.add(matches[0])
        left, top = points.min(axis=0)
        right, bottom = points.max(axis=0)
        detections.append(Detection(
            float(left / width), float(top / height),
            float((right - left) / width), float((bottom - top) / height),
            float(angle), 0.94,
            points=tuple((float(x / width), float(y / height)) for x, y in points),
            evidence=("separated_rotated_object", "threshold_185", "threshold_205",
                      "physical_object_envelope", "current_object_layout",
                      "grid_oversegmentation_rejected",
                      *(("source_edge_contact",) if (
                          (points.min(axis=0) <= 2).any()
                          or (points.max(axis=0) >= (width - 2, height - 2)).any()
                      ) else ())),
        ))
    if _layout_conflicts(detections):
        return []
    return sorted(detections, key=lambda item: (item.y, item.x))


def _local_candidates(content: bytes, *, dpi: int = 300) -> list[Detection]:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码扫描图像")
    height, width = image.shape[:2]
    rotated_objects = _separated_rotated_objects(image)
    if rotated_objects:
        return rotated_objects
    irregular = _irregular_slab_detections(image)
    associated_labels = sum("grading_label_association" in item.evidence for item in irregular)
    single_slab_mixed_is_complete = _single_slab_mixed_layout_is_complete(image, irregular)
    irregular_areas = [item.width * item.height for item in irregular]
    irregular_is_complete = (
        len(irregular) >= 3
        and associated_labels >= 2
        and min(irregular_areas, default=0) > 0
        and max(irregular_areas, default=0) / min(irregular_areas, default=1) <= 2.6
        and not _layout_conflicts(irregular)
    )
    if irregular_is_complete:
        # Mixed graded/raw sheets have no shared row, column, or object size.
        # Their independent instances are stronger evidence than a forced grid.
        return irregular
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (7, 7), 0), 35, 110)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found: list[Detection] = []
    for contour in contours:
        rect = cv2.minAreaRect(contour)
        (cx, cy), (rw, rh), angle = rect
        area = rw * rh
        if area < width * height * 0.015:
            continue
        short, long = sorted((rw, rh))
        ratio = long / max(short, 1)
        if not 1.25 <= ratio <= 1.85:
            continue
        box = cv2.boxPoints(rect)
        x, y, bw, bh = cv2.boundingRect(box.astype(np.float32))
        clipped = x <= 2 or y <= 2 or x + bw >= width - 2 or y + bh >= height - 2
        confidence = min(
            0.99,
            0.55
            + 0.30 * (1 - abs(ratio - 1.4) / 0.55)
            + 0.15 * min(area / (width * height * 0.08), 1),
        )
        found.append(
            Detection(
                x / width,
                y / height,
                bw / width,
                bh / height,
                angle,
                round(confidence, 4),
                "severely_clipped" if clipped else None,
                tuple((float(px / width), float(py / height)) for px, py in box),
            )
        )
    foreground = _foreground_detections(image)
    structural_envelopes = _structural_envelope_detections(image)
    # Multi-threshold and adaptive passes complement Canny: reflective sleeves
    # can erase a gradient while still separating statistically from the bed.
    threshold_candidates: list[Detection] = []
    threshold_passes = [
        (f"threshold_{value}", cv2.threshold(gray, value, 255, cv2.THRESH_BINARY_INV)[1])
        for value in (165, 205, 235)
    ]
    threshold_passes.extend(
        (
            (
                "otsu_threshold",
                cv2.threshold(
                    gray,
                    0,
                    255,
                    cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
                )[1],
            ),
            (
                "adaptive_threshold",
                cv2.adaptiveThreshold(
                    gray,
                    255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY_INV,
                    81,
                    7,
                ),
            ),
        )
    )
    for route, mask in threshold_passes:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            area = bw * bh
            ratio = max(bw, bh) / max(1, min(bw, bh))
            if (
                not width * height * 0.015 <= area <= width * height * 0.35
                or not 1.18 <= ratio <= 1.9
            ):
                continue
            if route == "otsu_threshold":
                candidate_gray = gray[y : y + bh, x : x + bw]
                candidate_saturation = cv2.cvtColor(
                    image[y : y + bh, x : x + bw], cv2.COLOR_BGR2HSV
                )[:, :, 1]
                candidate_edges = cv2.Canny(cv2.GaussianBlur(candidate_gray, (5, 5), 0), 30, 90)
                if (
                    float(np.mean(candidate_saturation > 22)) < 0.08
                    and float(np.mean(candidate_edges > 0)) < 0.005
                    and float(np.std(candidate_gray)) < 25.0
                ):
                    # Otsu is exposure-invariant, but a smooth scanner-bed
                    # shadow can also form a card-sized threshold component.
                    # It may corroborate an object only when the rectangle has
                    # independent colour, edge, or tonal structure.
                    continue
            threshold_candidates.append(
                Detection(
                    x / width,
                    y / height,
                    bw / width,
                    bh / height,
                    0,
                    0.62,
                    "severely_clipped"
                    if x <= 2 or y <= 2 or x + bw >= width - 2 or y + bh >= height - 2
                    else None,
                    (
                        (x / width, y / height),
                        ((x + bw) / width, y / height),
                        ((x + bw) / width, (y + bh) / height),
                        (x / width, (y + bh) / height),
                    ),
                    (route,),
                )
            )
    # Keep this route as independent evidence. Its boxes are intentionally not
    # greedily merged here: the global layout layer compares signals, while
    # contour-level containment would let repeated threshold boxes suppress a
    # stronger perspective/connected-component observation.
    # Similar-sized foreground boxes do not prove every object was found:
    # pale backs can leave distributed text/holder edges outside those boxes.
    full_bed_grid = (
        None
        if _foreground_layout_is_complete(foreground)
        and not _cell_has_unexplained_object(
            image, Detection(0, 0, 1, 1, 0, 1), foreground
        )
        else _full_bed_grid_detections(
            image,
            foreground,
            [*found, *threshold_candidates, *irregular, *structural_envelopes],
            dpi,
        )
    )
    if full_bed_grid is not None and single_slab_mixed_is_complete:
        slab = next(item for item in irregular if "grading_label_association" in item.evidence)
        reconciled_slab = any(
            "current_instance_grid_reconciliation" in item.evidence
            and _intersection_over_union(slab, item) >= GRID_INSTANCE_MIN_CORROBORATION_IOU
            for item in full_bed_grid
        )
        if _grid_fragments_detection(slab, full_bed_grid) or reconciled_slab:
            saturation_recall = _saturation_content_recall(image, irregular)
            return [
                Detection(
                    item.x,
                    item.y,
                    item.width,
                    item.height,
                    item.angle,
                    item.confidence,
                    item.failure,
                    item.points,
                    (
                        *item.evidence,
                        "single_slab_grid_fragmentation_guard",
                        f"saturation_content_recall:{saturation_recall:.4f}",
                    ),
                )
                for item in irregular
            ]
    if full_bed_grid is not None:
        # The grid cells include the clear-holder border; partial edge
        # rectangles describe only printed card interiors and would otherwise
        # win deduplication by confidence, clipping the condition-inspection
        # margin on those cells.
        found = full_bed_grid
        foreground = []
    for candidate in foreground:
        overlapping = [
            index
            for index, existing in enumerate(found)
            if _intersection_over_union(existing, candidate) >= 0.55
        ]
        if not overlapping:
            found.append(candidate)
        elif candidate.confidence > found[overlapping[0]].confidence:
            found[overlapping[0]] = candidate
    filtered = []
    for index, candidate in enumerate(found):
        contained = []
        for other_index, other in enumerate(found):
            if index == other_index:
                continue
            intersection_width = max(
                0.0,
                min(candidate.x + candidate.width, other.x + other.width)
                - max(candidate.x, other.x),
            )
            intersection_height = max(
                0.0,
                min(candidate.y + candidate.height, other.y + other.height)
                - max(candidate.y, other.y),
            )
            other_area = other.width * other.height
            if other_area and intersection_width * intersection_height / other_area >= 0.85:
                contained.append(other)
        contained_area = sum(item.width * item.height for item in contained)
        if len(contained) >= 2 and contained_area >= candidate.width * candidate.height * 0.75:
            continue
        filtered.append(candidate)
    found = filtered
    # Edge and foreground passes can describe the same sleeved card with a
    # vertical offset. IoU understates that overlap, so retain only the
    # strongest similarly-sized box when it covers almost half the other.
    deduplicated: list[Detection] = []
    for candidate in sorted(found, key=lambda item: item.confidence, reverse=True):
        duplicate = any(
            _intersection_over_smaller(candidate, existing) >= 0.45
            and 0.7 <= candidate.width / existing.width <= 1.43
            and 0.7 <= candidate.height / existing.height <= 1.43
            for existing in deduplicated
        )
        if not duplicate:
            deduplicated.append(candidate)
    found = deduplicated
    found.sort(key=lambda item: (round(item.y / max(item.height, 1e-6)), item.x))
    return found


def _layout_conflicts(detections: list[Detection] | tuple[Detection, ...]) -> tuple[str, ...]:
    reasons: list[str] = []
    for index, left in enumerate(detections):
        if left.width * left.height > 0.45:
            reasons.append("whole_sheet_candidate")
        for right in detections[index + 1 :]:
            overlap = _intersection_over_smaller(left, right)
            if overlap >= 0.80:
                reasons.append("nested_card_regions")
            elif overlap > 0.15:
                reasons.append("overlapping_card_regions")
    return tuple(dict.fromkeys(reasons))


def _non_conflicting_subset(detections: list[Detection]) -> list[Detection]:
    without_containers = [
        item
        for item in detections
        if item.width * item.height <= 0.45
        and not any(
            item is not other
            and other.width * other.height < item.width * item.height
            and _intersection_over_smaller(item, other) >= 0.80
            for other in detections
        )
    ]
    selected: list[Detection] = []
    for candidate in sorted(without_containers, key=lambda item: item.confidence, reverse=True):
        if all(_intersection_over_smaller(candidate, other) <= 0.15 for other in selected):
            selected.append(candidate)
    return sorted(selected, key=lambda item: (round(item.y / max(item.height, 1e-6)), item.x))


def _projection_evidence(
    image: np.ndarray, boxes: list[Detection]
) -> tuple[list[float], list[float]]:
    """Return normalized edge-energy at every candidate centre (1-D grid evidence)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gradient_x = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)).mean(axis=0)
    gradient_y = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1)).mean(axis=1)
    gradient_x /= max(float(gradient_x.max()), 1.0)
    gradient_y /= max(float(gradient_y.max()), 1.0)
    return gradient_x.tolist(), gradient_y.tolist()


def _grid_shape(count: int, image: np.ndarray, prior: list[Detection]) -> tuple[int, int]:
    # A partially occupied 4x2 sheet may contain seven cards. Prime cardinality
    # must not relabel its observed capacity as a fictitious seven-row column.
    for item in prior:
        for signal in item.evidence:
            if signal.startswith("grid_capacity:"):
                row_count, column_count = signal.removeprefix("grid_capacity:").split("x")
                return int(row_count), int(column_count)
    if prior:
        xs = sorted({round(item.x + item.width / 2, 2) for item in prior})
        ys = sorted({round(item.y + item.height / 2, 2) for item in prior})
        if len(xs) * len(ys) == count:
            return len(ys), len(xs)
    aspect = image.shape[1] / image.shape[0]
    choices = [(r, count // r) for r in range(1, count + 1) if count % r == 0]
    return min(choices, key=lambda rc: abs((rc[1] / rc[0]) - aspect * 1.35))


def _projection_gap_grid_detections(
    image: np.ndarray, rows: int, columns: int
) -> tuple[list[Detection], float]:
    """Recover a packed grid from the white seams between neighbouring cards.

    Front and back scans often contain the same number of cards without keeping
    the holders at pixel-identical positions. Reusing transformed front quads in
    that situation shifts every crop into its neighbour. The scanner-bed seams
    are much stronger evidence for the current image: find the lowest-ink valley
    near each expected division and use those valleys as non-overlapping cells.
    """
    if rows <= 0 or columns <= 0:
        return [], 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1]
    ink = ((saturation > 22) | (gray < 190)).astype(np.float32)

    def divisions(profile: np.ndarray, count: int) -> tuple[list[float], list[float]]:
        if count == 1:
            return [0.0, 1.0], []
        smoothing = max(5, len(profile) // 300)
        smoothed = np.convolve(
            profile, np.ones(smoothing, dtype=np.float32) / smoothing, mode="same"
        )
        boundaries = [0.0]
        qualities: list[float] = []
        radius = 0.35 / count
        for index in range(1, count):
            expected = index / count
            start = max(1, round((expected - radius) * len(smoothed)))
            stop = min(len(smoothed) - 1, round((expected + radius) * len(smoothed)))
            window = smoothed[start:stop]
            if not len(window):
                return [], []
            offset = int(np.argmin(window))
            valley = float(window[offset])
            local_median = float(np.median(window))
            qualities.append(max(0.0, min(1.0, (local_median - valley) / max(local_median, 0.05))))
            boundaries.append((start + offset) / len(smoothed))
        boundaries.append(1.0)
        return boundaries, qualities

    x_edges, x_quality = divisions(ink.mean(axis=0), columns)
    y_edges, y_quality = divisions(ink.mean(axis=1), rows)
    qualities = [*x_quality, *y_quality]
    if len(x_edges) != columns + 1 or len(y_edges) != rows + 1 or not qualities:
        return [], 0.0
    gap_quality = float(np.mean(qualities))
    # A weak local fluctuation is not enough to override a confirmed front-side
    # prior. Real scanner seams provide a clearly darker/lighter occupancy valley.
    if gap_quality < 0.20:
        return [], gap_quality

    # Each division is chosen from a local projection valley. Without a global
    # geometry check, one boundary can jump past a weak inter-row seam to the
    # much stronger transition into the unused scanner bed. That makes a
    # nominal 3x2 layout contain one double-height row plus a blank trailing
    # row. A grid hypothesis is only credible when its allocation spans remain
    # broadly comparable; genuinely mixed holder sizes must use independent
    # component detections instead of a forced grid.
    x_spans = np.diff(np.asarray(x_edges, dtype=np.float32))
    y_spans = np.diff(np.asarray(y_edges, dtype=np.float32))
    x_regularity = float(np.min(x_spans) / max(float(np.max(x_spans)), 1e-6))
    y_regularity = float(np.min(y_spans) / max(float(np.max(y_spans)), 1e-6))
    if min(x_regularity, y_regularity) < GRID_MIN_PROJECTION_SPAN_REGULARITY:
        return [], gap_quality

    detections: list[Detection] = []
    for row in range(rows):
        for column in range(columns):
            left, right = x_edges[column], x_edges[column + 1]
            top, bottom = y_edges[row], y_edges[row + 1]
            occupied, occupancy = _grid_cell_occupancy(image, left, top, right, bottom)
            if not occupied:
                # A projection valley at the transition from the final card row
                # into unused bed space can still form a geometrically regular
                # cell. Never preserve cardinality by manufacturing a crop from
                # a cell whose own pixels say it is empty.
                continue
            occupancy_evidence = (
                "cell_occupied:"
                f"ink={occupancy['ink_ratio']:.3f},"
                f"edge={occupancy['edge_ratio']:.3f},"
                f"luma={occupancy['mean_luma']:.1f}"
            )
            refined = _refine_grid_cell_to_ink_extent(image, left, top, right, bottom)
            if refined is not None:
                detections.append(
                    Detection(
                        refined.x,
                        refined.y,
                        refined.width,
                        refined.height,
                        refined.angle,
                        max(refined.confidence, 0.78 + 0.16 * gap_quality),
                        refined.failure,
                        refined.points,
                        (
                            *refined.evidence,
                            "projection_gap_grid",
                            "global_count",
                            occupancy_evidence,
                        ),
                    )
                )
                continue
            confidence = 0.72 + 0.16 * gap_quality
            detections.append(
                Detection(
                    left,
                    top,
                    right - left,
                    bottom - top,
                    0.0,
                    confidence,
                    "card_outline_unresolved",
                    ((left, top), (right, top), (right, bottom), (left, bottom)),
                    (
                        "projection_gap_grid",
                        "grid_cell_fallback",
                        "global_count",
                        occupancy_evidence,
                    ),
                )
            )
    return detections, gap_quality


def _transform_prior(prior: list[Detection], transform: str) -> list[Detection]:
    result = []
    for item in prior:
        x, y = item.x, item.y
        points = item.points
        if "mirror_x" in transform:
            x = 1 - item.x - item.width
            points = tuple((1 - px, py) for px, py in points or ()) or None
        if "mirror_y" in transform:
            y = 1 - item.y - item.height
            points = tuple((px, 1 - py) for px, py in points or ()) or None
        result.append(
            Detection(
                x,
                y,
                item.width,
                item.height,
                item.angle,
                0.0,
                points=points,
                evidence=item.evidence,
            )
        )
    return result


def _stationary_layout_similarity(original: list[Detection], transformed: list[Detection]) -> float:
    """Measure whether a transform preserves the same physical bed regions."""
    if not original or len(original) != len(transformed):
        return 0.0
    return float(
        np.mean(
            [
                max(_intersection_over_union(item, reference) for reference in original)
                for item in transformed
            ]
        )
    )


def _boundary_score(gray: np.ndarray, item: Detection) -> float:
    h, w = gray.shape
    x1, x2 = sorted((int(item.x * w), int((item.x + item.width) * w)))
    y1, y2 = sorted((int(item.y * h), int((item.y + item.height) * h)))
    x1, x2, y1, y2 = max(0, x1), min(w - 1, x2), max(0, y1), min(h - 1, y2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    grad = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    band = max(2, round(min(w, h) * 0.002))
    samples = np.concatenate(
        (
            grad[y1 : y1 + band, x1:x2].ravel(),
            grad[max(y1, y2 - band) : y2, x1:x2].ravel(),
            grad[y1:y2, x1 : x1 + band].ravel(),
            grad[y1:y2, max(x1, x2 - band) : x2].ravel(),
        )
    )
    return min(1.0, float(np.mean(samples)) / 80.0) if samples.size else 0.0


def _current_side_grid_score(
    image: np.ndarray,
    gray: np.ndarray,
    detections: list[Detection],
) -> float | None:
    """Score an occupancy grid using current-side evidence, or reject it.

    This intentionally does not use the front-side coordinates. A strong grid
    must have card-like physical aspect, comparable cell sizes, distributed
    two-dimensional content in every cell, and boundary energy in this scan.
    """
    if not detections or not all("full_bed_grid" in item.evidence for item in detections):
        return None
    pixel_areas: list[float] = []
    aspect_qualities: list[float] = []
    body_qualities: list[float] = []
    for item in detections:
        pixel_width = item.width * image.shape[1]
        pixel_height = item.height * image.shape[0]
        pixel_areas.append(pixel_width * pixel_height)
        ratio = max(pixel_width, pixel_height) / max(1.0, min(pixel_width, pixel_height))
        if not 1.15 <= ratio <= 1.90:
            return None
        aspect_qualities.append(max(0.0, 1 - abs(ratio - 1.40) / 0.50))
        occupied, metrics = _grid_cell_occupancy(
            image,
            item.x,
            item.y,
            item.x + item.width,
            item.y + item.height,
        )
        if not occupied:
            return None
        axis_coverage = min(metrics["ink_row_coverage"], metrics["ink_column_coverage"])
        # Reject a neighbour edge or shadow that happens to survive the basic
        # occupancy threshold. Pale/text-heavy backs are kept by either their
        # distributed ink or their stronger structural edge density.
        if axis_coverage < 0.12 and metrics["edge_ratio"] < 0.015:
            return None
        body_qualities.append(min(1.0, max(axis_coverage / 0.35, metrics["edge_ratio"] / 0.03)))

    size_regularity = min(pixel_areas) / max(max(pixel_areas), 1.0)
    if size_regularity < 0.65:
        return None
    mean_boundary = float(np.mean([_boundary_score(gray, item) for item in detections]))
    score = (
        0.72
        + 0.06 * size_regularity
        + 0.05 * float(np.mean(aspect_qualities))
        + 0.06 * float(np.mean(body_qualities))
        + 0.06 * mean_boundary
    )
    return round(min(0.94, score), 4)


def _grid_shape_support_from_evidence(detections: list[Detection]) -> tuple[int, int]:
    observations: list[tuple[int, int]] = []
    for item in detections:
        for signal in item.evidence:
            if not signal.startswith("grid_shape_instance_support:"):
                continue
            try:
                supported, capacity = signal.rsplit(":", 1)[1].split("/", 1)
                observations.append((int(supported), int(capacity)))
            except (TypeError, ValueError):
                continue
    return max(observations, key=lambda value: value[0] / max(value[1], 1), default=(0, 0))


def _prior_topology_split_evidence(
    prior: list[Detection],
    current: list[Detection],
) -> dict[str, float | int | str] | None:
    """Reject a coarse prior only when current pixels prove systematic splits.

    Count disagreement alone is not evidence: internal artwork can make one
    slab look like several rectangles. The veto requires a distributed,
    multi-route current grid plus the same one-to-many relationship across
    several prior slots. Each child must be card-sized, mutually isolated, and
    collectively fill its parent. A single noisy slot therefore cannot revoke
    an otherwise valid stationary front layout.
    """
    if len(prior) < 2 or len(current) <= len(prior):
        return None
    if _prior_is_manually_confirmed(prior):
        # A manually confirmed region may intentionally represent a booklet or
        # multi-panel object. Pixel topology may warn elsewhere, but must not
        # silently replace an explicit user geometry decision.
        return None
    if _layout_conflicts(prior) or _layout_conflicts(current):
        return None

    support_count, capacity = _grid_shape_support_from_evidence(current)
    if (
        capacity < len(current)
        or support_count < GRID_SHAPE_INSTANCE_MIN_SUPPORTED_CELLS
        or support_count / max(len(current), 1) < PRIOR_TOPOLOGY_MIN_CURRENT_SUPPORT_RATIO
    ):
        return None

    current_areas = [item.width * item.height for item in current]
    prior_areas = [item.width * item.height for item in prior]
    if (
        min(current_areas, default=0.0) / max(max(current_areas, default=0.0), 1e-9) < 0.60
        or min(prior_areas, default=0.0) / max(max(prior_areas, default=0.0), 1e-9) < 0.60
    ):
        return None

    assigned: dict[int, list[Detection]] = {index: [] for index in range(len(prior))}
    for child in current:
        matches = sorted(
            (
                (_intersection_over_smaller(parent, child), index)
                for index, parent in enumerate(prior)
            ),
            reverse=True,
        )
        if not matches or matches[0][0] < PRIOR_TOPOLOGY_MIN_CHILD_CONTAINMENT:
            continue
        # Overlapping/ambiguous priors cannot establish a unique topology.
        if len(matches) > 1 and matches[1][0] >= PRIOR_TOPOLOGY_MIN_CHILD_CONTAINMENT:
            return None
        assigned[matches[0][1]].append(child)

    def separated_siblings(parent: Detection, children: list[Detection]) -> bool:
        for index, left in enumerate(children):
            for right in children[index + 1 :]:
                x_overlap = max(
                    0.0,
                    min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
                ) / max(min(left.width, right.width), 1e-9)
                y_overlap = max(
                    0.0,
                    min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
                ) / max(min(left.height, right.height), 1e-9)
                center_dx = abs(left.x + left.width / 2 - (right.x + right.width / 2)) / max(
                    parent.width, 1e-9
                )
                center_dy = abs(left.y + left.height / 2 - (right.y + right.height / 2)) / max(
                    parent.height, 1e-9
                )
                if (center_dx >= 0.28 and y_overlap >= 0.55) or (
                    center_dy >= 0.28 and x_overlap >= 0.55
                ):
                    return True
        return False

    split_indices: list[int] = []
    mapped_children = 0
    for prior_index, children in assigned.items():
        if len(children) < 2:
            continue
        parent = prior[prior_index]
        parent_area = parent.width * parent.height
        child_area_ratios = [
            child.width * child.height / max(parent_area, 1e-9) for child in children
        ]
        if not all(
            PRIOR_TOPOLOGY_MIN_CHILD_AREA_RATIO <= ratio <= PRIOR_TOPOLOGY_MAX_CHILD_AREA_RATIO
            for ratio in child_area_ratios
        ):
            continue
        if any(
            _intersection_over_smaller(left, right) > GRID_INSTANCE_MAX_PEER_OVERLAP
            for index, left in enumerate(children)
            for right in children[index + 1 :]
        ):
            continue
        if not separated_siblings(parent, children):
            continue
        filled_area = sum(
            _intersection_over_smaller(parent, child) * child.width * child.height
            for child in children
        )
        if filled_area / max(parent_area, 1e-9) < PRIOR_TOPOLOGY_MIN_PRIOR_FILL_RATIO:
            continue
        split_indices.append(prior_index)
        mapped_children += len(children)

    required_split_count = max(
        2,
        int(np.ceil(len(prior) * PRIOR_TOPOLOGY_MIN_SPLIT_PRIOR_RATIO)),
    )
    if len(split_indices) < required_split_count or mapped_children / max(len(current), 1) < 0.75:
        return None
    return {
        "prior_topology_rejected": 1,
        "prior_topology_rejection_reason": "current_layout_splits_multiple_prior_slots",
        "prior_topology_merged_slot_count": len(split_indices),
        "prior_topology_mapped_current_count": mapped_children,
        "current_topology_count": len(current),
        "current_grid_instance_support_count": support_count,
        "current_grid_capacity": capacity,
    }


def _prior_is_manually_confirmed(prior: list[Detection]) -> bool:
    return any(
        signal in {"manual_override", "user_confirmed_quad", "user_confirmed_quads"}
        for item in prior
        for signal in item.evidence
    )


def detect_layout(
    content: bytes,
    *,
    dpi: int = 300,
    expected_count: int | None = None,
    prior: list[Detection] | None = None,
    stationary_prior: bool = False,
) -> LayoutHypothesis:
    """Recover a sheet layout from current pixels and optional front-side slots.

    ``stationary_prior`` represents the product's normal workflow: the user
    flips each card in place. In that mode front regions are topology/search
    slots, not evidence that a back-side object exists. Empty slots are omitted
    and the full-bed count projection is disabled; it cannot manufacture a
    region merely to make the two side counts agree.
    """
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码扫描图像")
    local = _local_candidates(content, dpi=dpi)
    local_conflicts = _layout_conflicts(local)
    if local_conflicts:
        local = _non_conflicting_subset(local)
    single_slab_guard_active = any(
        "single_slab_grid_fragmentation_guard" in item.evidence for item in local
    )
    prior = prior or []
    count = expected_count or len(local)
    if count <= 0:
        return _clamp_layout_to_source(
            LayoutHypothesis(
                (),
                0.0,
                0,
                0,
                signals={"candidate_count": 0},
                rejected_reasons=("no_card_outline_detected",),
            )
        )
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    projection_x, projection_y = _projection_evidence(image, local)

    def periodicity(values: list[float]) -> float:
        sampled = np.asarray(values, dtype=np.float32)
        sampled -= sampled.mean()
        correlation = np.correlate(sampled, sampled, mode="full")[len(sampled) - 1 :]
        start = max(2, len(sampled) // 12)
        return (
            round(float(correlation[start:].max() / max(correlation[0], 1e-6)), 4)
            if len(sampled) > start
            else 0.0
        )

    small = cv2.resize(gray, (min(900, gray.shape[1]), min(900, gray.shape[0])))
    lines = cv2.HoughLinesP(
        cv2.Canny(small, 45, 130),
        1,
        np.pi / 180,
        80,
        minLineLength=min(small.shape) // 8,
        maxLineGap=20,
    )
    recovered_instances = [
        item for item in local if "current_instance_grid_reconciliation" in item.evidence
    ]
    physical_objects = [item for item in local if "physical_object_envelope" in item.evidence]
    complete_current_object_layout = (
        bool(local)
        and len(physical_objects) == len(local)
        and all(
            "current_object_layout" in item.evidence
            and "grid_oversegmentation_rejected" in item.evidence
            for item in local
        )
    )
    current_object_layout_authoritative = complete_current_object_layout and not (
        stationary_prior and _prior_is_manually_confirmed(prior)
    )
    replaced_grid_region_count = sum(
        int(signal.removeprefix("replaced_grid_regions:"))
        for item in recovered_instances
        for signal in item.evidence
        if signal.startswith("replaced_grid_regions:")
    )
    base_signals: dict[str, float | int | str | list[float]] = {
        "projection_x_periodicity": periodicity(projection_x),
        "projection_y_periodicity": periodicity(projection_y),
        "line_segment_count": 0 if lines is None else len(lines),
        "candidate_routes": "multi_threshold+adaptive+gradient_lines+projection+components+mixed_slab_instances",
        "single_slab_grid_fragmentation_guard": int(single_slab_guard_active),
        "row_local_body_recovery": int(
            any("row_local_body_recovery" in item.evidence for item in local)
        ),
        "current_instance_recovery": len(recovered_instances),
        "current_instance_replaced_grid_regions": replaced_grid_region_count,
        "current_instance_reference_boxes": [
            round(value, 6)
            for item in recovered_instances
            for value in (item.x, item.y, item.width, item.height)
        ],
        "physical_object_envelope_count": len(physical_objects),
        "grid_oversegmentation_rejected": int(
            any("grid_oversegmentation_rejected" in item.evidence for item in physical_objects)
        ),
        "current_object_layout_authoritative": int(current_object_layout_authoritative),
        "mixed_orientation_object_layout": int(
            any("mixed_orientation_object_layout" in item.evidence for item in physical_objects)
        ),
        "physical_object_reference_boxes": [
            round(value, 6)
            for item in physical_objects
            for value in (item.x, item.y, item.width, item.height)
        ],
        "irregular_saturation_content_recall": (
            round(_saturation_content_recall(image, local), 4) if single_slab_guard_active else 0.0
        ),
        "prior_topology_rejected": 0,
        "current_topology_count": len(local),
    }
    prior_topology: dict[str, float | int | str] | None = None
    if stationary_prior and expected_count and prior:
        if current_object_layout_authoritative:
            if len(prior) > len(local):
                rejection_reason = "current_complete_objects_replace_oversegmented_prior"
            elif len(prior) < len(local):
                rejection_reason = "current_complete_objects_replace_undersegmented_prior"
            else:
                rejection_reason = "current_complete_object_layout_preferred"
            prior_topology = {
                "prior_topology_rejected": 1,
                "prior_topology_rejection_reason": rejection_reason,
                "prior_topology_prior_count": len(prior),
                "current_topology_count": len(local),
            }
        else:
            prior_topology = _prior_topology_split_evidence(prior, local)
    if prior_topology is not None:
        base_signals.update(prior_topology)
    hypotheses: list[LayoutHypothesis] = []
    if expected_count and prior and len(prior) == count and prior_topology is None:
        prior_transforms = (
            ("identity",)
            if stationary_prior
            else ("mirror_x", "mirror_y", "mirror_x+mirror_y", "identity")
        )
        for transform in prior_transforms:
            transformed = _transform_prior(prior, transform)
            boxes: list[Detection] = []
            slot_occupancy: list[float] = []
            for slot_index, box in enumerate(transformed):
                occupied, metrics = _grid_cell_occupancy(
                    image,
                    box.x,
                    box.y,
                    box.x + box.width,
                    box.y + box.height,
                )
                if stationary_prior and not occupied:
                    continue
                slot_occupancy.append(
                    min(
                        metrics["ink_row_coverage"],
                        metrics["ink_column_coverage"],
                    )
                )
                boxes.append(
                    Detection(
                        box.x,
                        box.y,
                        box.width,
                        box.height,
                        box.angle,
                        box.confidence,
                        box.failure,
                        box.points,
                        (
                            *box.evidence,
                            f"front_slot_index:{slot_index}",
                            "stationary_slot_occupancy"
                            if stationary_prior
                            else "transformed_prior_occupancy",
                        ),
                    )
                )
            if not boxes:
                continue
            scores = [_boundary_score(gray, box) for box in boxes]
            stationary_similarity = _stationary_layout_similarity(prior, boxes)
            movement_penalty = 0.04 * (1 - stationary_similarity)
            refined = tuple(
                Detection(
                    b.x,
                    b.y,
                    b.width,
                    b.height,
                    b.angle,
                    round(0.68 + 0.28 * s, 4),
                    "boundary_evidence_weak" if s < 0.08 else None,
                    b.points,
                    (*b.evidence, "front_to_back_prior", "gradient_boundary", "global_count"),
                )
                for b, s in zip(boxes, scores)
            )
            rows, columns = _grid_shape(len(boxes), image, boxes)
            hypotheses.append(
                LayoutHypothesis(
                    refined,
                    round(0.72 + 0.23 * float(np.mean(scores)) - movement_penalty, 4),
                    rows,
                    columns,
                    transform,
                    {
                        **base_signals,
                        "candidate_count": len(local),
                        "expected_count": count,
                        "mean_boundary_energy": round(float(np.mean(scores)), 4),
                        "stationary_layout_similarity": round(stationary_similarity, 4),
                        "movement_penalty": round(movement_penalty, 4),
                        "dpi": dpi,
                        "prior_weight": 0.72,
                        "prior_role": "identity_slot_topology_only"
                        if stationary_prior
                        else "layout_hypothesis",
                        "occupied_slot_count": len(boxes),
                        "missing_slot_count": count - len(boxes),
                        "minimum_slot_body_coverage": round(min(slot_occupancy, default=0.0), 4),
                    },
                )
            )
        if not stationary_prior:
            rows, columns = _grid_shape(count, image, prior)
            independent, gap_quality = _projection_gap_grid_detections(image, rows, columns)
            if len(independent) == count and not _layout_conflicts(independent):
                hypotheses.append(
                    LayoutHypothesis(
                        tuple(independent),
                        round(0.78 + 0.16 * gap_quality, 4),
                        rows,
                        columns,
                        "independent",
                        {
                            **base_signals,
                            "candidate_count": len(local),
                            "expected_count": count,
                            "projection_gap_quality": round(gap_quality, 4),
                            "dpi": dpi,
                            "prior_weight": 0.0,
                        },
                    )
                )
    if (len(local) == count or prior_topology is not None) and not _layout_conflicts(local):
        rows, columns = _grid_shape(
            len(local) if prior_topology is not None else count,
            image,
            local,
        )
        current_grid_score = _current_side_grid_score(image, gray, local)
        full_bed_grid_exact_count = current_grid_score is not None
        # Occupancy-filtered full-bed cells come from the current image and can
        # correctly leave unused scanner-bed capacity below the last row. In
        # contrast, an expected-count projection grid always partitions the
        # entire bed and can therefore invent blank rows. When its count agrees
        # with the confirmed front, treat current-side occupancy as stronger
        # geometry evidence than a transformed front prior.
        local_score = (
            current_grid_score
            if full_bed_grid_exact_count
            else round(float(np.mean([x.confidence for x in local])), 4)
        )
        hypotheses.append(
            LayoutHypothesis(
                tuple(local),
                local_score,
                rows,
                columns,
                signals={
                    **base_signals,
                    "candidate_count": len(local),
                    "expected_count": count,
                    "dpi": dpi,
                    "sources": "full_bed_grid+current_side_occupancy"
                    if full_bed_grid_exact_count
                    else "multi_threshold+gradient+components",
                    "front_prior_role": (
                        "rejected_coarse_topology"
                        if prior_topology is not None
                        else (
                            "count_only"
                            if full_bed_grid_exact_count and expected_count
                            else "not_applicable"
                        )
                    ),
                    "current_grid_evidence_score": current_grid_score or 0.0,
                },
            )
        )
    if expected_count and len(prior) != count and len(local) != count:
        # Count is metadata/expectation, never permission to synthesize evenly
        # divided crops. Preserve the current-side candidates so missing slots
        # remain visible as a count mismatch and can be edited or rescanned.
        rows, columns = _grid_shape(len(local), image, local) if local else (0, 0)
        hypotheses.append(
            LayoutHypothesis(
                tuple(local),
                round(float(np.mean([item.confidence for item in local])), 4) if local else 0.0,
                rows,
                columns,
                signals={
                    **base_signals,
                    "candidate_count": len(local),
                    "expected_count": count,
                    "dpi": dpi,
                    "manufactured_count": 0,
                },
                rejected_reasons=("prior_missing", "expected_count_not_manufactured"),
            )
        )
    if not hypotheses:
        rows, columns = _grid_shape(len(local), image, local)
        hypotheses.append(
            LayoutHypothesis(
                tuple(local),
                round(float(np.mean([x.confidence for x in local])), 4),
                rows,
                columns,
                signals={
                    **base_signals,
                    "candidate_count": len(local),
                    "expected_count": count,
                    "dpi": dpi,
                },
            )
        )
    valid = [item for item in hypotheses if not _layout_conflicts(item.detections)]
    if valid:
        return _clamp_layout_to_source(
            max(
                valid,
                key=lambda item: (
                    int(
                        stationary_prior
                        and item.transform == "identity"
                        and item.signals.get("prior_role") == "identity_slot_topology_only"
                        and len(item.detections) == count
                    ),
                    item.score - 0.3 * abs(len(item.detections) - count),
                ),
            )
        )
    safest = _non_conflicting_subset(list(hypotheses[0].detections))
    rows, columns = _grid_shape(len(safest), image, safest) if safest else (0, 0)
    return _clamp_layout_to_source(
        LayoutHypothesis(
            tuple(safest),
            0.0,
            rows,
            columns,
            signals={
                **base_signals,
                "candidate_count": len(local),
                "expected_count": count,
                "dpi": dpi,
            },
            rejected_reasons=local_conflicts or ("invalid_layout_geometry",),
        )
    )


def detect_cards(
    content: bytes,
    *,
    dpi: int = 300,
    expected_count: int | None = None,
    prior: list[Detection] | None = None,
    stationary_prior: bool = False,
) -> list[Detection]:
    return list(
        detect_layout(
            content,
            dpi=dpi,
            expected_count=expected_count,
            prior=prior,
            stationary_prior=stationary_prior,
        ).detections
    )


def explain_detection(content: bytes, detections: list[Detection]) -> list[str]:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return ["image_decode_failed"]
    failures = [item.failure for item in detections if item.failure]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if not detections:
        failures.append("no_card_outline_detected")
    if float(np.mean(gray >= 250)) > 0.35:
        failures.append("possible_glare_or_empty_bed")
    if any(item.confidence < 0.65 for item in detections):
        failures.append("low_outline_confidence")
    for index, left in enumerate(detections):
        for right in detections[index + 1 :]:
            x_overlap = max(
                0.0, min(left.x + left.width, right.x + right.width) - max(left.x, right.x)
            )
            y_overlap = max(
                0.0, min(left.y + left.height, right.y + right.height) - max(left.y, right.y)
            )
            intersection = x_overlap * y_overlap
            smaller = min(left.width * left.height, right.width * right.height)
            if smaller and intersection / smaller > 0.15:
                failures.append("possible_overlapping_cards")
                break
    return list(dict.fromkeys(failures))


def visual_signals(image: np.ndarray) -> dict[str, list[float] | int]:
    resized = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).flatten()
    histogram /= max(float(histogram.sum()), 1.0)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    dct = cv2.dct(gray.astype(np.float32))[:8, :8]
    median = float(np.median(dct[1:]))
    bits = (dct > median).flatten()
    phash = sum(int(value) << index for index, value in enumerate(bits))
    return {
        "color_histogram": [round(float(value), 6) for value in histogram],
        "perceptual_hash": phash,
    }
