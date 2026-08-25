from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import re
from pathlib import Path
from uuid import UUID, uuid4

import cv2
import numpy as np
from psycopg.types.json import Jsonb

from card_platform.card_orientation import (
    AppleVisionOrientationClassifier,
    OrientationDecision,
    rotate_image_content,
)
from card_platform.flatbed_detection import (
    DETECTOR_VERSION,
    Detection,
    detect_layout,
    explain_detection,
    visual_signals,
)
from card_platform.flatbed_matching import CardRegion, propose_pairs


class FlatbedBridgeError(RuntimeError):
    pass


_bridge_session_lock = asyncio.Lock()

# Grid recovery describes the allocation cell, not always the physical holder:
# tightly packed holders can cross an inferred row/column boundary by several
# percent. Keep enough real scanner-bed context to avoid clipping labels,
# corners, and holder lips used for condition review.
CROP_SHORT_AXIS_PADDING_RATIO = 0.06
CROP_SHORT_AXIS_LEADING_INSET_RATIO = 0.06
CROP_LONG_AXIS_PADDING_RATIO = 0.0


def padded_source_quad(
    points: np.ndarray, image_width: int, image_height: int
) -> np.ndarray:
    expanded = points.copy()
    span_x = float(np.ptp(points[:, 0]))
    span_y = float(np.ptp(points[:, 1]))
    if span_x >= span_y:
        midpoint = points[:, 1].mean()
        leading = points[:, 1] < midpoint
        trailing = points[:, 1] > midpoint
        if float(points[:, 1].min()) > 1:
            expanded[leading, 1] += span_y * CROP_SHORT_AXIS_LEADING_INSET_RATIO
        expanded[trailing, 1] += span_y * CROP_SHORT_AXIS_PADDING_RATIO
    else:
        midpoint = points[:, 0].mean()
        leading = points[:, 0] < midpoint
        trailing = points[:, 0] > midpoint
        if float(points[:, 0].min()) > 1:
            expanded[leading, 0] += span_x * CROP_SHORT_AXIS_LEADING_INSET_RATIO
        expanded[trailing, 0] += span_x * CROP_SHORT_AXIS_PADDING_RATIO
    expanded[:, 0] = np.clip(expanded[:, 0], 0, image_width - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, image_height - 1)
    return expanded.astype(np.float32)


