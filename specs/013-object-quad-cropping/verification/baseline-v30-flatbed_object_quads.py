"""Resolve current-pixel card quads; search allocations are never object truth."""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from card_platform.flatbed_detection import Detection, LayoutHypothesis, _foreground_detections, _structural_envelope_detections

from card_platform.flatbed_object_content import build_content_references, content_preservation
from card_platform.flatbed_object_references import independent_object_references


def _polygon(item: Detection) -> np.ndarray:
    return cv2.convexHull(
        np.asarray(
            item.points
            or (
                (item.x, item.y),
                (item.x + item.width, item.y),
                (item.x + item.width, item.y + item.height),
                (item.x, item.y + item.height),
            ),
            np.float32,
        )
    ).reshape(-1, 2)


def _intersection(left: np.ndarray, right: np.ndarray) -> float:
    return max(0.0, float(cv2.intersectConvexConvex(left, right)[0]))


def _iou(left: np.ndarray, right: np.ndarray) -> float:
    intersection = _intersection(left, right)
    return intersection / max(cv2.contourArea(left) + cv2.contourArea(right) - intersection, 1e-9)


def _observations(image: np.ndarray, prompt: Detection):
    height, width = image.shape[:2]
    # These are search margins only; every accepted corner comes from pixels.
    left = max(0, round((prompt.x - prompt.width * 0.08) * width))
    top = max(0, round((prompt.y - prompt.height * 0.15) * height))
    right = min(width, round((prompt.x + prompt.width * 1.08) * width))
    bottom = min(height, round((prompt.y + prompt.height * 1.15) * height))
    roi = image[top:bottom, left:right]
    if roi.size == 0:
        return []
    scale = min(1.0, 900 / max(roi.shape[:2]))
    small = cv2.resize(roi, None, fx=scale, fy=scale)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    masks = []
    for low, high in ((20, 60), (35, 100), (45, 130), (50, 150), (60, 180), (80, 200)):
        edge = cv2.Canny(gray, low, high)
        masks.append(
            (f"edge_{low}", cv2.morphologyEx(edge, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)))
        )
    for value in (130, 165, 185, 205):
        masks.append(
            (f"threshold_{value}", cv2.threshold(gray, value, 255, cv2.THRESH_BINARY_INV)[1])
        )
    masks.append(("otsu", cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]))
    observations = []
    prompt_area = prompt.width * prompt.height
    for route, mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            bx, by, bw, bh = cv2.boundingRect(contour)
            # A search-ROI cut is not a physical card edge. Source-image
            # clipping is retained with a warning; internal ROI clipping is
            # rejected so an allocation boundary cannot become a corner.
            if (
                (left > 0 and bx <= 1)
                or (top > 0 and by <= 1)
                or (right < width and bx + bw >= small.shape[1] - 1)
                or (bottom < height and by + bh >= small.shape[0] - 1)
            ):
                continue
            rect = cv2.minAreaRect(contour)
            short, long = sorted(rect[1])
            if short <= 0:
                continue
            area = short * long / (scale * scale * width * height)
            if not (
                0.015 <= area <= 0.40
                and 0.20 <= area / max(prompt_area, 1e-9) <= 1.45
                and 1.15 <= long / short <= 2.6
            ):
                continue
            fill = cv2.contourArea(contour) / max(short * long, 1)
            if not route.startswith("edge_") and fill < 0.80:
                continue
            raw = cv2.boxPoints(rect) / scale + [left, top]
            raw = raw / [width, height]
            if (raw < -0.06).any() or (raw > 1.06).any():
                continue
            center = raw.mean(axis=0)
            if (
                abs(center[0] - (prompt.x + prompt.width / 2)) > prompt.width * 0.32
                or abs(center[1] - (prompt.y + prompt.height / 2)) > prompt.height * 0.32
            ):
                continue
            # A four-sided contour gives perspective geometry; otherwise fit
            # the observed rotated rectangle instead of a horizontal box.
            approx = cv2.approxPolyDP(contour, 0.015 * cv2.arcLength(contour, True), True)
            if (
                len(approx) == 4
                and cv2.isContourConvex(approx)
                and cv2.contourArea(approx) >= cv2.contourArea(contour) * 0.95
            ):
                raw = (approx.reshape(4, 2) / scale + [left, top]) / [width, height]
            points = cv2.convexHull(raw.astype(np.float32)).reshape(-1, 2)
            if len(points) != 4:
                continue
            observations.append((points, route, area, fill))
    return observations


