from decimal import Decimal
import asyncio
from uuid import uuid4

import httpx

from card_platform.valuation_worker import (
    AutomatedValuationWorker,
    ComparableSearchResult,
    DiscoveredComparable,
    FallbackValuation,
    PlayerAdjustment,
    PlayerPricePair,
    ValuationReference,
    ebay_completed_search_url,
    ebay_item_id,
    market_search_query_ladder,
    recognition_candidate_overrides,
    search_index_hit_to_comparable,
    search_index_query_ladder,
    third_party_exact_search_queries,
)
from card_platform.schemas import CardRecognitionResult
from card_platform.search_index import SearchIndexHit


def _public_comparable(url: str, evidence_level: str) -> DiscoveredComparable:
    return DiscoveredComparable(
        title="Jaren Jackson Jr. 2020-21 Mosaic #88",
        source_url=url,
        occurred_at="2026-08-01T00:00:00Z",
        sale_price=Decimal("5"),
        currency="USD",
        fx_rate_to_base=Decimal("7"),
        match_confidence=Decimal("0.9"),
        source_reliability=Decimal("0.95"),
        evidence_level=evidence_level,
    )


def test_resolved_identity_accepts_memorabilia_type_list() -> None:
    result = ComparableSearchResult(
        resolved_identity={"memorabilia_types": ["jersey"]}
    )

    assert result.resolved_identity["memorabilia_types"] == ["jersey"]


def test_public_ebay_completed_sale_is_tier_two_evidence() -> None:
    comp = AutomatedValuationWorker._normalize_public_comparable(
        _public_comparable("https://www.ebay.com/itm/123", "public_completed_sale")
        .model_copy(update={
            "sale_status": "sold",
            "sale_status_evidence": "This listing sold on Aug 1, 2026",
            "price_basis": "displayed_sold_price",
        })
    )
    assert comp is not None
    assert comp.source_code == "ebay_public"
    assert comp.source_tier == 2
    assert comp.source_reliability == Decimal("0.82")


def test_public_ebay_active_listing_is_not_a_formal_comparable() -> None:
    comp = AutomatedValuationWorker._normalize_public_comparable(
        _public_comparable("https://www.ebay.com/itm/123", "active_listing")
    )
    assert comp is None


def test_public_ebay_requires_item_level_sold_proof() -> None:
    search_page = _public_comparable(
        "https://www.ebay.com/sch/i.html?LH_Sold=1", "public_completed_sale"
    ).model_copy(update={
        "sale_status": "sold",
        "sale_status_evidence": "Sold",
        "price_basis": "displayed_sold_price",
    })
    missing_status = _public_comparable(
        "https://www.ebay.com/itm/123", "public_completed_sale"
    )

    assert AutomatedValuationWorker._normalize_public_comparable(search_page) is None
    assert AutomatedValuationWorker._normalize_public_comparable(missing_status) is None


def test_model_cannot_self_declare_cardhobby_first_party_evidence() -> None:
    spoofed = _public_comparable(
        "https://www.cardhobby.com.cn/market/item/1389274", "ended_auction"
    ).model_copy(update={
        "source_code": "cardhobby",
        "matching_fields": ["cardhobby:ended_auction"],
    })

    assert AutomatedValuationWorker._normalize_public_comparable(spoofed) is None


def test_direct_cardhobby_provider_evidence_is_tier_one() -> None:
    verified = _public_comparable(
        "https://www.cardhobby.com.cn/market/item/123866225", "ended_auction"
    ).model_copy(update={
        "source_code": "cardhobby_provider",
        "matching_fields": ["cardhobby_status_sold", "cardhobby_bid_count"],
    })

    normalized = AutomatedValuationWorker._normalize_public_comparable(verified)

    assert normalized is not None
    assert normalized.source_code == "cardhobby"
    assert normalized.source_tier == 1


