"""Purchase-only observation contract: version evidence never means a ledger write."""

import hashlib
from uuid import uuid4
import pytest
from card_platform.purchase_cost_images import (
    FieldObservation,
    PhotoObservation,
    PurchaseObservation,
    evaluate_observation,
    observation_binding,
    image_url_allowed,
    read_bounded,
)

H = hashlib.sha256(b"photo").hexdigest()


def field(kind, value, photo=0, origin="printed"):
    return FieldObservation(kind=kind, value=value, text=value, photo=photo, origin=origin)


def evidence(target=None, candidate=None):
    return PurchaseObservation(
        photos=[PhotoObservation(index=0, role="front"), PhotoObservation(index=1, role="back")],
        target_fields=target or [],
        candidate_fields=candidate or [],
        roster_match=True,
        design_match="same",
        finish_match="same",
        front_back_consistent=True,
        finish_basis="printed_mark",
        finish_detail="Both backs explicitly printed REFRACTOR",
    )


def test_serial_mismatch_cannot_be_same_even_with_same_layout():
    result = evaluate_observation(
        evidence([field("serial", "26/75")], [field("serial", "27/75")]), {}, [H, H]
    )
    assert result["verdict"] == "different"
    assert "serial_conflict" in result["reasons"]


@pytest.mark.parametrize("origin", ["sticker", "grading_label", "unknown"])
def test_non_printed_numbers_do_not_resolve_missing_serial(origin):
    obs = evidence([field("serial", "26/75")], [field("serial", "26/75", origin=origin)])
    assert (
        evaluate_observation(obs, {"serial_number": 26, "serial_total": 75}, [H, H])["verdict"]
        == "uncertain"
    )


def test_publication_quantity_is_not_serial_evidence():
    obs = evidence([field("edition", "75")], [field("edition", "75")])
    assert evaluate_observation(obs, {"serial_total": 75}, [H, H])["verdict"] == "uncertain"


def test_advertising_photo_cannot_supply_card_number_or_serial():
    obs = evidence([field("serial", "26/75")], [field("serial", "26/75", photo=1)])
    obs.photos[1].role = "advertisement"
    assert evaluate_observation(obs, {"serial_total": 75}, [H, H])["verdict"] == "uncertain"


def test_serial_same_retains_physical_copy_limitation_without_final_cost_authority():
    obs = evidence([field("serial", "26/75")], [field("serial", "26/75")])
    result = evaluate_observation(obs, {"serial_number": 26, "serial_total": 75}, [H, H])
    assert result["verdict"] == "same"
    assert result["physical_anchor"] == "serial"
    assert result["cost_authorized"] is False


def test_no_front_back_mismatch_or_reflection_only_positive():
    obs = evidence()
    obs.front_back_consistent = False
    assert evaluate_observation(obs, {}, [H, H])["verdict"] == "uncertain"
    obs.front_back_consistent = True
    obs.finish_basis = "reflection"
    assert evaluate_observation(obs, {}, [H, H])["verdict"] == "uncertain"


def test_conflicting_printed_card_number_rejects():
    obs = evidence([field("card_number", "SE-1")], [field("card_number", "SE-2")])
    assert evaluate_observation(obs, {}, [H, H])["verdict"] == "different"


def test_binding_changes_for_back_photo_order_identity_and_order_evidence():
    base = observation_binding({"player_name": "A"}, "123", "order-a", [H], [H, H])
    assert base != observation_binding({"player_name": "A"}, "123", "order-a", [H], [H])
    assert base != observation_binding({"player_name": "B"}, "123", "order-a", [H], [H, H])
    assert base != observation_binding({"player_name": "A"}, "124", "order-a", [H], [H, H])
    assert base != observation_binding({"player_name": "A"}, "123", "order-b", [H], [H, H])