class FlatbedService:
    def __init__(self, root: str, bridge_script: str, ocr_script: str | None = None) -> None:
        self.root = Path(root).resolve()
        self.bridge_script = Path(bridge_script).resolve()
        self.ocr_script = Path(ocr_script).resolve() if ocr_script else None
        self.orientation_classifier = AppleVisionOrientationClassifier(
            str(self.bridge_script.with_name("orient_card_images.swift"))
        )

    async def ocr_signals(self, image_path: Path) -> dict:
        if self.ocr_script is None or not self.ocr_script.is_file():
            return {}
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.ocr_script),
                str(image_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
            if process.returncode:
                return {"ocr_failure": "local_vision_failed"}
            payload = json.loads(stdout.decode())
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
            return {"ocr_failure": "local_vision_failed"}
        text = str(payload.get("text") or "")
        tokens = sorted(set(re.findall(r"[A-Za-z0-9#/-]{2,}", text.casefold())))
        signals: dict = {"ocr_text": text, "ocr_tokens": tokens}
        if match := re.search(r"\b(?:19|20)\d{2}\b", text):
            signals["year"] = match.group(0)
        if match := re.search(r"\b(\d{1,4})\s*/\s*(\d{1,4})\b", text):
            signals["serial_number"] = match.group(1)
            signals["serial_total"] = match.group(2)
        if match := re.search(r"(?:NO\.?|#)\s*([A-Z0-9-]{1,12})", text, re.IGNORECASE):
            signals["card_number"] = match.group(1)
        return signals

    async def bridge(
        self, command: str, *, output: Path | None = None, dpi: int = 300, timeout: float = 60
    ) -> list[dict]:
        # ImageCaptureCore grants an exclusive device session. FlatbedService
        # instances are request-scoped, so an instance lock would still allow
        # capability probes and scans from separate HTTP requests to race.
        # Serialize every bridge command process-wide and let each Swift
        # invocation close its session before the next command starts.
        async with _bridge_session_lock:
            return await self._run_bridge(
                command, output=output, dpi=dpi, timeout=timeout
            )

    async def _run_bridge(
        self, command: str, *, output: Path | None = None, dpi: int = 300, timeout: float = 60
    ) -> list[dict]:
        arguments = [str(self.bridge_script), command, "--dpi", str(dpi), "--timeout", str(timeout)]
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.exists():
                raise FlatbedBridgeError("拒绝覆盖已存在的扫描文件")
            arguments += ["--output", str(output)]
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout + 5)
        except asyncio.TimeoutError as error:
            process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            raise FlatbedBridgeError("scanner_timeout") from error
        except asyncio.CancelledError:
            process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            raise
        events = [json.loads(line) for line in stdout.decode().splitlines() if line.strip()]
        if process.returncode:
            message = next(
                (item.get("error") for item in reversed(events) if item.get("event") == "error"),
                stderr.decode().strip() or "scanner_error",
            )
            raise FlatbedBridgeError(message)
        return events

    async def create_session(self, connection, owner_id: UUID, dpi: int) -> dict:
        row = await connection.execute(
            """INSERT INTO flatbed_scan_session(owner_id,device_serial,dpi) VALUES (%s,'4706E5',%s) RETURNING *""",
            (owner_id, dpi),
        )
        return dict(await row.fetchone())

    async def store_sheet(
        self,
        connection,
        owner_id: UUID,
        session_id: UUID,
        side: str,
        content: bytes,
        content_type: str,
        detections_override: list[Detection] | None = None,
    ) -> dict:
        session_result = await connection.execute(
            "SELECT * FROM flatbed_scan_session WHERE id=%s AND owner_id=%s FOR UPDATE",
            (session_id, owner_id),
        )
        session = await session_result.fetchone()
        if session is None:
            raise LookupError("扫描会话不存在")
        prior: list[Detection] = []
        expected_count = None
        if side == "back":
            prior_result = await connection.execute(
                """SELECT r.bounds FROM flatbed_scan_region r JOIN flatbed_scan_sheet s ON s.id=r.sheet_id
                   WHERE s.session_id=%s AND s.owner_id=%s AND s.side='front'
                     AND s.revision=(SELECT max(revision) FROM flatbed_scan_sheet WHERE session_id=%s AND side='front')
                   ORDER BY r.ordinal""",
                (session_id, owner_id, session_id),
            )
            for row in await prior_result.fetchall():
                b = row["bounds"]
                points = tuple((float(p["x"]), float(p["y"])) for p in b.get("points", [])) or None
                prior.append(Detection(float(b["x"]), float(b["y"]), float(b["width"]), float(b["height"]), float(b.get("angle", 0)), 1, points=points))
            expected_count = len(prior) or None
        layout = detect_layout(content, dpi=session["dpi"], expected_count=expected_count, prior=prior)
        detections = detections_override if detections_override is not None else list(layout.detections)
        detection_failures = explain_detection(content, detections)
        if expected_count and len(detections) != expected_count:
            detection_failures.append("front_back_count_mismatch")
        if layout.score < 0.78 or any(item.failure for item in detections):
            detection_failures.append("layout_requires_review")
        revision_result = await connection.execute(
            "SELECT COALESCE(max(revision),0)+1 AS revision FROM flatbed_scan_sheet WHERE session_id=%s AND side=%s",
            (session_id, side),
        )
        revision = (await revision_result.fetchone())["revision"]
        sheet_id = uuid4()
        directory = self.root / str(owner_id) / str(session_id) / str(sheet_id)
        directory.mkdir(parents=True, exist_ok=False)
        extension = ".png" if content_type == "image/png" else ".jpg"
        original = directory / f"original{extension}"
        fd = os.open(original, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        height, width = decoded.shape[:2]
        relative_original = str(original.relative_to(self.root))
        await connection.execute(
            """INSERT INTO flatbed_scan_sheet(id,session_id,owner_id,side,revision,original_path,content_type,sha256,detector_version,layout_hypothesis,front_to_back_prior,requires_review) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                sheet_id,
                session_id,
                owner_id,
                side,
                revision,
                relative_original,
                content_type,
                hashlib.sha256(content).hexdigest(),
                "manual-layout-v1" if detections_override is not None else DETECTOR_VERSION,
                Jsonb({**layout.as_dict(), "manual_override": detections_override is not None}),
                Jsonb({"expected_count": expected_count, "transform": layout.transform} if side == "back" else {}),
                bool(detection_failures),
            ),
        )
        regions = []
        for ordinal, detection in enumerate(detections):
            region_id = uuid4()
            x1 = max(0, int(detection.x * width))
            y1 = max(0, int(detection.y * height))
            x2 = min(width, int((detection.x + detection.width) * width))
            y2 = min(height, int((detection.y + detection.height) * height))
            crop = decoded[y1:y2, x1:x2]
            if detection.points:
                source = np.array(
                    [[point[0] * width, point[1] * height] for point in detection.points],
                    dtype=np.float32,
                )
                sums = source.sum(axis=1)
                differences = np.diff(source, axis=1).reshape(-1)
                ordered = np.array(
                    [
                        source[np.argmin(sums)],
                        source[np.argmin(differences)],
                        source[np.argmax(sums)],
                        source[np.argmax(differences)],
                    ],
                    dtype=np.float32,
                )
                ordered = padded_source_quad(ordered, width, height)
                top_left, top_right, bottom_right, bottom_left = ordered
                target_width = max(
                    1,
                    round(
                        max(
                            np.linalg.norm(top_right - top_left),
                            np.linalg.norm(bottom_right - bottom_left),
                        )
                    ),
                )
                target_height = max(
                    1,
                    round(
                        max(
                            np.linalg.norm(bottom_left - top_left),
                            np.linalg.norm(bottom_right - top_right),
                        )
                    ),
                )
                destination = np.array(
                    [[0, 0], [target_width - 1, 0], [target_width - 1, target_height - 1], [0, target_height - 1]],
                    dtype=np.float32,
                )
                crop = cv2.warpPerspective(
                    decoded,
                    cv2.getPerspectiveTransform(ordered, destination),
                    (target_width, target_height),
                )
            crop_path = directory / f"region-{ordinal}.jpg"
            ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if ok:
                fd = os.open(crop_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded.tobytes())
            orientation = await self.orientation_classifier.classify(crop_path) if ok else None
            if orientation is not None and orientation.confidence >= 0.35:
                rotation = orientation.rotation_degrees
            elif crop.shape[1] > crop.shape[0] * 1.08:
                rotation = 90
                orientation = OrientationDecision(rotation, 0.25, "aspect_ratio_fallback")
            else:
                rotation = 0
                orientation = orientation or OrientationDecision(0, 0, "unresolved")
            if ok and rotation:
                normalized = rotate_image_content(encoded.tobytes(), rotation)
                temporary = crop_path.with_suffix(".tmp.jpg")
                temporary.write_bytes(normalized)
                temporary.replace(crop_path)
                crop = cv2.imdecode(np.frombuffer(normalized, np.uint8), cv2.IMREAD_COLOR)
            signals = visual_signals(crop)
            signals["detection_evidence"] = list(detection.evidence)
            signals["layout_score"] = layout.score
            signals["detector_version"] = DETECTOR_VERSION
            signals["orientation_rotation_degrees"] = rotation
            signals["orientation_confidence"] = round(orientation.confidence, 4)
            signals["orientation_method"] = orientation.method
            signals["orientation_text_count"] = orientation.text_count
            signals["orientation_face_count"] = orientation.face_count
            signals.update(await self.ocr_signals(crop_path))
            bounds = {
                "x": detection.x,
                "y": detection.y,
                "width": detection.width,
                "height": detection.height,
                "angle": detection.angle,
                "points": [
                    {"x": point[0], "y": point[1]} for point in detection.points or ()
                ],
                "crop_padding_ratio": CROP_SHORT_AXIS_PADDING_RATIO,
                "crop_padding_axes": {
                    "short_trailing": CROP_SHORT_AXIS_PADDING_RATIO,
                    "short_leading_inset": CROP_SHORT_AXIS_LEADING_INSET_RATIO,
                    "long": CROP_LONG_AXIS_PADDING_RATIO,
                },
            }
            await connection.execute(
                """INSERT INTO flatbed_scan_region(id,sheet_id,owner_id,ordinal,bounds,confidence,failure_reason,crop_path,signals) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    region_id,
                    sheet_id,
                    owner_id,
                    ordinal,
                    Jsonb(bounds),
                    detection.confidence,
                    detection.failure,
                    str(crop_path.relative_to(self.root)) if ok else None,
                    Jsonb(signals),
                ),
            )
            regions.append(
                {
                    "id": region_id,
                    "ordinal": ordinal,
                    "bounds": bounds,
                    "confidence": detection.confidence,
                    "failure_reason": detection.failure,
                }
            )
        status = "front_scanned" if side == "front" else "back_scanned"
        # Any new sheet revision changes the matching input. Keeping proposals
        # that reference regions from an older revision can silently display or
        # submit stale pairs, so force a fresh global match.
        await connection.execute(
            "DELETE FROM flatbed_pair_proposal WHERE session_id=%s AND owner_id=%s",
            (session_id, owner_id),
        )
        await connection.execute(
            "UPDATE flatbed_scan_session SET status=%s,pairing_requires_review=true,pairing_transform=NULL,updated_at=now() WHERE id=%s",
            (status, session_id),
        )
        if detections_override is not None:
            await connection.execute(
                """INSERT INTO flatbed_layout_override(sheet_id,owner_id,revision,regions,confirmed)
                   VALUES(%s,%s,1,%s,true)""",
                (sheet_id, owner_id, Jsonb([region["bounds"] for region in regions])),
            )
        return {
            "id": sheet_id,
            "side": side,
            "revision": revision,
            "regions": regions,
            "failures": detection_failures,
            "layout_hypothesis": layout.as_dict(),
            "requires_review": bool(detection_failures),
        }

    async def pair(self, connection, owner_id: UUID, session_id: UUID) -> dict:
        result = await connection.execute(
            """SELECT r.*,s.side FROM flatbed_scan_region r JOIN flatbed_scan_sheet s ON s.id=r.sheet_id WHERE s.session_id=%s AND r.owner_id=%s AND s.revision=(SELECT max(s2.revision) FROM flatbed_scan_sheet s2 WHERE s2.session_id=s.session_id AND s2.side=s.side) ORDER BY r.ordinal""",
            (session_id, owner_id),
        )
        rows = [dict(row) for row in await result.fetchall()]

        def convert(row: dict) -> CardRegion:
            b = row["bounds"]
            return CardRegion(
                str(row["id"]),
                b["x"],
                b["y"],
                b["width"],
                b["height"],
                row["confidence"],
                row["signals"] or {},
            )

        proposal = propose_pairs(
            [convert(r) for r in rows if r["side"] == "front"],
            [convert(r) for r in rows if r["side"] == "back"],
        )
        await connection.execute(
            "DELETE FROM flatbed_pair_proposal WHERE session_id=%s AND owner_id=%s",
            (session_id, owner_id),
        )
        selected_keys = {(pair["front_id"], pair["back_id"]) for pair in proposal["pairs"]}
        for pair in proposal["candidates"]:
            confidence_level = (
                "high"
                if pair["score"] >= 0.82 and not pair["conflicts"]
                else "medium"
                if pair["score"] >= 0.62
                else "low"
            )
            await connection.execute(
                """INSERT INTO flatbed_pair_proposal(session_id,owner_id,front_region_id,back_region_id,score,confidence_level,transform,signals,conflicts,selected) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    session_id,
                    owner_id,
                    UUID(pair["front_id"]),
                    UUID(pair["back_id"]),
                    pair["score"],
                    confidence_level,
                    pair["transform"],
                    Jsonb(pair["signals"]),
                    Jsonb(list(pair["conflicts"])),
                    (pair["front_id"], pair["back_id"]) in selected_keys,
                ),
            )
        await connection.execute(
            "UPDATE flatbed_scan_session SET status='pairing',pairing_transform=%s,pairing_requires_review=%s,updated_at=now() WHERE id=%s AND owner_id=%s",
            (proposal["transform"], proposal["requires_review"], session_id, owner_id),
        )
        return proposal

    def resolve_path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("invalid flatbed image path")
        return path