def test_market_search_query_ladder_relaxes_unconfirmed_ocr_fields() -> None:
    queries = market_search_query_ladder(
        {
            "player_name": "Justin Champagnie",
            "season_start": 2020,
            "season_end": 2021,
            "product_name": "Chronicles Hoops Draft Picks",
            "insert_name": "Tribute",
            "set_name": "Hoops Retro Autographs",
            "card_number": "HR-JCH",
            "serial_total": 133,
            "is_autograph": True,
        }
    )

    assert any("/133" in query for query in queries)
    assert any(query == "Justin Champagnie HR-JCH" for query in queries)
    assert any("2021-22" in query for query in queries)
    assert "Justin Champagnie 2021 Chronicles Hoops Draft Picks" in queries
    assert any("Hoops Retro Autographs" in query for query in queries)
    sold_url = ebay_completed_search_url(queries[0])
    assert "LH_Sold=1" in sold_url
    assert "LH_Complete=1" in sold_url


def test_market_search_query_ladder_includes_single_copyright_year_without_ocr_insert() -> None:
    queries = market_search_query_ladder(
        {
            "player_name": "Tracy McGrady",
            "season_start": 1997,
            "season_end": 1998,
            "product_name": "Finest",
            "insert_name": "Protector",
            "card_number": "28",
        }
    )

    assert "Tracy McGrady 1998 Finest" in queries


def test_search_index_queries_and_ebay_item_id_are_bounded() -> None:
    identity = {
        "player_name": "Justin Champagnie",
        "season_start": 2021,
        "season_end": 2022,
        "product_name": "Chronicles Draft Picks",
        "set_name": "Hoops Retro Autographs",
        "card_number": "HR-JCH",
    }

    queries = search_index_query_ladder(identity)

    assert len(queries) <= 7
    assert any(query.startswith("site:ebay.com/itm ") for query in queries)
    assert any('"' in query and "sold ebay" in query for query in queries)
    assert any("130point" in query for query in queries)
    assert ebay_item_id("https://www.ebay.com/itm/title/123456789012") == "123456789012"
    assert ebay_item_id("https://ebay.com/itm/123456789012") == "123456789012"
    assert ebay_item_id("https://www.ebay.com/sch/i.html?LH_Sold=1") is None
    assert ebay_item_id("https://example.com/itm/123456789012") is None


def test_search_index_sale_requires_self_contained_sold_date_and_price() -> None:
    base = _public_comparable(
        "https://www.ebay.com/itm/title/123456789012", "search_index_sale"
    ).model_copy(update={
        "sale_status": "sold",
        "sale_status_evidence": "search result snapshot",
        "price_basis": "search_index_displayed_sold_price",
        "search_provider": "Bing",
        "search_query": '"Jaren Jackson Jr Mosaic 88" sold ebay',
        "search_snippet": "Sold Aug 12, 2026 US $5.00 Jaren Jackson Jr Mosaic #88",
    })

    accepted = AutomatedValuationWorker._normalize_public_comparable(base)

    assert accepted is not None
    assert accepted.source_code == "public_market_research"
    assert accepted.source_tier == 3
    assert accepted.source_reliability == Decimal("0.55")
    assert AutomatedValuationWorker._normalize_public_comparable(
        base.model_copy(update={"search_snippet": "Sold US $5.00 Best Offer accepted"})
    ) is None
    assert AutomatedValuationWorker._normalize_public_comparable(
        base.model_copy(update={"search_snippet": "Sold Aug 12, 2026 US $9.00"})
    ) is None


def test_search_index_hit_is_parsed_without_inventing_sale_fields() -> None:
    accepted = search_index_hit_to_comparable(SearchIndexHit(
        provider="brave",
        query='"Justin Champagnie HR-JCH" sold ebay',
        title="2021 Chronicles Draft Picks Justin Champagnie HR-JCH PSA 10",
        url="https://www.ebay.com/itm/card/123456789012",
        snippet="Sold on Aug 12, 2026 · US $8.50",
    ))
    rejected = search_index_hit_to_comparable(SearchIndexHit(
        provider="brave",
        query='"Justin Champagnie HR-JCH" sold ebay',
        title="2021 Chronicles Draft Picks Justin Champagnie HR-JCH",
        url="https://www.ebay.com/itm/card/123456789013",
        snippet="Aug 12, 2026 · US $8.50 · Buy It Now",
    ))

    assert accepted is not None
    assert accepted.sale_price == Decimal("8.50")
    assert accepted.grade_company == "PSA"
    assert accepted.grade_value == Decimal("10")
    assert rejected is None


