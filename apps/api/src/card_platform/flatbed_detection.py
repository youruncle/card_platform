from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

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


DETECTOR_VERSION = "layout-recovery-v3"


@dataclass(frozen=True)
class LayoutHypothesis:
    detections: tuple[Detection, ...]
    score: float
    rows: int
    columns: int
    transform: str = "independent"
    signals: dict[str, float | int | str | list[float]] = field(default_factory=dict)
    rejected_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "detector_version": DETECTOR_VERSION,
            "score": self.score,
            "rows": self.rows,
            "columns": self.columns,
            "transform": self.transform,
            "signals": self.signals,
            "rejected_reasons": list(self.rejected_reasons),
        }


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
        item for item in edge_references
        if item.width * item.height < 0.20 and 1.15 <= max(
            item.width * image.shape[1], item.height * image.shape[0]
        ) / max(1.0, min(item.width * image.shape[1], item.height * image.shape[0])) <= 1.9
    ]
    if whole_bed is None and len(provisional_references) >= 2:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        border = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
        if float(np.mean(border)) < 235:
            whole_bed = Detection(0, 0, 1, 1, 0, 0.5, evidence=("bed_edge_coverage",))
    references = [
        item
        for item in edge_references
        if item is not whole_bed and item.width * item.height < 0.20
        and whole_bed is not None and _intersection_over_smaller(whole_bed, item) >= 0.90
        and 1.15 <= max(
            item.width * image.shape[1], item.height * image.shape[0]
        ) / max(1.0, min(item.width * image.shape[1], item.height * image.shape[0])) <= 1.9
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
    shapes: list[tuple[float, int, int]] = []
    for rows in range(2, 11):
        for columns in range(2, 7):
            count = rows * columns
            if count <= len(references) or count > 20:
                continue
            cell_width_px = sheet_width_px / columns
            cell_height_px = sheet_height_px / rows
            short_px, long_px = sorted((cell_width_px, cell_height_px))
            ratio = long_px / max(short_px, 1.0)
            if not 1.20 <= ratio <= 1.85:
                continue
            if short_px < dpi * 2.15 or not dpi * 3.0 <= long_px <= dpi * 5.5:
                continue
            shapes.append((abs(ratio - 1.40) + count * 0.001, rows, columns))
    if not shapes:
        # Small synthetic/preview images do not carry real DPI scale. Keep the
        # same aspect/count objective as a scale-free fallback for those.
        for rows in range(2, 11):
            for columns in range(2, 7):
                count = rows * columns
                if count <= len(references) or count > 20:
                    continue
                ratio = max(sheet_width_px / columns, sheet_height_px / rows) / max(
                    1.0, min(sheet_width_px / columns, sheet_height_px / rows)
                )
                if 1.20 <= ratio <= 1.85:
                    shapes.append((abs(ratio - 1.40) + count * 0.001, rows, columns))
    if not shapes:
        return None
    _, rows, columns = min(shapes)
    cell_width = whole_bed.width / columns
    cell_height = whole_bed.height / rows
    pixel_ratio = max(cell_width * image.shape[1], cell_height * image.shape[0]) / max(
        1.0, min(cell_width * image.shape[1], cell_height * image.shape[0])
    )
    if not 1.15 <= pixel_ratio <= 1.9:
        return None

    grid = []
    for row in range(rows):
        for column in range(columns):
            x = whole_bed.x + column * cell_width
            y = whole_bed.y + row * cell_height
            right = x + cell_width
            bottom = y + cell_height
            clipped = (
                x <= 0.002 or y <= 0.002 or right >= 0.998 or bottom >= 0.998
            )
            grid.append(
                Detection(
                    x,
                    y,
                    cell_width,
                    cell_height,
                    0.0,
                    0.84,
                    "severely_clipped" if clipped else None,
                    ((x, y), (right, y), (right, bottom), (x, bottom)),
                )
            )
    return grid


def _local_candidates(content: bytes, *, dpi: int = 300) -> list[Detection]:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码扫描图像")
    height, width = image.shape[:2]
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
    # Multi-threshold and adaptive passes complement Canny: reflective sleeves
    # can erase a gradient while still separating statistically from the bed.
    threshold_candidates: list[Detection] = []
    masks = [cv2.threshold(gray, value, 255, cv2.THRESH_BINARY_INV)[1] for value in (165, 205, 235)]
    masks.append(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 81, 7))
    for pass_index, mask in enumerate(masks):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            area = bw * bh
            ratio = max(bw, bh) / max(1, min(bw, bh))
            if not width * height * .015 <= area <= width * height * .35 or not 1.18 <= ratio <= 1.9:
                continue
            threshold_candidates.append(Detection(x/width, y/height, bw/width, bh/height, 0, .62,
                "severely_clipped" if x <= 2 or y <= 2 or x+bw >= width-2 or y+bh >= height-2 else None,
                ((x/width,y/height),((x+bw)/width,y/height),((x+bw)/width,(y+bh)/height),(x/width,(y+bh)/height)),
                (("adaptive_threshold" if pass_index == 3 else f"threshold_{(165,205,235)[pass_index]}"),)))
    # Keep this route as independent evidence. Its boxes are intentionally not
    # greedily merged here: the global layout layer compares signals, while
    # contour-level containment would let repeated threshold boxes suppress a
    # stronger perspective/connected-component observation.
    full_bed_grid = _full_bed_grid_detections(image, foreground, found, dpi)
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
        item for item in detections
        if item.width * item.height <= 0.45
        and not any(item is not other and other.width * other.height < item.width * item.height
                    and _intersection_over_smaller(item, other) >= 0.80 for other in detections)
    ]
    selected: list[Detection] = []
    for candidate in sorted(without_containers, key=lambda item: item.confidence, reverse=True):
        if all(_intersection_over_smaller(candidate, other) <= 0.15 for other in selected):
            selected.append(candidate)
    return sorted(selected, key=lambda item: (round(item.y / max(item.height, 1e-6)), item.x))