@pytest.mark.parametrize(
    "url",
    [
        "http://images.cardhobby.com/a",
        "https://images.cardhobby.com.evil/a",
        "https://user@images.cardhobby.com/a",
        "https://images.cardhobby.com:444/a",
        "https://127.0.0.1/a",
    ],
)
def test_image_urls_restricted(url):
    assert not image_url_allowed(url)


@pytest.mark.asyncio
async def test_streaming_download_stops_before_oversize_body():
    import httpx

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 5
            yield b"y" * 5
            raise AssertionError("reader must stop before next chunk")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Body()))
    ) as client:
        with pytest.raises(ValueError, match="too_large"):
            await read_bounded(client, "https://images.cardhobby.com/a", limit=8)


def test_current_serial_pair_can_review_missing_title_but_not_wrong_edition():
    from card_platform.purchase_cost_inference import apply_inference
    from card_platform.purchase_cost_images import observation_binding

    card = {
        "serial_number": 26,
        "serial_total": 75,
        "image_content_hashes": {"front": H, "back": H},
    }
    binding = observation_binding(card, "123", "order", [H, H], [H, H])
    decision = {
        **evaluate_observation(
            evidence([field("serial", "26/75")], [field("serial", "26/75")]), card, [H, H]
        ),
        "source": "purchase_multi_image",
        "confidence": "0.90",
        "evidence_binding": binding,
    }
    from test_purchase_cost_recall_repair import image_correction

    decision["independent_confirmation"] = image_correction()["independent_confirmation"]
    decision["independent_confirmation"]["evidence_binding"] = binding
    match = dict(
        automatic=False,
        recalled=True,
        conflicts=[],
        missing=["限编"],
        reasons=["产品一致"],
        item_id="123",
        evidence_hash="order",
    )
    assert apply_inference(card, match, decision)["automatic"]
    assert not apply_inference(card, {**match, "conflicts": ["限编总数冲突"]}, decision)[
        "automatic"
    ]
    assert not apply_inference(card, {**match, "evidence_hash": "changed"}, decision)["automatic"]
    assert not apply_inference({**card, "serial_number": 27}, match, decision)["automatic"]


def test_stale_or_legacy_different_decision_does_not_poison_new_multi_photo_evidence():
    from card_platform.purchase_cost_inference import apply_inference

    card = {"image_content_hashes": {"front": H}}
    match = dict(
        automatic=False,
        recalled=True,
        conflicts=[],
        missing=["Insert"],
        reasons=["产品一致"],
        item_id="123",
        evidence_hash="order",
    )
    stale = dict(
        source="purchase_multi_image", verdict="different", confidence="0", evidence_binding={}
    )
    assert apply_inference(card, match, stale) == match


@pytest.mark.parametrize("value", ["TOP-LEFT", "top right", "bottom_left", "BOTTOM-RIGHT"])
def test_ocr_position_is_not_card_number(value):
    from card_platform.domain.card_identity import CardIdentityResolver

    assert not CardIdentityResolver().resolve({"card_number": value}).card_number


def test_roty_favorites_is_specific_insert_alias():
    from card_platform.domain.card_identity import canonical_insert

    assert canonical_insert("ROTY favorites") == canonical_insert("Rookie of the Year Favorites")


