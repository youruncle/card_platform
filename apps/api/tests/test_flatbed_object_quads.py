from pathlib import Path

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, LayoutHypothesis, detect_layout
from card_platform.flatbed_object_quads import resolve_object_layout


def _coverage(crop, polygon):
    intersection, _ = cv2.intersectConvexConvex(
        cv2.convexHull(np.asarray(crop.points, np.float32)), polygon
    )
    return intersection / cv2.contourArea(polygon)


def test_real_tilted_kobe_back_keeps_four_corners_and_excludes_neighbours():
    root = Path(__file__).parents[3]
    paths = list(
        (root / "data/flatbed-scans").glob(
            "*/c8f1d7f1*/984db2bd-75aa-4189-a7c4-f50c59472636/original.png"
        )
    )
    if not paths:
        pytest.skip("Private original scan is not installed")
    content = paths[0].read_bytes()
    layout = resolve_object_layout(content, detect_layout(content))
    assert len(layout.detections) == 4
    gold = [
        np.array([(0.024, 0.065), (0.469, 0.066), (0.469, 0.284), (0.024, 0.284)], np.float32),
        np.array([(0.550, 0.144), (0.956, 0.091), (0.997, 0.303), (0.600, 0.352)], np.float32),
        np.array([(0.040, 0.338), (0.466, 0.338), (0.466, 0.558), (0.040, 0.558)], np.float32),
        np.array([(0.051, 0.615), (0.482, 0.610), (0.486, 0.831), (0.055, 0.837)], np.float32),
    ]
    owners = []
    for polygon in gold:
        values = [_coverage(d, polygon) for d in layout.detections]
        assert max(values) >= 0.98, values
        owners.append(int(np.argmax(values)))
    assert len(set(owners)) == 4
    tilted = layout.detections[owners[1]]
    points = np.asarray(tilted.points) * [2480, 3507]
    edges = np.roll(points, -1, axis=0) - points
    long_edge = edges[np.argmax(np.linalg.norm(edges, axis=1))]
    angle = abs(np.degrees(np.arctan2(long_edge[1], long_edge[0]))) % 180
    assert 5 < min(angle, 180 - angle) < 20
    for owner, polygon in zip(owners, gold):
        for peer in gold:
            if peer is not polygon:
                assert _coverage(layout.detections[owner], peer) <= 0.01


@pytest.mark.parametrize("angle", [0, 7, 27, 45, 70, 90])
def test_single_object_resolves_arbitrary_orientation_without_grid(angle):
    image = np.full((1000, 1000, 3), 240, np.uint8)
    polygon = cv2.boxPoints(((500, 500), (500, 350), angle))
    cv2.fillConvexPoly(image, np.rint(polygon).astype(np.int32), (60, 80, 120))
    _, encoded = cv2.imencode(".png", image)
    prompt = Detection(
        0.10, 0.10, 0.80, 0.80, 0, 0.5, evidence=("full_bed_grid", "cell_extent_fallback")
    )
    resolved = resolve_object_layout(encoded.tobytes(), LayoutHypothesis((prompt,), 0.5, 0, 0))
    assert len(resolved.detections) == 1
    assert _coverage(resolved.detections[0], polygon / 1000) >= 0.98
    assert "current_pixel_quad" in resolved.detections[0].evidence
    assert resolved.detections[0].failure is None


def test_blank_grid_allocation_cannot_become_an_automatic_card_crop():
    image = np.full((1000, 1000, 3), 240, np.uint8)
    _, encoded = cv2.imencode(".png", image)
    prompt = Detection(
        0.1, 0.1, 0.8, 0.8, 0, 0.5, evidence=("full_bed_grid", "cell_extent_fallback")
    )
    resolved = resolve_object_layout(encoded.tobytes(), LayoutHypothesis((prompt,), 0.5, 0, 0))
    assert resolved.detections[0].failure == "card_outline_unresolved"
    assert "quad_unresolved" in resolved.detections[0].evidence
    assert "physical_object_envelope" not in resolved.detections[0].evidence