def test_third_party_queries_keep_exact_identity_anchors() -> None:
    queries = third_party_exact_search_queries(
        {
            "player_name": "Paolo Banchero",
            "product_name": "NBA Hoops",
            "set_name": "Oasis",
            "card_number": "O-17",
        }
    )

    assert queries == [
        "site:sportscardspro.com/game Paolo Banchero NBA Hoops Oasis O-17",
        "site:sportscardinvestor.com/cards Paolo Banchero NBA Hoops Oasis O-17",
    ]


def test_rich_recognition_becomes_review_candidate_without_serial_as_card_number() -> None:
    current = {
        "player_name": "Zach LaVine",
        "season_start": 2016,
        "season_end": 2017,
        "brand_name": "Panini",
        "product_name": "Preferred",
        "card_number": "53/61",
        "insert_name": None,
        "parallel_name": None,
        "serial_number": 53,
        "serial_total": 61,
        "is_rookie": False,
        "is_autograph": True,
        "autograph_type": "on_card",
        "is_memorabilia": True,
        "memorabilia_types": ["jersey"],
        "grade_company": None,
        "grade_value": None,
    }
    recognized = CardRecognitionResult(
        player_name="Zach LaVine",
        season_start=2016,
        season_end=2017,
        brand_name="Panini",
        product_name="Preferred Basketball",
        insert_name="Silhouettes Autograph Relics",
        card_number="53/60",
        serial_number=53,
        serial_total=60,
        is_rookie=False,
        is_autograph=True,
        autograph_type="on_card",
        is_memorabilia=True,
        memorabilia_types=["jersey"],
        confidence=Decimal("0.96"),
    )

    overrides = recognition_candidate_overrides(current, recognized)
    candidates = __import__(
        "card_platform.domain.identity_research", fromlist=["generate_candidates"]
    ).generate_candidates(current, {"identity_overrides": overrides, "confidence": "0.96"})

    assert overrides["card_number"] is None
    assert candidates[0]["is_valid"] is True
    assert candidates[0]["identity"]["serial_total"] == 60
    assert "None" not in candidates[0]["search_queries"][0]


def test_sportscardspro_sale_is_tier_three_aggregated_evidence() -> None:
    comp = AutomatedValuationWorker._normalize_public_comparable(
        _public_comparable(
            "https://www.sportscardspro.com/game/"
            "basketball-cards-2001-upper-deck-mjs-back/michael-jordan-mj-90",
            "aggregated_sale",
        )
    )
    assert comp is not None
    assert comp.source_code == "sportscardspro"
    assert comp.source_tier == 3
    assert comp.evidence_level == "aggregated_sale"


def test_sportscardspro_non_card_path_is_rejected() -> None:
    comp = AutomatedValuationWorker._normalize_public_comparable(
        _public_comparable(
            "https://www.sportscardspro.com/search-products?q=jordan",
            "aggregated_sale",
        )
    )
    assert comp is None


def test_sportscardspro_cloudflare_challenge_requires_browser_verification() -> None:
    async def run() -> str:
        transport = httpx.MockTransport(lambda request: httpx.Response(
            403,
            request=request,
            text=(
                "<title>Just a moment...</title>"
                "<script src='https://challenges.cloudflare.com/test'></script>"
            ),
        ))
        async with httpx.AsyncClient(transport=transport) as client:
            return await AutomatedValuationWorker._url_verification_status(
                client,
                "https://www.sportscardspro.com/game/"
                "basketball-cards-2001-upper-deck-mjs-back/michael-jordan-mj-90",
            )

    assert asyncio.run(run()) == "browser_required"


def test_ebay_item_akamai_denial_requires_browser_verification() -> None:
    async def run() -> str:
        transport = httpx.MockTransport(lambda request: httpx.Response(
            403,
            request=request,
            text="<title>Access Denied</title><p>Akamai security measure</p>",
        ))
        async with httpx.AsyncClient(transport=transport) as client:
            return await AutomatedValuationWorker._url_verification_status(
                client,
                "https://www.ebay.com/itm/card/123456789012",
            )

    assert asyncio.run(run()) == "browser_required"


