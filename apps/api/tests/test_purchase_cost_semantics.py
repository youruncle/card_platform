"""Real audit failure categories: semantic rereads and bound finish supplementation."""
import copy
import pytest
from test_purchase_cost_images import H, evidence, field
from test_purchase_cost_recall_repair import image_correction
from card_platform.purchase_cost_images import (
    FieldObservation, evaluate_observation, observation_binding, apply_multi_observation,
)


def bound(card=None):
    card = card or {"image_content_hashes": {"front": H, "back": H}}
    binding = observation_binding(card, "123", "order", [H, H], [H, H])
    proof = copy.deepcopy(image_correction()["independent_confirmation"])
    proof["evidence_binding"] = binding
    return card, binding, proof


@pytest.mark.parametrize("kind", ["brand", "product", "season", "jersey_number", "score"])
def test_non_identity_text_has_explicit_field_kind(kind):
    f = FieldObservation(kind=kind, value="PRIZM", text="PRIZM", photo=0, origin="printed")
    assert f.kind == kind


@pytest.mark.parametrize("kind", ["brand", "product", "season"])
def test_new_identity_fields_preserve_explicit_printed_conflicts(kind):
    obs = evidence([field(kind, "A")], [field(kind, "B")])
    assert evaluate_observation(obs, {}, [H, H])["verdict"] != "same"


@pytest.mark.parametrize("kind,value,text", [
    ("grade_cert", "PRIZM", "PRIZM"),
    ("grade", "Topps", "Topps"),
    ("card_number", "11", "GUARD #11"),
    ("edition", "101/105", "101/105"),
])
def test_semantically_misclassified_fields_require_rereading(kind, value, text):
    from card_platform.purchase_cost_images import validate_observation_semantics
    obs = evidence()
    obs.candidate_fields = [field(kind, value)]
    obs.candidate_fields[0].text = text
    assert validate_observation_semantics(obs)


def test_finish_unknown_can_use_only_current_independent_positive_evidence():
    card, binding, proof = bound()
    obs = evidence([field("card_number", "77")], [field("card_number", "77")])
    obs.finish_match = "unknown"
    obs.finish_basis = "unknown"
    obs.finish_detail = ""
    assert evaluate_observation(obs, card, [H, H])["verdict"] == "uncertain"
    result = evaluate_observation(obs, card, [H, H], binding=binding, independent_confirmation=proof)
    assert result["verdict"] == "same"
    assert result["observations"]["finish_match"] == "unknown"
    assert result["cost_authorized"] is False
    stale = {**binding, "order_hash": "old"}
    assert evaluate_observation(obs, card, [H, H], binding=stale, independent_confirmation=proof)["verdict"] == "uncertain"


@pytest.mark.parametrize("blocker", ["serial", "number", "roster", "grade", "different_finish"])
def test_independent_finish_never_overrides_other_identity_gates(blocker):
    card, binding, proof = bound()
    obs = evidence([field("card_number", "77")], [field("card_number", "77")])
    obs.finish_match = "unknown"
    if blocker == "serial":
        obs.target_fields.append(field("serial", "26/75"))
        obs.candidate_fields.append(field("serial", "27/75"))
    elif blocker == "number":
        obs.candidate_fields[0].value = "78"
    elif blocker == "roster":
        obs.front_back_consistent = False
    elif blocker == "grade":
        obs.photos[1].role = "grading_label"
    else:
        obs.finish_match = "different"
    result = evaluate_observation(obs, card, [H, H], binding=binding, independent_confirmation=proof)
    assert result["verdict"] != "same"


def test_soft_match_does_not_require_unprinted_parallel_name():
    card, binding, proof = bound()
    obs = evidence([field("card_number", "77")], [field("card_number", "77")])
    obs.finish_match = "unknown"
    obs.finish_basis = "unknown"
    decision = {
        **evaluate_observation(obs, card, [H, H], binding=binding, independent_confirmation=proof),
        "source": "purchase_multi_image", "evidence_binding": binding,
        "independent_confirmation": proof,
    }
    match = dict(automatic=False, recalled=True, conflicts=[], missing=["平行完整名称"],
                 reasons=["产品一致"], item_id="123", evidence_hash="order")
    assert apply_multi_observation(card, match, decision)["automatic"]
    assert not apply_multi_observation(card, match, {**decision, "independent_confirmation": None})["automatic"]


