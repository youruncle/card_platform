from pathlib import Path

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, detect_layout
from card_platform.flatbed_object_references import independent_object_references


def _private_scan(sheet_prefix):
    root = Path(__file__).parents[3]
    paths = list((root / "data/flatbed-scans").glob(f"*/*/{sheet_prefix}*/original.png"))
    if not paths:
        pytest.skip("Private original scan is not installed")
    content = paths[0].read_bytes()
    return content, cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)


def _coverage(reference, points):
    polygon = cv2.convexHull(np.asarray(reference.points, np.float32))
    gold = cv2.convexHull(np.asarray(points, np.float32))
    return cv2.intersectConvexConvex(polygon, gold)[0] / cv2.contourArea(gold)


def test_real_two_graded_holders_retain_independent_label_and_body_references():
    # Removing pre-resolver geometry would lose Kawhi's separate grading label.
    content, image = _private_scan("916c9c36")
    references = independent_object_references(image, detect_layout(content).detections)
    assert len(references) == 2
    for top, bottom in ((0.02, 0.285), (0.321, 0.58)):
        whole_holder = [(0.005, top), (0.638, top), (0.638, bottom), (0.005, bottom)]
        assert max(_coverage(item, whole_holder) for item in references) >= 0.98
    assert all("independent_source_object" in item.evidence for item in references)


def test_blank_capacity_cells_are_never_independent_object_references():
    image = np.full((1000, 1000, 3), 225, np.uint8)
    prompt = Detection(0.1, 0.1, 0.8, 0.8, 0, 0.49, evidence=("full_bed_grid",))
    assert independent_object_references(image, [prompt]) == ()


def test_one_canny_internal_quad_is_not_reference_truth():
    image = np.full((1000, 1000, 3), 225, np.uint8)
    cv2.line(image, (200, 500), (800, 300), (30, 30, 30), 2)
    prompt = Detection(
        0.2, 0.3, 0.6, 0.2, 0, 0.9,
        evidence=("current_pixel_quad", "quad_routes:edge_35,edge_45", "physical_object_envelope"),
    )
    assert independent_object_references(image, [prompt]) == ()


def test_post_resolver_quad_cannot_inherit_its_way_into_source_reference_truth():
    image = np.full((1000, 1000, 3), 225, np.uint8)
    prompt = Detection(
        0.1, 0.1, 0.6, 0.4, 0, 0.92,
        evidence=("current_pixel_quad", "grading_label_association", "saturation_content_island",
                  "multi_route_instance_corroboration", "physical_object_envelope"),
    )
    assert independent_object_references(image, [prompt]) == ()


def test_reference_discovery_rejects_an_envelope_containing_two_card_centres():
    image = np.full((1000, 1000, 3), 240, np.uint8)
    cv2.rectangle(image, (100, 150), (340, 550), (40, 90, 150), -1)
    cv2.rectangle(image, (360, 150), (600, 550), (100, 40, 150), -1)
    common = ("multi_route_instance_corroboration", "physical_object_envelope")
    left = Detection(0.1, 0.15, 0.24, 0.4, 0, 0.9, evidence=common)
    right = Detection(0.36, 0.15, 0.24, 0.4, 0, 0.9, evidence=common)
    merged = Detection(0.1, 0.15, 0.5, 0.4, 0, 0.94, evidence=common)
    references = independent_object_references(image, [left, right, merged])
    assert len(references) == 2
    assert all(item.width < 0.3 for item in references)


@pytest.mark.parametrize("angle", [7, 27, 45, 70])
def test_independently_proven_rotated_geometry_keeps_its_polygon(angle):
    image = np.full((1000, 1000, 3), 240, np.uint8)
    points = cv2.boxPoints(((500, 500), (500, 350), angle)) / 1000
    cv2.fillConvexPoly(image, np.rint(points * 1000).astype(np.int32), (50, 80, 130))
    lo, hi = points.min(axis=0), points.max(axis=0)
    prompt = Detection(
        float(lo[0]), float(lo[1]), float(hi[0]-lo[0]), float(hi[1]-lo[1]), angle, 0.94,
        points=tuple(tuple(float(value) for value in point) for point in points),
        evidence=("separated_rotated_object", "physical_object_envelope", "current_object_layout"),
    )
    references = independent_object_references(image, [prompt])
    assert len(references) == 1
    assert _coverage(references[0], points) >= 0.999
    assert cv2.contourArea(cv2.convexHull(np.asarray(references[0].points, np.float32))) < 0.18


def test_historical_grid_source_recovers_only_current_pixel_corroborated_objects():
    # The right middle and bottom cards have independent global structure and
    # saturation observations; capacity cells must not become reference truth.
    content, image = _private_scan("c1cc16cb")
    references = independent_object_references(image, detect_layout(content).detections)
    gold = [(0.575, 0.301), (0.989, 0.301), (0.989, 0.511), (0.575, 0.511)]
    assert max((_coverage(item, gold) for item in references), default=0) >= 0.98
    assert all("independent_source_object" in item.evidence for item in references)
    assert not any(item.x == 0.5 and item.y == 0 and item.width == 0.5 for item in references)


def test_real_kobe_reference_excludes_old_padding_with_neighbour_edge_noise():
    from card_platform.flatbed_object_content import build_content_references, content_preservation

    content, image = _private_scan("984db2bd")
    references = independent_object_references(image, detect_layout(content).detections)
    features = build_content_references(image, references)
    third = min(features, key=lambda item: abs(item.source.y + item.source.height / 2 - 0.445)
                + item.source.x)
    # Independently checked local perimeter covers the complete third card,
    # whereas the original margin included the card above and scanner rail.
    correct = np.array(
        [(0.486559, 0.573234), (0, 0.573234), (0, 0.311852), (0.486559, 0.311852)],
        np.float32,
    )
    assert content_preservation(third, correct)["preserved"]


def test_real_shadowed_sheet_has_all_eight_independent_body_references():
    content, image = _private_scan("046a695e")
    references = independent_object_references(image, detect_layout(content).detections)
    assert len(references) == 8
    for left, top, right, bottom in [(0.075, 0.27, 0.49, 0.478), (0.046, 0.521, 0.468, 0.733)]:
        gold = [(left, top), (right, top), (right, bottom), (left, bottom)]
        assert max(_coverage(item, gold) for item in references) >= 0.98


def test_real_internal_print_panels_have_eight_separate_source_references():
    content, image = _private_scan("b9e6a078")
    references = independent_object_references(image, detect_layout(content).detections)
    assert len(references) == 8
    assert not any("grading_label_association" in item.evidence for item in references)
    for left, top, right, bottom in [(0.05, 0.02, 0.455, 0.225), (0.04, 0.79, 0.46, 0.995)]:
        gold = [(left, top), (right, top), (right, bottom), (left, bottom)]
        assert max(_coverage(item, gold) for item in references) >= 0.98


def test_real_pale_backs_get_corroborated_threshold_and_structure_sources():
    content, image = _private_scan("c1cc16cb")
    references = independent_object_references(image, detect_layout(content).detections)
    assert len(references) >= 5
    gold = [(0.06, 0.294), (0.478, 0.294), (0.478, 0.492), (0.06, 0.492)]
    assert max(_coverage(item, gold) for item in references) >= 0.98
