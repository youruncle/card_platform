"""Keep source-image object evidence separate from candidate crop geometry."""

from collections.abc import Sequence
from dataclasses import replace

import cv2
import numpy as np

from card_platform.flatbed_detection import (
    Detection,
    _corroborated_current_instances,
    _foreground_detections,
    _irregular_slab_detections,
    _structural_envelope_detections,
)


def _polygon(item: Detection) -> np.ndarray:
    values = np.asarray(
        item.points
        or (
            (item.x, item.y),
            (item.x + item.width, item.y),
            (item.x + item.width, item.y + item.height),
            (item.x, item.y + item.height),
        ),
        dtype=np.float32,
    )
    return cv2.convexHull(values).reshape(-1, 2)


def _source_supported(item: Detection) -> bool:
    evidence = set(item.evidence)
    if evidence.intersection(
        {"current_pixel_quad", "cell_extent_fallback", "quad_unresolved", "search_region_only", "front_to_back_prior",
         "slot_prompt_only", "manual_override", "user_confirmed_quad", "user_confirmed_quads"}
    ):
        return False
    return bool(
        evidence.intersection(
            {"multi_route_instance_corroboration", "independent_outer_envelope_support",
             "separated_rotated_object"}
        )
        or (
            "grading_label_association" in evidence
            and evidence.intersection({"saturation_content_island", "structural_edge_envelope"})
        )
    )


def _owns_multiple_centres(item: Detection, prompts: Sequence[Detection]) -> bool:
    # Capacity centres can legitimately divide one slab into several cells.
    # Only actual source-image proposals participate in the anti-merge check.
    actual_prompts = [
        peer for peer in prompts
        if not set(peer.evidence).intersection(
            {"full_bed_grid", "cell_extent_fallback", "search_region_only", "slot_prompt_only",
             "front_to_back_prior"}
        )
    ]
    centres = {(float(peer.x + peer.width / 2), float(peer.y + peer.height / 2))
               for peer in actual_prompts}
    polygon = _polygon(item)
    if sum(cv2.pointPolygonTest(polygon, centre, False) > 0 for centre in centres) > 1:
        return True
    if not actual_prompts:
        return False
    own = max(actual_prompts, key=lambda peer: _overlap(item, peer)[0])
    own_centre = (float(own.x + own.width / 2), float(own.y + own.height / 2))
    for peer in actual_prompts:
        if peer is own or cv2.pointPolygonTest(_polygon(peer), own_centre, False) >= 0:
            continue
        intersection = max(0.0, float(cv2.intersectConvexConvex(polygon, _polygon(peer))[0]))
        # A label-shaped island can annex a third of the adjacent card while
        # its centre stays outside the false envelope. This is the same peer
        # ownership limit used by the existing multi-panel object collector.
        if intersection / max(cv2.contourArea(_polygon(peer)), 1e-9) > 0.25:
            return True
    return False


def _threshold_observations(image: np.ndarray) -> list[Detection]:
    """Whole-image intensity routes; threshold variants form ONE family.

    Closed pale-card interiors can be holes within a dark scanner component,
    so use all contours. No scanner capacity or selected crop supplies bounds.
    """
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    masks = [(f"threshold_{value}", cv2.threshold(gray, value, 255, cv2.THRESH_BINARY_INV)[1])
             for value in (95, 120, 165, 205, 235)]
    masks.extend([
        ("otsu_threshold", cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]),
        ("adaptive_threshold", cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 81, 5)),
    ])
    observations = []
    for route, mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            area = bw * bh
            aspect = max(bw, bh) / max(1, min(bw, bh))
            if not (0.015 <= area / (width * height) <= 0.35 and 1.18 <= aspect <= 1.95
                    and cv2.contourArea(contour) / max(area, 1) >= 0.12):
                continue
            region = gray[y:y+bh, x:x+bw]
            saturation = cv2.cvtColor(image[y:y+bh, x:x+bw], cv2.COLOR_BGR2HSV)[:, :, 1]
            edges = cv2.Canny(cv2.GaussianBlur(region, (5, 5), 0), 30, 90)
            if (np.mean(saturation > 22) < 0.08 and np.mean(edges > 0) < 0.005
                    and np.std(region) < 25):
                continue
            observations.append(Detection(
                x/width, y/height, bw/width, bh/height, 0.0, 0.62,
                points=((x/width, y/height), ((x+bw)/width, y/height),
                        ((x+bw)/width, (y+bh)/height), (x/width, (y+bh)/height)),
                evidence=(route,),
            ))
    return observations


