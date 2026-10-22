"""A flat scan may omit seller-photo reflections without changing card identity."""

from copy import deepcopy
import hashlib

import pytest

from card_platform.purchase_cost_images import (
    FieldObservation, PurchaseObservation, apply_multi_observation, evaluate_observation,
    observation_binding,
)

H = hashlib.sha256(b"similarity-photo").hexdigest()


def similarity_pair():
    card = dict(player_name="Shai Gilgeous-Alexander", card_number="DD-2",
                brand_name="Topps", product_name="Daily Dribble",
                image_content_hashes=dict(front=H, back=H))
    fields = [dict(kind=kind, value=value, text=value, photo=photo, origin="printed")
              for kind, value, photo in [
                  ("player", "Shai Gilgeous-Alexander", 0),
                  ("brand", "Topps", 0), ("insert", "Daily Dribble", 0),
                  ("card_number", "DD-2", 1)]]
    obs = PurchaseObservation.model_validate(dict(
        photos=[dict(index=0, role="front"), dict(index=1, role="back")],
        target_fields=fields, candidate_fields=deepcopy(fields), roster_match=True,
        front_back_consistent=True, design_match="same", finish_match="unknown",
        finish_basis="unknown", finish_detail="Seller photo reflects light; flat scan does not."))
    binding = observation_binding(card, "123", "order", [H, H], [H, H])
    decision = dict(source="purchase_multi_image", verdict="uncertain", confidence="0",
                    reasons=["fixed_finish_evidence_required", "independent_finish_unconfirmed"],
                    observations=obs.model_dump(mode="json"), evidence_binding=binding)
    match = dict(automatic=False, recalled=True, conflicts=[],
                 missing=["产品", "Insert", "库存未确认平行"],
                 reasons=["球员姓名或知识库别名一致", "年份一致", "品牌一致"],
                 item_id="123", evidence_hash="order")
    return card, obs, decision, match


def test_paired_identity_can_infer_cost_when_scan_has_no_refractor_glare():
    card, obs, decision, match = similarity_pair()
    checked = evaluate_observation(obs, card, [H, H])
    assert checked["verdict"] == "same"
    assert checked["cost_authorized"] is False
    result = apply_multi_observation(card, match, decision)
    assert result["automatic"] is True
    assert result["match_method"] == "inferred"
    assert result["missing"] == []
    assert result["inference_warnings"]


@pytest.mark.parametrize("kind,value", [
    ("card_number", "DD-3"), ("player", "Other Player"),
    ("parallel", "Gold"), ("serial", "2/99"),
])
def test_similarity_does_not_override_printed_identity_conflicts(kind, value):
    card, obs, decision, match = similarity_pair()
    for fields, text in [(obs.target_fields, "1/99" if kind == "serial" else "Silver"),
                         (obs.candidate_fields, value)]:
        if kind in {"parallel", "serial"}:
            fields.append(FieldObservation(kind=kind, value=text, text=text, photo=1, origin="printed"))
        else:
            next(f for f in fields if f.kind == kind).value = text
    # Validate appended fields through the real contract.
    decision["observations"] = PurchaseObservation.model_validate(
        obs.model_dump(mode="json")).model_dump(mode="json")
    assert not apply_multi_observation(card, match, decision)["automatic"]


def test_reflection_difference_can_be_inferred_without_fixed_version_claim():
    card, obs, decision, match = similarity_pair()
    obs.finish_match = "different"
    obs.finish_basis = "reflection"
    decision["observations"] = obs.model_dump(mode="json")
    assert apply_multi_observation(card, match, decision)["automatic"]


def test_bound_independent_artwork_difference_still_blocks_similarity():
    card, obs, decision, match = similarity_pair()
    decision["independent_confirmation"] = dict(
        evidence_binding=decision["evidence_binding"], observation=dict(artwork_match="different"))
    assert not apply_multi_observation(card, match, decision)["automatic"]


@pytest.mark.asyncio
async def test_observer_does_not_reblock_similarity_for_missing_finish_confirmation():
    import json
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings

    card, obs, _, _ = similarity_pair()

    async def create(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps(obs.model_dump(mode="json", by_alias=True))))])

    async def unexpected_finish_check(*args, **kwargs):
        raise AssertionError("Paired identity should not require a second finish confirmation")

    observer = PurchaseImageObserver(Settings(vision_api_key="fixture"))
    observer.verifier.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    observer.verifier._confirm_unknown_finish = unexpected_finish_check
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
    photo = buffer.getvalue()
    result = await observer.observe_bytes(card, dict(item_id="123", title="Fixture"),
        "order", [photo, photo], [photo, photo], {"image_urls": ["a", "b"]})
    assert result["verdict"] == "same"
    assert result["similarity_inferred"] is True
    assert result["cost_authorized"] is False


@pytest.mark.parametrize("key,value", [
    ("parallel_name", "Gold"), ("serial_total", 99), ("print_run", 99),
    ("grade_company", "PSA"), ("is_autograph", True), ("is_memorabilia", True),
])
def test_similarity_does_not_resolve_missing_special_edition_evidence(key, value):
    card, obs, decision, match = similarity_pair()
    card[key] = value
    decision["evidence_binding"] = observation_binding(card, "123", "order", [H, H], [H, H])
    assert not apply_multi_observation(card, match, decision)["automatic"]


@pytest.mark.parametrize("change", ["fixed_finish", "repair_drop", "stale", "title_conflict", "no_number"])
def test_similarity_preserves_conflict_integrity_and_photo_binding(change):
    card, obs, decision, match = similarity_pair()
    if change == "fixed_finish":
        obs.finish_match = "different"
        obs.finish_basis = "fixed_pattern"
    elif change == "repair_drop":
        decision["reasons"] = ["repair_dropped_identity_observation"]
    elif change == "stale":
        match["evidence_hash"] = "changed-order"
    elif change == "title_conflict":
        match["conflicts"] = ["限编总数冲突"]
    else:
        obs.target_fields = [f for f in obs.target_fields if f.kind != "card_number"]
    decision["observations"] = obs.model_dump(mode="json")
    assert not apply_multi_observation(card, match, decision)["automatic"]
