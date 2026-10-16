import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from card_platform.local_vision_process import LocalVisionCircuitOpen, run_local_vision


logger = logging.getLogger(__name__)
VALID_ROTATIONS = {0, 90, 180, 270}


@dataclass(frozen=True)
class OrientationDecision:
    rotation_degrees: int
    confidence: float
    method: str
    text_count: int = 0
    face_count: int = 0
    local_vision: dict | None = None


def fuse_apple_vision_orientation(
    *,
    text_rotation: int | None,
    text_confidence: float,
    text_count: int,
    face_rotation: int | None,
    face_confidence: float,
    face_count: int,
) -> OrientationDecision | None:
    """Prefer strong semantic face orientation over decorative text baselines."""
    text_valid = text_rotation in VALID_ROTATIONS and text_confidence > 0
    face_valid = face_rotation in VALID_ROTATIONS and face_confidence >= 0.62
    if face_valid and (
        not text_valid
        or face_rotation == text_rotation
        or face_count >= 2
        or face_confidence >= 0.82
    ):
        conflict = text_valid and face_rotation != text_rotation
        confidence = (
            min(0.97, 0.55 + face_confidence * 0.4)
            if conflict
            else min(0.97, max(face_confidence, text_confidence))
        )
        return OrientationDecision(
            int(face_rotation),
            confidence,
            "apple_vision_face_override" if conflict else "apple_vision_face_text",
            text_count,
            face_count,
        )
    if text_valid:
        return OrientationDecision(
            int(text_rotation),
            min(0.97, text_confidence),
            "apple_vision_text",
            text_count,
            face_count,
        )
    return None


def rotate_image_content(content: bytes, degrees_clockwise: int) -> bytes:
    """Return a JPEG rotated by a clockwise quarter-turn amount."""
    degrees_clockwise %= 360
    if degrees_clockwise not in VALID_ROTATIONS:
        raise ValueError("rotation must be 0, 90, 180, or 270 degrees")
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("invalid image content")
    if degrees_clockwise == 0:
        rotated = image
    else:
        rotation = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }[degrees_clockwise]
        rotated = cv2.rotate(image, rotation)
    success, encoded = cv2.imencode(".jpg", rotated, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not success:
        raise ValueError("could not encode rotated image")
    return encoded.tobytes()


class AppleVisionOrientationClassifier:
    """Fast four-way card orientation signal available on macOS.

    Vision returns semantic text boxes, so their baselines distinguish all four
    quarter turns. Sparse card fronts deliberately receive a low calibrated
    confidence and are delegated to the visual-model fallback.
    """

    def __init__(self, script_path: str) -> None:
        configured = Path(script_path)
        resolved = configured.resolve()
        repository_copy = Path(__file__).resolve().parents[4] / "scripts" / configured.name
        self.script_path = resolved if resolved.is_file() else repository_copy
        self._timed_out = False
        self.last_outcome = {"status": "not_started"}

    @property
    def available(self) -> bool:
        return sys.platform == "darwin" and self.script_path.is_file()

    async def classify(self, image_path: Path) -> OrientationDecision | None:
        if not self.available or self._timed_out:
            self.last_outcome = {"status": "skipped", "reason": "previous_timeout" if self._timed_out else "unavailable"}
            return None
        try:
            stdout, stderr, returncode = await run_local_vision(
                "swift",
                str(self.script_path),
                str(image_path),
            )
            if returncode != 0:
                self.last_outcome = {"status": "failed", "reason": "process_failed"}
                logger.warning("Apple Vision orientation failed: %s", stderr.decode()[:300])
                return None
            payload = json.loads(stdout)[0]
            if payload.get("error"):
                self.last_outcome = {"status": "failed", "reason": "process_image_failed"}
                return None
            rotation = payload.get("rotationDegrees")
            text_count = int(payload.get("textCount") or 0)
            raw_confidence = float(payload.get("confidence") or 0)
            # Two or more independently recognized strings with the same
            # semantic baseline are a strong signal; a lone logo is not.
            evidence_factor = min(1.0, text_count / 2)
            text_confidence = min(0.97, raw_confidence * evidence_factor)
            self.last_outcome = {"status": "succeeded"}
            return fuse_apple_vision_orientation(
                text_rotation=rotation,
                text_confidence=text_confidence,
                text_count=text_count,
                face_rotation=payload.get("faceRotationDegrees"),
                face_confidence=float(payload.get("faceConfidence") or 0),
                face_count=int(payload.get("faceCount") or 0),
            )
        except LocalVisionCircuitOpen:
            self.last_outcome = {"status": "skipped", "reason": "local_vision_cooldown"}
            return None
        except TimeoutError:
            self._timed_out = True
            self.last_outcome = {"status": "failed", "reason": "local_vision_timeout"}
            logger.warning("Apple Vision orientation timed out; remaining images use fallback")
            return None
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            self.last_outcome = {"status": "failed", "reason": "invalid_or_unavailable"}
            logger.warning("Apple Vision orientation unavailable: %s", error)
            return None


def rotation_from_upright_index(value: object) -> int:
    """Convert the visual model's strict 1-based candidate index to degrees."""
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            value = int(value)
    if value not in {1, 2, 3, 4}:
        raise ValueError("orientation response had an invalid image index")
    return (value - 1) * 90
