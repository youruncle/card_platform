"""Public semantic extracts; original private replays live in release receipts."""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.image_service import sanitize_identity_field_roles
from card_platform.ingestion.cardhobby import CardHobbyProvider
from card_platform.knowledge import KnowledgeResolver
from card_platform.schemas import CardRecognitionResult


@pytest.mark.parametrize("location", ["TOP-LEFT", "TOP-RIGHT", "BOTTOM-CENTER"])
def test_layout_labels_cannot_become_market_catalog_anchors(location):
    identity = dict(player_name="Paolo Banchero", brand_name="Panini",
                    product_name="Chronicles Essentials", season_start=2022,
                    season_end=2023, card_number=location)
    plan = CardHobbyProvider.build_query_plan(identity)
    assert CardIdentityResolver().resolve(identity).card_number is None
    assert all("TOP" not in query.upper() and "BOTTOM" not in query.upper()
               for _, query in plan)
    assert any("Chronicles Essentials" in query for _, query in plan)
    assert len(plan) <= 12


def test_multi_subject_queries_use_reviewed_preferred_aliases_with_short_insert():
    identity = dict(player_name="Nikola Jokić / Stephen Curry", brand_name="Panini",
                    product_name="Photogenic", season_start=2022, season_end=2023,
                    card_number="4", insert_name="Respect")
    plan = CardHobbyProvider.build_query_plan(identity)
    queries = [query for _, query in plan]
    assert "约基奇 Respect" in queries
    assert "库里 Respect" in queries
    assert "约基奇 库里 Respect" in queries
    assert len(plan) <= 12
    assert identity["player_name"] == "Nikola Jokić / Stephen Curry"


@pytest.mark.asyncio
async def test_runtime_knowledge_merge_preserves_multi_subject_discovery_and_alias_budget(monkeypatch):
    resolver = SimpleNamespace(
        entities={"available": True}, aliases_for=lambda *_: (),
        build_query_plan=lambda *_: [SimpleNamespace(query="Example Alternate Subject Respect"),
                                    SimpleNamespace(query="Example Other Subject Respect")],
    )
    provider = CardHobbyProvider(None, "unused", resolver=resolver)

    async def empty_public_response(*_args, **_kwargs):
        return {"result": 1, "data": {"Total": 0, "TotalCount": 0, "PageIndex": 1,
                                      "PagedMarketItemList": []}}

    monkeypatch.setattr(provider, "_get_search_payload", empty_public_response)
    result = await provider._search_direct(dict(
        player_name="Nikola Jokić / Stephen Curry", brand_name="Panini",
        product_name="Photogenic", season_start=2022, season_end=2023,
        card_number="4", insert_name="Respect"))
    emitted = [(diagnostic.stage, diagnostic.query) for diagnostic in result.query_diagnostics]
    assert ("multi_subject_insert", "约基奇 库里 Respect") in emitted
    assert sum(stage == "knowledge_alias" for stage, _ in emitted) == 2
    assert len(emitted) <= 12


@pytest.mark.parametrize("number", ["VFH-21", "VHF-21", "GA-ON", "TT-LBJ", "GR-19"])
def test_real_letter_order_is_preserved_in_identity_and_market_queries(number):
    identity = dict(player_name="Example Person", product_name="Example Product",
                    card_number=number, insert_name="Example Insert")
    assert CardIdentityResolver().resolve(identity).card_number == number
    plan = CardHobbyProvider.build_query_plan(identity)
    assert any(number in query for _, query in plan)
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal(".95"), card_number=number,
        field_evidence={"card_number": [f"背面卡号{number}"]}))
    assert result.card_number == number


@pytest.mark.parametrize(("number", "evidence"), [
    ("24", "正面球衣号24；背面No.15"),
    ("30", "背面顶部清晰印刷 'No. 18' —— OCR误识'30'系因正面球衣号码干扰"),
    ("31", "正面壳贴31；背面No.18"),
])
def test_proposed_jersey_or_sticker_token_is_removed_without_replacing_identity(number, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal(".95"), card_number=number,
        field_evidence={"card_number": [evidence]}))
    assert result.card_number is None


@pytest.mark.parametrize(("number", "evidence"), [
    ("15", "背面No.15；正面球衣号24"),
    ("2", "背面CARD NO.2；正面球衣号35；壳贴9"),
    ("18", "背面顶部清晰印刷 'No. 18' —— OCR误识'30'系因正面球衣号码干扰"),
    ("71", "背面右上71；正面球衣号3"),
])
def test_unrelated_jersey_and_sticker_evidence_preserves_real_catalog_token(number, evidence):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal(".95"), card_number=number,
        field_evidence={"card_number": [evidence]}))
    assert result.card_number == number


