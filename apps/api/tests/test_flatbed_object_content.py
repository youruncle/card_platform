from pathlib import Path

import cv2
import numpy as np
import pytest

from card_platform.flatbed_detection import Detection, detect_layout
from card_platform.flatbed_object_content import build_content_references, content_preservation


def test_gray_holder_lines_are_not_counted_as_missing_card_ink():
    image = np.full((1000, 1000, 3), 245, np.uint8)
    cv2.rectangle(image, (100, 100), (900, 900), (130, 130, 130), 12)
    cv2.rectangle(image, (170, 180), (830, 820), (70, 90, 140), -1)
    source = Detection(.09, .09, .82, .82, 0, .9,
        evidence=('independent_source_object', 'multi_route_instance_corroboration'))
    reference = build_content_references(image, [source])[0]
    assert content_preservation(reference, np.array([
        (.16, .17), (.84, .17), (.84, .83), (.16, .83),
    ], np.float32))['preserved']


def test_monochrome_small_print_and_gray_photo_remain_content_evidence():
    image = np.full((1000, 1000, 3), 245, np.uint8)
    for row in range(10):
        cv2.putText(image, 'PLAYER 23 STATS 100', (150, 200 + row * 30),
                    cv2.FONT_HERSHEY_SIMPLEX, .6, (105, 105, 105), 1, cv2.LINE_AA)
    for row in range(20):
        for column in range(20):
            cv2.circle(image, (170 + column * 20, 600 + row * 12), 5,
                       (80 + (row + column) % 5 * 20,) * 3, -1)
    source = Detection(.1, .1, .65, .8, 0, .9,
        evidence=('independent_source_object', 'multi_route_instance_corroboration'))
    ref = build_content_references(image, [source])[0]
    for points in [((.1, .53), (.75, .53), (.75, .9), (.1, .9)),
                   ((.1, .1), (.75, .1), (.75, .53), (.1, .53))]:
        assert not content_preservation(ref, np.array(points, np.float32))['preserved']


def test_faint_gray_text_on_bright_paper_is_not_lost_to_dark_bed_sampling():
    image = np.full((1000, 1000, 3), 225, np.uint8)
    cv2.rectangle(image, (100, 100), (900, 900), (245, 245, 245), -1)
    for row in range(16):
        for left, gray in [(140, 180), (540, 195)]:
            cv2.putText(image, 'Player 23 Stats', (left, 180 + row * 40),
                cv2.FONT_HERSHEY_SIMPLEX, .45, (gray,) * 3, 1, cv2.LINE_AA)
    source = Detection(.1, .1, .8, .8, 0, .9,
        evidence=('independent_source_object', 'multi_route_instance_corroboration'))
    refs = build_content_references(image, [source])
    assert refs
    result = content_preservation(refs[0], np.array([
        (.1, .1), (.5, .1), (.5, .9), (.1, .9),
    ], np.float32))
    assert not result['preserved'] and result['content_coverage'] < .8


def test_real_internal_diagonal_cannot_preserve_two_panel_graded_card():
    paths = list((Path(__file__).parents[3] / 'data/flatbed-scans').glob('*/*/916c9c36*/original.png'))
    if not paths:
        pytest.skip('Private original scan is not installed')
    content = paths[0].read_bytes()
    image = cv2.imdecode(np.frombuffer(content,np.uint8),cv2.IMREAD_COLOR)
    raw = detect_layout(content)
    references = build_content_references(image,raw.detections)
    # The reported wrong diagonal is frozen independently of the fixed resolver.
    wrong = np.array([(.2820,.7005),(.1537,.5506),(.6076,.3563),(.7360,.5063)],np.float32)
    result = content_preservation(references[1],wrong)
    assert not result['preserved']
    assert result['content_coverage'] < .5
    assert result['minimum_partition_coverage'] < .5
    correct = content_preservation(references[1],np.array(raw.detections[1].points,np.float32))
    assert correct['preserved']


@pytest.mark.parametrize('background', [240,195,35])
def test_correct_tilt_keeps_content_without_horizontal_background_area(background):
    image = np.full((900,900,3),background,np.uint8)
    points = cv2.boxPoints(((450,450),(500,330),27))
    cv2.fillConvexPoly(image,np.rint(points).astype(np.int32),(75,90,145))
    lo,hi = points.min(axis=0)/900,points.max(axis=0)/900
    source = Detection(*lo,*(hi-lo),0,.9,evidence=('multi_route_instance_corroboration',))
    ref = build_content_references(image,[source])[0]
    expanded = ((points-points.mean(axis=0))*1.025+points.mean(axis=0))/900
    assert content_preservation(ref,expanded)['preserved']
    assert cv2.contourArea(expanded.astype(np.float32)) / (source.width*source.height) < .7


def test_small_label_loss_is_not_hidden_by_large_intact_body():
    image = np.full((1000,1000,3),240,np.uint8)
    cv2.rectangle(image,(200,300),(700,800),(50,90,130),-1)
    cv2.rectangle(image,(220,140),(680,160),(50,80,100),-1)
    source = Detection(.18,.1,.54,.72,0,.9,evidence=('grading_label_association','multi_route_instance_corroboration'))
    ref = build_content_references(image,[source])[0]
    body = np.array([(.18,.29),(.72,.29),(.72,.82),(.18,.82)],np.float32)
    result = content_preservation(ref,body)
    assert result['content_coverage'] > .95
    assert not result['preserved']
    assert result['minimum_partition_coverage'] < .1


def test_coarse_grid_is_not_an_independent_content_reference():
    image = np.zeros((500,500,3),np.uint8)
    grid = Detection(0,0,1,1,0,.9,evidence=('full_bed_grid','cell_extent_fallback'))
    assert build_content_references(image,[grid]) == ()


def test_sub_one_percent_separate_label_must_still_be_retained():
    image=np.full((1200,1200,3),245,np.uint8)
    cv2.rectangle(image,(150,400),(700,1000),(60,80,120),-1)
    cv2.rectangle(image,(160,160),(219,171),(60,80,120),-1)
    obj=Detection(.1,.1,.5,.8,0,.9,evidence=('grading_label_association','multi_route_instance_corroboration'))
    reference=build_content_references(image,[obj])[0]
    result=content_preservation(reference,np.array([(.12,.32),(.59,.32),(.59,.85),(.12,.85)],np.float32))
    assert result['content_coverage'] > .99
    assert not result['preserved']


def test_overlapping_references_cannot_both_ignore_shared_label_content():
    image=np.full((1200,1200,3),245,np.uint8)
    cv2.rectangle(image,(144,360),(540,780),(60,80,120),-1)
    cv2.rectangle(image,(660,360),(1020,780),(60,80,120),-1)
    cv2.rectangle(image,(582,180),(594,204),(60,80,120),-1)
    objects=[Detection(x,.1,.4,.6,0,.9,evidence=('grading_label_association','multi_route_instance_corroboration'))
             for x in [.1,.48]]
    refs=build_content_references(image,objects)
    crops=[np.array([(.1,.1),(.475,.1),(.475,.7),(.1,.7)],np.float32),
           np.array([(.505,.1),(.88,.1),(.88,.7),(.505,.7)],np.float32)]
    assert not all(content_preservation(r,p)['preserved'] for r,p in zip(refs,crops,strict=True))