@pytest.mark.asyncio
@pytest.mark.parametrize("semantic_retry,repair_loss", [(False, None), (True, None), (True, "drop"), (True, "text"), (True, "schema"), (True, "scope"), (True, "different_finish"), (True, "roster"), (True, "design"), (True, "grading_scope"), (True, "grade_field"), (True, "target_grade_field"), (True, "alias"), (True, "coerced_bool"), (True, "partial_alias"), (True, "partial_bool")])
async def test_observer_reaches_independent_pass_without_changing_unknown(semantic_retry, repair_loss, tmp_path):
    import json
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings

    obs = evidence([field("card_number", "77")], [field("card_number", "77")])
    obs.finish_match = "unknown"
    obs.finish_basis = "unknown"
    good = obs.model_dump(mode="json", by_alias=True)
    bad = copy.deepcopy(good)
    bad["candidate_fields"].append(dict(kind="grade_cert", value="PRIZM", text="PRIZM", photo=1, origin="printed"))
    if repair_loss in {"drop", "text", "schema", "scope"}:
        bad["target_fields"].append(field("serial", "26/75").model_dump())
        bad["candidate_fields"].append(field("serial", "27/75").model_dump())
    if repair_loss == "schema":
        bad["candidate_fields"][1]["kind"] = "unsupported_field"
    if repair_loss == "text":
        good["target_fields"].append(field("serial", "26/75").model_dump())
        changed = field("serial", "27/75").model_dump()
        changed["text"] = "unreadable"
        good["candidate_fields"].append(changed)
    if repair_loss == "scope":
        good["target_fields"].append(field("serial", "26/75").model_dump())
        good["candidate_fields"].append(field("serial", "27/75", photo=1).model_dump())
        bad["candidate_fields"][-1]["photo"] = 1
        good["candidate_photos"][1]["role"] = "advertisement"
    if repair_loss == "different_finish":
        bad["finish_match"] = "different"
    if repair_loss == "roster":
        bad["front_back_consistent"] = False
    if repair_loss == "design":
        bad["design_match"] = "different"
    if repair_loss == "grading_scope":
        bad["candidate_photos"][1]["role"] = "grading_label"
    if repair_loss in {"grade_field", "target_grade_field"}:
        side = "target_fields" if repair_loss == "target_grade_field" else "candidate_fields"
        bad[side].append(field("grade_cert", "123456789", origin="grading_label").model_dump())
    if repair_loss in {"alias", "partial_alias"}:
        bad["photos"] = bad.pop("candidate_photos")
        bad["photos"][1]["role"] = "grading_label"
    if repair_loss in {"coerced_bool", "partial_bool"}:
        bad["front_back_consistent"] = "false" if repair_loss == "coerced_bool" else 0
    if repair_loss in {"partial_alias", "partial_bool"}:
        bad["candidate_fields"][1]["kind"] = "unsupported_field"
    responses = [bad, good] if semantic_retry else [good]
    calls, confirmations = [], []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(responses.pop(0))))])

    async def confirm(target, target_type, row, binding):
        confirmations.append(binding)
        proof = copy.deepcopy(image_correction()["independent_confirmation"])
        proof["evidence_binding"] = binding
        return proof

    observer = PurchaseImageObserver(Settings(vision_api_key="fixture", upload_dir=str(tmp_path)))
    observer.verifier.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    observer.verifier._confirm_unknown_finish = confirm
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
    photo = buffer.getvalue()
    result = await observer.observe_bytes({}, dict(item_id="123", title="Fixture"), "order", [photo, photo], [photo, photo], {"image_urls": ["a", "b"]})
    assert len(calls) == (2 if semantic_retry else 1)
    assert result["schema_repair_attempted"] is semantic_retry
    assert len(confirmations) == (0 if repair_loss else 1)
    assert result["verdict"] == ("uncertain" if repair_loss else "same")
    if repair_loss:
        assert "repair_dropped_identity_observation" in result["reasons"]
    assert result["observations"]["finish_match"] == "unknown"
    assert result["cost_authorized"] is False


def test_target_grading_label_also_requires_review():
    obs = evidence([field("grade_cert", "123456789", origin="grading_label")])
    assert evaluate_observation(obs, {}, [H, H])["verdict"] == "uncertain"
