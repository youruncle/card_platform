from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import cv2
import numpy as np

from card_platform.flatbed_detection import Detection

SAM2_SAFETY_MARGIN_RATIO = 0.012
SAM2_MAX_PEER_OVERLAP = 0.18
SAM2_MIN_PROTECTED_OBJECT_COVERAGE = 0.95


def _expand_safety_margin(
    x: float, y: float, width: float, height: float
) -> tuple[float, float, float, float]:
    """Add a narrow source-image border around an accepted SAM 2 mask."""
    margin_x = width * SAM2_SAFETY_MARGIN_RATIO
    margin_y = height * SAM2_SAFETY_MARGIN_RATIO
    left = max(0.0, x - margin_x)
    top = max(0.0, y - margin_y)
    right = min(1.0, x + width + margin_x)
    bottom = min(1.0, y + height + margin_y)
    return left, top, right - left, bottom - top


def _expanded_oriented_geometry(
    raw_points: object,
    fallback: tuple[float, float, float, float],
) -> tuple[float, float, float, float, tuple[tuple[float, float], ...]]:
    if not isinstance(raw_points, list) or len(raw_points) != 4:
        x, y, width, height = _expand_safety_margin(*fallback)
        return (
            x,
            y,
            width,
            height,
            (
                (x, y),
                (x + width, y),
                (x + width, y + height),
                (x, y + height),
            ),
        )
    points = np.array(raw_points, dtype=np.float64)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("invalid_points")
    center = points.mean(axis=0)
    points = center + (points - center) * (1 + 2 * SAM2_SAFETY_MARGIN_RATIO)
    points = np.clip(points, 0.0, 1.0)
    left, top = points.min(axis=0)
    right, bottom = points.max(axis=0)
    normalized = tuple((float(point[0]), float(point[1])) for point in points)
    return float(left), float(top), float(right - left), float(bottom - top), normalized


def _points(detection: Detection) -> np.ndarray:
    if detection.points:
        return np.array(detection.points, dtype=np.float32)
    return np.array(
        [
            (detection.x, detection.y),
            (detection.x + detection.width, detection.y),
            (detection.x + detection.width, detection.y + detection.height),
            (detection.x, detection.y + detection.height),
        ],
        dtype=np.float32,
    )


def _intersection_over_smaller(left: Detection, right: Detection) -> float:
    left_points, right_points = _points(left), _points(right)
    left_area = abs(float(cv2.contourArea(left_points)))
    right_area = abs(float(cv2.contourArea(right_points)))
    smaller = min(left_area, right_area)
    if not smaller:
        return 0.0
    overlap, _ = cv2.intersectConvexConvex(
        cv2.convexHull(left_points), cv2.convexHull(right_points)
    )
    return float(overlap) / smaller


def _original_polygon_coverage(original: Detection, candidate: Detection) -> float:
    """Measure retained original geometry, not mask size or containment of a fragment.

    The denominator must be the complete original object. Intersection over
    the smaller polygon would give a half-card mask perfect coverage. Sorting
    through a convex hull also keeps corner ordering out of this measurement.
    """
    polygons = []
    for detection in (original, candidate):
        points = _points(detection)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            raise ValueError("invalid_protected_object_polygon")
        polygon = cv2.convexHull(points)
        if len(polygon) != 4 or abs(float(cv2.contourArea(polygon))) <= 1e-9:
            raise ValueError("degenerate_protected_object_polygon")
        polygons.append(polygon)
    original_area = abs(float(cv2.contourArea(polygons[0])))
    intersection, _ = cv2.intersectConvexConvex(*polygons)
    return max(0.0, min(1.0, float(intersection) / original_area))


