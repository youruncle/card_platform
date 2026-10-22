"""Explicit operator evidence may correct observations, never rewrite a sale."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from test_player_price_face_observations import faces, compose
from test_player_price_image_evidence import fixture, admit


def reviewed_fixture():
    raw = faces()
    raw[0]["surface"].update(fixed_colors=["blue"], pattern="other")
    obs, receipt, blobs, official = fixture(compose(raw).model_dump(mode="json"))
    corrected = deepcopy(raw[0]["surface"])
    corrected.update(
        fixed_colors=[],
        pattern="none",
        detail="front outer border has smooth rainbow glare; inner blue team frame matches printed layout",
    )
    review = dict(
        contract="price-image-review-v1",
        reviewer_kind="codex_visual_review",
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        based_on_receipt_sha256=receipt["receipt_sha256"],
        observation_sha256=obs["raw_row_sha256"],
        reviewed_images=[
            dict(
                image_index=i,
                sha256=image["sha256"],
                detail=f"Face {i} directly inspected, full card and printed subject/product independently visible",
            )
            for i, image in enumerate(receipt["images"])
        ],
        corrections=[
            dict(
                image_index=0,
                field="surface",
                before=raw[0]["surface"],
                after=corrected,
                detail="front blue team print lies inside the frame; outer rainbow border is smooth without extra fixed texture",
            )
        ],
    )
    return obs, receipt, blobs, official, review


def archive(blobs, review):
    data = json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    key = hashlib.sha256(data).hexdigest()
    blobs[key] = data
    return key


def check(obs, receipt, blobs, official, review, *, approved=True):
    from card_platform.player_prices.image_reviews import admit_review

    sha = archive(blobs, review)
    return admit_review(
        obs,
        receipt,
        "Stephen Curry",
        official,
        review_sha256=sha,
        approved_review_sha256s={sha} if approved else set(),
        blob_reader=blobs.__getitem__,
    )


def test_authorized_visual_correction_preserves_raw_model_and_declares_its_provenance():
    obs, receipt, blobs, official, review = reviewed_fixture()
    original = deepcopy(receipt)
    assert admit(obs, receipt, blobs, official)["status"] == "rejected"
    decision = check(obs, receipt, blobs, official, review)
    assert decision["status"] == "confirmed"
    assert receipt == original
    proof = decision["identity"]["image_evidence"]
    assert proof["observation_method"] == "codex_reviewed_model"
    assert proof["review"]["reviewer_kind"] == "codex_visual_review"
    assert proof["review"]["correction_count"] == 1
    assert proof["receipt_sha256"] == original["receipt_sha256"]


def test_self_declared_review_without_independent_server_approval_is_rejected():
    assert check(*reviewed_fixture(), approved=False)["status"] == "rejected"


@pytest.mark.parametrize(
    "change",
    ["before", "receipt", "image", "future", "too_early", "duplicate", "confidence", "sale_price"],
)
def test_review_cannot_escape_its_original_images_fields_or_observation(change):
    obs, receipt, blobs, official, review = reviewed_fixture()
    if change == "before":
        review["corrections"][0]["before"]["fixed_colors"] = ["red"]
    elif change == "receipt":
        review["based_on_receipt_sha256"] = "f" * 64
    elif change == "image":
        review["reviewed_images"][0]["sha256"] = receipt["images"][1]["sha256"]
    elif change == "future":
        review["reviewed_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    elif change == "too_early":
        review["reviewed_at"] = "2000-01-01T00:00:00+00:00"
    elif change == "duplicate":
        review["corrections"].append(deepcopy(review["corrections"][0]))
    else:
        review["corrections"][0].update(field=change, before="0.5", after="1")
    assert check(obs, receipt, blobs, official, review)["status"] == "rejected"


@pytest.mark.parametrize("changed", ["sale", "photo", "model_trace"])
def test_original_evidence_tampering_is_not_healed_by_an_approved_review(changed):
    obs, receipt, blobs, official, review = reviewed_fixture()
    if changed == "sale":
        obs["sale"]["sale_price"] = "10000"
    elif changed == "photo":
        blobs[receipt["images"][0]["sha256"]] = b"changed photograph"
    else:
        blobs[receipt["usage"]["observation_trace_sha256"]] = b"changed paid response"
    assert check(obs, receipt, blobs, official, review)["status"] == "rejected"


def test_a_known_review_key_does_not_authorize_changed_review_bytes():
    from card_platform.player_prices.image_reviews import admit_review

    obs, receipt, blobs, official, review = reviewed_fixture()
    key = archive(blobs, review)
    review["corrections"][0]["after"]["fixed_colors"] = ["red"]
    blobs[key] = json.dumps(review).encode()
    decision = admit_review(
        obs,
        receipt,
        "Stephen Curry",
        official,
        review_sha256=key,
        approved_review_sha256s={key},
        blob_reader=blobs.__getitem__,
    )
    assert decision["status"] == "rejected"


def test_an_unreviewed_unknown_condition_is_not_filled_by_a_surface_correction():
    obs, receipt, blobs, official, review = reviewed_fixture()
    raw = deepcopy(receipt["result"]["face_observations"])
    raw[1]["presence"]["major_damage"]["state"] = "unreadable"
    obs, receipt, blobs, official = fixture(compose(raw).model_dump(mode="json"))
    review["based_on_receipt_sha256"] = receipt["receipt_sha256"]
    review["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    review["reviewed_images"] = [
        dict(
            image_index=i,
            sha256=im["sha256"],
            detail="Full matching photograph inspected independently for a surface-only correction",
        )
        for i, im in enumerate(receipt["images"])
    ]
    decision = check(obs, receipt, blobs, official, review)
    assert decision["status"] == "rejected"
    assert decision["reason_codes"] != ["invalid_image_review"]