@pytest.mark.asyncio
async def test_gallery_rejects_wrong_item_and_does_not_download_other_items():
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings

    observer = PurchaseImageObserver(Settings(vision_api_key=""))
    with pytest.raises(ValueError, match="binding"):
        await observer.gallery(
            None,
            dict(
                item_id="123",
                item_url="https://evil.example/market/item/124",
                image_urls=["https://images.cardhobby.com/a.jpg"],
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("conflicts", [[], ["年份冲突"]])
async def test_service_uses_all_photos_and_records_uncertainty_without_legacy_fallback(
    monkeypatch, conflicts
):
    from card_platform.purchase_cost_service import PurchaseCostService
    from card_platform.purchase_cost_images import PurchaseImageObserver

    calls = []

    async def observe(self, card, line, order_hash):
        calls.append((line["item_id"], order_hash))
        return {
            "source": "purchase_multi_image",
            "verdict": "uncertain",
            "reasons": ["printed_serial_pair_required"],
        }

    monkeypatch.setattr(PurchaseImageObserver, "observe", observe)

    class Conn:
        async def execute(self, *args):
            calls.append("stored")

    service = PurchaseCostService()

    async def incomplete(*args):
        calls.append("field_incomplete")

    monkeypatch.setattr(service, "incomplete_field", incomplete)
    result = await service.observe_purchase_field(
        Conn(),
        uuid4(),
        "card",
        {},
        [
            dict(
                unit_id="unit",
                order_id="order",
                item_id="123",
                evidence_hash="hash",
                automatic=False,
                recalled=True,
                conflicts=conflicts,
                missing=["Insert"],
                reasons=["产品一致"],
            )
        ],
        [],
    )
    assert result["attempted"] == 1 and result["issues"]
    assert calls == [("123", "hash"), "stored", "field_incomplete"]


@pytest.mark.asyncio
async def test_gallery_uses_captured_url_and_primary_when_public_number_differs_from_url_id():
    import httpx
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings

    html = '<a href="/market/bidrecords/987654321"><span id="bidcount">1</span></a><div id="layer-photos-demo"><img src="https://images.cardhobby.com/a.jpg"><img src="https://images.cardhobby.com/b.jpg"></div>'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html))
    ) as client:
        gallery = await PurchaseImageObserver(Settings(vision_api_key="")).gallery(
            client,
            dict(
                item_id="123456789012345",
                item_url="https://cardhobby.com.cn/market/item/987654321",
                image_urls=["https://images.cardhobby.com/a.jpg"],
            ),
        )
    assert gallery["source_url"].endswith("/987654321")
    assert len(gallery["image_urls"]) == 2


def test_normalized_serial_cannot_invent_fraction_absent_from_raw_printed_text():
    obs = evidence([field("serial", "26/75")], [field("serial", "26/75")])
    for f in obs.target_fields + obs.candidate_fields:
        f.text = "75 copies"
    result = evaluate_observation(obs, {"serial_number": 26, "serial_total": 75}, [H, H])
    assert result["verdict"] == "uncertain"


def test_compressed_pixel_bomb_rejected_before_image_decode():
    from io import BytesIO
    from PIL import Image
    from card_platform.purchase_cost_images import validate_photo

    buffer = BytesIO()
    Image.new("1", (6000, 6000)).save(buffer, format="PNG")
    with pytest.raises(ValueError, match="dimensions"):
        validate_photo(buffer.getvalue())


@pytest.mark.asyncio
async def test_resolution_keeps_stage_trace_for_unmatched_and_does_not_report_commit(monkeypatch):
    from card_platform.purchase_cost_service import PurchaseCostService

    service = PurchaseCostService()

    async def resolve(*args, trace, **kwargs):
        trace["recall"] = {"keywords": ["Fixture Player"], "complete": True, "orders": 1}
        trace["verification"] = {
            "attempted": 1,
            "issues": [{"code": "printed_serial_pair_required"}],
        }
        return {"status": "unmatched", "reason": "fixture"}

    monkeypatch.setattr(service, "_resolve_search", resolve)
    result = await service.resolve_search(None, uuid4(), "item", [], True)
    assert result["trace"]["recall"]["complete"]
    assert result["trace"]["verification"]["issues"]
    assert result["trace"]["commit"]["written"] is False


def test_matching_images_cannot_silently_replace_stored_checklist_number():
    obs = evidence([field("card_number", "SE-1")], [field("card_number", "SE-1")])
    assert evaluate_observation(obs, {"card_number": "SE-2"}, [H, H])["verdict"] == "uncertain"


def test_graded_candidate_is_not_raw_target_even_if_layout_and_finish_match():
    obs = evidence()
    obs.photos[1].role = "grading_label"
    assert evaluate_observation(obs, {}, [H, H])["verdict"] == "uncertain"


