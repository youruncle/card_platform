from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from math import hypot
from typing import Any


TRANSFORMS = ("identity", "mirror_x", "mirror_y", "rotate_180")
OCR_STOPWORDS = {
    "born", "college", "drafted", "experience", "fleer", "forward", "guard",
    "height", "printed", "properties", "season", "showcase", "team", "totals",
    "trade", "weight", "year", "postseason", "acquired",
}


@dataclass(frozen=True)
class CardRegion:
    id: str
    x: float
    y: float
    width: float
    height: float
    confidence: float = 1.0
    signals: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PairCandidate:
    front_id: str
    back_id: str
    score: float
    transform: str
    signals: dict[str, float]
    conflicts: tuple[str, ...]


def _position(region: CardRegion, transform: str) -> tuple[float, float]:
    x = region.x + region.width / 2
    y = region.y + region.height / 2
    if transform in {"mirror_x", "rotate_180"}:
        x = 1 - x
    if transform in {"mirror_y", "rotate_180"}:
        y = 1 - y
    return x, y


def _text_similarity(left: CardRegion, right: CardRegion) -> float:
    keys = ("player_name", "card_number", "year", "brand", "serial_number")
    compared = 0
    matched = 0
    for key in keys:
        a = str(left.signals.get(key) or "").strip().casefold()
        b = str(right.signals.get(key) or "").strip().casefold()
        if a and b:
            compared += 1
            matched += a == b
    if compared:
        return matched / compared
    left_tokens = {
        str(token).casefold()
        for token in left.signals.get("ocr_tokens") or []
        if str(token).isalpha() and len(str(token)) >= 3 and str(token).casefold() not in OCR_STOPWORDS
    }
    right_tokens = {
        str(token).casefold()
        for token in right.signals.get("ocr_tokens") or []
        if str(token).isalpha() and len(str(token)) >= 3 and str(token).casefold() not in OCR_STOPWORDS
    }
    if left_tokens and right_tokens:
        # Card backs contain large stats tables and boilerplate, while fronts
        # often contain only a player/team name. Measure how much of the smaller
        # identity token set is present, tolerating common OCR substitutions.
        smaller, larger = sorted((left_tokens, right_tokens), key=len)
        matched = sum(
            any(
                token == candidate
                or SequenceMatcher(None, token, candidate).ratio() >= 0.78
                for candidate in larger
            )
            for token in smaller
        )
        return matched / len(smaller)
    return 0.5


def _visual_similarity(left: CardRegion, right: CardRegion) -> float:
    left_hist = left.signals.get("color_histogram")
    right_hist = right.signals.get("color_histogram")
    histogram_score = 0.5
    if (
        isinstance(left_hist, list)
        and isinstance(right_hist, list)
        and len(left_hist) == len(right_hist)
    ):
        histogram_score = max(
            0.0, 1.0 - sum(abs(float(a) - float(b)) for a, b in zip(left_hist, right_hist)) / 2
        )
    left_hash = left.signals.get("perceptual_hash")
    right_hash = right.signals.get("perceptual_hash")
    hash_score = 0.5
    if isinstance(left_hash, int) and isinstance(right_hash, int):
        hash_score = 1.0 - (left_hash ^ right_hash).bit_count() / 64
    return (histogram_score + hash_score) / 2


def score_pair(front: CardRegion, back: CardRegion, transform: str) -> PairCandidate:
    fx, fy = _position(front, "identity")
    bx, by = _position(back, transform)
    geometry = max(0.0, 1.0 - hypot(fx - bx, fy - by) / 0.55)
    front_ratio = front.width / max(front.height, 1e-6)
    back_ratio = back.width / max(back.height, 1e-6)
    shape = max(0.0, 1.0 - abs(front_ratio - back_ratio) / max(front_ratio, back_ratio))
    outline = 1.0 - min(
        1.0,
        abs(front.width * front.height - back.width * back.height)
        / max(front.width * front.height, back.width * back.height, 1e-6),
    )
    text = _text_similarity(front, back)
    explicit_visual = back.signals.get("visual_similarity", {})
    visual = float(explicit_visual.get(front.id, _visual_similarity(front, back)))
    recognition = float(back.signals.get("recognition_similarity", {}).get(front.id, text))
    values = {
        "geometry": geometry,
        "shape": shape,
        "outline": outline,
        "ocr": text,
        "visual": visual,
        "recognition": recognition,
    }
    score = (
        0.38 * geometry
        + 0.14 * shape
        + 0.10 * outline
        + 0.16 * text
        + 0.10 * visual
        + 0.12 * recognition
    )
    conflicts: list[str] = []
    if geometry < 0.35:
        conflicts.append("layout_position_conflict")
    if text < 0.25:
        conflicts.append("ocr_identity_conflict")
    if shape < 0.75:
        conflicts.append("outline_size_conflict")
    return PairCandidate(front.id, back.id, round(score, 6), transform, values, tuple(conflicts))


def _hungarian_max(matrix: list[list[float]]) -> list[int]:
    """Maximum-weight rectangular assignment; returns a column per row."""
    if not matrix:
        return []
    rows, cols = len(matrix), len(matrix[0])
    size = max(rows, cols)
    maximum = max((max(row) for row in matrix), default=0.0)
    cost = [
        [maximum - (matrix[i][j] if i < rows and j < cols else 0.0) for j in range(size)]
        for i in range(size)
    ]
    u = [0.0] * (size + 1)
    v = [0.0] * (size + 1)
    p = [0] * (size + 1)
    way = [0] * (size + 1)
    for i in range(1, size + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta, j1 = float("inf"), 0
            for j in range(1, size + 1):
                if not used[j]:
                    current = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if current < minv[j]:
                        minv[j], way[j] = current, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [-1] * rows
    for j in range(1, size + 1):
        if 0 < p[j] <= rows and j <= cols:
            result[p[j] - 1] = j - 1
    return result


def propose_pairs(fronts: list[CardRegion], backs: list[CardRegion]) -> dict[str, Any]:
    best: tuple[float, str, list[PairCandidate], list[int], list[list[PairCandidate]]] | None = None
    for transform in TRANSFORMS:
        candidates = [[score_pair(front, back, transform) for back in backs] for front in fronts]
        assignment = _hungarian_max([[candidate.score for candidate in row] for row in candidates])
        selected = [
            candidates[index][column] for index, column in enumerate(assignment) if column >= 0
        ]
        total = sum(pair.score for pair in selected)
        if best is None or total > best[0]:
            best = (total, transform, selected, assignment, candidates)
    selected = best[2] if best else []
    paired_fronts = {pair.front_id for pair in selected}
    paired_backs = {pair.back_id for pair in selected}
    pairs = []
    for pair in selected:
        confidence = pair.score
        level = (
            "high"
            if confidence >= 0.82 and not pair.conflicts
            else "medium"
            if confidence >= 0.62
            else "low"
        )
        pairs.append(
            {
                **pair.__dict__,
                "confidence": confidence,
                "confidence_level": level,
                "requires_review": level != "high",
            }
        )
    return {
        "transform": best[1] if best else "identity",
        "pairs": pairs,
        "candidates": [candidate.__dict__ for row in best[4] for candidate in row] if best else [],
        "missing_front": [back.id for back in backs if back.id not in paired_backs],
        "missing_back": [front.id for front in fronts if front.id not in paired_fronts],
        "requires_review": len(fronts) != len(backs)
        or any(pair["requires_review"] for pair in pairs),
    }
