from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from card_platform.flatbed_detection import Detection, detect_layout
from card_platform.flatbed_ml import refine_with_sam2


SLOT_REFINEMENT_VERSION = "slot-local-v2"
SLOT_SEARCH_MARGIN_RATIO = 0.16
SLOT_NEIGHBOUR_BOUNDARY_BUFFER_RATIO = 0.12
SLOT_MIN_INK_RATIO = 0.055
SLOT_MIN_EDGE_RATIO = 0.012
SLOT_MIN_BODY_COVERAGE = 0.30
SLOT_MIN_CANDIDATE_AREA_RATIO = 0.45
SLOT_MAX_CANDIDATE_AREA_RATIO = 1.70
SLOT_MIN_WINDOW_CONTAINMENT = 0.90
SLOT_MAX_PEER_OVERLAP = 0.18
SLOT_EDGE_SEARCH_RADIUS_RATIO = 0.10
SLOT_EDGE_MIN_PROMINENCE = 8.0
SLOT_EDGE_MIN_STRENGTH = 14.0
SLOT_CURRENT_CANDIDATE_MIN_WINDOW_OVERLAP = 0.55
SLOT_CURRENT_CANDIDATE_MAX_ACCEPTED_OVERLAP = 0.50
SLOT_CURRENT_GRID_EDGE_INSET_RATIO = 0.12


@dataclass(frozen=True)
class SlotEvidence:
    ink_ratio: float
    edge_ratio: float
    row_coverage: float
    column_coverage: float
    mean_luma: float
    luma_stddev: float

    @property
    def body_coverage(self) -> float:
        return min(self.row_coverage, self.column_coverage)

    @property
    def object_present(self) -> bool:
        has_distributed_body = self.body_coverage >= SLOT_MIN_BODY_COVERAGE
        has_structure = (
            self.ink_ratio >= SLOT_MIN_INK_RATIO
            or self.edge_ratio >= SLOT_MIN_EDGE_RATIO
        )
        uniformly_empty = (
            self.mean_luma > 225
            and self.luma_stddev < 13
            and self.edge_ratio < 0.006
        )
        return has_distributed_body and has_structure and not uniformly_empty


def _bounds(item: Detection) -> tuple[float, float, float, float]:
    return item.x, item.y, item.x + item.width, item.y + item.height


def _intersection_over_smaller(left: Detection, right: Detection) -> float:
    left_x, left_y, left_right, left_bottom = _bounds(left)
    right_x, right_y, right_right, right_bottom = _bounds(right)
    intersection = max(0.0, min(left_right, right_right) - max(left_x, right_x)) * max(
        0.0, min(left_bottom, right_bottom) - max(left_y, right_y)
    )
    smaller = min(left.width * left.height, right.width * right.height)
    return intersection / smaller if smaller else 0.0


def _axis_overlap_ratio(left: Detection, right: Detection, *, horizontal: bool) -> float:
    if horizontal:
        left_start, left_end = left.x, left.x + left.width
        right_start, right_end = right.x, right.x + right.width
        smaller = min(left.width, right.width)
    else:
        left_start, left_end = left.y, left.y + left.height
        right_start, right_end = right.y, right.y + right.height
        smaller = min(left.height, right.height)
    overlap = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    return overlap / smaller if smaller else 0.0