def _expand_inner_search_prompts(image: np.ndarray, prompts: list[Detection]) -> list[Detection]:
    """Use containing foreground only to search beyond a printed inner panel.

    Foreground may describe a capacity cell, so it supplies no accepted corners.
    Independent pixel contours must still resolve the enlarged search region.
    Never merge two prompt centres into one search region.
    """
    foreground = _foreground_detections(image)
    expanded = []
    for index, prompt in enumerate(prompts):
        original = _polygon(prompt)
        choices = []
        for candidate in foreground:
            polygon = _polygon(candidate)
            ratio = candidate.width * candidate.height / max(prompt.width * prompt.height, 1e-9)
            if not 1.4 <= ratio <= 3.0:
                continue
            if _intersection(polygon, original) / max(cv2.contourArea(original), 1e-9) < 0.95:
                continue
            if any(
                j != index
                and cv2.pointPolygonTest(
                    polygon,
                    (float(peer.x + peer.width / 2), float(peer.y + peer.height / 2)),
                    False,
                ) >= 0
                for j, peer in enumerate(prompts)
            ):
                continue
            choices.append(candidate)
        expanded.append(min(choices, key=lambda d: d.width * d.height) if choices else prompt)
    return expanded


def resolve_object_layout(content: bytes, layout: LayoutHypothesis) -> LayoutHypothesis:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码扫描图像")
    resolved = []
    candidate_sets = []
    raw_prompts = list(layout.detections)
    source_objects = independent_object_references(image, raw_prompts)
    # References are frozen before choosing any four corners. They may expand
    # a search allocation, but selected geometry never becomes its own proof.
    owners = {index: [] for index in range(len(raw_prompts))}
    for reference in source_objects:
        if raw_prompts:
            index = max(range(len(raw_prompts)), key=lambda i: _iou(_polygon(reference), _polygon(raw_prompts[i])))
            if _iou(_polygon(reference), _polygon(raw_prompts[index])) >= 0.15:
                owners[index].append(reference)
    all_prompts = _expand_inner_search_prompts(image, raw_prompts)
    for index, references in owners.items():
        if len(references) == 1:
            all_prompts[index] = references[0]
    content_references = build_content_references(image, source_objects, scene_regions=all_prompts)
    source_content = {index: [r for r in content_references if r.source in references]
                      for index, references in owners.items()}
    search_content = build_content_references(image, all_prompts, search_only=True, scene_regions=all_prompts)
    rejected_content = []
    structural_envelopes = _structural_envelope_detections(image)
    for index, prompt in enumerate(all_prompts):
        guards = source_content[index]
        observations = _observations(image, prompt)
        # Reflective artwork can erase the outer border at higher Canny
        # thresholds. Whole-image structural closure can corroborate an
        # observed perimeter, but cannot invent a quad without a local contour.
        local_observations = tuple(observations)
        for points, _, area, fill in local_observations:
            if any(_iou(_polygon(envelope), points) >= 0.85 for envelope in structural_envelopes):
                observations.append((points, "structural_envelope", area, fill))
        ranked = []
        for points, route, area, fill in observations:
            peers = [other for other in observations if _iou(points, other[0]) >= 0.75]
            routes = {other[1] for other in peers}
            if len(routes) < 2:
                continue
            # Reject contours belonging to a neighbouring search region, not
            # merely rectangles whose axis-aligned boxes happen to overlap.
            owns_neighbour = any(
                peer_index != index
                and cv2.pointPolygonTest(
                    points, (float(peer.x + peer.width / 2), float(peer.y + peer.height / 2)), False
                )
                > 0
                for peer_index, peer in enumerate(all_prompts)
            )
            if owns_neighbour:
                continue
            content_checks = [content_preservation(reference, points) for reference in guards]
            if any(not check["preserved"] for check in content_checks):
                rejected_content.append({"index": index, "route": route,
                                         "reason": "original_content_not_preserved", "checks": content_checks})
                continue
            # Prefer the complete supported perimeter over nested art/text.
            ranked.append((area + 0.0005 * len(routes) + 0.001 * min(fill, 1), points, routes))
        # Existing multi-route outer-holder/label associations are current
        # pixel object observations too. Preserve the whole multi-panel holder
        # instead of selecting a closed contour from art printed inside it.
        protected_outer = (
            "independent_outer_envelope_support" in prompt.evidence
            or "grading_label_association" in prompt.evidence
            or ("structural_edge_envelope" in prompt.evidence and prompt.y + prompt.height >= 0.985)
        )

        def near_axis(points):
            edges = (np.roll(points, -1, axis=0) - points) * [image.shape[1], image.shape[0]]
            edge = edges[np.argmax(np.linalg.norm(edges, axis=1))]
            angle = abs(float(np.degrees(np.arctan2(edge[1], edge[0])))) % 90
            return min(angle, 90 - angle) < 3

        # Only independently content-verified perimeters may compete with an
        # outer object. Horizontal span is never evidence of completeness.
        full_perimeters = list(ranked) if guards else []
        tilted_perimeter = any(not near_axis(choice[1]) for choice in full_perimeters)
        if protected_outer and tilted_perimeter:
            ranked = [
                (max(score, prompt.width * prompt.height + 0.03), points, routes)
                if any(points is choice[1] for choice in full_perimeters) and not near_axis(points)
                else (score, points, routes)
                for score, points, routes in ranked
            ]
        corroborated = "multi_route_instance_corroboration" in prompt.evidence
        axis_outer = (
            corroborated
            and "physical_object_envelope" in prompt.evidence
            and full_perimeters
            and not tilted_perimeter
        )
        polygon = _polygon(prompt)
        outer_preserved = all(content_preservation(reference, polygon)["preserved"] for reference in guards)
        if (protected_outer or axis_outer or (not ranked and corroborated)) and outer_preserved:
            ranked.append(
                (
                    cv2.contourArea(polygon)
                    + (
                        0.2
                        if "grading_label_association" in prompt.evidence and not tilted_perimeter
                        else 0.01
                    ),
                    polygon,
                    {"current_multiroute_outer_object"},
                )
            )
        distinct = []
        for choice in sorted(ranked, key=lambda item: item[0], reverse=True):
            if not any(_iou(choice[1], other[1]) > 0.97 for other in distinct):
                distinct.append(choice)
        candidate_sets.append(distinct[:20])
    beam = [(0.0, [])]
    for index, choices in enumerate(candidate_sets):
        next_beam = []
        for score, selected in beam:
            for choice in choices:
                points = choice[1]
                expanded = np.clip(
                    points.mean(axis=0) + (points - points.mean(axis=0)) * 1.025, 0, 1
                ).astype(np.float32)
                if any(
                    peer_index != index and _intersection(expanded, _polygon(peer)) > 1e-7
                    for peer_index, peer in enumerate(all_prompts)
                ):
                    expanded = np.clip(points, 0, 1).astype(np.float32)
                if any(
                    _intersection(expanded, previous[1])
                    / max(min(cv2.contourArea(expanded), cv2.contourArea(previous[1])), 1e-9)
                    > (
                        0.10
                        if "current_multiroute_outer_object" in choice[2]
                        and "current_multiroute_outer_object" in previous[2]
                        else 0.025
                    )
                    for previous in selected
                    if previous is not None
                ):
                    continue
                next_beam.append((score + choice[0], selected + [(choice[0], expanded, choice[2])]))
            # Unresolved search hints carry a score penalty and never cut cards.
            next_beam.append((score - 0.5, selected + [None]))
        beam = sorted(next_beam, key=lambda item: item[0], reverse=True)[:64]
    def solution_rank(solution):
        score, selected = solution
        failures = 0
        for i, choice in enumerate(selected):
            if source_content[i]:
                continue
            checks = [r for r in search_content if r.source is all_prompts[i]]
            peers = [other[1] for j, other in enumerate(selected) if j != i and other is not None]
            if choice is None or any(not content_preservation(r, choice[1], explained_points=peers)["preserved"] for r in checks):
                failures += 1
        return (-failures, score)
    selected = max(beam, key=solution_rank)[1] if beam else []
    for index, (prompt, choice) in enumerate(zip(all_prompts, selected, strict=True)):
        guards = source_content[index]
        if choice is None:
            # Preserve already proven non-axis-aligned object geometry. Never
            # turn an unresolved capacity cell into an automatic card crop.
            polygon = _polygon(prompt)
            edges = np.roll(polygon, -1, axis=0) - polygon
            tilted = any(abs(e[0]) > 1e-4 and abs(e[1]) > 1e-4 for e in edges)
            if tilted and "separated_rotated_object" in prompt.evidence:
                resolved.append(replace(prompt, evidence=(*prompt.evidence, "current_pixel_quad")))
            else:
                evidence = tuple(
                    e
                    for e in prompt.evidence
                    if e not in ("physical_object_envelope", "current_object_layout")
                )
                resolved.append(
                    replace(
                        prompt,
                        confidence=min(prompt.confidence, 0.49),
                        failure="card_outline_unresolved",
                        evidence=(*evidence, "quad_unresolved", "search_region_only"),
                    )
                )
            continue
        _, points, routes = choice
        points = cv2.convexHull(points).reshape(-1, 2)
        if len(points) != 4:
            resolved.append(
                replace(
                    prompt,
                    failure="card_outline_unresolved",
                    evidence=(*prompt.evidence, "quad_unresolved"),
                )
            )
            continue
        lo, hi = points.min(axis=0), points.max(axis=0)
        edges = (np.roll(points, -1, axis=0) - points) * [image.shape[1], image.shape[0]]
        long_edge = edges[np.argmax(np.linalg.norm(edges, axis=1))]
        angle = float(np.degrees(np.arctan2(long_edge[1], long_edge[0])))
        clipped = bool((points <= 0.002).any() or (points >= 0.998).any())
        evidence = tuple(
            e
            for e in prompt.evidence
            if e not in ("cell_extent_fallback", "ink_extent_refinement", "source_edge_contact", "independent_source_object")
        )
        resolved.append(
            Detection(
                float(lo[0]),
                float(lo[1]),
                float(hi[0] - lo[0]),
                float(hi[1] - lo[1]),
                angle,
                0.92,
                "source_edge_contact" if clipped else None,
                tuple((float(x), float(y)) for x, y in points),
                tuple(
                    dict.fromkeys(
                        (
                            *evidence,
                            "current_pixel_quad",
                            *(("original_content_verified",) if guards else ()),
                            "physical_object_envelope",
                            "current_object_layout",
                            "quad_routes:" + ",".join(sorted(routes)),
                            *(("source_edge_contact",) if clipped else ()),
                        )
                    )
                ),
            )
        )
    # Preserve sequence/identity slots. The downstream polygon overlap and
    # ownership audit will mark ambiguous candidates for human correction.
    unresolved = sum("quad_unresolved" in d.evidence for d in resolved)
    return replace(
        layout,
        detections=tuple(resolved),
        object_references=source_objects,
        search_regions=tuple(all_prompts),
        signals={
            **layout.signals,
            "independent_source_object_count": len(source_objects),
            "content_rejected_candidate_count": len(rejected_content),
            "content_candidate_rejections": rejected_content[:100],
            "geometry_source": "current_pixel_quads",
            "grid_role": "search_and_missing_object_advisory",
            "resolved_quad_count": len(resolved) - unresolved,
            "unresolved_quad_count": unresolved,
        },
    )
