from dataclasses import replace

import pytest

from card_platform.flatbed_crop_quality import CropQualityReport
from card_platform.flatbed_detection import Detection
from card_platform.flatbed_object_quality import audit_object_crop_integrity


def source(x=0.1, y=0.1, width=0.3, height=0.6, **kwargs):
    return Detection(x, y, width, height, 0, 0.9, evidence=("physical_object_envelope",), **kwargs)


def report():
    return CropQualityReport(1, 1, 1, 1, True, False, (), {})


def check(crops, objects):
    return audit_object_crop_integrity([report() for _ in crops], crops, objects)


def test_complete_objects_have_unique_intact_crops():
    objects = [source(), source(x=0.6)]
    reports, summary = check(objects, objects)
    assert summary["status"] == "accepted"
    assert summary["minimum_object_coverage"] == 1.0
    assert all(r.usable for r in reports)


@pytest.mark.parametrize("confidence", [0.01, 0.99])
def test_halves_are_not_accepted_as_two_confident_cards(confidence):
    obj = source()
    parts = [
        replace(obj, height=0.3, confidence=confidence),
        replace(obj, y=0.4, height=0.3, confidence=confidence),
    ]
    reports, summary = check(parts, [obj])
    assert {"incomplete_card_object", "object_split_or_duplicate"} <= set(summary["error_codes"])
    assert len(reports) == 2  # Mark for editing; do not delete output or pretend count is fixed.
    assert all(not r.usable and r.needs_review for r in reports)


def test_same_bounds_but_inner_diamond_is_a_fragment():
    obj = source()
    diamond = replace(obj, points=((0.25, 0.1), (0.4, 0.4), (0.25, 0.7), (0.1, 0.4)))
    reports, summary = check([diamond], [obj])
    assert summary["minimum_object_coverage"] == pytest.approx(0.5)
    assert "incomplete_card_object" in summary["error_codes"]
    assert not reports[0].usable


def test_single_crop_cannot_own_two_objects():
    objects = [source(), source(x=0.6)]
    reports, summary = check([replace(objects[0], width=0.8)], objects)
    assert "multiple_card_objects" in summary["error_codes"]
    assert not reports[0].usable


def test_missing_object_is_a_sheet_error_without_invalidating_good_crop():
    objects = [source(), source(x=0.6)]
    reports, summary = check([objects[0]], objects)
    assert summary["missing_object_count"] == 1
    assert summary["status"] == "requires_review"
    assert reports[0].usable


def test_empty_bed_crop_has_no_object_owner_when_reference_set_is_complete():
    obj = source()
    blank = Detection(0.6, 0.75, 0.3, 0.2, 0, 0.95)
    reports, summary = check([obj, blank], [obj])
    assert "crop_without_card_object" in summary["error_codes"]
    assert reports[0].usable and not reports[1].usable


def test_partial_reference_set_cannot_reject_unmodelled_real_crops():
    obj = source()
    ordinary = replace(source(x=0.6), evidence=("current_candidate",))
    reports, summary = check([obj, ordinary], [obj, ordinary])
    assert summary["reference_count"] == 1
    assert summary["status"] == "accepted"
    assert all(r.usable for r in reports)


@pytest.mark.parametrize("signal", ["front_to_back_prior", "slot_prompt_only", "manual_override"])
def test_stale_front_slots_and_user_quads_are_not_current_object_truth(signal):
    prior = replace(source(), evidence=("physical_object_envelope", signal))
    reports, summary = check([replace(prior, y=0.3, height=0.2)], [prior])
    assert summary["status"] == "not_applicable"
    assert reports[0].usable


def test_coarse_grid_does_not_protect_an_incorrect_allocation():
    coarse = replace(source(), evidence=("full_bed_grid", "cell_extent_fallback"))
    reports, summary = check([replace(coarse, width=0.2, height=0.3)], [coarse])
    assert summary["status"] == "not_applicable"
    assert reports[0].usable


