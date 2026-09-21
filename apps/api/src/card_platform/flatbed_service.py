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
from card_platform.flatbed_crop_quality import assess_sheet_crop_quality
from card_platform.flatbed_capture_resolution import resolve_source_resolution
from card_platform.flatbed_detection import (
    DETECTOR_VERSION,
    Detection,
    LayoutHypothesis,
    detect_layout,
    explain_detection,
    visual_signals,
)
from card_platform.flatbed_matching import CardRegion, propose_pairs
from card_platform.flatbed_ml import refine_with_sam2
from card_platform.flatbed_object_quality import audit_object_crop_integrity


class FlatbedBridgeError(RuntimeError):
    pass


_bridge_session_lock = asyncio.Lock()

# Independent contour detections get a small, symmetric safety margin. Refined
# full-bed grid detections already include their own local margin and bypass it;
# shifting both crop edges in one direction used to clip one card while pulling
# pixels from its neighbour into the crop.
CROP_SHORT_AXIS_PADDING_RATIO = 0.03
CROP_LONG_AXIS_PADDING_RATIO = 0.0
CROP_OUTPUT_BORDER_RATIO = 0.008


def _axis_aligned_intersection_over_smaller(left: Detection, right: Detection) -> float:
    intersection_width = max(
        0.0,
        min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
    )
    intersection_height = max(
        0.0,
        min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
    )
    smaller = min(left.width * left.height, right.width * right.height)
    return intersection_width * intersection_height / smaller if smaller else 0.0


def back_refinement_prompts(
    detections: list[Detection], prior: list[Detection]
) -> list[Detection] | None:
    """Use a compatible front envelope only as a SAM prompt for the back.

    Current-side detections remain the fallback geometry. This gives a weak
    back outline a tighter object-shaped prompt without allowing the front to
    force a moved card into stale coordinates.
    """
    if not detections or len(detections) != len(prior):
        return None
    ranked_pairs = sorted(
        (
            (
                _axis_aligned_intersection_over_smaller(current, candidate),
                current_index,
                prior_index,
            )
            for current_index, current in enumerate(detections)
            for prior_index, candidate in enumerate(prior)
        ),
        reverse=True,
    )
    assignment: dict[int, tuple[float, int]] = {}
    assigned_prior: set[int] = set()
    for overlap, current_index, prior_index in ranked_pairs:
        if current_index in assignment or prior_index in assigned_prior:
            continue
        assignment[current_index] = (overlap, prior_index)
        assigned_prior.add(prior_index)
    prompts: list[Detection] = []
    for current_index, current in enumerate(detections):
        if current.failure is None and current.confidence >= 0.70:
            prompts.append(current)
            continue
        match = assignment.get(current_index)
        if match is None:
            prompts.append(current)
            continue
        overlap, index = match
        candidate = prior[index]
        area_ratio = candidate.width * candidate.height / max(current.width * current.height, 1e-9)
        if overlap < 0.55 or not 0.60 <= area_ratio <= 1.65:
            prompts.append(current)
            continue
        prompts.append(
            Detection(
                candidate.x,
                candidate.y,
                candidate.width,
                candidate.height,
                candidate.angle,
                current.confidence,
                current.failure,
                candidate.points,
                current.evidence,
            )
        )
    return prompts


def stored_region_as_prior(row: dict) -> Detection:
    bounds = row["bounds"]
    points = (
        tuple((float(point["x"]), float(point["y"])) for point in bounds.get("points", [])) or None
    )
    source_evidence = tuple(
        str(value)
        for value in (row.get("signals") or {}).get("detection_evidence", [])
        if not str(value).startswith("sam2_")
    )
    return Detection(
        float(bounds["x"]),
        float(bounds["y"]),
        float(bounds["width"]),
        float(bounds["height"]),
        float(bounds.get("angle", 0)),
        1,
        points=points,
        evidence=tuple(dict.fromkeys((*source_evidence, "front_to_back_prior"))),
    )