async def refine_with_sam2(
    content: bytes,
    detections: list[Detection],
    *,
    prompt_detections: list[Detection] | None = None,
    python_path: str,
    worker_script: str,
    checkpoint: str,
    timeout_seconds: float,
    content_guarded: bool = False,
) -> tuple[list[Detection], dict]:
    """Refine classical boxes with box-prompted SAM 2, with an all-safe fallback."""
    if not detections:
        return detections, {"status": "skipped", "reason": "no_classical_detections"}
    prompts = prompt_detections or detections
    if len(prompts) != len(detections):
        return detections, {"status": "fallback", "reason": "prompt_count_mismatch"}
    # Do not resolve the virtualenv interpreter symlink: executing its real
    # target bypasses the sidecar environment and loses torch/SAM 2 imports.
    paths = [Path(python_path).expanduser().absolute()]
    paths.extend(Path(value).expanduser().resolve() for value in (worker_script, checkpoint))
    if not all(path.is_file() for path in paths):
        return detections, {"status": "skipped", "reason": "runtime_unavailable"}
    boxes = [
        {
            "x": item.x,
            "y": item.y,
            "width": item.width,
            "height": item.height,
            "failure": item.failure,
            "evidence": list(item.evidence),
        }
        for item in prompts
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            str(paths[0]),
            str(paths[1]),
            "--checkpoint",
            str(paths[2]),
            "--boxes-json",
            json.dumps(boxes, separators=(",", ":")),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(content), timeout=timeout_seconds
        )
        if process.returncode:
            detail = stderr.decode(errors="replace").strip().splitlines()
            return detections, {
                "status": "fallback",
                "reason": "worker_failure",
                "detail": detail[-1][:240] if detail else None,
            }
        payload = json.loads(stdout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return detections, {"status": "fallback", "reason": "worker_timeout"}
    except (OSError, ValueError, json.JSONDecodeError):
        return detections, {"status": "fallback", "reason": "invalid_worker_response"}

    if not isinstance(payload, dict):
        return detections, {"status": "fallback", "reason": "invalid_worker_response"}
    raw_items = payload.get("detections")
    if not isinstance(raw_items, list) or len(raw_items) != len(detections):
        return detections, {"status": "fallback", "reason": "count_mismatch"}
    refined: list[Detection] = []
    active_refinements: set[int] = set()
    object_preservation_fallback_indices: list[int] = []
    label_preservation_fallback_indices: list[int] = []
    preservation_fallback_details: list[dict] = []
    try:
        for item_index, (original, prompt, raw) in enumerate(
            zip(detections, prompts, raw_items, strict=True)
        ):
            x, y = float(raw["x"]), float(raw["y"])
            width, height = float(raw["width"]), float(raw["height"])
            angle = float(raw.get("angle", 0.0))
            mask_confidence = float(raw.get("confidence", 0.0))
            if (
                not all(
                    math.isfinite(value) for value in (x, y, width, height, angle, mask_confidence)
                )
                or min(x, y) < 0
                or min(width, height) <= 0
                or not 0 <= mask_confidence <= 1
                or x + width > 1.0001
                or y + height > 1.0001
            ):
                raise ValueError("out_of_bounds")
            was_refined = raw.get("refined", False)
            if not isinstance(was_refined, bool):
                raise ValueError("invalid_refined_flag")
            object_preservation_fallback = False
            label_preservation_fallback = False
            if was_refined:
                if "quad_unresolved" in original.evidence:
                    observed = np.asarray(raw.get("points"), dtype=np.float32)
                    if (
                        observed.shape != (4, 2)
                        or not np.isfinite(observed).all()
                        or (observed < 0).any() or (observed > 1).any()
                        or len(cv2.convexHull(observed)) != 4
                        or cv2.contourArea(cv2.convexHull(observed)) <= 1e-6
                    ):
                        raise ValueError("unresolved_quad_without_mask_corners")
                x, y, width, height, points = _expanded_oriented_geometry(
                    raw.get("points"), (x, y, width, height)
                )
                # Front-side geometry can carry the same descriptive evidence
                # into a back-side search slot. It is not proof of a complete
                # object at that location in the current pixels.
                is_old_front_prompt = any(
                    signal in original.evidence
                    for signal in ("slot_prompt_only", "front_to_back_prior")
                )
                protects_label = (
                    "grading_label_association" in original.evidence and not is_old_front_prompt
                )
                protects_object = (
                    "physical_object_envelope" in original.evidence and not is_old_front_prompt
                )
                # The caller must run the independent original-pixel audit
                # when opting in. An axis envelope's empty background is not
                # a polygon-area preservation target in that guarded path.
                independent_content_guard = content_guarded and "original_content_verified" in original.evidence
                if (protects_label or protects_object) and not independent_content_guard:
                    coverage = _original_polygon_coverage(
                        original,
                        Detection(x, y, width, height, angle, mask_confidence, points=points),
                    )
                    if coverage < SAM2_MIN_PROTECTED_OBJECT_COVERAGE:
                        # A high-confidence inner-art mask can omit a pale
                        # autograph area or grading label, including with the
                        # same area after a shift/rotation. Only proven object
                        # envelopes get this protection: a grid allocation is
                        # not a complete object and must remain free to move.
                        x, y = original.x, original.y
                        width, height = original.width, original.height
                        points = original.points
                        was_refined = False
                        object_preservation_fallback = True
                        label_preservation_fallback = protects_label
                        object_preservation_fallback_indices.append(item_index)
                        if protects_label:
                            label_preservation_fallback_indices.append(item_index)
                        preservation_fallback_details.append(
                            {
                                "index": item_index,
                                "reason": "label_envelope_coverage_below_minimum"
                                if protects_label
                                else "object_envelope_coverage_below_minimum",
                                "original_polygon_coverage": round(coverage, 6),
                                "minimum_coverage": SAM2_MIN_PROTECTED_OBJECT_COVERAGE,
                            }
                        )
            else:
                x, y = original.x, original.y
                width, height = original.width, original.height
                points = original.points
            refinement_method = raw.get("method")
            refinement_evidence = {
                "edge_consensus": "edge_consensus_oriented",
                "strong_axis_edge": "strong_axis_edge",
            }.get(refinement_method, "sam2_oriented_mask")
            geometry_points = np.asarray(points, dtype=np.float64)
            touches_source_edge = bool(
                (geometry_points <= 0.002).any() or (geometry_points >= 0.998).any()
            )
            failure = (
                "source_edge_contact"
                if was_refined and touches_source_edge
                else None
                if was_refined
                else "card_outline_unresolved"
                if "cell_extent_fallback" in original.evidence
                else original.failure
            )
            confidence = (
                max(original.confidence, mask_confidence)
                if was_refined
                else min(original.confidence, 0.49)
            )
            candidate = Detection(
                x,
                y,
                width,
                height,
                angle if was_refined else original.angle,
                confidence,
                failure,
                points,
                tuple(
                    signal for signal in original.evidence
                    if not was_refined or signal not in {"quad_unresolved", "search_region_only"}
                )
                + (("front_prior_soft_prompt",) if prompt is not original else ())
                + (
                    (
                        "sam2_box_prompt",
                        refinement_evidence,
                        "sam2_safety_margin",
                    )
                    if was_refined
                    else (
                        (
                            ("sam2_object_preservation_fallback",)
                            if object_preservation_fallback
                            else ()
                        )
                        + (
                            ("sam2_label_preservation_fallback",)
                            if label_preservation_fallback
                            else ()
                        )
                        + ("sam2_fallback",)
                    )
                ),
            )
            geometry_supported = not was_refined or (
                _intersection_over_smaller(candidate, original) >= 0.45
                and 0.45
                <= candidate.width * candidate.height / max(original.width * original.height, 1e-9)
                <= 1.65
            )
            if not geometry_supported:
                candidate = Detection(
                    original.x,
                    original.y,
                    original.width,
                    original.height,
                    original.angle,
                    min(original.confidence, 0.49),
                    "card_outline_unresolved",
                    original.points,
                    original.evidence + ("sam2_geometry_fallback",),
                )
                was_refined = False
            refined.append(candidate)
            if was_refined:
                active_refinements.add(item_index)
    except (KeyError, TypeError, ValueError):
        return detections, {"status": "fallback", "reason": "invalid_geometry"}

    preservation_metadata = {
        "object_preservation_fallback_count": len(object_preservation_fallback_indices),
        "object_preservation_fallback_indices": object_preservation_fallback_indices,
        "label_preservation_fallback_count": len(label_preservation_fallback_indices),
        "label_preservation_fallback_indices": label_preservation_fallback_indices,
        "preservation_fallback_details": preservation_fallback_details,
    }

    # Validate simultaneous proposals against each other, not against stale
    # coarse peer boxes. Two correctly moved masks can be perfectly isolated
    # even when either mask overlaps the other card's old search/allocation box.
    # If proposals really conflict, revert only a conflicting refinement and
    # check again: a fallback itself can reintroduce a conflict with another
    # accepted mask. Each iteration removes one active proposal, so it terminates.
    overlap_fallback_indices: list[int] = []
    while active_refinements:
        conflict = next(
            (
                (left_index, right_index)
                for left_index, left in enumerate(refined)
                for right_index, right in enumerate(refined[left_index + 1 :], left_index + 1)
                if (left_index in active_refinements or right_index in active_refinements)
                and _intersection_over_smaller(left, right) > SAM2_MAX_PEER_OVERLAP
            ),
            None,
        )
        if conflict is None:
            break

        def fallback_cost(index: int) -> tuple[float, float, int]:
            def penalty(left: Detection, right: Detection) -> float:
                return max(0.0, _intersection_over_smaller(left, right) - SAM2_MAX_PEER_OVERLAP)

            delta = sum(
                penalty(detections[index], peer) - penalty(refined[index], peer)
                for peer_index, peer in enumerate(refined)
                if peer_index != index
            )
            return delta, refined[index].confidence, index

        index = min((i for i in conflict if i in active_refinements), key=fallback_cost)
        original = detections[index]
        refined[index] = Detection(
            original.x,
            original.y,
            original.width,
            original.height,
            original.angle,
            min(original.confidence, 0.49),
            "card_outline_unresolved",
            original.points,
            original.evidence + ("sam2_overlap_fallback",),
        )
        active_refinements.remove(index)
        overlap_fallback_indices.append(index)

    if any(
        _intersection_over_smaller(left, right) > SAM2_MAX_PEER_OVERLAP
        for index, left in enumerate(refined)
        for right in refined[index + 1 :]
    ):
        return detections, {"status": "fallback", "reason": "overlap_gate", **preservation_metadata}
    refinement_evidence_by_item = [
        candidate.evidence[
            len(original.evidence) - sum(
                signal in {"quad_unresolved", "search_region_only"} and signal not in candidate.evidence
                for signal in original.evidence
            ) :
        ]
        for original, candidate in zip(detections, refined, strict=True)
    ]
    refined_count = sum("sam2_box_prompt" in evidence for evidence in refinement_evidence_by_item)
    edge_refined_count = sum(
        "edge_consensus_oriented" in evidence or "strong_axis_edge" in evidence
        for evidence in refinement_evidence_by_item
    )
    return refined, {
        "status": "accepted" if refined_count else "fallback",
        "reason": None
        if refined_count
        else "protected_object_not_preserved"
        if object_preservation_fallback_indices
        else "no_confident_masks",
        "model": "sam2.1-hiera-tiny",
        "refined_count": refined_count,
        "edge_refined_count": edge_refined_count,
        "fallback_count": len(refined) - refined_count,
        "overlap_fallback_indices": overlap_fallback_indices,
        "geometry_validation": "joint_proposals_v3_object_coverage",
        **preservation_metadata,
        "inference_seconds": payload.get("inference_seconds"),
    }