def test_previous_four_fronts_still_cover_card_borders_without_neighbour_ink():
    root = Path(__file__).parents[3]
    paths = list(
        (root / "data/flatbed-scans").glob(
            "*/c8f1d7f1*/eb670aba-74aa-47e7-81d8-38cca6a65695/original.png"
        )
    )
    if not paths:
        pytest.skip("Private original scan is not installed")
    content = paths[0].read_bytes()
    layout = resolve_object_layout(content, detect_layout(content))
    gold = [
        np.array(p, np.float32)
        for p in [
            [(0.040, 0.038), (0.450, 0.036), (0.450, 0.257), (0.040, 0.258)],
            [(0.526, 0.041), (0.925, 0.027), (0.946, 0.252), (0.550, 0.266)],
            [(0.046, 0.325), (0.486, 0.330), (0.485, 0.550), (0.047, 0.546)],
            [(0.044, 0.600), (0.466, 0.597), (0.475, 0.823), (0.046, 0.827)],
        ]
    ]
    owners = [int(np.argmax([_coverage(d, p) for d in layout.detections])) for p in gold]
    assert len(set(owners)) == 4
    for owner, polygon in zip(owners, gold):
        assert _coverage(layout.detections[owner], polygon) >= 0.98
        for peer in gold:
            if peer is not polygon:
                assert _coverage(layout.detections[owner], peer) <= 0.01


def test_perspective_trapezoid_keeps_observed_four_corners():
    image = np.full((1000, 1000, 3), 240, np.uint8)
    gold = np.array([[300, 250], [700, 250], [800, 750], [200, 750]], np.float32)
    cv2.fillConvexPoly(image, gold.astype(np.int32), (60, 80, 120))
    _, encoded = cv2.imencode(".png", image)
    prompt = Detection(0.1, 0.1, 0.8, 0.8, 0, 0.5, evidence=("cell_extent_fallback",))
    layout = resolve_object_layout(encoded.tobytes(), LayoutHypothesis((prompt,), 0.5, 0, 0))
    actual = np.asarray(layout.detections[0].points) * 1000
    assert max(min(np.linalg.norm(corner - p) for p in actual) for corner in gold) < 20
    assert _coverage(layout.detections[0], gold / 1000) >= 0.98


@pytest.mark.parametrize(
    "sheet_id",
    [
        "58da166f-c6ce-4dc1-a27f-09a23655883f",
        "5bf6682a-7f9b-4fc7-95d0-fc32ac3dcaf4",
    ],
)
def test_real_multi_panel_holder_and_grading_label_are_not_internal_art(sheet_id):
    import json

    root = Path(__file__).parents[3]
    paths = list((root / "data/flatbed-scans").glob("*/*/" + sheet_id + "/original.png"))
    if not paths:
        pytest.skip("Private original scan is not installed")
    manifest = json.loads((root / "data/flatbed-replay-manifest.json").read_text())
    content = paths[0].read_bytes()
    layout = resolve_object_layout(content, detect_layout(content, dpi=300))
    regions = manifest["geometry_rules_by_sheet"][sheet_id]["required_content_regions"]
    owners = []
    for region in regions:
        x, y, w, h = [region[key] for key in ("x", "y", "width", "height")]
        polygon = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], np.float32)
        coverage = [_coverage(d, polygon) for d in layout.detections]
        assert max(coverage) >= region["minimum_coverage"], coverage
        owners.append(int(np.argmax(coverage)))
    assert len(set(owners)) == len(regions)


def test_tilted_grading_holder_prefers_observed_corners_over_axis_envelope():
    image = np.full((1000, 1000, 3), 240, np.uint8)
    gold = cv2.boxPoints(((500, 500), (500, 350), 12))
    cv2.fillConvexPoly(image, np.rint(gold).astype(np.int32), (60, 80, 120))
    _, encoded = cv2.imencode(".png", image)
    lo, hi = gold.min(axis=0) / 1000, gold.max(axis=0) / 1000
    prompt = Detection(
        float(lo[0]),
        float(lo[1]),
        float(hi[0] - lo[0]),
        float(hi[1] - lo[1]),
        0,
        0.9,
        evidence=(
            "grading_label_association",
            "physical_object_envelope",
            "multi_route_instance_corroboration",
        ),
    )
    resolved = resolve_object_layout(
        encoded.tobytes(), LayoutHypothesis((prompt,), 0.9, 0, 0)
    ).detections[0]
    actual = np.asarray(resolved.points) * 1000
    assert max(min(np.linalg.norm(corner - p) for p in actual) for corner in gold) < 20


def test_sparse_three_cards_keep_complete_graded_holder_without_blank_grid_cells():
    root = Path(__file__).parents[3]
    paths = list((root / 'data/flatbed-scans').glob('*/470d031e*/9d4e859c-d88d-4423-9d89-0d534bd7f1f8/original.png'))
    if not paths:
        pytest.skip('Private original scan is not installed')
    content = paths[0].read_bytes()
    layout = resolve_object_layout(content, detect_layout(content))
    assert len(layout.detections) == 3
    assert all('quad_unresolved' not in d.evidence for d in layout.detections)
    bottom = max(layout.detections, key=lambda d: d.y)
    assert bottom.x < .04 and bottom.x + bottom.width > .62
    assert bottom.y < .53 and bottom.y + bottom.height > .77
    assert bottom.x + bottom.width < .70
    assert bottom.y + bottom.height < .83


