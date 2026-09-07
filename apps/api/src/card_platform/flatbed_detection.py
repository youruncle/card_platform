from __future__ import annotations

from dataclasses import dataclass, field
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


DETECTOR_VERSION = "component-first-layout-v14"

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

IRREGULAR_PRIMARY_MIN_AREA_RATIO = 0.045
IRREGULAR_PRIMARY_MAX_AREA_RATIO = 0.16
IRREGULAR_SATELLITE_MIN_AREA_RATIO = 0.006
IRREGULAR_SATELLITE_MAX_AREA_RATIO = 0.06
IRREGULAR_COMPONENT_MIN_FILL_RATIO = 0.12
IRREGULAR_PRIMARY_MIN_FILL_RATIO = 0.20
IRREGULAR_COMPONENT_MARGIN_RATIO = 0.06


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


def _component_intersection_over_smaller(left: dict, right: dict) -> float:
    intersection_width = max(
        0,
        min(left["x"] + left["width"], right["x"] + right["width"])
        - max(left["x"], right["x"]),
    )
    intersection_height = max(
        0,
        min(left["y"] + left["height"], right["y"] + right["height"])
        - max(left["y"], right["y"]),
    )
    smaller = min(
        left["width"] * left["height"], right["width"] * right["height"]
    )
    return intersection_width * intersection_height / smaller if smaller else 0.0


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
            if (
                area_ratio < 0.002
                or fill_ratio < IRREGULAR_COMPONENT_MIN_FILL_RATIO
            ):
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
                    or candidate["x"] + candidate["width"]
                    > card["x"] + card["width"] * 1.02
                )
                is_compatible = (
                    extends
                    and overlap >= 0.65
                    and gap <= card["height"] * 0.42
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
                    or candidate["y"] + candidate["height"]
                    > card["y"] + card["height"] * 1.02
                )
                is_compatible = (
                    extends
                    and overlap >= 0.65
                    and gap <= card["width"] * 0.42
                    and candidate["height"] <= card["height"] * 0.55
                    and candidate["width"] >= card["width"] * 0.60
                )
            if is_compatible:
                compatible.append((float(gap), -float(overlap), candidate))

        label = (
            min(compatible, key=lambda item: (item[0], item[1]))[2]
            if compatible
            else None
        )
        members = [card, *([label] if label is not None else [])]
        left = min(item["x"] for item in members)
        top = min(item["y"] for item in members)
        right = max(item["x"] + item["width"] for item in members)
        bottom = max(item["y"] + item["height"] for item in members)
        margin = round(
            min(card["width"], card["height"])
            * IRREGULAR_COMPONENT_MARGIN_RATIO
        )
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
    labelled = [
        item
        for item in detections
        if "grading_label_association" in item.evidence
    ]
    if len(labelled) < 2:
        return detections

    horizontal = sum(item.width > item.height for item in labelled) >= len(labelled) / 2
    same_orientation = [
        item for item in labelled if (item.width > item.height) == horizontal
    ]
    if len(same_orientation) < 2:
        return detections

    if horizontal:
        outer_start = float(np.median([item.x for item in same_orientation]))
        outer_end = float(
            np.median([item.x + item.width for item in same_orientation])
        )
    else:
        outer_start = float(np.median([item.y for item in same_orientation]))
        outer_end = float(
            np.median([item.y + item.height for item in same_orientation])
        )
    envelope_size = outer_end - outer_start
    if envelope_size <= 0:
        return detections

    completed: list[Detection] = []
    for item in detections:
        if (
            "grading_label_association" in item.evidence
            or (item.width > item.height) != horizontal
        ):
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
    confidently_empty = uniformly_empty or trailing_neighbour_sliver
    return not confidently_empty, metrics


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
            if short_px < dpi * 2.15 or not dpi * 3.0 <= long_px <= dpi * 5.5:
                continue
            shapes.append((abs(ratio - 1.40) + count * 0.001, rows, columns))
    if not shapes:
        # Small synthetic/preview images do not carry real DPI scale. Keep the
        # same aspect/count objective as a scale-free fallback for those.
        for rows in range(2, 11):
            for columns in range(2, 7):
                count = rows * columns
                if count < 4 or count > 20:
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
    return grid


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

    local_left, local_right = np.quantile(
        ink_x, (GRID_INK_LOW_QUANTILE, GRID_INK_HIGH_QUANTILE)
    )
    local_top, local_bottom = np.quantile(
        ink_y, (GRID_INK_LOW_QUANTILE, GRID_INK_HIGH_QUANTILE)
    )
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


def _local_candidates(content: bytes, *, dpi: int = 300) -> list[Detection]:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码扫描图像")
    height, width = image.shape[:2]
    irregular = _irregular_slab_detections(image)
    associated_labels = sum(
        "grading_label_association" in item.evidence for item in irregular
    )
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
    full_bed_grid = (
        None
        if _foreground_layout_is_complete(foreground)
        else _full_bed_grid_detections(
            image,
            foreground,
            [*found, *threshold_candidates, *irregular],
            dpi,
        )
    )
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
            qualities.append(
                max(0.0, min(1.0, (local_median - valley) / max(local_median, 0.05)))
            )
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
            occupied, occupancy = _grid_cell_occupancy(
                image, left, top, right, bottom
            )
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
            refined = _refine_grid_cell_to_ink_extent(
                image, left, top, right, bottom
            )
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


