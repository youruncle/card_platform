import cv2
import numpy as np
import pytest

from card_platform.card_orientation import (
    fuse_apple_vision_orientation,
    rotation_from_upright_index,
    rotate_image_content,
)


def test_two_consistent_faces_override_decorative_vertical_text() -> None:
    decision = fuse_apple_vision_orientation(
        text_rotation=180,
        text_confidence=0.97,
        text_count=4,
        face_rotation=90,
        face_confidence=0.76,
        face_count=2,
    )

    assert decision is not None
    assert decision.rotation_degrees == 90
    assert decision.method == "apple_vision_face_override"
    assert decision.confidence > 0.8


def test_single_weak_face_does_not_override_strong_text() -> None:
    decision = fuse_apple_vision_orientation(
        text_rotation=0,
        text_confidence=0.97,
        text_count=5,
        face_rotation=270,
        face_confidence=0.7,
        face_count=1,
    )

    assert decision is not None
    assert decision.rotation_degrees == 0
    assert decision.method == "apple_vision_text"


def _asymmetric_image() -> bytes:
    image = np.zeros((40, 70, 3), dtype=np.uint8)
    image[:12, :18] = (0, 0, 255)
    success, encoded = cv2.imencode(".png", image)
    assert success
    return encoded.tobytes()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 0), (2, 90), (3, 180), (4, 270), ("4", 270)],
)
def test_rotation_from_upright_index(value: object, expected: int) -> None:
    assert rotation_from_upright_index(value) == expected


@pytest.mark.parametrize("value", [-1, 0, 5, 90, None, "left"])
def test_rotation_from_upright_index_rejects_invalid_value(value: object) -> None:
    with pytest.raises(ValueError):
        rotation_from_upright_index(value)


def test_rotate_image_content_rotates_clockwise() -> None:
    rotated = cv2.imdecode(
        np.frombuffer(rotate_image_content(_asymmetric_image(), 90), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert rotated.shape[:2] == (70, 40)
    # The red top-left marker moves to the top-right after a clockwise turn.
    assert int(rotated[:18, -12:, 2].mean()) > 200


def test_zero_rotation_still_normalizes_to_jpeg() -> None:
    normalized = rotate_image_content(_asymmetric_image(), 0)
    assert normalized.startswith(b"\xff\xd8\xff")