def slot_search_windows(prior: list[Detection]) -> list[Detection]:
    """Expand prior slots, capped at neighbour-centre ownership boundaries.

    The returned envelopes are search/validation windows only. They must never
    be used as crop geometry. This supports imperfect front/back registration
    without allowing one slot to absorb a neighbouring card.
    """
    windows: list[Detection] = []
    for index, item in enumerate(prior):
        center_x = item.x + item.width / 2
        center_y = item.y + item.height / 2
        left = max(0.0, item.x - item.width * SLOT_SEARCH_MARGIN_RATIO)
        top = max(0.0, item.y - item.height * SLOT_SEARCH_MARGIN_RATIO)
        right = min(1.0, item.x + item.width * (1 + SLOT_SEARCH_MARGIN_RATIO))
        bottom = min(1.0, item.y + item.height * (1 + SLOT_SEARCH_MARGIN_RATIO))
        for peer_index, peer in enumerate(prior):
            if index == peer_index:
                continue
            peer_center_x = peer.x + peer.width / 2
            peer_center_y = peer.y + peer.height / 2
            if _axis_overlap_ratio(item, peer, horizontal=False) >= 0.25:
                boundary_x = (center_x + peer_center_x) / 2
                boundary_buffer = (
                    min(item.width, peer.width)
                    * SLOT_NEIGHBOUR_BOUNDARY_BUFFER_RATIO
                )
                if peer_center_x < center_x:
                    left = max(left, boundary_x - boundary_buffer)
                elif peer_center_x > center_x:
                    right = min(right, boundary_x + boundary_buffer)
            if _axis_overlap_ratio(item, peer, horizontal=True) >= 0.25:
                boundary_y = (center_y + peer_center_y) / 2
                boundary_buffer = (
                    min(item.height, peer.height) * SLOT_NEIGHBOUR_BOUNDARY_BUFFER_RATIO
                )
                if peer_center_y < center_y:
                    top = max(top, boundary_y - boundary_buffer)
                elif peer_center_y > center_y:
                    bottom = min(bottom, boundary_y + boundary_buffer)
        windows.append(
            Detection(
                left,
                top,
                max(0.0, right - left),
                max(0.0, bottom - top),
                0.0,
                0.0,
                "search_window_only",
                evidence=(f"front_slot_index:{index}", "slot_search_window"),
            )
        )
    return windows


def slot_object_evidence(image: np.ndarray, item: Detection) -> SlotEvidence:
    image_height, image_width = image.shape[:2]
    x1 = max(0, round(item.x * image_width))
    y1 = max(0, round(item.y * image_height))
    x2 = min(image_width, round((item.x + item.width) * image_width))
    y2 = min(image_height, round((item.y + item.height) * image_height))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return SlotEvidence(0.0, 0.0, 0.0, 0.0, 255.0, 0.0)
    inset_y = round(crop.shape[0] * 0.08)
    inset_x = round(crop.shape[1] * 0.08)
    core = crop[
        inset_y : max(inset_y + 1, crop.shape[0] - inset_y),
        inset_x : max(inset_x + 1, crop.shape[1] - inset_x),
    ]
    gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)[:, :, 1]
    ink = (saturation > 20) | (gray < 202)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 30, 90) > 0
    return SlotEvidence(
        ink_ratio=float(np.mean(ink)),
        edge_ratio=float(np.mean(edges)),
        row_coverage=float(np.mean(np.mean(ink, axis=1) > 0.025)),
        column_coverage=float(np.mean(np.mean(ink, axis=0) > 0.025)),
        mean_luma=float(np.mean(gray)),
        luma_stddev=float(np.std(gray)),
    )


def _source_edge_has_object(
    image: np.ndarray,
    item: Detection,
    *,
    side: str,
) -> tuple[bool, float]:
    """Require current pixels at a source edge before using it as a boundary."""
    image_height, image_width = image.shape[:2]
    center_x = round((item.x + item.width / 2) * image_width)
    center_y = round((item.y + item.height / 2) * image_height)
    half_width = max(2, round(item.width * image_width * 0.34))
    half_height = max(2, round(item.height * image_height * 0.34))
    strip_x = max(3, round(image_width * 0.008))
    strip_y = max(3, round(image_height * 0.008))
    if side == "left":
        crop = image[
            max(0, center_y - half_height) : min(image_height, center_y + half_height),
            :strip_x,
        ]
    elif side == "right":
        crop = image[
            max(0, center_y - half_height) : min(image_height, center_y + half_height),
            image_width - strip_x :,
        ]
    elif side == "top":
        crop = image[
            :strip_y,
            max(0, center_x - half_width) : min(image_width, center_x + half_width),
        ]
    else:
        crop = image[
            image_height - strip_y :,
            max(0, center_x - half_width) : min(image_width, center_x + half_width),
        ]
    if crop.size == 0:
        return False, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 1]
    ink_ratio = float(np.mean((saturation > 20) | (gray < 202)))
    edge_ratio = float(
        np.mean(cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 30, 90) > 0)
    )
    luma_stddev = float(np.std(gray))
    strength = max(ink_ratio, min(1.0, edge_ratio / 0.04), min(1.0, luma_stddev / 35))
    return ink_ratio >= 0.08 or edge_ratio >= 0.008 or luma_stddev >= 12, strength