def test_integrity_check_never_upgrades_existing_pixel_quality_failure():
    obj = source()
    bad = replace(report(), usable=False, usability_score=0.2, needs_review=True)
    reports, summary = audit_object_crop_integrity([bad], [obj], [obj])
    assert summary["status"] == "accepted"
    assert not reports[0].usable and reports[0].usability_score == 0.2


def test_original_content_guard_rejects_geometry_that_self_audit_accepts():
    import cv2
    import numpy as np
    from card_platform.flatbed_object_quality import audit_original_content_integrity
    image = np.full((1000,1000,3),240,np.uint8)
    cv2.rectangle(image,(120,100),(450,800),(80,90,120),-1)
    obj = Detection(
        .1,.08,.37,.75,0,.9,evidence=('multi_route_instance_corroboration','independent_source_object'))
    fragment = Detection(.1,.4,.37,.4,0,.99,evidence=('physical_object_envelope','current_pixel_quad'))
    _, previous = check([fragment],[fragment])
    assert previous['status'] == 'accepted'
    reports, summary = audit_original_content_integrity([report()],[fragment],image,[obj])
    assert not reports[0].usable
    assert 'original_card_content_missing' in summary['error_codes']
    assert summary['minimum_content_coverage'] < .7


def test_search_residual_does_not_promote_capacity_region_to_physical_truth():
    import cv2
    import numpy as np
    from card_platform.flatbed_object_quality import audit_original_content_integrity
    image = np.full((600,600,3),240,np.uint8)
    cv2.rectangle(image,(130,130),(460,470),(70,90,140),-1)
    grid = Detection(.1,.1,.8,.8,0,.5,evidence=('full_bed_grid','cell_extent_fallback'))
    complete = Detection(.2,.2,.6,.6,0,.9)
    reports, summary = audit_original_content_integrity([report()],[complete],image,[],[grid])
    assert reports[0].usable
    assert summary['independent_reference_count'] == 0
    assert summary['status'] == 'search_content_checked'


def test_refiner_losing_label_restores_only_previously_verified_quad():
    import cv2
    import numpy as np
    from card_platform.flatbed_object_quality import restore_content_preserving_quads
    image=np.full((900,900,3),240,np.uint8)
    cv2.rectangle(image,(250,250),(650,750),(70,90,140),-1)
    cv2.rectangle(image,(100,260),(200,740),(60,80,130),-1)
    reference=Detection(.09,.24,.65,.61,0,.9,evidence=('independent_source_object','multi_route_instance_corroboration'))
    verified=replace(reference,points=((.09,.24),(.74,.24),(.74,.85),(.09,.85)),
                     evidence=('current_pixel_quad','original_content_verified','physical_object_envelope'))
    lost=replace(verified,x=.27,width=.47,points=((.27,.24),(.74,.24),(.74,.85),(.27,.85)))
    restored,metadata=restore_content_preserving_quads(image,[lost],[verified],[reference])
    assert restored == [verified]
    assert metadata['fallback_indices'] == [0]
    unverified=replace(verified,evidence=('quad_unresolved','search_region_only'))
    restored,metadata=restore_content_preserving_quads(image,[lost],[unverified],[reference])
    assert restored == [lost]
    assert metadata['fallback_indices'] == []


@pytest.mark.parametrize('signal', ['quad_unresolved', 'search_region_only'])
def test_unrendered_search_peer_cannot_explain_missing_label(signal):
    import cv2
    import numpy as np
    from card_platform.flatbed_object_quality import audit_original_content_integrity
    image = np.full((1000, 1000, 3), 240, np.uint8)
    cv2.rectangle(image, (200, 300), (700, 800), (70, 90, 140), -1)
    cv2.rectangle(image, (220, 140), (680, 160), (60, 80, 130), -1)
    search = Detection(.18, .1, .54, .72, 0, .5, evidence=('full_bed_grid',))
    body = Detection(.18, .29, .54, .53, 0, .9)
    label_search = Detection(.20, .12, .5, .06, 0, .4, evidence=(signal,))
    reports, summary = audit_original_content_integrity(
        [report(), report()], [body, label_search], image, [], [search],
    )
    assert not reports[0].usable
    assert summary['status'] == 'requires_review'
    assert 'unexplained_card_content' in summary['error_codes']
    assert summary['objects'][0]['crop_index'] == 0


