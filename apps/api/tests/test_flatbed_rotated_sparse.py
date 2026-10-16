from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import _separated_rotated_objects, detect_layout
from card_platform.flatbed_service import FlatbedService


def assert_complete_polygons(layout, polygons, shape):
    assert len(layout.detections) == len(polygons)
    scale = np.array([shape[1], shape[0]], dtype=np.float32)
    owners = []
    for polygon in polygons:
        coverages = []
        for item in layout.detections:
            assert item.points is not None
            crop = np.asarray(item.points, dtype=np.float32) * scale
            intersection, _ = cv2.intersectConvexConvex(crop, polygon)
            coverages.append(intersection / cv2.contourArea(polygon))
        assert max(coverages) >= 0.97, coverages
        owners.append(int(np.argmax(coverages)))
    assert len(set(owners)) == len(polygons)


@pytest.mark.parametrize("angles", [(27, -20), (45, -45), (15, 70)])
def test_separated_tilted_cards_keep_complete_four_corner_crops(angles):
    image = np.full((1400, 1000, 3), 230, np.uint8)
    polygons = []
    for center, angle in zip(((500, 340), (500, 1050)), angles):
        polygon = cv2.boxPoints((center, (480, 350), angle))
        polygons.append(polygon)
        cv2.fillConvexPoly(image, np.rint(polygon).astype(np.int32), (90, 120, 160))
    _, encoded = cv2.imencode(".png", image)
    layout = detect_layout(encoded.tobytes(), dpi=120)
    assert_complete_polygons(layout, polygons, image.shape)


def test_real_two_curry_cards_are_not_four_grid_fragments():
    root = Path(__file__).parents[3]
    paths = list(
        (root / "data/flatbed-scans").glob(
            "*/070e8976-ac2f-4d70-8b89-1666fc820f35/53898155-464c-49ce-b3fb-709a2bee9a82/original.png"
        )
    )
    if not paths:
        pytest.skip("Local original scan is not installed")
    content = paths[0].read_bytes()
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    # Hand-marked printed card corners on the original; excludes holder shadows.
    polygons = [
        np.array([(938, 239), (1873, 721), (1508, 1390), (593, 927)], np.float32),
        np.array([(781, 2420), (1788, 2070), (2050, 2805), (1033, 3150)], np.float32),
    ]
    assert_complete_polygons(detect_layout(content), polygons, image.shape)


@pytest.mark.parametrize("extra", ["pale_card", "touching_cards", "irregular_object"])
def test_incomplete_or_joined_foreground_cannot_claim_complete_layout(extra):
    image = np.full((1400, 1000, 3), 240, np.uint8)
    polygon = cv2.boxPoints(((500, 330), (480, 350), 27))
    cv2.fillConvexPoly(image, np.rint(polygon).astype(np.int32), (90, 120, 160))
    if extra == "pale_card":
        # Only the brighter threshold sees this second object.
        cv2.rectangle(image, (250, 900), (750, 1250), (198, 198, 198), -1)
    elif extra == "touching_cards":
        cv2.rectangle(image, (100, 850), (900, 1150), (90, 120, 160), -1)
    else:
        cv2.circle(image, (500, 1050), 170, (90, 120, 160), -1)
    assert _separated_rotated_objects(image) == []


def test_tilted_card_does_not_make_two_touching_cards_one_authoritative_object():
    image = np.full((1800, 1400, 3), 240, np.uint8)
    polygon = cv2.boxPoints(((700, 330), (480, 350), 27))
    cv2.fillConvexPoly(image, np.rint(polygon).astype(np.int32), (90, 120, 160))
    cv2.rectangle(image, (460, 750), (940, 1100), (80, 100, 120), -1)
    cv2.rectangle(image, (460, 1100), (940, 1450), (150, 170, 190), -1)
    cv2.line(image, (460, 1100), (940, 1100), (30, 30, 30), 3)
    _, encoded = cv2.imencode(".png", image)
    layout = detect_layout(encoded.tobytes(), dpi=120)
    assert len(layout.detections) == 3


