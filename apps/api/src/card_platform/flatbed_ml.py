from __future__ import annotations

import asyncio
import json
from pathlib import Path

import cv2
import numpy as np

from card_platform.flatbed_detection import Detection

SAM2_SAFETY_MARGIN_RATIO = 0.012


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


async def refine_with_sam2(
    content: bytes,
    detections: list[Detection],
    *,
    prompt_detections: list[Detection] | None = None,
    python_path: str,
    worker_script: str,
    checkpoint: str,
    timeout_seconds: float,
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

    raw_items = payload.get("detections")
    if not isinstance(raw_items, list) or len(raw_items) != len(detections):
        return detections, {"status": "fallback", "reason": "count_mismatch"}
    refined: list[Detection] = []
    refinement_evidence_by_item: list[tuple[str, ...]] = []
    try:
        for item_index, (original, prompt, raw) in enumerate(
            zip(detections, prompts, raw_items, strict=True)
        ):
            x, y = float(raw["x"]), float(raw["y"])
            width, height = float(raw["width"]), float(raw["height"])
            if min(x, y, width, height) < 0 or x + width > 1.0001 or y + height > 1.0001:
                raise ValueError("out_of_bounds")
            was_refined = bool(raw.get("refined"))
            label_preservation_fallback = False
            if (
                was_refined
                and "grading_label_association" in original.evidence
                and width * height < original.width * original.height * 0.90
            ):
                # A generic SAM mask often selects only the colourful card art
                # inside a transparent slab. The grading label is identity and
                # condition evidence, so a refinement that drops a material
                # part of the grouped envelope is strictly worse than the
                # classical content-island geometry.
                x, y = original.x, original.y
                width, height = original.width, original.height
                was_refined = False
                label_preservation_fallback = True
            if was_refined:
                x, y, width, height, points = _expanded_oriented_geometry(
                    raw.get("points"), (x, y, width, height)
                )
                if (
                    "grading_label_association" in original.evidence
                    and width * height < original.width * original.height * 0.90
                ):
                    x, y = original.x, original.y
                    width, height = original.width, original.height
                    points = original.points
                    was_refined = False
                    label_preservation_fallback = True
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
                (geometry_points <= 0.002).any()
                or (geometry_points >= 0.998).any()
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
                max(original.confidence, float(raw.get("confidence", 0.0)))
                if was_refined
                else min(original.confidence, 0.49)
            )
            candidate = Detection(
                x,
                y,
                width,
                height,
                float(raw.get("angle", 0.0)) if was_refined else original.angle,
                confidence,
                failure,
                points,
                original.evidence
                + (
                    ("front_prior_soft_prompt",)
                    if prompt is not original
                    else ()
                )
                + (
                    (
                        "sam2_box_prompt",
                        refinement_evidence,
                        "sam2_safety_margin",
                    )
                    if was_refined
                    else (
                        (
                            "sam2_label_preservation_fallback",
                            "sam2_fallback",
                        )
                        if label_preservation_fallback
                        else ("sam2_fallback",)
                    )
                ),
            )
            geometry_supported = (
                not was_refined
                or (
                    _intersection_over_smaller(candidate, original) >= 0.45
                    and 0.45
                    <= candidate.width * candidate.height
                    / max(original.width * original.height, 1e-9)
                    <= 1.65
                )
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
            conflicts_with_peer = was_refined and any(
                _intersection_over_smaller(candidate, peer) > 0.18
                for peer_index, peer in enumerate(detections)
                if peer_index != item_index
            )
            if conflicts_with_peer:
                candidate = Detection(
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
            refined.append(candidate)
            refinement_evidence_by_item.append(
                candidate.evidence[len(original.evidence) :]
            )
    except (KeyError, TypeError, ValueError):
        return detections, {"status": "fallback", "reason": "invalid_geometry"}

    if any(
        _intersection_over_smaller(left, right) > 0.18
        for index, left in enumerate(refined)
        for right in refined[index + 1 :]
    ):
        return detections, {"status": "fallback", "reason": "overlap_gate"}
    refined_count = sum(
        "sam2_box_prompt" in evidence for evidence in refinement_evidence_by_item
    )
    edge_refined_count = sum(
        "edge_consensus_oriented" in evidence or "strong_axis_edge" in evidence
        for evidence in refinement_evidence_by_item
    )
    return refined, {
        "status": "accepted" if refined_count else "fallback",
        "reason": None if refined_count else "no_confident_masks",
        "model": "sam2.1-hiera-tiny",
        "refined_count": refined_count,
        "edge_refined_count": edge_refined_count,
        "fallback_count": len(refined) - refined_count,
        "inference_seconds": payload.get("inference_seconds"),
    }
