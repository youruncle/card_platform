"""Local, auditable multi-orientation OCR for printed serial fractions."""

from __future__ import annotations

import json
import math
import platform
import re
import shutil
import tempfile
from pathlib import Path

from card_platform.domain.identity_review import is_tcg_collector_number
from card_platform.domain.serial_evidence import serial_field_evidence, unique_visible_serial, visible_serial_evidence
from card_platform.schemas import CardRecognitionResult
from card_platform.local_vision_process import LocalVisionCircuitOpen, run_local_vision

SCRIPT_PATH = Path(__file__).resolve().parents[4] / "scripts" / "ocr_card_serials.swift"
PARTIAL_SERIAL_CONTRACT = 'physical-serial-field-consensus-v1'


def _original_box(observation: dict) -> tuple[float, float, float, float] | None:
    """Convert Vision's oriented bottom-left rectangle to the original image."""
    values = observation.get('bbox')
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return None
    x, y, w, h = values
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > 1.000001 or y + h > 1.000001:
        return None
    angle = observation.get('orientation_degrees')
    if angle == 0:
        return x, y, w, h
    if angle == 90:
        return 1-y-h, x, h, w
    if angle == 180:
        return 1-x-w, 1-y-h, w, h
    if angle == 270:
        return y, 1-x-w, h, w
    return None


def _same_stamp_box(a, b) -> bool:
    overlap = max(0, min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0])) * max(
        0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))
    return overlap / (a[2]*a[3] + b[2]*b[3] - overlap) >= .4


def _partial_serial_total(result: CardRecognitionResult, payload: dict) -> int | None:
    """Use repeated original-region reads, never a vote or an inferred digit."""
    if payload.get('status') != 'succeeded' or payload.get('errors') or payload.get('truncated'):
        return None
    observations = payload.get('observations', [])
    if not isinstance(observations, list):
        return None
    # A collector fraction/TCG context is ambiguous even if OCR omitted '#'.
    context = ' '.join([str(result.brand_name or ''), str(result.product_name or ''), *[
        str(o.get('context', '')) for o in observations if isinstance(o, dict)]])
    if re.search(r'\bpok[eé]mon\b|宝可梦|寶可夢|ポケモン', context, re.I) or re.fullmatch(
        r'\s*#?\s*\d+\s*/\s*\d+\s*', str(result.card_number or '')):
        return None
    accepted, full = [], []
    for observation in observations:
        if not isinstance(observation, dict) or not serial_observation_evidence({'observations':[observation]}):
            continue
        if re.search(r'(?:\b(?:card\s*(?:number|no\.?)|collector\s*number)|卡号|卡號)\s*[:：#]?\s*\d+\s*[/／⁄∕]',
                     str(observation.get('context', '')), re.I):
            return None
        stamp = unique_visible_serial([observation['text']], source_is_physical=True)
        accepted.append(stamp)
        confidence = observation.get('confidence')
        if (observation.get('read_pass') == 'full'
                and isinstance(confidence, (int, float)) and .8 <= confidence <= 1
                and observation.get('image_index') in (0, 1)
                and re.fullmatch(r'\s*\d{1,6}\s*[/／⁄∕]\s*\d{1,6}\s*', observation['text'])
                and (box := _original_box(observation)) is not None):
            full.append((observation, box))
    totals = {stamp.total for stamp in accepted}
    if len(totals) != 1 or len(full) < 2:
        return None
    total = next(iter(totals))
    # Keep conflicts with any other physical field evidence unresolved too.
    if any(s.total != total for s in visible_serial_evidence(serial_field_evidence(result.field_evidence))):
        return None
    first, box = full[0]
    if any(o['image_index'] != first['image_index'] or not _same_stamp_box(box, b) for o, b in full):
        return None
    if len({o['orientation_degrees'] for o, _ in full}) < 2:
        return None
    return total


def verified_ocr_serial_total(result: CardRecognitionResult) -> int | None:
    """Recompute partial provenance when canonicalizing, including JSON reloads."""
    if result.serial_number is not None or result.serial_total is None:
        return None
    traces = [r for r in result.recognition_route if r.get('tier') == 'local_serial_ocr']
    if not traces:
        return None
    trace = traces[-1]
    if trace.get('field_consensus_contract') != PARTIAL_SERIAL_CONTRACT or trace.get('outcome') != 'partial':
        return None
    evidence = serial_observation_evidence(trace)
    if not evidence or not set(evidence) <= set(result.field_evidence.get('serial_total', [])):
        return None
    total = _partial_serial_total(result, trace)
    return total if total == result.serial_total else None