def test_provider_priority_tiers_are_deterministic() -> None:
    assert AutomatedValuationWorker._source_tier("cardhobby") == 1
    assert AutomatedValuationWorker._source_tier("ebay_public") == 2
    assert AutomatedValuationWorker._source_tier("sportscardspro") == 3


def test_search_result_discards_incomplete_player_adjustment_pairs() -> None:
    result = ComparableSearchResult.model_validate(
        {
            "player_adjustments": [
                {
                    "target_player": "VJ Edgecombe",
                    "reference_player": "Comparable Rookie",
                    "segment": "rookie parallel",
                    "pairs": [
                        {
                            "target_title": "bad",
                            "target_url": "https://example.com/bad",
                            "target_price_base": None,
                            "reference_title": "reference",
                            "reference_url": "https://example.com/reference",
                            "reference_price_base": "100",
                            "condition_type": "raw",
                        }
                    ],
                }
            ]
        }
    )

    assert result.player_adjustments[0].pairs == []


def test_search_result_discards_zero_price_fallback_reference() -> None:
    result = ComparableSearchResult.model_validate(
        {
            "fallback_valuation": {
                "low_value": "10",
                "fair_value": "20",
                "high_value": "30",
                "confidence": "0.5",
                "methodology": "thin market model prior",
                "references": [
                    {
                        "title": "invalid free reference",
                        "source_url": "https://example.com/free",
                        "evidence_type": "analog_sale",
                        "observed_price_base": 0,
                        "match_confidence": "0.7",
                        "adjustment": "none",
                        "condition_type": "raw",
                        "player_name": "Example Player",
                    },
                    {
                        "title": "valid reference",
                        "source_url": "https://example.com/valid",
                        "evidence_type": "analog_sale",
                        "observed_price_base": 100,
                        "match_confidence": "0.7",
                        "adjustment": "none",
                        "condition_type": "raw",
                        "player_name": "Example Player",
                    },
                ],
            }
        }
    )

    assert [reference.title for reference in result.fallback_valuation.references] == [
        "valid reference"
    ]


def _fallback(reference_count: int, confidence: str = "0.60") -> FallbackValuation:
    references = [
        ValuationReference(
            title=f"Reference {index}",
            source_url=f"https://example.com/{index}",
            evidence_type="analog_sale",
            observed_price_base=Decimal("100"),
            match_confidence=Decimal("0.7"),
            adjustment="model analogy",
            condition_type="raw",
            player_name="Example Player",
        )
        for index in range(reference_count)
    ]
    return FallbackValuation(
        low_value=Decimal("70"),
        fair_value=Decimal("100"),
        high_value=Decimal("140"),
        confidence=Decimal(confidence),
        methodology="model prior",
        references=references,
    )


def test_model_fallback_confidence_is_capped_by_verifiable_references() -> None:
    confidence = AutomatedValuationWorker._model_fallback_confidence
    assert confidence(_fallback(0)) == Decimal("0")
    assert confidence(_fallback(1)) == Decimal("0.25")
    assert confidence(_fallback(2)) == Decimal("0.35")
    assert confidence(_fallback(2, "0.20")) == Decimal("0.20")


def test_source_outage_cannot_be_reported_as_zero_market_results() -> None:
    notes = AutomatedValuationWorker._truthful_source_availability_notes(
        [
            "来源诊断 ebay: source_limited, 抓取候选 0",
            "No completed eBay sales found for the exact card.",
            "卡淘检索完成",
        ],
        [{"provider": "ebay", "status": "source_limited"}],
    )

    assert "No completed eBay sales found for the exact card." not in notes
    assert "来源诊断 ebay: source_limited, 抓取候选 0" in notes
    assert any("无法判断是否存在已售记录" in note for note in notes)


