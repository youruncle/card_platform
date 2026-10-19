from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from math import hypot
from typing import Any

from card_platform.flatbed_pair_signals import (
    canonical_product,
    extract_pair_signals,
    normalized_text,
)


TRANSFORMS = ("identity", "mirror_x", "mirror_y", "rotate_180")
OCR_STOPWORDS = {
    "born",
    "college",
    "drafted",
    "experience",
    "fleer",
    "forward",
    "guard",
    "height",
    "printed",
    "properties",
    "season",
    "showcase",
    "team",
    "totals",
    "trade",
    "weight",
    "year",
    "postseason",
    "acquired",
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
    failure_reason: str | None = None


@dataclass(frozen=True)
class PairCandidate:
    front_id: str
    back_id: str
    score: float
    transform: str
    signals: dict[str, float]
    conflicts: tuple[str, ...]
    identity_status: str = "unknown"
    identity_reason_codes: tuple[str, ...] = ()


def _position(region: CardRegion, transform: str) -> tuple[float, float]:
    x = region.x + region.width / 2
    y = region.y + region.height / 2
    if transform in {"mirror_x", "rotate_180"}:
        x = 1 - x
    if transform in {"mirror_y", "rotate_180"}:
        y = 1 - y
    return x, y


def _identity_signals(region: CardRegion) -> dict:
    signals = dict(region.signals)
    # Old rows contain the first biography/date year and even date fragments
    # as serials. Re-read preserved text instead of trusting those old fields.
    signals.pop("year", None)
    if text := signals.get("ocr_text"):
        for key in ("card_number", "serial_number", "serial_total"):
            signals.pop(key, None)
        signals.update(extract_pair_signals(str(text)))
    if signals.get("product_name"):
        signals["product_name"] = canonical_product(signals["product_name"])
    return signals


def _token_similarity(left: dict, right: dict) -> float | None:
    left_tokens = {
        str(token).casefold()
        for token in left.get("ocr_tokens") or []
        if str(token).isalpha()
        and len(str(token)) >= 3
        and str(token).casefold() not in OCR_STOPWORDS
    }
    right_tokens = {
        str(token).casefold()
        for token in right.get("ocr_tokens") or []
        if str(token).isalpha()
        and len(str(token)) >= 3
        and str(token).casefold() not in OCR_STOPWORDS
    }
    if left_tokens and right_tokens:
        # Card backs contain large stats tables and boilerplate, while fronts
        # often contain only a player/team name. Measure how much of the smaller
        # identity token set is present, tolerating common OCR substitutions.
        smaller, larger = sorted((left_tokens, right_tokens), key=len)
        matched = sum(
            any(
                token == candidate or SequenceMatcher(None, token, candidate).ratio() >= 0.78
                for candidate in larger
            )
            for token in smaller
        )
        return matched / len(smaller)
    return None


def _identity_comparison(left: CardRegion, right: CardRegion) -> tuple[float, str, tuple[str, ...]]:
    a, b = _identity_signals(left), _identity_signals(right)
    compared = {}
    for key in ("product_name", "insert_name", "card_number", "player_name"):
        if a.get(key) and b.get(key):
            compared[key] = normalized_text(a[key]) == normalized_text(b[key])
    if all(
        s.get("serial_number") is not None and s.get("serial_total") is not None for s in (a, b)
    ):
        compared["serial"] = (a["serial_number"], a["serial_total"]) == (
            b["serial_number"],
            b["serial_total"],
        )
    if compared and all(s.get("season_start") and s.get("season_end") for s in (a, b)):
        compared["season"] = (a["season_start"], a["season_end"]) == (
            b["season_start"],
            b["season_end"],
        )
    conflicts = tuple(
        f"ocr_{key.removesuffix('_name')}_conflict"
        for key, matched in compared.items()
        if not matched
    )
    # Shared product and checklist number can describe a legitimate two-sided
    # multi-subject design. Its name difference needs design review, not an
    # automatic wrong-card claim from OCR alone.
    design_review = (
        compared.get("player_name") is False
        and all(compared.get(key) is True for key in ("product_name", "card_number"))
        and normalized_text(a.get("insert_name")) in {"generations", "rookie veteran"}
    )
    if design_review:
        conflicts = tuple(code for code in conflicts if code != "ocr_player_conflict")
    if conflicts:
        return 0.0, "conflict", conflicts
    tokens = _token_similarity(a, b)
    if not compared:
        return (0.5 if tokens is None else tokens), "unknown", ("ocr_identity_unverified",)
    weights = {
        "product_name": 0.4,
        "insert_name": 0.2,
        "card_number": 0.3,
        "player_name": 0.1,
        "serial": 0.4,
        "season": 0.05,
    }
    matched_weight = sum(weights[key] for key, matched in compared.items() if matched)
    observed_weight = sum(weights[key] for key in compared)
    score = matched_weight / observed_weight
    if tokens is not None:
        score = 0.8 * score + 0.2 * tokens
    verified = (
        compared.get("product_name") is True
        and (compared.get("player_name") is True or compared.get("card_number") is True)
    ) or compared.get("serial") is True
    if design_review:
        return score, "unknown", ("multi_subject_design_requires_review",)
    return (
        score,
        "consistent" if verified else "unknown",
        (() if verified else ("ocr_identity_unverified",)),
    )


def _text_similarity(left: CardRegion, right: CardRegion) -> float:
    return _identity_comparison(left, right)[0]


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


def _crop_quality(region: CardRegion) -> tuple[float, tuple[str, ...]]:
    raw = region.signals.get("crop_quality")
    if not isinstance(raw, dict):
        return region.confidence, ()
    score = float(raw.get("usability_score", region.confidence))
    violations = raw.get("violations")
    if not isinstance(violations, list):
        return score, ()
    errors = tuple(
        str(item.get("code"))
        for item in violations
        if isinstance(item, dict) and item.get("severity") == "error" and item.get("code")
    )
    return score, errors


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
    text, identity_status, identity_reasons = _identity_comparison(front, back)
    explicit_visual = back.signals.get("visual_similarity", {})
    visual = float(explicit_visual.get(front.id, _visual_similarity(front, back)))
    independent = back.signals.get("recognition_similarity", {}).get(front.id)
    recognition_observed = isinstance(independent, (int, float)) and 0 <= independent <= 1
    recognition = float(independent) if recognition_observed else 0.5
    front_crop_quality, front_crop_errors = _crop_quality(front)
    back_crop_quality, back_crop_errors = _crop_quality(back)
    values = {
        "geometry": geometry,
        "shape": shape,
        "outline": outline,
        "ocr": text,
        "visual": visual,
        "recognition": recognition,
        "recognition_observed": float(recognition_observed),
        "crop_quality": min(front_crop_quality, back_crop_quality),
    }
    score = (
        0.38 * geometry
        + 0.14 * shape
        + 0.10 * outline
        + 0.16 * text
        + 0.10 * visual
        + (0.12 * recognition if recognition_observed else 0.0)
    ) / (1.0 if recognition_observed else 0.88)
    if identity_status == "conflict":
        score *= 0.45
    conflicts: list[str] = []
    if geometry < 0.35:
        conflicts.append("layout_position_conflict")
    if identity_status == "conflict":
        conflicts.append("ocr_identity_conflict")
    if shape < 0.75:
        conflicts.append("outline_size_conflict")
    if front.failure_reason:
        conflicts.append(f"front_crop_{front.failure_reason}")
    if back.failure_reason:
        conflicts.append(f"back_crop_{back.failure_reason}")
    conflicts.extend(f"front_crop_quality_{code}" for code in front_crop_errors)
    conflicts.extend(f"back_crop_quality_{code}" for code in back_crop_errors)
    return PairCandidate(
        front.id,
        back.id,
        round(score, 6),
        transform,
        values,
        tuple(conflicts),
        identity_status,
        identity_reasons,
    )


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
    back_signatures = {}
    for back in backs:
        signals = _identity_signals(back)
        signature = tuple(
            normalized_text(signals.get(key))
            for key in (
                "product_name",
                "insert_name",
                "player_name",
                "card_number",
                "serial_number",
                "serial_total",
            )
        )
        back_signatures[back.id] = signature
    for pair in selected:
        confidence = pair.score
        signature = back_signatures[pair.back_id]
        ambiguous = (
            bool(signature[0]) and sum(other == signature for other in back_signatures.values()) > 1
        )
        identity_status = (
            "unknown" if ambiguous and pair.identity_status != "conflict" else pair.identity_status
        )
        identity_reasons = (
            (*pair.identity_reason_codes, "same_design_copy_unverified")
            if ambiguous
            else pair.identity_reason_codes
        )
        level = (
            "high"
            if confidence >= 0.82 and not pair.conflicts and identity_status == "consistent"
            else "medium"
            if confidence >= 0.62
            else "low"
        )
        pairs.append(
            {
                **pair.__dict__,
                "identity_status": identity_status,
                "identity_reason_codes": identity_reasons,
                "identity_ambiguous": ambiguous,
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