def serial_observation_evidence(payload: dict) -> list[str]:
    """Keep the recognized line and its context tied to the original source."""
    evidence = []
    for observation in payload.get("observations", []):
        if not isinstance(observation, dict):
            continue
        text = observation.get("text", "")
        context = observation.get("context", text)
        if not isinstance(text, str) or not isinstance(context, str):
            continue
        stamp = unique_visible_serial([text], source_is_physical=True)
        if str(observation.get("read_pass", "")).startswith("expanded_isolated_digits_roi_"):
            seed_text = observation.get("seed_text")
            if not isinstance(seed_text, str) or not re.fullmatch(r"\s*[0-9]{3,6}\s*", seed_text):
                continue
            seed_value = int(seed_text)
            if stamp is None or seed_value not in {stamp.number, stamp.total}:
                continue
        supported = visible_serial_evidence([context], source_is_physical=True)
        if stamp is None or not any(
            (candidate.number, candidate.total) == (stamp.number, stamp.total)
            for candidate in supported
        ):
            continue
        side = "正面" if observation.get("image_index") == 0 else "背面"
        evidence.append(
            f"Apple Vision 实卡{side} OCR，旋转 {observation.get('orientation_degrees', 0)}°，"
            f"{observation.get('read_pass', 'full')}，区域 {observation.get('bbox', [])}："
            f"{text}；相邻文字：{context}"
        )
    return list(dict.fromkeys(evidence))


def apply_serial_ocr(
    result: CardRecognitionResult, payload: dict, latency_ms: int
) -> CardRecognitionResult:
    evidence = serial_observation_evidence(payload)
    if is_tcg_collector_number(result.model_dump()):
        # Ordinary card/attack fractions are not serial stamps in this scope.
        # A separate explicitly labelled serial can still supply physical proof.
        evidence = [text for text in evidence if re.search(
            r'\b(?:serial|numbered|print\s*run)\b|限编|限編', text, re.I)]
    stamp = unique_visible_serial(evidence, source_is_physical=True)
    partial_total = _partial_serial_total(result, payload) if evidence and not stamp else None
    trace = {
        "tier": "local_serial_ocr",
        "model": "apple_vision",
        "reason": "four_orientations_and_bounded_expanded_roi",
        "latency_ms": latency_ms,
        "status": payload.get("status", "failed"),
        "failure_reason": payload.get("reason"),
        "estimated_cost_cny": "0",
        "strategy_version": payload.get("strategy_version"),
        "detail_seed_count": payload.get("detail_seed_count", 0),
        "detail_request_count": payload.get("detail_request_count", 0),
        "observations": payload.get("observations", []),
        "errors": payload.get("errors", []),
        "truncated": payload.get("truncated", False),
        "field_consensus_contract": PARTIAL_SERIAL_CONTRACT,
        "outcome": "confirmed" if stamp else "partial" if partial_total else "conflicting" if evidence else "not_observed",
    }
    updates: dict = {"recognition_route": [*result.recognition_route, trace]}
    if evidence:
        fields = {key: list(values) for key, values in result.field_evidence.items()}
        for field in ("is_numbered", "serial_number", "serial_total"):
            fields[field] = list(dict.fromkeys([*fields.get(field, []), *evidence]))
        updates["field_evidence"] = fields
        # Conflicting OCR readings remain explicitly unresolved; never pick the
        # smallest denominator or manufacture a digit from a likely parallel.
        updates.update(
            is_numbered=True if stamp or partial_total else None,
            serial_number=stamp.number if stamp else None,
            serial_total=stamp.total if stamp else partial_total,
        )
        if not stamp:
            updates["caveats"] = [
                *result.caveats,
                ("实卡印章多方向原图读数的分母一致，已保留总限编；分子冲突，保持未知并待复核。"
                 if partial_total else "实卡限编 OCR 出现冲突读数，需核对原图；未猜测印编。"),
            ]
    return result.model_copy(update=updates)


async def recognize_serial_images(
    images: list[tuple[bytes, str]], *, timeout_seconds: float = 25
) -> dict:
    """Return original OCR observations, not a guessed identity or print run.

    Bounding boxes use Vision's normalized, bottom-left coordinates in the
    explicitly recorded orientation. Consumers must validate fractions/date
    context and resolve conflicting observations before applying any update.
    """
    if (
        not images
        or platform.system() != "Darwin"
        or not SCRIPT_PATH.is_file()
        or not shutil.which("swift")
    ):
        return {"status": "skipped", "reason": "local_serial_ocr_unavailable", "observations": []}
    with tempfile.TemporaryDirectory(prefix="card-serial-ocr-") as directory:
        paths = []
        for index, (content, content_type) in enumerate(images):
            suffix = ".png" if "png" in content_type.lower() else ".jpg"
            path = Path(directory) / f"image-{index}{suffix}"
            path.write_bytes(content)
            paths.append(str(path))
        try:
            stdout, _, returncode = await run_local_vision(
                "swift", str(SCRIPT_PATH), *paths, timeout_seconds=timeout_seconds
            )
        except OSError:
            return {
                "status": "failed",
                "reason": "local_serial_ocr_start_failed",
                "observations": [],
            }
        except LocalVisionCircuitOpen:
            return {"status": "skipped", "reason": "local_vision_cooldown", "observations": []}
        except TimeoutError:
            return {"status": "failed", "reason": "local_serial_ocr_timeout", "observations": []}
        if returncode:
            return {
                "status": "failed",
                "reason": "local_serial_ocr_worker_failed",
                "observations": [],
            }
        try:
            payload = json.loads(stdout)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("observations"), list
            ):
                raise ValueError("invalid serial OCR response")
        except (ValueError, TypeError):
            return {
                "status": "failed",
                "reason": "local_serial_ocr_invalid_response",
                "observations": [],
            }
        return {
            "status": (
                "partial" if payload.get("errors") or payload.get("truncated") else "succeeded"
            ),
            **payload,
        }