def test_raw_card_rejects_all_graded_references() -> None:
    identity = {"grade_company": None, "grade_value": None}
    raw = ValuationReference(
        title="Raw sale",
        source_url="https://example.com/raw",
        evidence_type="analog_sale",
        observed_price_base=Decimal("100"),
        match_confidence=Decimal("0.8"),
        adjustment="same raw condition",
        condition_type="raw",
        player_name="Tim Duncan",
    )
    graded = raw.model_copy(
        update={
            "title": "PSA 10 sale",
            "condition_type": "graded",
            "grade_company": "PSA",
            "grade_value": Decimal("10"),
        }
    )
    assert AutomatedValuationWorker._grade_matches(identity, raw)
    assert not AutomatedValuationWorker._grade_matches(identity, graded)


def test_graded_card_requires_exact_company_and_grade() -> None:
    identity = {"grade_company": "PSA", "grade_value": Decimal("10")}
    exact = DiscoveredComparable(
        title="PSA 10 sale",
        source_url="https://example.com/psa10",
        occurred_at="2026-01-01T00:00:00Z",
        sale_price=Decimal("500"),
        currency="USD",
        fx_rate_to_base=Decimal("7"),
        grade_company="PSA",
        grade_value=Decimal("10"),
        match_confidence=Decimal("0.9"),
    )
    assert AutomatedValuationWorker._comparable_grade_matches(identity, exact)
    assert not AutomatedValuationWorker._comparable_grade_matches(
        identity, exact.model_copy(update={"grade_value": Decimal("9")})
    )
    assert not AutomatedValuationWorker._comparable_grade_matches(
        identity, exact.model_copy(update={"grade_company": "BGS"})
    )


def test_cross_player_coefficient_uses_median_of_at_least_three_pairs() -> None:
    identity = {
        "player_name": "Tim Duncan",
        "grade_company": None,
        "grade_value": None,
    }
    ratios = [(84, 100), (170, 200), (82, 100)]
    pairs = [
        PlayerPricePair(
            target_title=f"Duncan comp {index}",
            target_url=f"https://example.com/duncan-{index}",
            target_price_base=Decimal(target),
            reference_title=f"Giannis comp {index}",
            reference_url=f"https://example.com/giannis-{index}",
            reference_price_base=Decimal(reference),
            condition_type="raw",
        )
        for index, (target, reference) in enumerate(ratios)
    ]
    coefficients = AutomatedValuationWorker.build_player_coefficients(
        identity,
        [
            PlayerAdjustment(
                target_player="Tim Duncan",
                reference_player="Giannis Antetokounmpo",
                segment="premium patch /26-/75 raw",
                pairs=pairs,
            )
        ],
    )
    result = coefficients["giannis antetokounmpo"]
    assert result["coefficient"] == Decimal("0.84")
    assert result["sample_count"] == 3


def test_specific_inferred_identity_reaches_automatic_market_pricing() -> None:
    worker = object.__new__(AutomatedValuationWorker)
    calls = []

    async def load_identity(_job_id):
        return {
            "collection_item_id": uuid4(), "player_name": "Stephen Curry",
            "season_start": 2025, "season_end": 2026,
            "product_name": "Topps Basketball", "card_number": "SA-5",
            "insert_name": "Stars of the NBA", "parallel_name": None,
            "serial_number": None, "serial_total": None,
            "latest_identity_review_status": None,
            "confirmed_identity_version_id": None,
        }

    async def finish(_job_id, status, summary, *_args):
        calls.append(("finish", status, summary))

    async def set_status(_job_id, status):
        calls.append(("status", status))

    async def search(_identity):
        calls.append(("search", _identity["card_number"]))
        return "search-result"

    async def store(_identity, search_result):
        calls.append(("store", search_result))

    async def calculate(_job_id, _identity, search_result):
        calls.append(("calculate", search_result))

    worker._load_identity = load_identity
    worker._finish = finish
    worker._set_status = set_status
    worker._search = search
    worker._store_comparables = store
    worker._calculate = calculate
    asyncio.run(worker._process(uuid4()))

    assert ("search", "SA-5") in calls
    assert ("store", "search-result") in calls
    assert ("calculate", "search-result") in calls