def test_printed_mavericks_logos_and_text_panels_are_not_card_boundaries():
    root = Path(__file__).parents[3]
    paths = list((root / 'data/flatbed-scans').glob('*/*/b9e6a078-b811-4c3f-9d13-f75ab7d7f448/original.png'))
    if not paths:
        pytest.skip('Private original scan is not installed')
    content = paths[0].read_bytes()
    layout = resolve_object_layout(content, detect_layout(content))
    assert len(layout.detections) == 8
    for y1, y2 in [(0.02, .225), (.29, .485), (.535, .735)]:
        gold = np.array([(.05,y1),(.455,y1),(.455,y2),(.05,y2)],np.float32)
        owners = [d for d in layout.detections if _coverage(d,gold) >= .98]
        assert len(owners) == 1
        assert 'quad_unresolved' not in owners[0].evidence


def test_six_objects_keep_offset_signature_card_and_drop_blank_capacity_cells():
    root = Path(__file__).parents[3]
    paths = list((root / 'data/flatbed-scans').glob('*/*/4ac7de65-8e1d-41d8-9083-90da02853d1c/original.png'))
    if not paths:
        pytest.skip('Private original scan is not installed')
    content = paths[0].read_bytes()
    layout = resolve_object_layout(content, detect_layout(content))
    assert len(layout.detections) == 6
    assert all('quad_unresolved' not in d.evidence for d in layout.detections)
    signature = np.array([(.56,.34),(.97,.34),(.97,.55),(.56,.55)],np.float32)
    owners = [d for d in layout.detections if _coverage(d,signature) >= .98]
    assert len(owners) == 1
    assert not any(d.x > .50 and d.y > .65 for d in layout.detections)


def test_curry_outer_border_is_supported_despite_reflective_artwork():
    root = Path(__file__).parents[3]
    paths = list((root / 'data/flatbed-scans').glob('*/*/329895ab-cb14-4456-b01f-d8be454b8cae/original.png'))
    if not paths:
        pytest.skip('Private original scan is not installed')
    content = paths[0].read_bytes()
    layout = resolve_object_layout(content, detect_layout(content))
    assert len(layout.detections) == 8
    gold = np.array([(.035,.025),(.44,.025),(.44,.22),(.035,.22)],np.float32)
    owners = [d for d in layout.detections if _coverage(d,gold) >= .98]
    assert len(owners) == 1
    assert 'quad_unresolved' not in owners[0].evidence


def test_structural_support_keeps_observed_three_degree_rotation():
    image = np.full((1000,1000,3),240,np.uint8)
    corners = cv2.boxPoints(((500,500),(500,350),3))
    cv2.fillConvexPoly(image,np.rint(corners).astype(np.int32),(60,80,120))
    _,encoded = cv2.imencode('.png',image)
    prompt = Detection(.1,.1,.8,.8,0,.5,evidence=('full_bed_grid','cell_extent_fallback'))
    resolved = resolve_object_layout(encoded.tobytes(),LayoutHypothesis((prompt,),.5,0,0))
    points = np.asarray(resolved.detections[0].points)*1000
    edges = np.roll(points,-1,axis=0)-points
    edge = edges[np.argmax(np.linalg.norm(edges,axis=1))]
    angle = abs(np.degrees(np.arctan2(edge[1],edge[0])))%90
    assert 2 < min(angle,90-angle) < 4


def test_graded_foil_diagonal_never_replaces_complete_body_and_label():
    paths = list((Path(__file__).parents[3] / 'data/flatbed-scans').glob('*/*/916c9c36*/original.png'))
    if not paths:
        pytest.skip('Private original scan is not installed')
    content = paths[0].read_bytes()
    layout = resolve_object_layout(content,detect_layout(content))
    assert len(layout.detections) == 2
    lower = layout.detections[1]
    for corners in [((.175,.345),(.60,.345),(.60,.562),(.175,.562)),
                    ((.01,.335),(.12,.335),(.12,.562),(.01,.562))]:
        assert _coverage(lower,np.array(corners,np.float32)) >= .98
    assert 'quad_unresolved' not in lower.evidence
