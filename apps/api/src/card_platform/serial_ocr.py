"""Local, auditable multi-orientation OCR for printed serial fractions."""

from __future__ import annotations

import asyncio
import json
import platform
import re
import shutil
import tempfile
from pathlib import Path

from card_platform.domain.serial_evidence import unique_visible_serial, visible_serial_evidence
from card_platform.schemas import CardRecognitionResult

SCRIPT_PATH = Path(__file__).resolve().parents[4] / "scripts" / "ocr_card_serials.swift"
_OCR_LIMIT = asyncio.Semaphore(2)


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
    stamp = unique_visible_serial(evidence, source_is_physical=True)
    trace = {
        "tier": "local_serial_ocr",
        "model": "apple_vision",
        "reason": "four_orientations_and_bounded_expanded_roi",
        "latency_ms": latency_ms,
        "status": payload.get("status", "failed"),
        "estimated_cost_cny": "0",
        "strategy_version": payload.get("strategy_version"),
        "detail_seed_count": payload.get("detail_seed_count", 0),
        "detail_request_count": payload.get("detail_request_count", 0),
        "observations": payload.get("observations", []),
        "outcome": "confirmed" if stamp else "conflicting" if evidence else "not_observed",
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
            is_numbered=True if stamp else None,
            serial_number=stamp.number if stamp else None,
            serial_total=stamp.total if stamp else None,
        )
        if not stamp:
            updates["caveats"] = [
                *result.caveats,
                "实卡限编 OCR 出现冲突读数，需核对原图；未猜测印编。",
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
    async with _OCR_LIMIT:
        with tempfile.TemporaryDirectory(prefix="card-serial-ocr-") as directory:
            paths = []
            for index, (content, content_type) in enumerate(images):
                suffix = ".png" if "png" in content_type.lower() else ".jpg"
                path = Path(directory) / f"image-{index}{suffix}"
                path.write_bytes(content)
                paths.append(str(path))
            try:
                process = await asyncio.create_subprocess_exec(
                    "swift",
                    str(SCRIPT_PATH),
                    *paths,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError:
                return {
                    "status": "failed",
                    "reason": "local_serial_ocr_start_failed",
                    "observations": [],
                }
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout_seconds)
            except (TimeoutError, asyncio.CancelledError) as error:
                if process.returncode is None:
                    process.kill()
                await process.communicate()
                if isinstance(error, asyncio.CancelledError):
                    raise
                return {
                    "status": "failed",
                    "reason": "local_serial_ocr_timeout",
                    "observations": [],
                }
            if process.returncode:
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