def _overlap(left: Detection, right: Detection) -> tuple[float, float]:
    a, b = _polygon(left), _polygon(right)
    intersection = max(0.0, float(cv2.intersectConvexConvex(a, b)[0]))
    area_a, area_b = cv2.contourArea(a), cv2.contourArea(b)
    return intersection / max(area_a + area_b - intersection, 1e-9), intersection / max(
        min(area_a, area_b), 1e-9
    )


def _nominal_object_area(prompts: Sequence[Detection]) -> float:
    capacities = []
    for item in prompts:
        for signal in item.evidence:
            if signal.startswith("grid_capacity:"):
                try:
                    rows, columns = map(int, signal.removeprefix("grid_capacity:").split("x"))
                    if rows > 0 and columns > 0:
                        capacities.append(1.0 / (rows * columns))
                except ValueError:
                    continue
    # A physical envelope may carry a padding margin. Capacity gives only a
    # size scale to the existing global route medoid, never reference corners.
    areas = capacities or [d.width * d.height for d in prompts if d.width > 0 and d.height > 0]
    return float(np.median(areas)) if areas else 0.0


def independent_object_references(
    image: np.ndarray, prompts: Sequence[Detection]
) -> tuple[Detection, ...]:
    """Freeze complete current-image evidence before any crop is selected.

    A resolver's ``current_pixel_quad``/Canny votes cannot establish reference
    truth. Existing independent instances keep their actual polygon. When some
    proposals are only search allocations, whole-image route families may
    supply independently corroborated objects; allocations supply size hints
    only, and never accepted coordinates.
    """
    if not prompts:
        return ()
    trusted = [item for item in prompts if _source_supported(item)]
    global_instances = []
    if not all("separated_rotated_object" in item.evidence for item in trusted) or not trusted:
        foreground = _foreground_detections(image)
        structural = _structural_envelope_detections(image)
        irregular = _irregular_slab_detections(image)
        intensity = _threshold_observations(image)
        observations = [item for item in (*structural, *irregular, *intensity)
                        if not _owns_multiple_centres(item, prompts)]
        global_instances = _corroborated_current_instances(
            image, foreground, observations,
            cell_area=_nominal_object_area(prompts), prefer_outer_envelope=False,
        )
    candidates = []
    for item in trusted:
        replacement = None
        # Keep genuinely observed rotated/labelled/outer geometries. An
        # ordinary margined physical envelope can include a neighbouring
        # holder's edge; recover its independently corroborated raw medoid.
        if "physical_object_envelope" in item.evidence and not set(item.evidence).intersection(
            {"grading_label_association", "independent_outer_envelope_support", "separated_rotated_object"}
        ):
            matching = [
                candidate for candidate in global_instances
                if _overlap(item, candidate)[0] >= 0.70
                and 0.65 <= candidate.width * candidate.height / max(item.width * item.height, 1e-9) <= 1.0
                and not _owns_multiple_centres(candidate, prompts)
            ]
            replacement = max(matching, key=lambda candidate: _overlap(item, candidate)[0], default=None)
        candidates.append(
            (replacement, "global_unpadded_instance") if replacement is not None
            else (item, "pre_resolver_instance")
        )
    if len(trusted) != len(prompts):
        candidates.extend(
            (item, "global_multi_route") for item in global_instances
            )
    accepted: list[Detection] = []
    for item, route in candidates:
        if _owns_multiple_centres(item, prompts):
            continue
        polygon = _polygon(item)
        if (
            polygon.shape != (4, 2)
            or not np.isfinite(polygon).all()
            or (polygon < -0.001).any()
            or (polygon > 1.001).any()
            or not 1e-6 < cv2.contourArea(polygon) <= 0.40
        ):
            continue
        if any(iou >= 0.70 or smaller >= 0.94 for iou, smaller in (
            _overlap(item, peer) for peer in accepted
        )):
            continue
        evidence = tuple(dict.fromkeys((*item.evidence, "independent_source_object", "source_reference:" + route)))
        accepted.append(replace(item, points=tuple(tuple(map(float, p)) for p in polygon), evidence=evidence))
    return tuple(sorted(accepted, key=lambda item: (item.y, item.x)))
