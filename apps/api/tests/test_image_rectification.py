import cv2
import numpy as np

from card_platform.image_rectification import _candidate_score, rectify_card_image


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


def test_severe_perspective_uses_real_corners_instead_of_rotated_box() -> None:
    card = np.full((700, 500, 3), 235, dtype=np.uint8)
    cv2.rectangle(card, (0, 0), (499, 699), (250, 250, 250), 14)
    cv2.rectangle(card, (18, 18), (481, 681), (25, 25, 25), 6)
    cv2.putText(card, "CARD", (105, 380), cv2.FONT_HERSHEY_SIMPLEX, 2, (30, 30, 30), 5)
    canvas = np.full((950, 1100, 3), 22, dtype=np.uint8)
    source = np.float32([[0, 0], [499, 0], [499, 699], [0, 699]])
    destination = np.float32([[320, 70], [720, 230], [930, 850], [80, 760]])
    transform = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(card, transform, (1100, 950))
    mask = cv2.warpPerspective(np.full((700, 500), 255, dtype=np.uint8), transform, (1100, 950))
    canvas[mask > 0] = warped[mask > 0]
    success, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert success

    result = rectify_card_image(encoded.tobytes(), "image/jpeg")

    assert result.transformed
    assert result.confidence >= 0.72
    assert result.normalized_quad is not None
    actual = np.array(result.normalized_quad) * np.array([1100, 950])
    expected = np.array([[320, 70], [720, 230], [930, 850], [80, 760]])
    # The detected contour can follow the printed inner border instead of the
    # synthetic card's outer white lip, but it must remain close to the actual
    # projective corners rather than the much larger rotated bounding box.
    assert np.mean(np.linalg.norm(actual - expected, axis=1)) < 90
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert height > width
    assert abs(width / height - 5 / 7) < 0.005


def test_brandon_miller_interior_corner_trapezoid_is_rejected() -> None:
    """Regression for job 7522e0c4: top/bottom edges were 0.468 apart."""
    bad_quad = np.array(
        [[1339, 530], [1918, 561], [1860, 1457], [621, 1386]], dtype=np.float32
    )

    score, _ = _candidate_score(bad_quad, (1927, 2569))
    relaxed_score, _ = _candidate_score(
        bad_quad,
        (1927, 2569),
        enforce_opposite_edge_stability=False,
    )

    assert score == 0
    assert relaxed_score >= 0.72


def test_scanner_auto_ratio_stabilizes_raw_card_shape() -> None:
    result = rectify_card_image(_perspective_card(), "image/jpeg", standardize_ratio=None)
    assert result.transformed
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert abs(width / height - 5 / 7) < 0.005


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


def test_small_graded_holder_on_scanner_mat_is_focused() -> None:
    """Regression for high-scanner frames where the slab occupies little of the image."""
    canvas = np.full((666, 1182, 3), 18, dtype=np.uint8)
    canvas[:, 1105:] = 238
    holder = np.full((280, 370, 3), 210, dtype=np.uint8)
    cv2.rectangle(holder, (0, 0), (369, 279), (235, 235, 235), 8)
    cv2.rectangle(holder, (18, 18), (351, 261), (35, 35, 35), 4)
    cv2.rectangle(holder, (36, 34), (334, 245), (242, 242, 242), -1)
    cv2.putText(
        holder,
        "CARD 29",
        (92, 140),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (20, 20, 20),
        3,
    )
    canvas[280:560, 205:575] = holder
    success, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert success

    result = rectify_card_image(
        encoded.tobytes(), "image/jpeg", standardize_ratio=False
    )

    assert result.transformed
    assert result.confidence >= 0.72
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert width < 500
    assert height < 400
    assert width * height < canvas.shape[0] * canvas.shape[1] * 0.3


def test_tiny_holder_covering_five_percent_of_scanner_frame_is_focused() -> None:
    canvas = np.full((720, 1280, 3), 18, dtype=np.uint8)
    canvas[:, 1190:] = 235
    holder = np.full((210, 240, 3), 225, dtype=np.uint8)
    cv2.rectangle(holder, (0, 0), (239, 209), (245, 245, 245), 7)
    cv2.rectangle(holder, (15, 15), (224, 194), (28, 55, 95), -1)
    cv2.putText(holder, "RC", (82, 125), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (245, 245, 245), 4)
    canvas[360:570, 155:395] = holder
    cv2.circle(canvas, (1220, 330), 34, (120, 155, 190), -1)
    success, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert success

    result = rectify_card_image(encoded.tobytes(), "image/jpeg", standardize_ratio=False)

    assert result.transformed
    assert result.confidence >= 0.72
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[0] < 300
    assert decoded.shape[1] < 320


