from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from decimal import Decimal

from card_platform.domain.comparables import Classification, ComparableTier, ComparableClassifier, ComparableInput
from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.formal_cardhobby_gate import enforce_formal_cardhobby_identity

from card_platform.ingestion.cardhobby import CardHobbyProvider, CardHobbySale, assess_cardhobby_candidate


def identity(player="KEVIN DURANT"):
    return dict(player_name=player, brand_name="Topps", product_name="Flagship",
                season_start=2025, season_end=2026, card_number="157",
                parallel_name="Black and White", is_autograph=False, is_rookie=False,
                is_patch=False, serial_total=None)


@pytest.mark.parametrize("player", ["KEVIN DURANT", "Example Player"])
def test_release_visual_parallel_query_survives_budget(player):
    plan = CardHobbyProvider.build_query_plan(identity(player))
    assert ("release_parallel", f"{player} 2025 黑白") in plan
    assert len(plan) <= 12


@pytest.mark.asyncio
async def test_runtime_knowledge_merge_keeps_visual_query():
    resolver = SimpleNamespace(entities={"present": True}, aliases_for=lambda *_: (),
                               build_query_plan=lambda *_: [SimpleNamespace(query="extra alias")])
    provider = CardHobbyProvider(None, "unused", resolver=resolver)
    provider._get_search_payload = AsyncMock(return_value={"data": {"PagedMarketItemList": []}})
    result = await provider._search_direct(identity())
    assert any(q.stage == "release_parallel" for q in result.query_diagnostics)
    assert len(result.query_diagnostics) <= 12


@pytest.mark.parametrize("product,accepted", [("Flagship", True), ("Chrome", False), ("Chrome Update", False)])
def test_chinese_visual_alias_does_not_bypass_product_gate(product, accepted):
    sale = CardHobbySale(
        item_id="133337089", title=f"2025 Topps {product} Kevin Durant 黑白折 折射",
        source_url="https://cardhobby.com.cn/Market/Item/133337089",
        occurred_at="2026-09-07T21:21:00+08:00", sale_price="6", shipping_price="18",
        bid_count=6, sale_type="auction", evidence_level="ended_auction",
        sold_status_evidence="Status=-2, 6 bids", match_confidence=".95",
    )
    score, _, tier = assess_cardhobby_candidate(identity(), sale)
    assert (score > 0 and tier is not None) == accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"result": 0, "data": {"PagedMarketItemList": []}},
    {"result": 1, "data": None},
    {"result": 1, "data": {}},
])
async def test_failed_business_response_is_not_zero_sales(payload):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload)
    )) as client:
        with pytest.raises(ValueError, match="不能作为零成交"):
            await CardHobbyProvider(None, "unused", min_interval=0)._get_search_payload(client, "example", 1)


@pytest.mark.asyncio
async def test_transient_connection_failure_is_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("transient", request=request)
        return httpx.Response(200, json={"result": 1, "data": {"PagedMarketItemList": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await CardHobbyProvider(None, "unused", min_interval=0)._get_search_payload(client, "example", 1)
    assert calls == 2
    assert result["data"]["PagedMarketItemList"] == []


@pytest.mark.parametrize("verdict,anchor,rejected", [
    (None, False, True), ("uncertain", False, True), ("compatible", False, True),
    ("same", False, True), ("same", True, False), ("different", True, True),
])
def test_descriptive_alias_never_becomes_formal_evidence_on_image_failure(verdict, anchor, rejected):
    preliminary = Classification(ComparableTier.T1, Decimal(".9"), Decimal(".7"),
                                 field_matches={"parallel": True, "visual_same_template_anchor": anchor})
    sale = CardHobbySale(
        item_id="133337089", title="2025 Topps Flagship Kevin Durant 黑白折", source_url="https://cardhobby.com.cn/Market/Item/133337089",
        occurred_at="2026-09-07T21:21:00+08:00", sale_price="6", shipping_price="18",
        bid_count=6, sale_type="auction", evidence_level="ended_auction",
        sold_status_evidence="Status=-2, 6 bids", match_confidence=".95",
    )
    result = enforce_formal_cardhobby_identity(identity(), sale.model_dump(mode="json"), preliminary,
                                              visual_verdict=verdict)
    assert (result.tier == ComparableTier.REJECTED) == rejected


@pytest.mark.parametrize("verdict,confidence,geometry,has_anchor", [
    ("same", ".96", True, True), ("same", ".89", True, False),
    ("same", ".99", False, False), ("compatible", ".99", True, False),
    ("different", ".99", True, False),
])
def test_unlisted_numeric_id_needs_joint_visual_geometry_and_finish(verdict, confidence, geometry, has_anchor):
    result = ComparableClassifier().classify(
        CardIdentityResolver().resolve(identity()),
        ComparableInput(title="2025 Topps Flagship Kevin Durant 黑白折", source_code="cardhobby",
                        visual_verdict=verdict, visual_confidence=Decimal(confidence),
                        visual_reasons=("geometry_same_template:matches=100,inliers=85,ratio=0.850",) if geometry else ()),
    )
    assert bool(result.field_matches.get("visual_same_template_anchor")) == has_anchor
    if has_anchor:
        assert result.tier == ComparableTier.T1  # no claimed candidate-back card number
