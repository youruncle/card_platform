"""Field evidence is distinct from model confidence and storage compatibility."""

from decimal import Decimal
from uuid import uuid4

import pytest

from card_platform.schemas import CardRecognitionResult, CardWebCandidate
from card_platform.vision_routing import finalize_recognition_status
from card_platform.image_service import sanitize_identity_field_roles
from card_platform.domain.recognition_evidence import (
    assess_recognition_fields,
    bind_recognition_storage,
    purchase_identity_view,
)


def recognized(**updates):
    return CardRecognitionResult(
        player_name="Test Player",
        brand_name="Topps",
        product_name="Topps Now",
        card_number="123",
        confidence=Decimal(".99"),
        **updates,
    )


def stored():
    return dict(
        card_definition_id=str(uuid4()),
        player_name="Test Player",
        brand_name="Topps",
        product_name="Topps Now",
        card_number="123",
        season_start=2026,
        season_end=None,
        insert_name=None,
        parallel_name=None,
        serial_number=None,
        serial_total=None,
        is_numbered=False,
        grade_company=None,
        grade_value=None,
        image_content_hashes={"front": "a" * 64, "back": "b" * 64},
    )


def test_high_confidence_does_not_verify_model_fields_or_invent_a_year():
    result = assess_recognition_fields(recognized())
    assert result.field_assessments["season_start"].status == "unknown"
    assert result.field_assessments["season_start"].value is None
    assert result.field_assessments["player_name"].status == "proposed"
    assert result.field_assessments["player_name"].source == "visual_model"


def test_provider_self_reported_assessments_are_recomputed():
    result = recognized().model_copy(
        update={"field_assessments": {"season_start": {"status": "verified", "value": 2026}}}
    )
    assert assess_recognition_fields(result).field_assessments["season_start"].status == "unknown"


@pytest.mark.parametrize(
    "evidence",
    ["卡背传记：in the 2024/25 season at Illinois", "Back biography: 2024/25 college season"],
)
def test_rejected_year_keeps_its_evidence_and_field_issue_after_finalization(evidence):
    raw = recognized(
        season_start=2024,
        season_end=2025,
        field_evidence={"season_start": [evidence], "season_end": [evidence]},
    )
    final = finalize_recognition_status(sanitize_identity_field_roles(raw))
    state = final.field_assessments["season_start"]
    assert state.status == "conflicted" and state.value is None
    assert state.evidence == [evidence]
    assert "season_start_not_direct_release_evidence" in state.issues
    assert "season_start_not_direct_release_evidence" in final.recognition_quality_issues


@pytest.mark.parametrize("prefix", ["联网候选：", "目录核验："])
def test_only_supported_applied_catalog_fields_are_verified(prefix):
    result = recognized(
        season_start=2025,
        season_end=2026,
        field_evidence={"season_start": [prefix + "发行年份2025"]},
        catalog_verified_fields={"season_start": 2025},
        web_candidates=[
            dict(
                title="2025-26 Topps Now Test Player",
                identity={"player_name": "Test Player", "card_number": "123", "season_start": 2025},
                matching_fields=["player_name", "card_number", "season_start"],
                source_urls=["https://www.topps.com/pages/checklists"],
                match_confidence=".96",
            )
        ],
    )
    state = assess_recognition_fields(result).field_assessments["season_start"]
    assert state.status == ("verified" if prefix == "目录核验：" else "proposed")
    assert state.source == ("catalog" if prefix == "目录核验：" else "visual_model")


def test_catalog_cannot_verify_a_physical_serial():
    result = recognized(
        serial_number=10, serial_total=50, field_evidence={"serial_number": ["目录核验：Gold /50"]}
    )
    assert assess_recognition_fields(result).field_assessments["serial_number"].status == "proposed"


def test_unknown_year_is_recovered_only_from_a_current_storage_binding():
    card = stored()
    result = bind_recognition_storage(recognized(), card)
    projected = purchase_identity_view(card, result.model_dump(mode="json"))
    assert projected["season_start"] is None
    assert card["season_start"] == 2026
    assert projected["player_name"] == "Test Player"


@pytest.mark.parametrize(
    "field,value",
    [
        ("card_definition_id", str(uuid4())),
        ("serial_number", 3),
        ("serial_total", 50),
        ("grade_company", "PSA"),
        ("image_content_hashes", {"front": "c" * 64, "back": "b" * 64}),
        ("image_content_hashes", {}),
        ("season_start", 2025),
        ("player_name", "Other Player"),
    ],
)
def test_identity_or_image_changes_invalidate_old_unknown_projection(field, value):
    card = stored()
    result = bind_recognition_storage(recognized(), card)
    changed = {**card, field: value}
    assert purchase_identity_view(changed, result.model_dump(mode="json")) == changed


