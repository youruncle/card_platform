"""An observed manufacturer belongs to the card, without inventing face text."""

from copy import deepcopy
import hashlib
import json

import pytest

from test_player_price_face_observations import compose, faces
from test_player_price_image_evidence import admit, fixture, visual
from test_player_price_image_reviews import check, reviewed_fixture

V2 = "price-pair-manufacturer-v2"


@pytest.mark.parametrize("unknown_index", [0, 1])
def test_approved_version_upgrade_uses_one_observed_manufacturer_without_fact_edits(unknown_index):
    raw = faces()
    raw[unknown_index]["brand_name"] = None
    obs, receipt, blobs, official = fixture(compose(raw).model_dump(mode="json"))
    review = reviewed_fixture()[-1]
    review.update(
        based_on_receipt_sha256=receipt["receipt_sha256"],
        observation_sha256=obs["raw_row_sha256"],
        identity_derivation_contract=V2,
        corrections=[],
    )
    review["reviewed_images"] = [
        dict(
            image_index=i,
            sha256=image["sha256"],
            detail="Full original face independently viewed; manufacturer unknown remains unknown",
        )
        for i, image in enumerate(receipt["images"])
    ]
    original = deepcopy(receipt)
    assert admit(obs, receipt, blobs, official)["status"] == "rejected"
    decision = check(obs, receipt, blobs, official, review)
    assert decision["status"] == "confirmed", decision
    proof = decision["identity"]["image_evidence"]["review"]
    assert proof["identity_derivation_contract"] == V2
    assert proof["correction_count"] == 0
    assert receipt == original
    assert receipt["result"]["face_observations"][unknown_index]["brand_name"] is None


@pytest.mark.parametrize("unknown_index", [0, 1])
@pytest.mark.parametrize("reversed_order", [False, True])
def test_v2_receipt_proves_manufacturer_source_and_preserves_unknown_face(
    unknown_index, reversed_order
):
    from card_platform.player_prices.image_observations import compose_faces

    raw = faces()
    raw[unknown_index]["brand_name"] = None
    if reversed_order:
        raw.reverse()
    result = compose_faces(raw, identity_derivation_contract=V2)
    assert result.brand_name == "Topps" and result.parallel_name == "Refractor"
    assert [face["brand_name"] for face in result.face_observations] == [
        f["brand_name"] for f in raw
    ]
    brand_proof = next(d for d in result.derivations if d["field"] == "brand_name")
    assert brand_proof["source_indices"] == [i for i, f in enumerate(raw) if f["brand_name"]]
    assert admit(*fixture(result.model_dump(mode="json")))["status"] == "confirmed"


@pytest.mark.parametrize(
    "change",
    [
        "both_unknown",
        "conflict",
        "product_unknown",
        "product_conflict",
        "subject",
        "number",
        "silver_unproven",
        "identity_detail",
    ],
)
def test_v2_does_not_infer_absent_identity_or_override_conflicting_facts(change):
    from card_platform.player_prices.image_observations import compose_faces

    raw = faces()
    raw[0]["brand_name"] = None
    if change == "both_unknown":
        raw[1]["brand_name"] = None
    elif change == "conflict":
        raw[0]["brand_name"] = "Panini"
    elif change == "product_unknown":
        raw[0]["product_name"] = None
    elif change == "product_conflict":
        raw[1]["product_name"] = "Prizm"
    elif change == "subject":
        raw[1]["player_names"] = ["LeBron James"]
    elif change == "number":
        raw[1]["card_number"]["value"] = None
    elif change == "silver_unproven":
        raw[1]["finish_markings"] = []
    else:
        raw[1]["identity_evidence"] = []
    result = compose_faces(raw, identity_derivation_contract=V2)
    assert admit(*fixture(result.model_dump(mode="json")))["status"] == "rejected"


def test_a_legacy_result_cannot_enable_v2_without_original_face_trace():
    result = visual()
    result["identity_derivation_contract"] = V2
    result["pair_validation"]["back"]["brand_name"] = None
    assert admit(*fixture(result))["status"] == "rejected"


@pytest.mark.parametrize("contract", [None, "unknown-version"])
def test_zero_correction_review_requires_explicit_known_version_upgrade(contract):
    values = list(reviewed_fixture())
    values[-1].update(corrections=[])
    if contract:
        values[-1]["identity_derivation_contract"] = contract
    assert check(*values)["status"] == "rejected"


def test_stored_unknown_derivation_contract_is_not_treated_as_legacy():
    from card_platform.player_prices.image_evidence import _seal

    obs, receipt, blobs, official = fixture(compose(faces()).model_dump(mode="json"))
    receipt["result"]["identity_derivation_contract"] = "unknown-version"
    receipt["receipt_sha256"] = _seal({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    assert admit(obs, receipt, blobs, official)["status"] == "rejected"


def test_a_frozen_v1_review_retains_its_original_result_fingerprint():
    from card_platform.player_prices.image_evidence import _seal

    obs, receipt, blobs, official, review = reviewed_fixture()
    receipt["result"].pop("identity_derivation_contract", None)
    receipt["receipt_sha256"] = _seal({k: v for k, v in receipt.items() if k != "receipt_sha256"})
    review["based_on_receipt_sha256"] = receipt["receipt_sha256"]
    expected = hashlib.sha256(
        json.dumps(
            receipt["result"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
    original = deepcopy(receipt)
    decision = check(obs, receipt, blobs, official, review)
    assert decision["status"] == "confirmed"
    assert (
        decision["identity"]["image_evidence"]["review"]["original_result_fingerprint"] == expected
    )
    assert receipt == original
