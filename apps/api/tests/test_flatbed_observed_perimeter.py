from dataclasses import replace
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection
from card_platform.flatbed_observed_perimeter import observed_perimeter_preservation


def fixture():
    image = np.full((1200, 1200, 3), 245, np.uint8)
    cv2.rectangle(image, (200, 180), (900, 1080), (150, 150, 150), 2)
    cv2.rectangle(image, (230, 215), (870, 1045), (70, 90, 140), -1)
    outer = Detection(1/6, .15, 7/12, .75, 0, .9,
        points=((1/6, .15), (.75, .15), (.75, .9), (1/6, .9)),
        evidence=('original_content_verified', 'quad_routes:edge_35,otsu'))
    inner = replace(outer, points=((230/1200,215/1200),(870/1200,215/1200),
                                  (870/1200,1045/1200),(230/1200,1045/1200)))
    return image, outer, inner


def test_actual_four_sided_holder_lines_are_protected():
    image, outer, inner = fixture()
    result = observed_perimeter_preservation(image, outer, inner)
    assert result['status'] == 'needs_fallback'
    assert result['retained_coverage'] < .1
    assert len(result['edges']) == 4


def test_unchanged_observed_perimeter_is_preserved():
    image, outer, _ = fixture()
    assert observed_perimeter_preservation(image, outer, outer)['status'] == 'preserved'


def test_axis_envelope_does_not_protect_empty_background():
    image, outer, inner = fixture()
    outer = replace(outer, evidence=('original_content_verified',
                                    'quad_routes:current_multiroute_outer_object'))
    assert observed_perimeter_preservation(image, outer, inner)['status'] == 'not_applicable'


def test_missing_fourth_edge_cannot_certify_perimeter():
    image, outer, inner = fixture()
    image[170:190, 190:910] = 245
    assert observed_perimeter_preservation(image, outer, inner)['status'] == 'not_applicable'


def test_slight_rotation_uses_actual_lines_not_axis_box_area():
    image = np.full((1200,1200,3),245,np.uint8)
    points = cv2.boxPoints(((600,600),(650,850),7))
    cv2.polylines(image,[np.rint(points).astype(np.int32)],True,(150,150,150),2)
    normalized = points / 1200
    source = Detection(.1,.1,.8,.8,7,.9,points=tuple(map(tuple,normalized)),
        evidence=('original_content_verified','quad_routes:edge_35,otsu'))
    result = observed_perimeter_preservation(image,source,source)
    assert result['status'] == 'preserved'


def test_real_holder_lost_by_cached_sam_requires_fallback():
    root = Path(__file__).parents[3]
    report = root / 'specs/013-object-quad-cropping/verification/replay-v30-final.json'
    if not report.exists():
        pytest.skip('Private replay cache is not installed')
    item = next((item for item in json.loads(report.read_text())['results']
                 if '815f4b75' in item['path']), None)
    if item is None or not (root / item['path']).exists():
        pytest.skip('Private original scan is not installed')
    content = (root / item['path']).read_bytes()
    image = cv2.imdecode(np.frombuffer(content,np.uint8),cv2.IMREAD_COLOR)
    # Immutable observed source lines from the reported pre-SAM run. Rebuilding
    # them with a changing resolver would no longer test this failure pair.
    source = Detection(.036701098, .336995959, .442161441, .229076028, -.842520494, .92,
        points=((.041370332, .566071987), (.036701098, .341545582),
                (.474193305, .336995959), (.478862524, .561522365)),
        evidence=('original_content_verified', 'quad_routes:edge_20,edge_35,otsu'))
    cached = item['detections'][2]
    final = replace(source,points=tuple(map(tuple,cached['points'])))
    result = observed_perimeter_preservation(image,source,final)
    assert result['status'] == 'needs_fallback'
    assert result['source_perimeter_supported']
    assert result['retained_coverage'] < .98
    assert observed_perimeter_preservation(image,source,source)['status'] == 'preserved'
