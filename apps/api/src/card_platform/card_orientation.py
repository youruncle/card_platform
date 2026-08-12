import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


logger = logging.getLogger(__name__)
VALID_ROTATIONS = {0, 90, 180, 270}


@dataclass(frozen=True)
class OrientationDecision:
    rotation_degrees: int
    confidence: float
    method: str
    text_count: int = 0


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

    @property
    def available(self) -> bool:
        return sys.platform == "darwin" and self.script_path.is_file()

    async def classify(self, image_path: Path) -> OrientationDecision | None:
        if not self.available:
            return None
        try:
            process = await asyncio.create_subprocess_exec(
                "swift",
                str(self.script_path),
                str(image_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
            if process.returncode != 0:
                logger.warning("Apple Vision orientation failed: %s", stderr.decode()[:300])
                return None
            payload = json.loads(stdout)[0]
            rotation = payload.get("rotationDegrees")
            text_count = int(payload.get("textCount") or 0)
            raw_confidence = float(payload.get("confidence") or 0)
            if rotation not in VALID_ROTATIONS:
                return None
            # Two or more independently recognized strings with the same
            # semantic baseline are a strong signal; a lone logo is not.
            evidence_factor = min(1.0, text_count / 2)
            confidence = min(0.97, raw_confidence * evidence_factor)
            return OrientationDecision(rotation, confidence, "apple_vision", text_count)
        except (OSError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as error:
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