def test_confident_pair_quad_can_focus_low_contrast_other_side() -> None:
    back = np.full((720, 1280, 3), 18, dtype=np.uint8)
    cv2.rectangle(back, (170, 300), (450, 690), (235, 235, 235), -1)
    cv2.rectangle(back, (188, 318), (432, 672), (45, 65, 105), 8)
    cv2.putText(back, "BACK", (235, 520), cv2.FONT_HERSHEY_SIMPLEX, 1, (245, 245, 245), 4)
    front = np.full_like(back, 18)
    cv2.putText(front, "AUTO", (235, 520), cv2.FONT_HERSHEY_SIMPLEX, 1, (35, 35, 35), 3)
    _, encoded_back = cv2.imencode(".jpg", back, [cv2.IMWRITE_JPEG_QUALITY, 95])
    _, encoded_front = cv2.imencode(".jpg", front, [cv2.IMWRITE_JPEG_QUALITY, 95])

    back_result = rectify_card_image(
        encoded_back.tobytes(), "image/jpeg", standardize_ratio=False
    )
    initial_front = rectify_card_image(
        encoded_front.tobytes(), "image/jpeg", standardize_ratio=False
    )
    propagated_front = rectify_card_image(
        encoded_front.tobytes(),
        "image/jpeg",
        standardize_ratio=False,
        fallback_normalized_quad=back_result.normalized_quad,
        fallback_confidence=back_result.confidence,
    )

    assert back_result.transformed
    assert not initial_front.transformed
    assert propagated_front.transformed
    assert propagated_front.confidence >= 0.72


def test_scanner_landscape_holder_is_rotated_to_portrait() -> None:
    source = cv2.imdecode(
        np.frombuffer(_perspective_card(), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    landscape = cv2.rotate(source, cv2.ROTATE_90_CLOCKWISE)
    success, encoded = cv2.imencode(".jpg", landscape)
    assert success
    result = rectify_card_image(
        encoded.tobytes(),
        "image/jpeg",
        standardize_ratio=False,
        orient_portrait=True,
    )
    assert result.transformed
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert height > width


def test_large_scanner_bed_quad_does_not_beat_inner_holder() -> None:
    """A 16:9 scanner-bed outline must not be selected as the card."""
    canvas = np.full((720, 1280, 3), 20, dtype=np.uint8)
    cv2.rectangle(canvas, (4, 4), (1275, 715), (75, 75, 75), 8)
    cv2.line(canvas, (640, 0), (640, 720), (95, 95, 95), 5)
    cv2.line(canvas, (0, 360), (1280, 360), (95, 95, 95), 5)
    holder = np.full((420, 300, 3), 218, dtype=np.uint8)
    cv2.rectangle(holder, (0, 0), (299, 419), (245, 245, 245), 8)
    cv2.rectangle(holder, (18, 18), (281, 401), (28, 28, 28), 5)
    cv2.putText(holder, "BARNES", (55, 340), cv2.FONT_HERSHEY_SIMPLEX, 1, (245, 245, 245), 3)
    canvas[170:590, 85:385] = holder
    success, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert success

    result = rectify_card_image(encoded.tobytes(), "image/jpeg", standardize_ratio=False)

    assert result.transformed
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert height > width
    assert width < 400
    assert height < 520


def test_holder_clipped_on_one_edge_can_still_be_cropped() -> None:
    canvas = np.full((720, 1280, 3), 18, dtype=np.uint8)
    holder = np.full((500, 350, 3), 225, dtype=np.uint8)
    cv2.rectangle(holder, (0, 0), (349, 499), (250, 250, 250), 9)
    cv2.rectangle(holder, (20, 20), (329, 479), (35, 35, 35), 5)
    cv2.putText(holder, "RC", (125, 280), cv2.FONT_HERSHEY_SIMPLEX, 2, (230, 230, 230), 5)
    canvas[260:720, 760:1110] = holder[:460]
    success, encoded = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert success

    result = rectify_card_image(encoded.tobytes(), "image/jpeg", standardize_ratio=False)

    assert result.transformed
    decoded = cv2.imdecode(np.frombuffer(result.content, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[0] > decoded.shape[1]
    assert decoded.shape[1] < 450