def padded_source_quad(
    points: np.ndarray,
    image_width: int,
    image_height: int,
    *,
    padding_ratio: float = CROP_SHORT_AXIS_PADDING_RATIO,
) -> np.ndarray:
    expanded = points.copy()
    span_x = float(np.ptp(points[:, 0]))
    span_y = float(np.ptp(points[:, 1]))
    if span_x >= span_y:
        midpoint = points[:, 1].mean()
        leading = points[:, 1] < midpoint
        trailing = points[:, 1] > midpoint
        expanded[leading, 1] -= span_y * padding_ratio
        expanded[trailing, 1] += span_y * padding_ratio
    else:
        midpoint = points[:, 0].mean()
        leading = points[:, 0] < midpoint
        trailing = points[:, 0] > midpoint
        expanded[leading, 0] -= span_x * padding_ratio
        expanded[trailing, 0] += span_x * padding_ratio
    expanded[:, 0] = np.clip(expanded[:, 0], 0, image_width - 1)
    expanded[:, 1] = np.clip(expanded[:, 1], 0, image_height - 1)
    return expanded.astype(np.float32)


def ordered_source_quad(points: np.ndarray) -> np.ndarray:
    """Order four distinct corners without sum/difference ties at 45 degrees."""
    points = np.asarray(points, dtype=np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("卡片边界需要四个有效顶点")
    hull = cv2.convexHull(points).reshape(-1, 2)
    if len(hull) != 4 or abs(cv2.contourArea(hull)) < 1:
        raise ValueError("卡片边界不能退化或包含重复顶点")
    center = points.mean(axis=0)
    ordered = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
    start = int(np.argmin(ordered.sum(axis=1)))
    return np.roll(ordered, -start, axis=0).astype(np.float32)


def peer_safe_padding_ratio(
    points: np.ndarray,
    detections: list[Detection],
    ordinal: int,
    image_width: int,
    image_height: int,
    requested: float,
) -> float:
    """A cosmetic source margin must never capture new pixels from a peer.

    Keep existing geometry intact; only cancel the optional margin. The neutral
    output border still supplies a visual margin without copying another card.
    Use polygons, not bounding boxes, so rotated neighbours remain independent.
    """
    if requested <= 0:
        return 0.0
    original = cv2.convexHull(points)
    expanded = cv2.convexHull(
        padded_source_quad(points, image_width, image_height, padding_ratio=requested)
    )
    for index, peer in enumerate(detections):
        if index == ordinal:
            continue
        polygon = np.asarray(
            peer.points
            or (
                (peer.x, peer.y),
                (peer.x + peer.width, peer.y),
                (peer.x + peer.width, peer.y + peer.height),
                (peer.x, peer.y + peer.height),
            ),
            dtype=np.float32,
        ) * np.array([image_width, image_height], dtype=np.float32)
        hull = cv2.convexHull(polygon)
        before, _ = cv2.intersectConvexConvex(original, hull)
        after, _ = cv2.intersectConvexConvex(expanded, hull)
        if after > before + 1.0:
            return 0.0
    return requested


def add_output_safety_border(image: np.ndarray) -> np.ndarray:
    """Add a very narrow neutral border after rectification.

    Source-space padding cannot exist beyond the scanner bed. Adding the final
    presentation border after warping keeps edge-touching cards from appearing
    over-cropped without pretending that missing source pixels were scanned.
    """
    if image.size == 0:
        return image
    border = max(2, round(min(image.shape[:2]) * CROP_OUTPUT_BORDER_RATIO))
    return cv2.copyMakeBorder(
        image,
        border,
        border,
        border,
        border,
        cv2.BORDER_CONSTANT,
        value=(245, 245, 245),
    )


class FlatbedService:
    def __init__(
        self,
        root: str,
        bridge_script: str,
        ocr_script: str | None = None,
        *,
        ml_enabled: bool = False,
        ml_python: str = ".runtime/flatbed-ml/bin/python",
        ml_worker_script: str = "scripts/flatbed_sam2_worker.py",
        ml_checkpoint: str = ".runtime/models/sam2.1_hiera_tiny.pt",
        ml_timeout_seconds: float = 20.0,
    ) -> None:
        self.root = Path(root).resolve()
        self.bridge_script = Path(bridge_script).resolve()
        self.ocr_script = Path(ocr_script).resolve() if ocr_script else None
        self.ml_enabled = ml_enabled
        self.ml_python = ml_python
        self.ml_worker_script = ml_worker_script
        self.ml_checkpoint = ml_checkpoint
        self.ml_timeout_seconds = ml_timeout_seconds
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
            return await self._run_bridge(command, output=output, dpi=dpi, timeout=timeout)

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
        # Opening the scanner tab and React development strict-mode can issue
        # concurrent initialization requests. Serialize them per account and
        # reuse the unfinished session instead of creating duplicate batches.
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"flatbed-session:{owner_id}",),
        )
        existing = await connection.execute(
            """SELECT * FROM flatbed_scan_session
               WHERE owner_id=%s AND submitted_at IS NULL
               ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
            (owner_id,),
        )
        row = await existing.fetchone()
        if row is not None:
            return dict(row)
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
        source_resolution: dict | None = None,
    ) -> dict:
        session_result = await connection.execute(
            "SELECT * FROM flatbed_scan_session WHERE id=%s AND owner_id=%s FOR UPDATE",
            (session_id, owner_id),
        )
        session = await session_result.fetchone()
        if session is None:
            raise LookupError("扫描会话不存在")
        decoded = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError("无法解码扫描图像")
        height, width = decoded.shape[:2]
        resolution = resolve_source_resolution(width, height, session["dpi"], source_resolution)
        effective_dpi = resolution["effective_dpi"]
        expected_count = None
        if side == "back":
            prior_result = await connection.execute(
                """SELECT r.bounds,r.signals FROM flatbed_scan_region r JOIN flatbed_scan_sheet s ON s.id=r.sheet_id
                   WHERE s.session_id=%s AND s.owner_id=%s AND s.side='front'
                     AND s.revision=(SELECT max(revision) FROM flatbed_scan_sheet WHERE session_id=%s AND side='front')
                   ORDER BY r.ordinal""",
                (session_id, owner_id, session_id),
            )
            # Only cardinality is relevant here; even malformed historical
            # front bounds must not affect decoding/cropping this back image.
            expected_count = len(await prior_result.fetchall()) or None
        layout = (
            LayoutHypothesis(
                tuple(detections_override),
                1.0,
                0,
                0,
                transform="manual",
                signals={"source": "user_confirmed_quads"},
            )
            if detections_override is not None
            else detect_layout(
                content,
                dpi=effective_dpi,
            )
        )
        detections = (
            detections_override if detections_override is not None else list(layout.detections)
        )
        ml_refinement = {"status": "skipped", "reason": "disabled"}
        # Front coordinates are identity/pairing hints, never back-image
        # geometry. Even a plausible slot can cut two moved cards in half and
        # pass an ink-presence check. Keep detection and SAM entirely local to
        # the current image, including when its count differs from the front.
        slot_refinement = {
            "status": "skipped",
            "reason": "independent_back_geometry" if side == "back" else "not_a_back_with_slots",
        }
        if detections_override is None and self.ml_enabled:
            detections, ml_refinement = await refine_with_sam2(
                content,
                detections,
                prompt_detections=None,
                python_path=self.ml_python,
                worker_script=self.ml_worker_script,
                checkpoint=self.ml_checkpoint,
                timeout_seconds=self.ml_timeout_seconds,
            )
        detector_version = (
            f"{DETECTOR_VERSION}+sam2.1-tiny"
            if ml_refinement.get("status") == "accepted"
            else DETECTOR_VERSION
        )
        quality_reports = assess_sheet_crop_quality(decoded, detections)
        quality_reports, object_integrity = audit_object_crop_integrity(
            quality_reports, detections, layout.detections
        )
        quality_error_codes = list(
            dict.fromkeys(
                violation.code
                for report in quality_reports
                for violation in report.violations
                if violation.severity == "error"
            )
        )
        crop_quality_summary = {
            "region_count": len(quality_reports),
            "usable_count": sum(report.usable for report in quality_reports),
            "needs_review_count": sum(report.needs_review for report in quality_reports),
            "error_count": sum(
                any(item.severity == "error" for item in report.violations)
                for report in quality_reports
            ),
            "error_codes": quality_error_codes,
            "minimum_usability_score": round(
                min(
                    (report.usability_score for report in quality_reports),
                    default=0.0,
                ),
                4,
            ),
        }
        detection_failures = explain_detection(content, detections)
        detection_failures.extend(f"crop_quality_{code}" for code in quality_error_codes)
        detection_failures.extend(
            f"crop_quality_{code}" for code in object_integrity.get("error_codes", [])
            if code not in quality_error_codes
        )
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
                "manual-layout-v1" if detections_override is not None else detector_version,
                Jsonb(
                    {
                        **layout.as_dict(),
                        "manual_override": detections_override is not None,
                        "ml_refinement": ml_refinement,
                        "slot_refinement": slot_refinement,
                        "crop_quality": crop_quality_summary,
                        "source_resolution": resolution,
                        "object_integrity": object_integrity,
                    }
                ),
                Jsonb(
                    {
                        "expected_count": expected_count,
                        "transform": layout.transform,
                        "prior_role": "count_and_pairing_advisory_only",
                        "geometry_source": "manual_back_quads" if detections_override is not None else "current_image",
                    }
                    if side == "back"
                    else {}
                ),
                bool(detection_failures),
            ),
        )
        regions = []
        for ordinal, (detection, quality_report) in enumerate(
            zip(detections, quality_reports, strict=True)
        ):
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
                ordered = ordered_source_quad(source)
                crop_padding_ratio = (
                    0.0
                    if any(
                        evidence in detection.evidence
                        for evidence in (
                            "ink_extent_refinement",
                            "current_candidate_inward_edges",
                            "current_instance_grid_reconciliation",
                        )
                    )
                    else CROP_SHORT_AXIS_PADDING_RATIO
                )
                crop_padding_ratio = peer_safe_padding_ratio(
                    ordered, detections, ordinal, width, height, crop_padding_ratio
                )
                ordered = padded_source_quad(
                    ordered, width, height, padding_ratio=crop_padding_ratio
                )
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
                    [
                        [0, 0],
                        [target_width - 1, 0],
                        [target_width - 1, target_height - 1],
                        [0, target_height - 1],
                    ],
                    dtype=np.float32,
                )
                crop = cv2.warpPerspective(
                    decoded,
                    cv2.getPerspectiveTransform(ordered, destination),
                    (target_width, target_height),
                )
            if detection.failure != "card_outline_unresolved":
                crop = add_output_safety_border(crop)
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
            signals["detector_version"] = detector_version
            signals["orientation_rotation_degrees"] = rotation
            signals["orientation_confidence"] = round(orientation.confidence, 4)
            signals["orientation_method"] = orientation.method
            signals["orientation_text_count"] = orientation.text_count
            signals["orientation_face_count"] = orientation.face_count
            signals["crop_quality"] = quality_report.as_dict()
            signals.update(await self.ocr_signals(crop_path))
            bounds = {
                "x": detection.x,
                "y": detection.y,
                "width": detection.width,
                "height": detection.height,
                "angle": detection.angle,
                "points": [{"x": point[0], "y": point[1]} for point in detection.points or ()],
                "source_crop_points": (
                    [
                        {"x": float(point[0] / width), "y": float(point[1] / height)}
                        for point in ordered
                    ]
                    if detection.points
                    else []
                ),
                "crop_padding_ratio": crop_padding_ratio if detection.points else 0.0,
                "output_border_ratio": (
                    CROP_OUTPUT_BORDER_RATIO
                    if detection.failure != "card_outline_unresolved"
                    else 0.0
                ),
                "crop_padding_axes": {
                    "short_leading": crop_padding_ratio if detection.points else 0.0,
                    "short_trailing": crop_padding_ratio if detection.points else 0.0,
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
                    "crop_quality": quality_report.as_dict(),
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
            "layout_hypothesis": {
                **layout.as_dict(),
                "manual_override": detections_override is not None,
                "ml_refinement": ml_refinement,
                "slot_refinement": slot_refinement,
                "crop_quality": crop_quality_summary,
                "source_resolution": resolution,
                "object_integrity": object_integrity,
            },
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
                row["failure_reason"],
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