def test_unusable_peer_cannot_explain_missing_label():
    import cv2
    import numpy as np
    from card_platform.flatbed_object_quality import audit_original_content_integrity
    image = np.full((1000, 1000, 3), 240, np.uint8)
    cv2.rectangle(image, (200, 300), (700, 800), (70, 90, 140), -1)
    cv2.rectangle(image, (220, 140), (680, 160), (60, 80, 130), -1)
    search = Detection(.18, .1, .54, .72, 0, .5, evidence=('full_bed_grid',))
    body = Detection(.18, .29, .54, .53, 0, .9)
    unusable_peer = Detection(.20, .12, .5, .06, 0, .4)
    bad_quality = replace(report(), usable=False, usability_score=.2, needs_review=True)
    reports, summary = audit_original_content_integrity(
        [report(), bad_quality], [body, unusable_peer], image, [], [search],
    )
    assert not reports[0].usable
    assert summary['status'] == 'requires_review'
    assert 'unexplained_card_content' in summary['error_codes']


def test_peer_rejected_in_same_audit_cannot_explain_another_crops_content():
    import cv2
    import numpy as np
    from card_platform.flatbed_object_quality import audit_original_content_integrity
    image = np.full((1000, 1000, 3), 245, np.uint8)
    cv2.rectangle(image, (120, 400), (350, 800), (70, 90, 140), -1)
    cv2.rectangle(image, (650, 450), (850, 800), (70, 90, 140), -1)
    cv2.rectangle(image, (550, 140), (750, 160), (60, 80, 130), -1)
    search = Detection(.1, .1, .7, .75, 0, .5,
        points=((.1, .1), (.8, .1), (.4, .85), (.1, .85)),
        evidence=('cell_extent_fallback',))
    reference = Detection(.5, .1, .4, .75, 0, .9,
        evidence=('independent_source_object', 'grading_label_association'))
    body = Detection(.1, .39, .27, .43, 0, .9)
    peer = Detection(.5, .1, .4, .29, 0, .9)
    reports, summary = audit_original_content_integrity(
        [report(), report()], [body, peer], image, [reference], [search],
    )
    assert all(not item.usable for item in reports)
    assert set(summary['error_codes']) == {'original_card_content_missing', 'unexplained_card_content'}


def test_refiner_cannot_remove_a_complete_observed_holder_perimeter():
    import cv2
    import numpy as np
    from card_platform.flatbed_object_quality import restore_content_preserving_quads
    image = np.full((1200, 1200, 3), 245, np.uint8)
    cv2.rectangle(image, (200, 180), (900, 1080), (150, 150, 150), 2)
    cv2.rectangle(image, (230, 215), (870, 1045), (70, 90, 140), -1)
    reference = Detection(1/6, .15, 7/12, .75, 0, .9,
        evidence=('independent_source_object', 'multi_route_instance_corroboration'))
    outer = replace(reference, points=((1/6, .15), (.75, .15), (.75, .9), (1/6, .9)),
        evidence=('current_pixel_quad', 'original_content_verified', 'quad_routes:edge_35,otsu'))
    inner = replace(outer, x=230/1200, y=215/1200, width=640/1200, height=830/1200,
        points=((230/1200, 215/1200), (870/1200, 215/1200),
                (870/1200, 1045/1200), (230/1200, 1045/1200)))
    restored, metadata = restore_content_preserving_quads(image, [inner], [outer], [reference])
    assert restored == [outer]
    assert metadata['observed_perimeter_fallback_indices'] == [0]
