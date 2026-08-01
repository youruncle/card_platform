import cv2
import numpy as np

from card_platform.image_rectification import rectify_card_image


def _perspective_card() -> bytes:
    card = np.full((700, 500, 3), 240, dtype=np.uint8)
    cv2.rectangle(card, (0, 0), (499, 699), (20, 20, 20), 12)
    cv2.circle(card, (250, 260), 100, (40, 90, 210), -1)
    cv2.putText(
        card,
        "CARD",
        (110, 580),
        cv2.FONT_HERSHEY_SIMPLEX,
        2,
        (0, 0, 0),
        5,
    )
    canvas = np.full((950, 1100, 3), (65, 95, 70), dtype=np.uint8)
    source = np.float32([[0, 0], [499, 0], [499, 699], [0, 699]])
    destination = np.float32([[250, 100], [820, 170], [750, 880], [160, 790]])
    transform = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(card, transform, (1100, 950))
    mask = cv2.warpPerspective(
        np.full((700, 500), 255, dtype=np.uint8),
        transform,
        (1100, 950),
    )
    canvas[mask > 0] = warped[mask > 0]
    success, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert success
    return encoded.tobytes()


def test_perspective_card_is_cropped_and_restored_to_standard_ratio() -> None:
    result = rectify_card_image(_perspective_card(), "image/jpeg")
    assert result.transformed
    assert result.confidence >= 0.72
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert height > width
    assert abs(width / height - 5 / 7) < 0.005
    assert height < 900
    assert width < 700


def test_ambiguous_image_is_preserved_instead_of_force_cropped() -> None:
    image = np.full((300, 500, 3), 127, dtype=np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    content = encoded.tobytes()
    result = rectify_card_image(content, "image/png")
    assert not result.transformed
    assert result.content == content
    assert result.content_type == "image/png"


def test_graded_holder_can_be_rectified_without_forcing_card_ratio() -> None:
    content = _perspective_card()
    result = rectify_card_image(content, "image/jpeg", standardize_ratio=False)
    assert result.transformed
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert abs(width / height - 5 / 7) > 0.01