@pytest.mark.parametrize("kind", ["player", "insert", "parallel", "autograph"])
def test_typed_field_conflict_overrides_scalar_sameness_and_matching_serial(kind):
    obs = evidence(
        [field("serial", "26/75"), field(kind, "Gold")],
        [field("serial", "26/75"), field(kind, "Silver")],
    )
    assert evaluate_observation(obs, {"serial_total": 75}, [H, H])["verdict"] == "uncertain"


def test_empty_fields_and_model_same_cannot_auto_promote_soft_missing():
    from card_platform.purchase_cost_images import apply_multi_observation

    card = {"image_content_hashes": {"front": H}}
    binding = observation_binding(card, "123", "order", [H], [H, H])
    decision = {
        **evaluate_observation(evidence(), {}, [H, H]),
        "source": "purchase_multi_image",
        "evidence_binding": binding,
    }
    match = dict(
        automatic=False,
        recalled=True,
        conflicts=[],
        missing=["Insert"],
        reasons=["产品一致"],
        item_id="123",
        evidence_hash="order",
    )
    assert not apply_multi_observation(card, match, decision)["automatic"]


@pytest.mark.asyncio
async def test_schema_failure_gets_one_bounded_repair_and_still_cannot_authorize_cost():
    import json
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from card_platform.purchase_cost_images import PurchaseImageObserver
    from card_platform.config import Settings

    obs = evidence()
    obs.design_match = "unknown"
    good = obs.model_dump(mode="json", by_alias=True)
    bad = {
        **good,
        "candidate_fields": [{"kind": "unsupported_field", "value": "x", "text": "x", "photo": 0}],
    }
    responses = [bad, good]
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(responses.pop(0))))]
        )

    observer = PurchaseImageObserver(Settings(vision_api_key="fixture"))
    observer.verifier.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
    photo = buffer.getvalue()
    result = await observer.observe_bytes(
        {},
        dict(item_id="123", title="Fixture"),
        "order",
        [photo, photo],
        [photo, photo],
        {"image_urls": ["a", "b"]},
    )
    assert len(calls) == 2
    assert result["schema_repair_attempted"] is True
    assert result["verdict"] == "uncertain" and result["cost_authorized"] is False


def test_soft_paired_fields_on_ad_or_out_of_scope_photo_cannot_authorize_cost():
    from card_platform.purchase_cost_images import apply_multi_observation
    from test_purchase_cost_recall_repair import image_correction

    card = {"image_content_hashes": {"front": H}}
    obs = evidence(
        [field("card_number", "A1", photo=7), field("insert", "Insert X", photo=7)],
        [field("card_number", "A1", photo=1), field("insert", "Insert X", photo=1)],
    )
    obs.photos[1].role = "advertisement"
    binding = observation_binding(card, "123", "order", [H], [H, H])
    proof = image_correction()["independent_confirmation"]
    proof["evidence_binding"] = binding
    decision = {
        **evaluate_observation(obs, card, [H, H]),
        "source": "purchase_multi_image",
        "evidence_binding": binding,
        "independent_confirmation": proof,
    }
    match = dict(
        automatic=False,
        recalled=True,
        conflicts=[],
        missing=["Insert"],
        reasons=["产品一致"],
        item_id="123",
        evidence_hash="order",
    )
    assert not apply_multi_observation(card, match, decision)["automatic"]


def test_chinese_printed_names_do_not_collapse_into_equal_empty_ascii():
    obs = evidence([field("player", "库里")], [field("player", "杜兰特")])
    assert evaluate_observation(obs, {}, [H, H])["verdict"] == "uncertain"


def test_seller_sticker_number_is_not_evidence_of_grading_change():
    obs = evidence()
    obs.candidate_fields.append(field("grade_cert", "Seller-17", origin="sticker"))
    assert evaluate_observation(obs, {}, [H, H])["verdict"] == "same"