def _axis_gradient_profile(
    image: np.ndarray,
    item: Detection,
    *,
    horizontal_position: bool,
) -> np.ndarray:
    """Average gradients through the slot centre to suppress neighbouring cards."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    image_height, image_width = gray.shape
    center_x = round((item.x + item.width / 2) * image_width)
    center_y = round((item.y + item.height / 2) * image_height)
    if horizontal_position:
        half_band = max(2, round(item.height * image_height * 0.34))
        band = gray[
            max(0, center_y - half_band) : min(image_height, center_y + half_band)
        ]
        return np.abs(cv2.Sobel(band, cv2.CV_32F, 1, 0)).mean(axis=0)
    half_band = max(2, round(item.width * image_width * 0.34))
    band = gray[
        :, max(0, center_x - half_band) : min(image_width, center_x + half_band)
    ]
    return np.abs(cv2.Sobel(band, cv2.CV_32F, 0, 1)).mean(axis=1)


def _current_edge_coordinate(
    profile: np.ndarray,
    *,
    expected: float,
    radius: float,
    outward: str,
    inward_only: bool = False,
    minimum_inset: float = 0.0,
) -> tuple[float, float] | None:
    """Select an outer current-image gradient peak near a slot-side hint."""
    if profile.size < 3:
        return None
    smoothing = max(3, len(profile) // 500)
    if smoothing % 2 == 0:
        smoothing += 1
    smoothed = np.convolve(
        profile,
        np.ones(smoothing, dtype=np.float32) / smoothing,
        mode="same",
    )
    if inward_only and outward in {"left", "top"}:
        start = max(1, round((expected + minimum_inset) * len(smoothed)))
        stop = min(len(smoothed) - 1, round((expected + radius) * len(smoothed)))
    elif inward_only:
        start = max(1, round((expected - radius) * len(smoothed)))
        stop = min(len(smoothed) - 1, round((expected - minimum_inset) * len(smoothed)))
    else:
        start = max(1, round((expected - radius) * len(smoothed)))
        stop = min(len(smoothed) - 1, round((expected + radius) * len(smoothed)))
    if stop - start < 3:
        return None
    window = smoothed[start:stop]
    baseline = float(np.median(window))
    maximum = float(np.max(window))
    if (
        maximum < SLOT_EDGE_MIN_STRENGTH
        or maximum - baseline < SLOT_EDGE_MIN_PROMINENCE
    ):
        return None
    threshold = baseline + 0.58 * (maximum - baseline)
    active = window >= threshold
    changes = np.diff(np.pad(active.astype(np.int8), (1, 1)))
    groups = list(zip(np.where(changes == 1)[0], np.where(changes == -1)[0]))
    peaks: list[tuple[int, float]] = []
    for group_start, group_stop in groups:
        segment = window[group_start:group_stop]
        if not len(segment):
            continue
        local_offset = int(np.argmax(segment))
        strength = float(segment[local_offset])
        if strength >= baseline + 0.58 * (maximum - baseline):
            peaks.append((start + group_start + local_offset, strength))
    if not peaks:
        return None
    position, strength = min(peaks) if outward in {"left", "top"} else max(peaks)
    confidence = min(1.0, (strength - baseline) / 60.0)
    return float(position / len(smoothed)), float(confidence)


def current_side_edge_fallback(
    image: np.ndarray,
    original: Detection,
    window: Detection,
    *,
    slot_index: int,
    inward_only: bool = False,
) -> Detection | None:
    """Fit four object boundaries from current-side gradients inside one slot.

    The prior supplies only neighbourhood and approximate search bands. Every
    non-source boundary is an observed gradient. A source-image boundary is
    usable only when the current pixels reach that edge with foreground or
    structural evidence.
    """
    x_profile = _axis_gradient_profile(image, original, horizontal_position=True)
    y_profile = _axis_gradient_profile(image, original, horizontal_position=False)
    sides: dict[str, tuple[float, float]] = {}
    side_specs = (
        ("left", original.x, original.width * SLOT_EDGE_SEARCH_RADIUS_RATIO, x_profile),
        (
            "right",
            original.x + original.width,
            original.width * SLOT_EDGE_SEARCH_RADIUS_RATIO,
            x_profile,
        ),
        ("top", original.y, original.height * SLOT_EDGE_SEARCH_RADIUS_RATIO, y_profile),
        (
            "bottom",
            original.y + original.height,
            original.height * SLOT_EDGE_SEARCH_RADIUS_RATIO,
            y_profile,
        ),
    )
    exclude_grid_seam = (
        inward_only
        and "full_bed_grid" in original.evidence
        and "cell_extent_fallback" in original.evidence
    )
    source_sides: list[str] = []
    for side, expected, radius, profile in side_specs:
        at_source = (side in {"left", "top"} and expected <= 0.015) or (
            side in {"right", "bottom"} and expected >= 0.985
        )
        if at_source:
            supported, strength = _source_edge_has_object(image, original, side=side)
            if supported:
                sides[side] = (0.0 if side in {"left", "top"} else 1.0, strength)
                source_sides.append(side)
                continue
        search_radius = max(0.012, radius)
        observed = _current_edge_coordinate(
            profile,
            expected=expected,
            radius=search_radius,
            outward=side,
            inward_only=inward_only,
            minimum_inset=(
                search_radius * SLOT_CURRENT_GRID_EDGE_INSET_RATIO
                if exclude_grid_seam
                else 0.0
            ),
        )
        if observed is None:
            return None
        sides[side] = observed

    left, right = sides["left"][0], sides["right"][0]
    top, bottom = sides["top"][0], sides["bottom"][0]
    if left >= right or top >= bottom:
        return None
    candidate = Detection(
        left,
        top,
        right - left,
        bottom - top,
        0.0,
        round(0.58 + 0.24 * float(np.mean([value[1] for value in sides.values()])), 4),
        "source_edge_contact" if source_sides else None,
        ((left, top), (right, top), (right, bottom), (left, bottom)),
        (
            f"front_slot_index:{slot_index}",
            "current_side_edge_fallback",
            "observed_gradient_boundaries",
            *(("current_candidate_inward_edges",) if inward_only else ()),
            *(("current_grid_seam_excluded",) if exclude_grid_seam else ()),
            *(f"current_source_edge:{side}" for side in source_sides),
        ),
    )
    supported, _, _ = _current_geometry_supported(image, candidate, original, window)
    return candidate if supported else None


def _slot_prompt(item: Detection, index: int) -> Detection:
    evidence = tuple(value for value in item.evidence if not value.startswith("sam2_"))
    return Detection(
        item.x,
        item.y,
        item.width,
        item.height,
        item.angle,
        min(item.confidence, 0.49),
        "card_outline_unresolved",
        item.points,
        tuple(
            dict.fromkeys(
                (*evidence, f"front_slot_index:{index}", "slot_prompt_only", "cell_extent_fallback")
            )
        ),
    )


def _current_geometry_supported(
    image: np.ndarray,
    candidate: Detection,
    original: Detection,
    window: Detection,
) -> tuple[bool, str | None, SlotEvidence]:
    evidence = slot_object_evidence(image, candidate)
    if not any(
        source in candidate.evidence
        for source in ("sam2_box_prompt", "current_side_edge_fallback")
    ):
        return False, "current_geometry_unresolved", evidence
    if not evidence.object_present:
        return False, "current_object_absent", evidence
    area_ratio = candidate.width * candidate.height / max(
        original.width * original.height, 1e-9
    )
    if not SLOT_MIN_CANDIDATE_AREA_RATIO <= area_ratio <= SLOT_MAX_CANDIDATE_AREA_RATIO:
        return False, "candidate_size_incompatible", evidence
    if _intersection_over_smaller(candidate, window) < SLOT_MIN_WINDOW_CONTAINMENT:
        return False, "candidate_outside_slot", evidence
    pixel_width = candidate.width * image.shape[1]
    pixel_height = candidate.height * image.shape[0]
    aspect = max(pixel_width, pixel_height) / max(1.0, min(pixel_width, pixel_height))
    if not 1.10 <= aspect <= 2.10:
        return False, "candidate_aspect_incompatible", evidence
    return True, None, evidence


def _match_current_side_candidates(
    image: np.ndarray,
    current: list[Detection],
    current_windows: list[Detection],
    prior: list[Detection],
    slot_windows: list[Detection],
    active_indices: list[int],
    unresolved_indices: list[int],
    accepted: dict[int, Detection],
) -> dict[int, tuple[Detection, Detection]]:
    """Match independent current-image bodies to unresolved identity slots.

    The independent detector is only a current-side anchor. A candidate must
    contain a distributed object, fit the front slot's physical scale, and win
    a one-to-one overlap match against all active slots. Candidates belonging
    to an already localized card are excluded before matching.
    """
    unresolved = set(unresolved_indices)
    accepted_items = list(accepted.values())
    ranked: list[tuple[float, int, int]] = []
    for candidate_position, candidate in enumerate(current):
        if not slot_object_evidence(image, candidate).object_present:
            continue
        if any(
            _intersection_over_smaller(candidate, item)
            > SLOT_CURRENT_CANDIDATE_MAX_ACCEPTED_OVERLAP
            for item in accepted_items
        ):
            continue

        candidate_matches: list[tuple[float, int]] = []
        candidate_area = candidate.width * candidate.height
        center_x = candidate.x + candidate.width / 2
        center_y = candidate.y + candidate.height / 2
        for index in active_indices:
            original = prior[index]
            window = slot_windows[index]
            area_ratio = candidate_area / max(original.width * original.height, 1e-9)
            if not SLOT_MIN_CANDIDATE_AREA_RATIO <= area_ratio <= SLOT_MAX_CANDIDATE_AREA_RATIO:
                continue
            if not (
                window.x <= center_x <= window.x + window.width
                and window.y <= center_y <= window.y + window.height
            ):
                continue
            window_overlap = _intersection_over_smaller(candidate, window)
            if window_overlap < SLOT_CURRENT_CANDIDATE_MIN_WINDOW_OVERLAP:
                continue
            prior_overlap = _intersection_over_smaller(candidate, original)
            candidate_matches.append((0.72 * window_overlap + 0.28 * prior_overlap, index))

        if not candidate_matches:
            continue
        candidate_matches.sort(reverse=True)
        score, index = candidate_matches[0]
        # Neighbour ownership windows intentionally overlap a little. Do not
        # use an independent body when two identity slots fit almost equally.
        if len(candidate_matches) > 1 and score - candidate_matches[1][0] < 0.10:
            continue
        if index in unresolved:
            ranked.append((score, index, candidate_position))

    matches: dict[int, tuple[Detection, Detection]] = {}
    claimed_candidates: set[int] = set()
    for _, index, candidate_position in sorted(ranked, reverse=True):
        if index in matches or candidate_position in claimed_candidates:
            continue
        matches[index] = (
            current[candidate_position],
            current_windows[candidate_position],
        )
        claimed_candidates.add(candidate_position)
    return matches


async def refine_back_slots(
    content: bytes,
    prior: list[Detection],
    *,
    python_path: str,
    worker_script: str,
    checkpoint: str,
    timeout_seconds: float,
    dpi: int = 300,
) -> tuple[list[Detection], dict]:
    """Localize backs inside identity front slots without count-based filling.

    A prior contributes only slot topology and prompts. Every returned detection
    must be independently refined from current-side pixels and pass an object
    presence check. Missing/empty slots are reported, never synthesized.
    """
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码扫描图像")
    if not prior:
        return [], {
            "version": SLOT_REFINEMENT_VERSION,
            "status": "skipped",
            "reason": "front_slots_missing",
            "slot_count": 0,
            "localized_count": 0,
            "manufactured_count": 0,
        }

    windows = slot_search_windows(prior)
    empty_slot_indices: list[int] = []
    active_indices: list[int] = []
    prompts: list[Detection] = []
    active_windows: list[Detection] = []
    for index, (item, window) in enumerate(zip(prior, windows, strict=True)):
        if not slot_object_evidence(image, window).object_present:
            empty_slot_indices.append(index)
            continue
        active_indices.append(index)
        prompts.append(_slot_prompt(item, index))
        active_windows.append(window)

    if not prompts:
        return [], {
            "version": SLOT_REFINEMENT_VERSION,
            "status": "empty",
            "reason": "no_current_side_objects",
            "slot_count": len(prior),
            "occupied_slot_count": 0,
            "localized_count": 0,
            "empty_slot_indices": empty_slot_indices,
            "missing_slot_indices": list(range(len(prior))),
            "manufactured_count": 0,
        }

    first, first_metadata = await refine_with_sam2(
        content,
        prompts,
        python_path=python_path,
        worker_script=worker_script,
        checkpoint=checkpoint,
        timeout_seconds=timeout_seconds,
    )
    accepted: dict[int, Detection] = {}
    rejected_reasons: dict[int, str] = {}
    unresolved_positions: list[int] = []
    for position, (index, candidate, original, window) in enumerate(
        zip(active_indices, first, prompts, active_windows, strict=True)
    ):
        supported, reason, evidence = _current_geometry_supported(
            image, candidate, original, window
        )
        if supported:
            accepted[index] = Detection(
                candidate.x,
                candidate.y,
                candidate.width,
                candidate.height,
                candidate.angle,
                candidate.confidence,
                candidate.failure,
                candidate.points,
                tuple(
                    dict.fromkeys(
                        (
                            *candidate.evidence,
                            "current_side_slot_evidence",
                            f"slot_body_coverage:{evidence.body_coverage:.3f}",
                        )
                    )
                ),
            )
        else:
            unresolved_positions.append(position)
            rejected_reasons[index] = reason or "current_geometry_unresolved"

    second_metadata: dict = {"status": "skipped", "reason": "all_slots_refined"}
    if unresolved_positions:
        second_originals = [prompts[position] for position in unresolved_positions]
        second_windows = [active_windows[position] for position in unresolved_positions]
        second, second_metadata = await refine_with_sam2(
            content,
            second_originals,
            prompt_detections=second_windows,
            python_path=python_path,
            worker_script=worker_script,
            checkpoint=checkpoint,
            timeout_seconds=timeout_seconds,
        )
        for position, candidate, original, window in zip(
            unresolved_positions,
            second,
            second_originals,
            second_windows,
            strict=True,
        ):
            index = active_indices[position]
            supported, reason, evidence = _current_geometry_supported(
                image, candidate, original, window
            )
            if not supported:
                second_reason = reason or "current_geometry_unresolved"
                if (
                    index not in rejected_reasons
                    or second_reason != "current_geometry_unresolved"
                ):
                    rejected_reasons[index] = second_reason
                continue
            accepted[index] = Detection(
                candidate.x,
                candidate.y,
                candidate.width,
                candidate.height,
                candidate.angle,
                candidate.confidence,
                candidate.failure,
                candidate.points,
                tuple(
                    dict.fromkeys(
                        (
                            *candidate.evidence,
                            "expanded_slot_retry",
                            "current_side_slot_evidence",
                            f"slot_body_coverage:{evidence.body_coverage:.3f}",
                        )
                    )
                ),
            )
            rejected_reasons.pop(index, None)

    geometry_fallback_indices: list[int] = []
    current_detection_status = "skipped"
    current_detection_count = 0
    current_candidate_fallback_indices: list[int] = []
    unresolved_indices = [index for index in active_indices if index not in accepted]
    if unresolved_indices:
        try:
            current_layout = detect_layout(content, dpi=dpi)
        except (ValueError, cv2.error):
            current_detection_status = "error"
        else:
            current = list(current_layout.detections)
            current_detection_count = len(current)
            current_detection_status = "accepted" if current else "empty"
            current_windows = slot_search_windows(current)
            matches = _match_current_side_candidates(
                image,
                current,
                current_windows,
                prior,
                windows,
                active_indices,
                unresolved_indices,
                accepted,
            )
            for index, (candidate, candidate_window) in matches.items():
                fallback = current_side_edge_fallback(
                    image,
                    candidate,
                    candidate_window,
                    slot_index=index,
                    inward_only=True,
                )
                if fallback is None:
                    rejected_reasons[index] = "current_candidate_edges_unresolved"
                    continue
                area_ratio = (
                    fallback.width
                    * fallback.height
                    / max(prior[index].width * prior[index].height, 1e-9)
                )
                if (
                    not SLOT_MIN_CANDIDATE_AREA_RATIO <= area_ratio <= SLOT_MAX_CANDIDATE_AREA_RATIO
                    or _intersection_over_smaller(fallback, windows[index])
                    < SLOT_CURRENT_CANDIDATE_MIN_WINDOW_OVERLAP
                ):
                    rejected_reasons[index] = "current_candidate_outside_slot"
                    continue
                evidence = slot_object_evidence(image, fallback)
                accepted[index] = Detection(
                    fallback.x,
                    fallback.y,
                    fallback.width,
                    fallback.height,
                    fallback.angle,
                    fallback.confidence,
                    fallback.failure,
                    fallback.points,
                    tuple(
                        dict.fromkeys(
                            (
                                *fallback.evidence,
                                "current_side_independent_detection",
                                "current_candidate_edge_fallback",
                                "current_side_slot_evidence",
                                f"slot_body_coverage:{evidence.body_coverage:.3f}",
                            )
                        )
                    ),
                )
                geometry_fallback_indices.append(index)
                current_candidate_fallback_indices.append(index)
                rejected_reasons.pop(index, None)

    # Keep the original prior-centred edge path as a final fallback. It is
    # useful when the independent layout is incomplete, but must not take
    # precedence over a current-image body whose position materially changed.
    for position, index in enumerate(active_indices):
        if index in accepted:
            continue
        fallback = current_side_edge_fallback(
            image,
            prompts[position],
            active_windows[position],
            slot_index=index,
        )
        if fallback is None:
            continue
        evidence = slot_object_evidence(image, fallback)
        accepted[index] = Detection(
            fallback.x,
            fallback.y,
            fallback.width,
            fallback.height,
            fallback.angle,
            fallback.confidence,
            fallback.failure,
            fallback.points,
            (
                *fallback.evidence,
                "current_side_slot_evidence",
                f"slot_body_coverage:{evidence.body_coverage:.3f}",
            ),
        )
        geometry_fallback_indices.append(index)
        rejected_reasons.pop(index, None)

    ordered: list[Detection] = []
    final_indices: list[int] = []
    for index in sorted(accepted):
        candidate = accepted[index]
        if any(
            _intersection_over_smaller(candidate, peer) > SLOT_MAX_PEER_OVERLAP
            for peer in ordered
        ):
            rejected_reasons[index] = "candidate_overlaps_peer"
            continue
        ordered.append(candidate)
        final_indices.append(index)
    missing = sorted(set(range(len(prior))) - set(final_indices))
    geometry_fallback_indices = sorted(
        index for index in geometry_fallback_indices if index in final_indices
    )
    current_candidate_fallback_indices = sorted(
        index for index in current_candidate_fallback_indices if index in final_indices
    )
    status = "accepted" if not missing else "partial" if ordered else "fallback"
    return ordered, {
        "version": SLOT_REFINEMENT_VERSION,
        "status": status,
        "reason": None if not missing else "one_or_more_slots_unresolved",
        "prior_role": "identity_slot_topology_only",
        "slot_count": len(prior),
        "occupied_slot_count": len(active_indices),
        "localized_count": len(ordered),
        "localized_slot_indices": final_indices,
        "empty_slot_indices": empty_slot_indices,
        "missing_slot_indices": missing,
        "rejected_reasons": rejected_reasons,
        "geometry_fallback_indices": geometry_fallback_indices,
        "geometry_fallback_count": len(geometry_fallback_indices),
        "current_detection_status": current_detection_status,
        "current_detection_count": current_detection_count,
        "current_candidate_fallback_indices": current_candidate_fallback_indices,
        "current_candidate_fallback_count": len(current_candidate_fallback_indices),
        "first_pass": first_metadata,
        "second_pass": second_metadata,
        "manufactured_count": 0,
    }