def test_legacy_results_and_unbound_model_metadata_cannot_erase_current_identity():
    card = stored()
    assert purchase_identity_view(card, recognized().model_dump(mode="json")) == card
    assert (
        purchase_identity_view(card, {"field_assessments": {"season_start": {"status": "unknown"}}})
        == card
    )


def test_binding_requires_a_real_front_hash_and_recomputes_provider_metadata():
    card = stored()
    card["image_content_hashes"] = {}
    result = bind_recognition_storage(recognized(), card)
    assert not result.identity_storage_binding
    assert purchase_identity_view(card, result.model_dump(mode="json")) == card


@pytest.mark.parametrize("spelling", ["JUN23", "JUN 23", "8.10"])
def test_topps_now_date_badges_are_never_catalog_numbers(spelling):
    raw = CardRecognitionResult(product_name="Topps Now", card_number=spelling, confidence=".99")
    result = finalize_recognition_status(sanitize_identity_field_roles(raw))
    assert result.card_number is None
    assert result.field_assessments["card_number"].status == "conflicted"


def test_product_guessed_from_insert_code_is_unknown_until_independently_supported():
    raw = CardRecognitionResult(
        product_name="Hoops",
        card_number="NS-20",
        confidence=".98",
        field_evidence={
            "product_name": ["Back: implied by NS-20 numbering; Hoops is standard for NS-XX format"]
        },
    )
    result = finalize_recognition_status(sanitize_identity_field_roles(raw))
    assert result.product_name is None
    assert result.field_assessments["product_name"].status == "conflicted"


def test_visible_product_logo_survives_separate_speculation():
    raw = CardRecognitionResult(
        product_name="Hoops",
        confidence=".98",
        field_evidence={
            "product_name": ["Front logo: 'Hoops'", "Back: implied by typical NS-XX format"]
        },
    )
    assert sanitize_identity_field_roles(raw).product_name == "Hoops"


def test_supported_catalog_can_independently_resolve_guessed_product():
    raw = CardRecognitionResult(
        player_name="Test Player",
        product_name="Hoops",
        card_number="NS-20",
        confidence=".98",
        field_evidence={"product_name": ["Back: implied by NS-XX format", "目录核验：2025 Hoops"]},
        catalog_verified_fields={"product_name": "Hoops"},
        web_candidates=[
            dict(
                title="2025 Hoops Test Player NS-20",
                identity={
                    "player_name": "Test Player",
                    "card_number": "NS-20",
                    "product_name": "Hoops",
                },
                matching_fields=["player_name", "card_number", "product_name"],
                source_urls=["https://www.topps.com/pages/checklists"],
                match_confidence=".96",
            )
        ],
    )
    result = finalize_recognition_status(sanitize_identity_field_roles(raw))
    assert result.product_name == "Hoops"
    assert result.field_assessments["product_name"].status == "verified"


def test_model_catalog_prefix_and_url_alone_cannot_claim_verified_evidence():
    raw = CardRecognitionResult(
        player_name="Test Player",
        product_name="Hoops",
        card_number="NS-20",
        confidence=".98",
        field_evidence={"product_name": ["目录核验：2025 Hoops"]},
        web_candidates=[
            dict(
                title="2025 Hoops Test Player NS-20",
                identity={
                    "player_name": "Test Player",
                    "card_number": "NS-20",
                    "product_name": "Hoops",
                },
                matching_fields=["player_name", "card_number", "product_name"],
                source_urls=["https://www.topps.com/pages/checklists"],
                match_confidence=".96",
            )
        ],
    )
    assert assess_recognition_fields(raw).field_assessments["product_name"].status == "proposed"