@pytest.mark.parametrize("edge", ["bottom", "top", "left", "right"])
def test_slightly_edge_clipped_tilted_card_keeps_whole_object_and_warning(edge):
    image = np.full((1400, 1000, 3), 240, np.uint8)
    polygon = cv2.boxPoints(((500, 1140), (480, 350), 27))
    cv2.fillConvexPoly(image, np.rint(polygon).astype(np.int32), (90, 120, 160))
    if edge == "top":
        image = cv2.rotate(image, cv2.ROTATE_180)
    elif edge == "left":
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif edge == "right":
        image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    _, encoded = cv2.imencode(".png", image)
    layout = detect_layout(encoded.tobytes(), dpi=120)
    assert len(layout.detections) == 1
    card = layout.detections[0]
    assert "separated_rotated_object" in card.evidence
    assert "source_edge_contact" in card.evidence
    assert card.points is not None
    assert all(0 <= value <= 1 for point in card.points for value in point)
    source_mask = (image[:, :, 0] < 185).astype(np.uint8)
    crop_mask = np.zeros_like(source_mask)
    points = np.asarray(card.points) * [image.shape[1], image.shape[0]]
    cv2.fillConvexPoly(crop_mask, np.rint(points).astype(np.int32), 1)
    assert np.count_nonzero(source_mask & crop_mask) / np.count_nonzero(source_mask) >= 0.97


def test_real_back_near_scanner_edge_is_two_complete_cards():
    root = Path(__file__).parents[3]
    paths = list((root / "data/flatbed-scans").glob("*/070e8976*/6510b71e*/original.png"))
    if not paths:
        pytest.skip("Local back scan is not installed")
    content = paths[0].read_bytes()
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    polygons = [
        np.array([(974, 664), (1944, 357), (2140, 1082), (1173, 1369)], np.float32),
        np.array([(290, 2509), (920, 2229), (1328, 3112), (697, 3392)], np.float32),
    ]
    layout = detect_layout(content)
    assert_complete_polygons(layout, polygons, image.shape)
    assert any("source_edge_contact" in card.evidence for card in layout.detections)


def test_materially_missing_corner_cannot_use_slight_overflow_tolerance():
    image = np.full((1400, 1000, 3), 240, np.uint8)
    points = cv2.boxPoints(((500, 1180), (480, 350), 27))
    cv2.fillConvexPoly(image, np.rint(points).astype(np.int32), (90, 120, 160))
    assert _separated_rotated_objects(image) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["front", "back"])
async def test_edge_touching_objects_survive_both_service_paths(side, tmp_path):
    image = np.full((1400, 1000, 3), 240, np.uint8)
    for center in ((500, 330), (500, 1140)):
        points = cv2.boxPoints((center, (480, 350), 27))
        cv2.fillConvexPoly(image, np.rint(points).astype(np.int32), (90, 120, 160))
    _, encoded = cv2.imencode(".png", image)
    owner, session = uuid4(), uuid4()

    async def execute(sql, params=None):
        result = AsyncMock()
        if sql.startswith("SELECT * FROM flatbed_scan_session"):
            result.fetchone.return_value = {"id": session, "owner_id": owner, "dpi": 120}
        elif sql.startswith("SELECT COALESCE"):
            result.fetchone.return_value = {"revision": 1}
        elif sql.startswith("SELECT r.bounds"):
            # An erroneous old front count must not recreate four back fragments.
            result.fetchall.return_value = [{}, {}, {}, {}]
        return result

    connection = AsyncMock()
    connection.execute.side_effect = execute
    service = FlatbedService(str(tmp_path), "unused.swift", ml_enabled=False)
    service.orientation_classifier.classify = AsyncMock(return_value=None)
    result = await service.store_sheet(
        connection, owner, session, side, encoded.tobytes(), "image/png"
    )
    assert len(result["regions"]) == 2
    assert result["layout_hypothesis"]["object_integrity"]["status"] == "accepted"
    assert result["layout_hypothesis"]["crop_quality"]["error_count"] == 0
    assert any(
        v["code"] == "source_edge_contact"
        for region in result["regions"]
        for v in region["crop_quality"]["violations"]
    )
    assert len(list(tmp_path.glob("*/*/*/region-*.jpg"))) == 2