@pytest.mark.parametrize(("number", "caveats", "expected"), [
    ("24", ["正面图像中球员球衣号码 '24' 被误识别为卡号；背面明确标注 'No. 15'，"
            "故 card_number = '15'。正面 '24' 仅为球员球衣号码，非卡号。",
            "Front '24' is player jersey number, not card number — resolved via "
            "unambiguous 'No. 15' on back."], None),
    ("15", ["正面图像中球员球衣号码 '24' 被误识别为卡号；背面明确标注 'No. 15'，"
            "故 card_number = '15'。正面 '24' 仅为球员球衣号码，非卡号。",
            "Front '24' is player jersey number, not card number — resolved via "
            "unambiguous 'No. 15' on back."], "15"),
    ("24", ["24不是球衣号，是背面No.24"], "24"),
])
def test_current_result_role_caveats_reject_only_the_explicit_conflicting_token(number, caveats, expected):
    result = sanitize_identity_field_roles(CardRecognitionResult(
        confidence=Decimal(".95"), card_number=number, caveats=caveats,
        field_evidence={"card_number": [f"背面No.{number}"]}))
    assert result.card_number == expected


def test_negated_english_clause_cannot_bind_a_different_number_across_punctuation():
    from card_platform.domain.number_roles import non_catalog_number_role
    evidence = ["Front '24' is player jersey number, not card number — resolved via "
                "unambiguous 'No. 15' on back."]
    assert non_catalog_number_role("15", evidence) is False


@pytest.fixture
def reviewed_runtime_provider(monkeypatch):
    resolver = KnowledgeResolver({"version": 1, "entities": [{
        "id": "product-reviewed", "entity_type": "product", "canonical_name": "Prizm",
        "aliases": ["Prizm Basketball"],
    }]})
    provider = CardHobbyProvider(None, "unused", resolver=resolver, min_interval=0)

    async def empty_public_response(*_args, **_kwargs):
        return {"result": 1, "data": {"Total": 0, "TotalCount": 0, "PageIndex": 1,
                                      "PagedMarketItemList": []}}

    monkeypatch.setattr(provider, "_get_search_payload", empty_public_response)
    async def empty_public_item(*_args, **_kwargs):
        return {"sale": None, "reasons": ["no_sale_observed"]}

    monkeypatch.setattr(provider, "_get_public_item_payload", empty_public_item)
    return provider


@pytest.mark.asyncio
async def test_runtime_real_knowledge_queries_cannot_restore_a_layout_number(reviewed_runtime_provider):
    identity = dict(player_name="Stephen Curry", brand_name="Panini", product_name="Prizm",
                    season_start=2022, season_end=2023, card_number="TOP-LEFT")
    result = await reviewed_runtime_provider._search_direct(identity)
    assert all("TOP" not in diagnostic.query.upper() for diagnostic in result.query_diagnostics)
    assert any(diagnostic.stage == "knowledge_alias" for diagnostic in result.query_diagnostics)
    assert identity["card_number"] == "TOP-LEFT"
    assert len(result.query_diagnostics) <= 12


@pytest.mark.asyncio
@pytest.mark.parametrize("number", ["4", "TT-LBJ"])
async def test_numbered_multi_subject_runtime_preserves_combination_and_knowledge_aliases(
    reviewed_runtime_provider, number,
):
    result = await reviewed_runtime_provider._search_direct(dict(
        player_name="Nikola Jokić / Stephen Curry", brand_name="Panini",
        product_name="National Treasures", insert_name="Timeless Talents", card_number=number,
        season_start=2022, season_end=2023, serial_total=25))
    emitted = [(diagnostic.stage, diagnostic.query) for diagnostic in result.query_diagnostics]
    assert ("multi_subject_insert", "约基奇 库里 Timeless Talents") in emitted
    assert any(stage == "refine_print_run" and "25编" in query for stage, query in emitted)
    assert any(stage == "player_release_recovery" for stage, _ in emitted)
    assert sum(stage == "knowledge_alias" for stage, _ in emitted) == 2
    assert len(emitted) <= 12


def test_two_aliases_of_one_person_do_not_become_a_multi_subject_query():
    plan = CardHobbyProvider.build_query_plan(dict(
        player_name="Example Person", player_name_aliases=["示例"], brand_name="Panini",
        product_name="Photogenic", season_start=2022, season_end=2023,
        card_number="4", insert_name="Respect"))
    assert all(stage != "multi_subject_insert" for stage, _ in plan)
    assert any(query.startswith("示例 ") for _, query in plan)
    assert any(query.startswith("Example Person ") for _, query in plan)


@pytest.mark.asyncio
@pytest.mark.parametrize("use_resolver", [False, True])
async def test_public_item_seeds_do_not_evict_numbered_multi_subject_query(
    reviewed_runtime_provider, use_resolver,
):
    if not use_resolver:
        reviewed_runtime_provider.resolver = None
    result = await reviewed_runtime_provider._search_direct(dict(
        player_name="Nikola Jokić / Stephen Curry", brand_name="Panini",
        product_name="National Treasures", insert_name="Timeless Talents", card_number="4",
        season_start=2022, season_end=2023, serial_total=25,
        public_source_urls=["https://www.cardhobby.com.cn/market/item/123",
                            "https://www.cardhobby.com.cn/market/item/456"]))
    emitted = [(diagnostic.stage, diagnostic.query) for diagnostic in result.query_diagnostics]
    assert ("multi_subject_insert", "约基奇 库里 Timeless Talents") in emitted
    assert any(stage == "refine_print_run" and "25编" in query for stage, query in emitted)
    assert any(stage == "player_release_recovery" for stage, _ in emitted)
    assert sum(stage == "public_item_source" for stage, _ in emitted) == 2
    assert len(emitted) <= 12