@pytest.mark.asyncio
@pytest.mark.parametrize("supports", [True, False])
async def test_catalog_verification_marker_requires_fetched_matching_source(monkeypatch, supports):
    import json
    from types import SimpleNamespace
    import httpx
    import card_platform.card_search_service as module
    from card_platform.config import Settings

    payload = {
        "resolved_fields": {"product_name": "Hoops"},
        "field_evidence": {"product_name": ["官方产品目录"]},
        "candidates": [
            dict(
                title="2025 Hoops Test Player NS-20",
                identity={
                    "player_name": "Test Player",
                    "card_number": "NS-20",
                    "product_name": "Hoops",
                },
                matching_fields=["player_name", "card_number", "product_name"],
                source_urls=["https://www.topps.com/pages/checklists"],
                match_confidence=".96",
            )
        ],
    }

    async def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
        )

    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(
            200, text="2025 Hoops Test Player NS-20" if supports else "Other Player NS-21"
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    service = module.CardSearchService(Settings(card_search_enabled=False))
    service.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    async def image(*args, **kwargs):
        return None

    async def reference(*args, **kwargs):
        return {}, {}

    monkeypatch.setattr(service, "_candidate_image", image)
    monkeypatch.setattr(service, "_reference_updates", reference)
    result = await service.enrich(
        CardRecognitionResult(player_name="Test Player", card_number="NS-20", confidence=".98")
    )
    assert requests == ["https://www.topps.com/pages/checklists"]
    assert result.catalog_verified_fields == ({"product_name": "Hoops"} if supports else {})
    state = assess_recognition_fields(result).field_assessments["product_name"]
    assert state.status == ("verified" if supports else "unknown")


@pytest.mark.asyncio
async def test_disabled_enrichment_clears_provider_catalog_claims():
    from card_platform.card_search_service import CardSearchService
    from card_platform.config import Settings

    raw = CardRecognitionResult(
        player_name="Test Player",
        product_name="Hoops",
        confidence=".98",
        catalog_verified_fields={"product_name": "Hoops"},
        identity_storage_binding={"provider": "fake"},
    )
    result = await CardSearchService(Settings(card_search_enabled=False)).enrich(raw)
    assert result.catalog_verified_fields == {}
    assert result.identity_storage_binding == {}


def test_catalog_recovery_supersedes_active_role_issue_but_preserves_original_history():
    raw = CardRecognitionResult(
        player_name="Test Player",
        product_name="Hoops",
        card_number="NS-20",
        season_start=2024,
        season_end=2025,
        confidence=".98",
        field_evidence={
            "product_name": ["Back: implied by NS-XX format"],
            "season_start": ["Back bio: 2024/25 college season"],
        },
    )
    rejected = sanitize_identity_field_roles(raw)
    assert rejected.product_name is None and rejected.season_start is None
    recovered = rejected.model_copy(
        update={
            "product_name": "Hoops",
            "season_start": 2025,
            "catalog_verified_fields": {"product_name": "Hoops", "season_start": 2025},
            "field_evidence": {
                **rejected.field_evidence,
                "product_name": [*rejected.field_evidence["product_name"], "目录核验：2025 Hoops"],
                "season_start": [*rejected.field_evidence["season_start"], "目录核验：2025 Hoops"],
            },
            "web_candidates": [
                CardWebCandidate(
                    title="2025 Hoops Test Player NS-20",
                    identity={
                        "player_name": "Test Player",
                        "card_number": "NS-20",
                        "product_name": "Hoops",
                        "season_start": 2025,
                    },
                    matching_fields=["player_name", "card_number", "product_name", "season_start"],
                    source_urls=["https://www.topps.com/pages/checklists"],
                    match_confidence=".96",
                )
            ],
        }
    )
    final = finalize_recognition_status(sanitize_identity_field_roles(recovered))
    for field in ("product_name", "season_start"):
        assert final.field_assessments[field].status == "verified"
        assert final.field_assessments[field].issues == []
    assert "season_start_not_direct_release_evidence" in final.caveats
    assert "season_start_not_direct_release_evidence" not in final.recognition_quality_issues
    card = stored()
    card.update(product_name="Hoops", season_start=2025, card_number="NS-20")
    bound = bind_recognition_storage(final, card)
    assert purchase_identity_view(card, bound.model_dump(mode="json"))["season_start"] == 2025


def test_later_explicit_confirmation_with_same_values_overrides_old_unknown():
    card = stored()
    bound = bind_recognition_storage(recognized(), card)
    confirmed = {**card, "has_confirmed_identity": True}
    assert purchase_identity_view(confirmed, bound.model_dump(mode="json"))["season_start"] == 2026


@pytest.mark.parametrize(
    "field,value",
    [
        ("set_name", "Different Set"),
        ("print_run", 50),
        ("autograph_type", "sticker"),
        ("has_inscription", True),
        ("inscription_text", "MVP"),
        ("is_patch", True),
        ("memorabilia_usage", "game_used"),
        ("memorabilia_types", ["jersey"]),
        ("memorabilia_player", "Other Player"),
    ],
)
def test_other_in_place_identity_changes_invalidate_storage_binding(field, value):
    card = stored()
    bound = bind_recognition_storage(recognized(), card)
    changed = {**card, field: value}
    assert purchase_identity_view(changed, bound.model_dump(mode="json"))["season_start"] == 2026


def test_local_ocr_recovery_supersedes_current_role_issue_without_claiming_catalog_verification():
    from card_platform.image_service import apply_local_ocr_anchors

    rejected = sanitize_identity_field_roles(
        CardRecognitionResult(product_name="Topps Now", card_number="JUN23", confidence=".99")
    )
    recovered = apply_local_ocr_anchors(
        rejected, {"card_number": "DACU", "card_number_evidence": "No. DACU"}, 1
    )
    final = finalize_recognition_status(recovered)
    state = final.field_assessments["card_number"]
    assert state.value == "DACU" and state.status == "proposed"
    assert state.source == "local_ocr" and state.issues == []
    assert "card_number_is_calendar_date_not_catalog_identifier" in final.caveats
    assert (
        "card_number_is_calendar_date_not_catalog_identifier"
        not in final.recognition_quality_issues
    )


@pytest.mark.asyncio
async def test_sheet_recognition_emits_assessments_and_clears_provider_provenance():
    from io import BytesIO
    from starlette.datastructures import UploadFile, Headers
    from card_platform.schemas import SheetCardRecognitionResult
    from card_platform.config import Settings
    from card_platform.image_service import CardVisionService

    class Provider:
        async def recognize_sheet(self, *args):
            return SheetCardRecognitionResult(
                detected_count=1,
                cards=[
                    {
                        "index": 0,
                        "result": CardRecognitionResult(
                            product_name="Topps Now",
                            card_number="JUN23",
                            confidence=".99",
                            local_ocr_observed_fields={"card_number": "JUN23"},
                            catalog_verified_fields={"card_number": "JUN23"},
                        ),
                    }
                ],
            )

    service = CardVisionService(Settings(card_search_enabled=False), provider=Provider())
    result = await service.recognize_sheet(
        UploadFile(
            file=BytesIO(b"\xff\xd8\xfffixture"),
            filename="sheet.jpg",
            headers=Headers({"content-type": "image/jpeg"}),
        )
    )
    card = result.cards[0].result
    assert card.card_number is None
    assert card.local_ocr_observed_fields == {} and card.catalog_verified_fields == {}
    assert card.field_assessments["card_number"].status == "conflicted"


@pytest.mark.parametrize(
    "line", ["2024/25 college basketball season", "In the 2024/25 college basketball season"]
)
def test_ocr_biography_season_is_not_a_release_anchor(line):
    from card_platform.local_ocr import extract_anchors

    anchors = extract_anchors([line])
    assert "season_start" not in anchors and "season_end" not in anchors


@pytest.mark.parametrize(
    "line", ["2024/25 college basketball season", "In the 2024/25 college basketball season"]
)
def test_ocr_provenance_alone_cannot_override_wrong_season_role(line):
    from card_platform.image_service import apply_local_ocr_anchors

    raw = CardRecognitionResult(
        season_start=2024,
        season_end=2025,
        confidence=".98",
        field_evidence={field: [line] for field in ("season_start", "season_end")},
    )
    rejected = sanitize_identity_field_roles(raw)
    # Even a preexisting OCR payload with this mistake must not restore it.
    restored = apply_local_ocr_anchors(
        rejected, {"season_start": 2024, "season_end": 2025, "season_evidence": line}, 1
    )
    result = finalize_recognition_status(sanitize_identity_field_roles(restored))
    assert result.season_start is None and result.season_end is None
    assert result.field_assessments["season_start"].status == "conflicted"


def test_real_ocr_release_line_remains_an_observation():
    from card_platform.image_service import apply_local_ocr_anchors
    from card_platform.local_ocr import extract_anchors

    result = apply_local_ocr_anchors(
        CardRecognitionResult(confidence=".98"), extract_anchors(["2025-26 TOPPS BASKETBALL"]), 1
    )
    result = finalize_recognition_status(sanitize_identity_field_roles(result))
    assert (result.season_start, result.season_end) == (2025, 2026)
    assert result.field_assessments["season_start"].source == "local_ocr"