def _stationary_layout_similarity(
    original: list[Detection], transformed: list[Detection]
) -> float:
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
    samples = np.concatenate((grad[y1 : y1 + band, x1:x2].ravel(), grad[max(y1, y2-band):y2, x1:x2].ravel(), grad[y1:y2, x1:x1+band].ravel(), grad[y1:y2, max(x1,x2-band):x2].ravel()))
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
    if not detections or not all(
        "full_bed_grid" in item.evidence for item in detections
    ):
        return None
    pixel_areas: list[float] = []
    aspect_qualities: list[float] = []
    body_qualities: list[float] = []
    for item in detections:
        pixel_width = item.width * image.shape[1]
        pixel_height = item.height * image.shape[0]
        pixel_areas.append(pixel_width * pixel_height)
        ratio = max(pixel_width, pixel_height) / max(
            1.0, min(pixel_width, pixel_height)
        )
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
        axis_coverage = min(
            metrics["ink_row_coverage"], metrics["ink_column_coverage"]
        )
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
        "candidate_routes": "multi_threshold+adaptive+gradient_lines+projection+components+mixed_slab_instances",
    }
    hypotheses: list[LayoutHypothesis] = []
    if expected_count and prior and len(prior) == count:
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
            refined = tuple(Detection(b.x, b.y, b.width, b.height, b.angle, round(0.68 + 0.28*s, 4),
                                      "boundary_evidence_weak" if s < .08 else None, b.points,
                                      (*b.evidence, "front_to_back_prior", "gradient_boundary", "global_count")) for b, s in zip(boxes, scores))
            rows, columns = _grid_shape(len(boxes), image, boxes)
            hypotheses.append(LayoutHypothesis(refined, round(.72 + .23*float(np.mean(scores)) - movement_penalty, 4), rows, columns, transform,
                {**base_signals, "candidate_count": len(local), "expected_count": count, "mean_boundary_energy": round(float(np.mean(scores)),4), "stationary_layout_similarity": round(stationary_similarity, 4), "movement_penalty": round(movement_penalty, 4), "dpi": dpi, "prior_weight": .72,
                 "prior_role": "identity_slot_topology_only" if stationary_prior else "layout_hypothesis",
                 "occupied_slot_count": len(boxes), "missing_slot_count": count - len(boxes),
                 "minimum_slot_body_coverage": round(min(slot_occupancy, default=0.0), 4)}))
        if not stationary_prior:
            rows, columns = _grid_shape(count, image, prior)
            independent, gap_quality = _projection_gap_grid_detections(
                image, rows, columns
            )
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
    if len(local) == count and not _layout_conflicts(local):
        rows, columns = _grid_shape(count, image, local)
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
        hypotheses.append(LayoutHypothesis(tuple(local), local_score, rows, columns,
            signals={**base_signals, "candidate_count": len(local), "expected_count": count, "dpi": dpi,
                     "sources": "full_bed_grid+current_side_occupancy" if full_bed_grid_exact_count else "multi_threshold+gradient+components",
                     "front_prior_role": "count_only" if full_bed_grid_exact_count and expected_count else "not_applicable",
                     "current_grid_evidence_score": current_grid_score or 0.0}))
    if expected_count and len(prior) != count and len(local) != count:
        # Count is metadata/expectation, never permission to synthesize evenly
        # divided crops. Preserve the current-side candidates so missing slots
        # remain visible as a count mismatch and can be edited or rescanned.
        rows, columns = _grid_shape(len(local), image, local) if local else (0, 0)
        hypotheses.append(
            LayoutHypothesis(
                tuple(local),
                round(float(np.mean([item.confidence for item in local])), 4)
                if local
                else 0.0,
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
        hypotheses.append(LayoutHypothesis(tuple(local), round(float(np.mean([x.confidence for x in local])),4), rows, columns,
            signals={**base_signals,"candidate_count":len(local),"expected_count":count,"dpi":dpi}))
    valid = [item for item in hypotheses if not _layout_conflicts(item.detections)]
    if valid:
        return max(
            valid,
            key=lambda item: (
                int(
                    stationary_prior
                    and item.transform == "identity"
                    and item.signals.get("prior_role")
                    == "identity_slot_topology_only"
                    and len(item.detections) == count
                ),
                item.score - 0.3 * abs(len(item.detections) - count),
            ),
        )
    safest = _non_conflicting_subset(list(hypotheses[0].detections))
    rows, columns = _grid_shape(len(safest), image, safest) if safest else (0, 0)
    return LayoutHypothesis(tuple(safest), 0.0, rows, columns,
        signals={**base_signals, "candidate_count": len(local), "expected_count": count, "dpi": dpi},
        rejected_reasons=local_conflicts or ("invalid_layout_geometry",))


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
