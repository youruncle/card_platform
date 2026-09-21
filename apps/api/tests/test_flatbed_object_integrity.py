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
