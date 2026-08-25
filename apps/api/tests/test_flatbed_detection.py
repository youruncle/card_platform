import cv2
import numpy as np

from card_platform.flatbed_detection import detect_cards, detect_layout, explain_detection


def encode(image: np.ndarray) -> bytes:
    ok, content = cv2.imencode(".png", image)
    assert ok
    return content.tobytes()


def test_detects_multiple_rows_and_small_rotation() -> None:
    image = np.full((1200, 900, 3), 235, dtype=np.uint8)
    for center, angle in [((220, 280), -4), ((650, 300), 3), ((240, 870), 0)]:
        box = cv2.boxPoints((center, (270, 400), angle)).astype(np.int32)
        cv2.fillConvexPoly(image, box, (40, 80, 150))
    detections = detect_cards(encode(image))
    assert len(detections) == 3
    assert all(item.confidence >= 0.8 for item in detections)
    assert all(item.points and len(item.points) == 4 for item in detections)
    assert any(abs(item.angle) >= 2 for item in detections)


def test_empty_reflective_bed_has_explainable_failure() -> None:
    content = encode(np.full((500, 700, 3), 255, dtype=np.uint8))
    detections = detect_cards(content)
    assert detections == []
    assert explain_detection(content, detections) == [
        "no_card_outline_detected",
        "possible_glare_or_empty_bed",
    ]


def test_edge_clipped_card_is_flagged() -> None:
    image = np.full((600, 800, 3), 230, dtype=np.uint8)
    cv2.rectangle(image, (0, 80), (310, 540), (20, 20, 20), -1)
    detection = detect_cards(encode(image))[0]
    assert detection.failure == "severely_clipped"


def test_recovers_eight_sleeved_cards_when_two_foregrounds_touch() -> None:
    image = np.full((1600, 1100, 3), 248, dtype=np.uint8)
    card_width, card_height = 440, 330
    for column, x in enumerate((20, 640)):
        for row in range(4):
            # The top two cards in the right column deliberately touch, matching
            # a real clear-sleeve scan whose dark foreground became one component.
            y = row * card_height if column == 1 and row < 2 else row * 390
            cv2.rectangle(
                image,
                (x, y),
                (min(x + card_width, image.shape[1] - 1), min(y + card_height, image.shape[0] - 1)),
                (55 + row * 10, 70, 120 + column * 20),
                -1,
            )
    detections = detect_cards(encode(image))
    assert len(detections) == 8


def test_recovers_full_touching_column_when_other_column_has_single_card_references() -> None:
    image = np.full((1600, 1100, 3), 248, dtype=np.uint8)
    card_width, card_height = 440, 330
    for row in range(4):
        # Every holder in the left column touches the next one, producing one
        # full-height foreground component like the real LiDE 400 scan.
        cv2.rectangle(
            image,
            (0, row * card_height),
            (card_width, (row + 1) * card_height),
            (45 + row * 10, 65, 110),
            -1,
        )
        # The right column supplies normal single-card size references.
        cv2.rectangle(
            image,
            (640, row * 390),
            (1080, row * 390 + card_height),
            (65 + row * 10, 75, 135),
            -1,
        )

    detections = detect_cards(encode(image))

    assert len(detections) == 8
    assert sum(item.x < 0.5 for item in detections) == 4


def test_recovers_two_by_four_grid_when_all_clear_holders_touch_bed_edges() -> None:
    image = np.full((1600, 1100, 3), 180, dtype=np.uint8)
    cell_width, cell_height = 550, 400
    for row in range(4):
        for column in range(2):
            x = column * cell_width
            y = row * cell_height
            # Touching pale holders make the foreground mask cover the entire
            # bed, while the darker inset preserves enough edge references to
            # infer cell size and recover the complete grid.
            cv2.rectangle(
                image,
                (x, y),
                (x + cell_width - 1, y + cell_height - 1),
                (190, 190, 190),
                -1,
            )
            if (row, column) in {(0, 0), (2, 1), (3, 0), (3, 1)}:
                cv2.rectangle(
                    image,
                    (x + 35, y + 30),
                    (x + cell_width - 35, y + cell_height - 30),
                    (35 + row * 8, 60, 105 + column * 15),
                    -1,
                )

    detections = detect_cards(encode(image))

    assert len(detections) == 8
    assert sum(item.x < 0.5 for item in detections) == 4


def test_deduplicates_offset_edge_and_foreground_boxes() -> None:
    image = np.full((1000, 700, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (100, 100), (390, 530), (35, 45, 80), -1)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    detections = detect_cards(encoded.tobytes())
    assert len(detections) == 1


def test_replaces_whole_sheet_and_internal_artwork_with_grid() -> None:
    image = np.full((1600, 1100, 3), 180, dtype=np.uint8)
    for row in range(4):
        for column in range(2):
            x1, y1 = column * 550, row * 400
            cv2.rectangle(image, (x1, y1), (x1 + 549, y1 + 399), (205, 205, 205), -1)
    for row, column in ((0, 1), (1, 0)):
        x1, y1 = column * 550 + 55, row * 400 + 40
        cv2.rectangle(image, (x1, y1), (x1 + 440, y1 + 320), (35, 60, 110), -1)

    detections = detect_cards(encode(image))

    assert len(detections) == 8
    assert all(item.width * item.height < 0.2 for item in detections)
    assert all(
        not (a.x <= b.x and a.y <= b.y and a.x + a.width >= b.x + b.width and a.y + a.height >= b.y + b.height)
        for index, a in enumerate(detections)
        for b in detections[index + 1 :]
    )


def test_back_layout_count_is_hard_constrained_by_confirmed_front() -> None:
    front = np.full((1600, 1100, 3), 248, dtype=np.uint8)
    for row in range(4):
        for column in range(2):
            cv2.rectangle(front, (column * 550, row * 400), ((column + 1) * 550 - 1, (row + 1) * 400 - 1), (40 + row * 8, 70, 130), -1)
    prior = detect_cards(encode(front))
    back = np.full_like(front, 185)
    for row, column in ((0, 0), (2, 1), (3, 0)):
        cv2.rectangle(back, (column * 550 + 30, row * 400 + 25), ((column + 1) * 550 - 30, (row + 1) * 400 - 25), (35, 55, 100), -1)
    layout = detect_layout(encode(back), expected_count=8, prior=prior)
    assert len(layout.detections) == 8
    assert layout.signals["expected_count"] == 8
    assert layout.transform == "independent" or all(
        "front_to_back_prior" in item.evidence for item in layout.detections
    )