def _projection_evidence(image: np.ndarray, boxes: list[Detection]) -> tuple[list[float], list[float]]:
    """Return normalized edge-energy at every candidate centre (1-D grid evidence)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gradient_x = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)).mean(axis=0)
    gradient_y = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1)).mean(axis=1)
    gradient_x /= max(float(gradient_x.max()), 1.0)
    gradient_y /= max(float(gradient_y.max()), 1.0)
    return gradient_x.tolist(), gradient_y.tolist()


def _grid_shape(count: int, image: np.ndarray, prior: list[Detection]) -> tuple[int, int]:
    if prior:
        xs = sorted({round(item.x + item.width / 2, 2) for item in prior})
        ys = sorted({round(item.y + item.height / 2, 2) for item in prior})
        if len(xs) * len(ys) == count:
            return len(ys), len(xs)
    aspect = image.shape[1] / image.shape[0]
    choices = [(r, count // r) for r in range(1, count + 1) if count % r == 0]
    return min(choices, key=lambda rc: abs((rc[1] / rc[0]) - aspect * 1.35))


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
        result.append(Detection(x, y, item.width, item.height, item.angle, 0.0, points=points))
    return result


def _boundary_score(gray: np.ndarray, item: Detection) -> float:
    h, w = gray.shape
    x1, x2 = sorted((int(item.x * w), int((item.x + item.width) * w)))
    y1, y2 = sorted((int(item.y * h), int((item.y + item.height) * h)))
    x1, x2, y1, y2 = max(0, x1), min(w - 1, x2), max(0, y1), min(h - 1, y2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    grad = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1))
    band = max(2, round(min(w, h) * 0.002))
    samples = np.concatenate((grad[y1 : y1 + band, x1:x2].ravel(), grad[max(y1, y2-band):y2, x1:x2].ravel(), grad[y1:y2, x1:x1+band].ravel(), grad[y1:y2, max(x1,x2-band):x2].ravel()))
    return min(1.0, float(np.mean(samples)) / 80.0) if samples.size else 0.0


def detect_layout(content: bytes, *, dpi: int = 300, expected_count: int | None = None,
                  prior: list[Detection] | None = None) -> LayoutHypothesis:
    """Recover a sheet layout from local evidence and a globally-scored grid/prior.

    The expected front count is a hard back-side constraint. Local contour evidence
    refines it but can never silently reduce an eight-card back to three cards.
    """
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码扫描图像")
    local = _local_candidates(content, dpi=dpi)
    local_conflicts = _layout_conflicts(local)
    if local_conflicts:
        local = _non_conflicting_subset(local)
    prior = prior or []
    count = expected_count or len(local)
    if count <= 0:
        return LayoutHypothesis((), 0.0, 0, 0, signals={"candidate_count": 0}, rejected_reasons=("no_card_outline_detected",))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    projection_x, projection_y = _projection_evidence(image, local)
    def periodicity(values: list[float]) -> float:
        sampled = np.asarray(values, dtype=np.float32)
        sampled -= sampled.mean()
        correlation = np.correlate(sampled, sampled, mode="full")[len(sampled)-1:]
        start = max(2, len(sampled)//12)
        return round(float(correlation[start:].max() / max(correlation[0], 1e-6)), 4) if len(sampled) > start else 0.0
    small = cv2.resize(gray, (min(900, gray.shape[1]), min(900, gray.shape[0])))
    lines = cv2.HoughLinesP(cv2.Canny(small, 45, 130), 1, np.pi/180, 80, minLineLength=min(small.shape)//8, maxLineGap=20)
    base_signals: dict[str, float | int | str | list[float]] = {
        "projection_x_periodicity": periodicity(projection_x),
        "projection_y_periodicity": periodicity(projection_y),
        "line_segment_count": 0 if lines is None else len(lines),
        "candidate_routes": "multi_threshold+adaptive+gradient_lines+projection+components",
    }
    hypotheses: list[LayoutHypothesis] = []
    if expected_count and prior and len(prior) == count:
        for transform in ("mirror_x", "mirror_y", "mirror_x+mirror_y", "identity"):
            boxes = _transform_prior(prior, transform)
            scores = [_boundary_score(gray, box) for box in boxes]
            refined = tuple(Detection(b.x, b.y, b.width, b.height, b.angle, round(0.68 + 0.28*s, 4),
                                      "boundary_evidence_weak" if s < .08 else None, b.points,
                                      ("front_to_back_prior", "gradient_boundary", "global_count")) for b, s in zip(boxes, scores))
            rows, columns = _grid_shape(count, image, boxes)
            hypotheses.append(LayoutHypothesis(refined, round(.72 + .23*float(np.mean(scores)), 4), rows, columns, transform,
                {**base_signals, "candidate_count": len(local), "expected_count": count, "mean_boundary_energy": round(float(np.mean(scores)),4), "dpi": dpi, "prior_weight": .72}))
    if len(local) == count and not _layout_conflicts(local):
        rows, columns = _grid_shape(count, image, local)
        hypotheses.append(LayoutHypothesis(tuple(local), round(float(np.mean([x.confidence for x in local])),4), rows, columns,
            signals={**base_signals, "candidate_count": len(local), "expected_count": count, "dpi": dpi, "sources": "multi_threshold+gradient+components"}))
    if expected_count and len(prior) != count and len(local) != count:
        rows, columns = _grid_shape(count, image, [])
        cell_w, cell_h = 1 / columns, 1 / rows
        boxes = tuple(Detection(c*cell_w, r*cell_h, cell_w, cell_h, 0, .55, "layout_requires_review",
            ((c*cell_w,r*cell_h),((c+1)*cell_w,r*cell_h),((c+1)*cell_w,(r+1)*cell_h),(c*cell_w,(r+1)*cell_h)),
            ("projection_grid", "global_count")) for r,c in product(range(rows), range(columns)))
        hypotheses.append(LayoutHypothesis(boxes, .55, rows, columns, signals={**base_signals,"candidate_count":len(local),"expected_count":count,"dpi":dpi}, rejected_reasons=("prior_missing",)))
    if not hypotheses:
        rows, columns = _grid_shape(len(local), image, local)
        hypotheses.append(LayoutHypothesis(tuple(local), round(float(np.mean([x.confidence for x in local])),4), rows, columns,
            signals={**base_signals,"candidate_count":len(local),"expected_count":count,"dpi":dpi}))
    valid = [item for item in hypotheses if not _layout_conflicts(item.detections)]
    if valid:
        return max(valid, key=lambda item: item.score - .3*abs(len(item.detections)-count))
    safest = _non_conflicting_subset(list(hypotheses[0].detections))
    rows, columns = _grid_shape(len(safest), image, safest) if safest else (0, 0)
    return LayoutHypothesis(tuple(safest), 0.0, rows, columns,
        signals={**base_signals, "candidate_count": len(local), "expected_count": count, "dpi": dpi},
        rejected_reasons=local_conflicts or ("invalid_layout_geometry",))


def detect_cards(content: bytes, *, dpi: int = 300, expected_count: int | None = None,
                 prior: list[Detection] | None = None) -> list[Detection]:
    return list(detect_layout(content, dpi=dpi, expected_count=expected_count, prior=prior).detections)


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
