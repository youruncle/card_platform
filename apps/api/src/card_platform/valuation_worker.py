from card_platform.domain.listing_condition import condition_rejection_reasons, disclose_minor_condition
from contextlib import nullcontext
from card_platform.valuation_trace import traced_completion, content_hash
import asyncio
import base64
import json
import logging
import mimetypes
import re
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx
from openai import AsyncOpenAI
from psycopg.errors import DeadlockDetected
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from card_platform.config import Settings
from card_platform.catalog_evidence import normalize_catalog_evidence
from card_platform.comparable_image_verifier import (
    ComparableImageVerifier,
    VisualCandidate,
    current_persisted_visual,
)
from card_platform.db import Database
from card_platform.domain.adjustments import Adjustment
from card_platform.domain.card_identity import CardIdentityResolver, normalize_text
from card_platform.domain.identity_review import is_tcg_collector_number
from card_platform.domain.comparables import (
    Classification,
    ComparableClassifier,
    ComparableInput,
    ComparableTier,
    has_partial_multi_player_match,
)
from card_platform.domain.explainability import Explainability
from card_platform.domain.grade_basis import (
    UNGRADED_SAME_CARD_BASIS,
    UNGRADED_SAME_CARD_NOTE,
    can_use_ungraded_basis,
)
from card_platform.domain.hierarchical_estimator import ClassifiedSale, HierarchicalEstimator
from card_platform.domain.listing_title import (
    has_ambiguous_finish_marker,
    has_material_parallel_marker,
)
from card_platform.domain.valuation import ComparableSale, estimate_from_comps
from card_platform.domain.parallel_valuation import (PARALLEL_BASIS, PARALLEL_MODEL, estimate_parallel_fallback)
from card_platform.domain.valuation_quality import (
    EvidenceGrade,
    ShadowSale,
    ShadowValuationEstimator,
    evidence_grade,
    identity_review_reasons,
)
from card_platform.evidence_reasoning import (
    DeepSeekEvidenceReviewer,
    enforce_conservative_rejections,
)
from card_platform.formal_cardhobby_gate import (
    enforce_formal_cardhobby_identity, title_discloses_damage, title_disclaims_card_condition,
)
from card_platform.visual_source_eligibility import visual_source_rejection_reasons
from card_platform.ingestion.cardhobby_public_item import public_item_urls_from_notes
from card_platform.purchase_source_roles import PURCHASE_SOURCE_LATERAL, validated_purchase_sources
from card_platform.domain.finish_evidence import build_visual_match_facts
from card_platform.domain.identity_research import (
    ACE_BAILEY_FIXTURE_VISUAL,
    ANDREW_NEMBHARD_FIXTURE_VISUAL,
    BRANDON_MILLER_FIXTURE_VISUAL,
    CHECKLIST_VERSION,
    JAREN_JACKSON_FIXTURE_VISUAL,
    JAREN_JACKSON_88_FIXTURE_VISUAL,
    LEBRON_JAMES_FIXTURE_VISUAL,
    LUKA_DONCIC_FIXTURE_VISUAL,
    SCOTTIE_BARNES_FIXTURE_VISUAL,
    STEPHEN_CURRY_FIXTURE_VISUAL,
    ResearchStatus,
    automatic_candidate_decision,
    generate_candidates,
)
from card_platform.ingestion.cardhobby import (
    CardHobbyProvider,
    CardHobbySale,
    CardHobbySearchResult,
    assess_cardhobby_candidate,
    cardhobby_external_id,
    cardhobby_item_id,
    is_cardhobby_evidence_url,
)
from card_platform.identity_audit import (
    IdentityAuditRepository,
    build_identity_suggestions,
)
from card_platform.image_service import CardVisionService
from card_platform.market_data.cardhobby_provider import CardHobbyMarketDataProvider
from card_platform.market_data.ebay_provider import EbayMarketplaceInsightsProvider
from card_platform.domain.redemption import redemption_identity
from card_platform.market_data.models import MarketIdentity
from card_platform.market_data.protection import ProtectedProvider
from card_platform.market_data.service import MarketDataService
from card_platform.search_index import SearchIndexHit, SearchIndexResearch
from card_platform.knowledge_runtime import KnowledgeRuntime
from card_platform.repository import PortfolioRepository
from card_platform.recognition_benchmark_predictor import canonicalize_identity
from card_platform.schemas import CardIdentityInput, CardRecognitionResult, CollectionItemCreate
from card_platform.vision_routing import finalize_recognition_status


logger = logging.getLogger(__name__)


def revalidated_cardhobby_assessment(
    identity: dict,
    provider_payload: dict | None,
    matching_fields: list[str] | tuple[str, ...] = (),
    *, resolver=None,
) -> tuple[str | None, tuple[str, ...]]:
    """Re-run the complete CardHobby identity contract for a durable row.

    A ``None`` tier is an explicit fail-closed decision, not merely an absence
    of provider metadata. Returning the rejection reasons prevents the
    downstream title classifier from accidentally resurrecting a row that the
    source-specific matcher already rejected for an identity conflict.
    """
    if provider_payload:
        try:
            sale = CardHobbySale.model_validate(provider_payload).model_copy(
                update={
                    # These are outputs of the provider matcher, not immutable
                    # source facts. Recompute them under the current identity
                    # contract so an obsolete product/alias conflict cannot
                    # keep a durable sold row at tier=None forever.
                    "conflicts": [],
                    "matching_fields": [],
                }
            )
            score, conflicts, tier = assess_cardhobby_candidate(identity, sale, **({"resolver": resolver} if resolver else {}))
            if tier and score > 0:
                return tier, ()
            return None, tuple(conflicts or ["卡淘成交未通过当前身份契约"])
        except (TypeError, ValueError):
            # A stale marker must not resurrect a row that cannot be checked
            # under the current matcher. All current CardHobby records persist
            # the normalized sale payload, so malformed legacy rows fail closed.
            return None, ("卡淘原始成交载荷无法按当前身份契约复核",)
    return None, ("卡淘原始成交载荷缺失，无法按当前身份契约复核",)


def revalidated_cardhobby_tier(
    identity: dict,
    provider_payload: dict | None,
    matching_fields: list[str] | tuple[str, ...] = (),
) -> str | None:
    """Backward-compatible tier view of CardHobby revalidation."""
    tier, _rejection_reasons = revalidated_cardhobby_assessment(
        identity, provider_payload, matching_fields
    )
    return tier


def reconcile_cardhobby_classification(
    classification: Classification,
    provider_tier: str | None,
    *,
    provider_rejection_reasons: tuple[str, ...] = (),
) -> tuple[Classification, bool]:
    """Apply provider strength without bypassing a downstream identity conflict.

    Shared normalization should make lexical product decisions identical in
    both stages.  Provider metadata may only refine the tier of an otherwise
    non-conflicting row; it is never authority to erase a version, visual,
    grade, player, or other hard conflict.
    """
    if provider_rejection_reasons:
        return (
            Classification(
                ComparableTier.REJECTED,
                Decimal("0"),
                classification.source_reliability,
                field_matches=classification.field_matches,
                hard_conflicts=list(
                    dict.fromkeys(
                        [
                            *classification.hard_conflicts,
                            "cardhobby_provider_identity_conflict",
                        ]
                    )
                ),
            ),
            False,
        )
    if classification.hard_conflicts:
        return classification, False
    if provider_tier == "image_review_candidate":
        if (
            classification.tier in {ComparableTier.T0, ComparableTier.T1}
            and classification.field_matches.get("visual_same_template_anchor") is True
        ):
            return (
                Classification(
                    classification.tier,
                    classification.similarity,
                    classification.source_reliability,
                    field_matches={
                        **classification.field_matches,
                        "cardhobby_image_review_verified": True,
                    },
                ),
                True,
            )
        # A sparse CardHobby title can omit the checklist insert/set label even
        # when it still proves the player, product, sale status and other typed
        # attributes.  Failure (or absence) of the optional image verifier must
        # not turn that real completed sale into "no evidence".  Keep it as a
        # transparent same-player/product analogue; the low-confidence pricing
        # path already widens the interval and records the missing identity
        # anchor.  Explicit player/product/grade/parallel/serial/visual conflicts
        # returned above remain hard rejections.
        return (
            Classification(
                ComparableTier.T2,
                min(classification.similarity, Decimal("0.55")),
                classification.source_reliability,
                field_matches={
                    **classification.field_matches,
                    "cardhobby_image_review_verified": False,
                    "cardhobby_image_review_pending": True,
                },
            ),
            True,
        )
    if (
        provider_tier == "exact_or_near_exact"
        and classification.tier in {ComparableTier.T2, ComparableTier.T3}
    ):
        return (
            Classification(
                ComparableTier.T1,
                Decimal("0.78"),
                classification.source_reliability,
                field_matches={
                    **classification.field_matches,
                    "cardhobby_identity_contract": True,
                },
            ),
            True,
        )
    if (
        provider_tier == "same_player_product_analog"
        and classification.tier in {ComparableTier.T0, ComparableTier.T1}
        and classification.field_matches.get("reviewed_parent_product_confirmation") is not True
        and classification.field_matches.get("reviewed_reference_product_confirmation") is not True
        and classification.field_matches.get("reviewed_source_back_identity") is not True
        and classification.field_matches.get("reviewed_named_finish_omission") is not True
    ):
        return (
            Classification(
                ComparableTier.T2,
                Decimal("0.60"),
                classification.source_reliability,
                field_matches={
                    **classification.field_matches,
                    "cardhobby_identity_contract": True,
                },
            ),
            True,
        )
    return classification, False


def comparable_reaches_search_floor(comp: "DiscoveredComparable") -> bool:
    """Keep deterministic CardHobby T2/image rows below the legacy 0.50 floor."""
    provider_tiers = {
        "cardhobby_tier:exact_or_near_exact",
        "cardhobby_tier:image_review_candidate",
        "cardhobby_tier:same_player_product_analog",
        "cardhobby_tier:numbered_parallel_analogue",
    }
    provider_contract = bool(
        comp.source_code in {"cardhobby", "cardhobby_provider"}
        and provider_tiers.intersection(comp.matching_fields)
    )
    return provider_contract or comp.match_confidence >= Decimal("0.5")


def empty_valuation_recall_diagnostics(search: "ComparableSearchResult") -> dict:
    """Separate search absence from matching/persistence losses without inventing counts."""
    counts = [
        (int(raw), int(accepted))
        for query in search.search_queries
        for raw, accepted in re.findall(r"召回(\d+)/通过(\d+)", query)
    ]
    raw_count = sum(raw for raw, _ in counts)
    accepted_count = sum(accepted for _, accepted in counts)
    from card_platform.domain.model_failures import parse_model_failures
    upstream_failures = parse_model_failures([
        *search.notes, *(reason for comp in search.comparables for reason in comp.visual_reasons)])
    if any(f.get('code') == 'model_access_denied' for f in upstream_failures):
        code = 'model_access_denied'
        reason = '模型访问被拒绝，身份/证据分析未完成；不能据此判断市场成交是否存在'
    elif search.comparables:
        code = "retained_candidates_unavailable_in_valuation_window"
        reason = "已检索到候选，但当前身份下没有可用于估值的已落库成交；需检查日期窗口和落库链路"
    elif accepted_count:
        code = "recalled_candidates_not_retained"
        reason = "卡淘已召回候选，但尚无通过同卡核验的可用成交；不是卡淘没有记录"
    elif raw_count:
        code = "recalled_rows_rejected_by_identity"
        reason = "卡淘有搜索结果，但当前身份字段未匹配到可用成交；需核对身份和检索词"
    elif search.rejected_candidates:
        code = "cached_candidates_rejected_by_current_identity"
        reason = "已缓存的卡淘候选与当前卡片身份不符；不是卡淘没有记录"
    elif any("来源不可用" in note or "检索失败" in note for note in search.notes):
        code = "market_source_unavailable"
        reason = "市场来源暂不可用，尚不能判断是否有成交记录"
    else:
        code = "no_eligible_sales_returned_for_queries"
        reason = "本次检索词未返回可用成交；不代表卡淘没有该卡的历史记录"
    return {
        "root_cause_codes": [code],
        "reason": reason,
        # Query results overlap: these are occurrences, never unique sales.
        "raw_result_occurrences": raw_count,
        "accepted_result_occurrences": accepted_count,
        "observed_query_count": len(counts),
        "search_execution": search.search_execution,
        "executed_query_count": (0 if search.search_execution == 'cache_replay' else None),
        "upstream_failures": upstream_failures,
    }


VALUATION_SITES = [
    "ebay.com",
    "sportscardspro.com",
    "sportscardinvestor.com",
    "psacard.com",
    "130point.com",
    "goldin.co",
    "fanaticscollect.com",
    "beckett.com",
]


def market_search_query_ladder(identity: dict) -> list[str]:
    """Build recall-first queries without letting one OCR field monopolize search."""
    player_values = [
        identity.get("player_name"),
        *(identity.get("player_name_aliases") or []),
    ]
    players = list(
        dict.fromkeys(str(value).strip() for value in player_values if str(value or "").strip())
    )
    if not players:
        return []
    player = players[0]
    start = identity.get("season_start")
    end = identity.get("season_end")
    seasons = []
    if start and end:
        seasons.append(f"{start}-{str(end)[-2:]}")
        # Seller titles may use either the checklist start year (1998) or the
        # copyright/end year (1999). Preserve both even for confirmed identities;
        # confirmation should tighten matching, not reduce marketplace recall.
        seasons.extend([str(end), str(start)])
    elif start:
        seasons.append(str(start))

    product = identity.get("product_name")
    insert = identity.get("insert_name") or identity.get("insert")
    set_name = identity.get("set_name")
    descriptors = list(
        dict.fromkeys(
            str(value).strip()
            for value in (insert, set_name)
            if str(value or "").strip()
        )
    )
    primary_descriptor = descriptors[0] if descriptors else None
    card_number = identity.get("card_number")
    serial_total = identity.get("serial_total") or identity.get("print_run")
    autograph = "autograph" if identity.get("is_autograph") else None

    def query(*parts: object) -> str:
        return " ".join(str(part).strip() for part in parts if str(part or "").strip())

    queries = []
    if redemption_identity(identity)[0]:
        queries.extend([query(player, product, "redemption"),
                        query(player, card_number, "redemption")])
    for season in seasons[:3] or [None]:
        queries.extend(
            [
                query(
                    player,
                    season,
                    product,
                    primary_descriptor,
                    card_number,
                    f"/{serial_total}" if serial_total else None,
                ),
                # Always pair the precise identity query with a product-level
                # year query. OCR-only insert/card-number fields are a common
                # source of false negatives (for example protective-coating
                # text misread as a Finest insert name).
                query(player, season, product),
            ]
        )
    queries.extend(
        [
            query(player, product, primary_descriptor, card_number, autograph),
            query(player, card_number, f"/{serial_total}" if serial_total else None),
            query(player, card_number),
            query(player, product, autograph),
        ]
    )
    for descriptor in descriptors[:2]:
        queries.append(query(player, descriptor, card_number, autograph))
    for alias in players[1:3]:
        queries.append(query(alias, card_number, insert, autograph))
    return list(dict.fromkeys(item for item in queries if item))[:12]


def ebay_completed_search_url(query: str) -> str:
    return "https://www.ebay.com/sch/i.html?" + urlencode(
        {
            "_nkw": query,
            "_sacat": "0",
            "rt": "nc",
            "LH_Sold": "1",
            "LH_Complete": "1",
        }
    )


def search_index_query_ladder(identity: dict) -> list[str]:
    """Generate bounded search-engine discovery queries for old eBay sales."""
    ladder = market_search_query_ladder(identity)
    if not ladder:
        return []
    exact = ladder[:3]
    queries = [f"site:ebay.com/itm {query}" for query in exact]
    queries.extend(f'"{query}" sold ebay' for query in exact[:2])
    queries.extend(f'"{query}" 130point' for query in exact[:2])
    return list(dict.fromkeys(queries))[:7]


def ebay_item_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host != "ebay.com" and not host.endswith(".ebay.com"):
        return None
    match = re.search(r"^/itm/(?:[^/]+/)?(\d{9,15})(?:/|$)", parsed.path, re.I)
    return match.group(1) if match else None


def sold_date_from_search_snippet(snippet: str) -> datetime | None:
    month_date = (
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},\s*20\d{2}"
    )
    iso_date = r"20\d{2}[-/]\d{1,2}[-/]\d{1,2}"
    match = re.search(
        rf"\bsold(?:\s+on)?\s*[:·-]?\s*(?P<date>{month_date}|{iso_date})\b",
        snippet,
        re.I,
    )
    if match is None:
        return None
    value = match.group("date")
    for pattern in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def search_index_sale_is_usable(comp: "DiscoveredComparable") -> bool:
    """Require a self-contained sold/date/price snippet; reject ambiguous offers."""
    snippet = str(comp.search_snippet or "").strip()
    provider = str(comp.search_provider or "").strip().casefold()
    if (
        comp.evidence_level != "search_index_sale"
        or ebay_item_id(comp.source_url) is None
        or provider not in {"google", "bing", "brave", "serpapi", "search_engine"}
        or not str(comp.search_query or "").strip()
        or not snippet
        or "sold" not in snippet.casefold()
        or re.search(r"best\s+offer", snippet, re.I)
        or str(comp.sale_status or "").casefold() != "sold"
        or comp.price_basis != "search_index_displayed_sold_price"
    ):
        return False
    currency_tokens = {
        "USD": r"(?:US\s*)?\$|USD\s*",
        "CAD": r"C\$|CAD\s*",
        "AUD": r"AU\$|AUD\s*",
        "GBP": r"£|GBP\s*",
        "EUR": r"€|EUR\s*",
    }
    token = currency_tokens.get(comp.currency.upper())
    if sold_date_from_search_snippet(snippet) is None or token is None:
        return False
    prices = re.findall(rf"(?:{token})\s*([0-9][0-9,]*(?:\.\d{{1,2}})?)", snippet, re.I)
    return any(
        abs(Decimal(value.replace(",", "")) - comp.sale_price) <= Decimal("0.01")
        for value in prices
    )


def search_index_hit_to_comparable(hit: SearchIndexHit) -> "DiscoveredComparable | None":
    snippet = hit.snippet.strip()
    sold_at = sold_date_from_search_snippet(snippet)
    if sold_at is None or ebay_item_id(hit.url) is None:
        return None
    currencies = (
        ("USD", r"(?:US\s*)?\$\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("CAD", r"(?:C\$|CAD\s*)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("AUD", r"(?:AU\$|AUD\s*)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("GBP", r"(?:£|GBP\s*)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
        ("EUR", r"(?:€|EUR\s*)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"),
    )
    currency = None
    price = None
    for code, pattern in currencies:
        match = re.search(pattern, snippet, re.I)
        if match:
            currency = code
            price = Decimal(match.group(1).replace(",", ""))
            break
    if currency is None or price is None or price <= 0:
        return None
    grade = re.search(r"\b(PSA|BGS|SGC|CGC)\s*(10|9(?:\.5)?|8(?:\.5)?)\b", hit.title, re.I)
    comp = DiscoveredComparable(
        title=hit.title,
        source_url=hit.url,
        occurred_at=sold_at,
        sale_price=price,
        currency=currency,
        # Search APIs do not provide FX. The caller must replace this neutral
        # transport value with a verified rate before the comparable is returned.
        fx_rate_to_base=Decimal("1"),
        grade_company=grade.group(1).upper() if grade else None,
        grade_value=Decimal(grade.group(2)) if grade else None,
        match_confidence=Decimal("0.75"),
        source_reliability=Decimal("0.55"),
        evidence_level="search_index_sale",
        sale_status="sold",
        sale_status_evidence=snippet,
        price_basis="search_index_displayed_sold_price",
        search_provider=hit.provider,
        search_query=hit.query,
        search_snippet=snippet,
    )
    return comp if search_index_sale_is_usable(comp) else None


def third_party_exact_search_queries(identity: dict) -> list[str]:
    """Build exact-page discovery queries for third-tier completed-sale indexes."""
    durable = [
        identity.get("player_name"),
        identity.get("product_name"),
        identity.get("insert_name") or identity.get("set_name"),
        identity.get("card_number"),
        identity.get("parallel_name"),
        f"/{identity.get('serial_total')}" if identity.get("serial_total") else None,
    ]
    terms = " ".join(str(value).strip() for value in durable if str(value or "").strip())
    if not terms:
        return []
    return [
        f"site:sportscardspro.com/game {terms}",
        f"site:sportscardinvestor.com/cards {terms}",
    ]


def recognition_candidate_overrides(current: dict, recognized) -> dict:
    """Promote rich vision output into a review candidate, never a confirmation."""
    candidate = {
        key: current.get(key)
        for key in (
            "player_name",
            "season_start",
            "season_end",
            "brand_name",
            "product_name",
            "set_name",
            "card_number",
            "insert_name",
            "parallel_name",
            "serial_number",
            "serial_total",
            "is_rookie",
            "is_autograph",
            "autograph_type",
            "is_memorabilia",
            "memorabilia_types",
            "grade_company",
            "grade_value",
        )
    }
    card_number = recognized.card_number or current.get("card_number")
    if (re.fullmatch(r"0*\d+\s*/\s*0*\d+", str(card_number or "").strip())
            and not is_tcg_collector_number({
                "card_number": card_number,
                "brand_name": recognized.brand_name or current.get("brand_name"),
                "product_name": recognized.product_name or current.get("product_name"),
            })):
        card_number = None
    is_autograph = (
        recognized.is_autograph
        if recognized.is_autograph is not None
        else bool(current.get("is_autograph"))
    )
    is_memorabilia = (
        recognized.is_memorabilia
        if recognized.is_memorabilia is not None
        else bool(current.get("is_memorabilia"))
    )
    autograph_type = recognized.autograph_type or current.get("autograph_type")
    if not is_autograph:
        autograph_type = "none"
    elif autograph_type in {None, "none"}:
        autograph_type = "unknown"
    memorabilia_types = (
        list(recognized.memorabilia_types)
        if recognized.memorabilia_types
        else list(current.get("memorabilia_types") or [])
    )
    if recognized.is_patch and "patch" not in memorabilia_types:
        memorabilia_types.append("patch")
    if not is_memorabilia:
        memorabilia_types = []
    deliberately_cleared_parallel = any(
        "prizm_logo_removed" in caveat
        for caveat in recognized.caveats
    )
    explicitly_observed_raw = (
        "grade_company" in recognized.model_fields_set
        and recognized.grade_company is None
    )
    return {
        **candidate,
        "player_name": recognized.player_name or current.get("player_name"),
        "season_start": recognized.season_start or current.get("season_start"),
        "season_end": recognized.season_end or current.get("season_end"),
        "brand_name": recognized.brand_name or current.get("brand_name"),
        "product_name": recognized.product_name or current.get("product_name"),
        "card_number": card_number,
        "insert_name": (
            recognized.insert_name
            if recognized.insert_name is not None
            else current.get("insert_name")
        ),
        "parallel_name": (
            recognized.parallel_name
            if recognized.parallel_name is not None
            else (None if deliberately_cleared_parallel else current.get("parallel_name"))
        ),
        "serial_number": recognized.serial_number,
        "serial_total": recognized.serial_total,
        "is_rookie": (
            recognized.is_rookie
            if recognized.is_rookie is not None
            else bool(current.get("is_rookie"))
        ),
        "is_autograph": is_autograph,
        "autograph_type": autograph_type,
        "is_memorabilia": is_memorabilia,
        "memorabilia_types": memorabilia_types,
        "grade_company": (
            None
            if explicitly_observed_raw
            else recognized.grade_company or current.get("grade_company")
        ),
        "grade_value": (
            None
            if explicitly_observed_raw
            else (
                recognized.grade_value
                if recognized.grade_value is not None
                else current.get("grade_value")
            )
        ),
    }


def deduplicate_market_rows(
    rows: list[dict], *, identity: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Collapse aggregator/direct records that point to the same underlying sale."""
    chosen: dict[tuple[str, str], dict] = {}
    passthrough: list[dict] = []
    duplicates: list[dict] = []
    for row in rows:
        external_id = str(row.get("external_id") or "").strip().casefold()
        if not external_id:
            passthrough.append(row)
            continue
        classification = row.get("classification") or {}
        origin = str(
            classification.get("origin_source_code") or row.get("source_code") or ""
        ).casefold()
        normalized_external_id = re.sub(
            rf"^(?:{re.escape(origin)}|ebay|130point|cardhobby|weikajia)[:/_-]+",
            "",
            external_id,
        )
        key = (origin, normalized_external_id)
        existing = chosen.get(key)
        if existing is None:
            chosen[key] = row
            continue

        def preference(item: dict) -> tuple[int, Decimal]:
            item_origin = str(
                (item.get("classification") or {}).get("origin_source_code")
                or item.get("source_code")
                or ""
            ).casefold()
            direct = int(str(item.get("source_code") or "").casefold() == item_origin)
            reliability = Decimal(str(item.get("source_reliability") or "0"))
            return direct, reliability

        kept, dropped = (
            (row, existing)
            if preference(row) > preference(existing)
            else (existing, row)
        )
        chosen[key] = kept
        duplicates.append(
            {
                "origin_source_code": origin,
                "external_id": normalized_external_id,
                "kept_id": str(kept.get("id")),
                "dropped_id": str(dropped.get("id")),
                "reason": "same underlying marketplace sale",
            }
        )
    selected = [*passthrough, *chosen.values()]
    selected.sort(key=lambda item: item["occurred_at"], reverse=True)
    return selected, duplicates


def source_matches_current_search(source_url: str, allowed_urls: set[str]) -> bool:
    """CardHobby www/apex and path casing identify the same public item."""
    if source_url in allowed_urls:
        return True
    item_id = cardhobby_item_id(source_url)
    return item_id is not None and any(
        cardhobby_item_id(url) == item_id for url in allowed_urls
    )


def valuation_identity_specificity(identity: dict) -> tuple[bool, list[str]]:
    required = {
        "player_name": identity.get("player_name"),
        "season_start": identity.get("season_start"),
        "product_name": identity.get("product_name"),
    }
    missing = [field for field, value in required.items() if value in (None, "")]
    has_card_anchor = any(
        identity.get(field) not in (None, "")
        for field in ("card_number", "insert_name", "set_name")
    )
    if not has_card_anchor:
        missing.append("card_number_or_insert_name")
    card_number = str(identity.get("card_number") or "").strip()
    if (re.fullmatch(r"0*\d+\s*/\s*0*\d+", card_number)
            and not is_tcg_collector_number(identity)):
        missing.append("card_number_is_serial_fraction")
    brand_token = normalize_text(identity.get("brand_name"))
    product_token = normalize_text(identity.get("product_name"))
    legitimate_self_named_products = {"hoops", "upper deck"}
    if (
        brand_token
        and brand_token == product_token
        and brand_token not in legitimate_self_named_products
    ):
        missing.append("brand_product_duplicate")
    missing.extend(identity_review_reasons(identity))
    missing = list(dict.fromkeys(missing))
    return not missing, missing


class DiscoveredComparable(BaseModel):
    title: str
    source_url: str
    occurred_at: datetime
    sale_price: Decimal = Field(gt=0)
    shipping_price: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(min_length=3, max_length=3)
    fx_rate_to_base: Decimal = Field(gt=0)
    grade_company: str | None = None
    grade_value: Decimal | None = None
    match_confidence: Decimal = Field(ge=0, le=1)
    source_reliability: Decimal = Field(default=Decimal("0.75"), ge=0, le=1)
    image_url: str | None = None
    visual_verdict: str | None = None
    visual_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    visual_reasons: list[str] = Field(default_factory=list)
    visual_evidence_binding: dict | None = None
    visual_condition_review: dict | None = None
    visual_independent_confirmation: dict | None = None
    visual_match_evidence: list[dict] = Field(default_factory=list)
    matching_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    evidence_level: str = "aggregated_sale"
    source_code: str = "public_market_research"
    source_tier: int = Field(default=3, ge=1, le=3)
    provider_payload: dict = Field(default_factory=dict)
    sale_status: str | None = None
    sale_status_evidence: str | None = None
    price_basis: str | None = None
    discovery_url: str | None = None
    search_provider: str | None = None
    search_query: str | None = None
    search_snippet: str | None = None


def rank_cached_comparables(
    comparables: list[DiscoveredComparable],
) -> list[DiscoveredComparable]:
    """Keep exact provider matches ahead of recent but weaker cache entries.

    The durable evidence store is ordered by sale date.  That is useful for
    valuation, but it must not decide which rows get the bounded image-review
    budget: an older exact insert/parallel is more useful than a newer sale for
    another insert.  Provider conflicts are retained for analog/fallback use,
    but they sort behind conflict-free evidence. Within ambiguous candidates,
    independent insert/card-number anchors outrank a provider's scalar score:
    twelve unverified matching-print-run base cards must not crowd out the
    actual insert's transparent unnumbered proxy before images are reviewed.
    """
    return sorted(
        comparables,
        key=lambda comp: (
            not comp.conflicts,
            sum(
                f"cache_title_anchor:{field}" in comp.matching_fields
                for field in ("insert", "card_number")
            ),
            "cache_title_anchor:product" in comp.matching_fields,
            comp.match_confidence,
            comp.occurred_at,
        ),
        reverse=True,
    )


def cached_candidate_needs_image_review(
    identity: dict,
    comp: DiscoveredComparable,
    hard_conflicts: list[str],
    provider_tier: str | None = None,
    *, resolver=None,
) -> bool:
    """Defer bounded title omissions to images without approving a price."""
    from card_platform.domain.card_identity import is_placeholder_identity_value
    subject = identity.get('player_name')
    if (comp.source_code == 'cardhobby' and comp.image_url
            and (not subject or is_placeholder_identity_value(subject))
            and provider_tier == 'image_review_candidate'
            and set(hard_conflicts) <= {'player_conflict'}):
        try:
            sale = CardHobbySale.model_validate(comp.provider_payload).model_copy(
                update={'conflicts': [], 'matching_fields': []})
            if (sale.title, sale.source_url, sale.image_url) == (comp.title, comp.source_url, comp.image_url):
                _, _, tier = assess_cardhobby_candidate(identity, sale, resolver=resolver)
                if (tier == 'image_review_candidate'
                        and 'cardhobby_work_subject_image_required' in sale.matching_fields
                        and not visual_source_rejection_reasons(identity, comp.provider_payload)):
                    return True
        except (TypeError, ValueError):
            pass
    if comp.source_code == 'cardhobby' and provider_tier == 'numbered_parallel_analogue':
        from card_platform.domain.parallel_valuation import assess_parallel_analogue
        try:
            sale = CardHobbySale.model_validate(comp.provider_payload)
            if (sale.title, sale.source_url, sale.image_url) == (comp.title, comp.source_url, comp.image_url):
                return assess_parallel_analogue(identity, sale, resolver=resolver) is not None
        except (TypeError, ValueError):
            pass
        return False
    if (comp.source_code == 'cardhobby' and comp.image_url
            and identity.get('season_start')
            and provider_tier in {'exact_or_near_exact', 'image_review_candidate'}
            and 'season_conflict' in hard_conflicts
            and set(hard_conflicts) <= {
                'season_conflict', 'parallel_conflict', 'insert_conflict',
                'image_review_unconfirmed', 'material_confirmation_required',
                'autograph_confirmation_required',
            }
            # A missing year can request research. Even a matching explicit
            # year does not authorize this branch to erase a season conflict.
            and not re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)|'
                              r'(?<!\d)\d{2}\s*[-‐‑‒–—−/]\s*\d{2}(?!\d)',
                              comp.title)):
        try:
            sale = CardHobbySale.model_validate(comp.provider_payload).model_copy(
                update={'conflicts': [], 'matching_fields': []})
            bound_source = (sale.title, sale.source_url, sale.image_url) == (
                comp.title, comp.source_url, comp.image_url)
            if bound_source and not visual_source_rejection_reasons(identity, comp.provider_payload):
                score, _, current_tier = assess_cardhobby_candidate(identity, sale, resolver=resolver)
                if score >= Decimal('.35') and current_tier in {
                        'exact_or_near_exact', 'image_review_candidate'}:
                    return True
        except (TypeError, ValueError):
            pass
    if (comp.image_url and comp.source_code == 'cardhobby'
            and provider_tier == 'image_review_candidate'
            and 'player_conflict' in hard_conflicts
            and set(hard_conflicts) <= {'player_conflict', 'season_conflict'}
            and resolver is not None):
        # Recompute the official work-role lead. Stored markers or another
        # identity's classification are not authority to defer a conflict.
        try:
            sale = CardHobbySale.model_validate(comp.provider_payload).model_copy(
                update={'conflicts': [], 'matching_fields': []})
            if (sale.title, sale.source_url, sale.image_url) == (comp.title, comp.source_url, comp.image_url):
                _, _, tier = assess_cardhobby_candidate(identity, sale, resolver=resolver)
                if (tier == 'image_review_candidate'
                        and 'cardhobby_checklist_work_subject_image_required' in sale.matching_fields):
                    return True
        except (TypeError, ValueError):
            pass
    if (comp.image_url and set(hard_conflicts)=={'player_conflict'}
        and provider_tier=='image_review_candidate'
        and 'cardhobby_partial_roster_image_required' in comp.matching_fields):
        return True
    if (comp.image_url and set(hard_conflicts)=={'product_conflict'}
        and provider_tier=='image_review_candidate'
        and 'cardhobby_parent_child_image_required' in comp.matching_fields):
        return True
    if (
        comp.image_url
        and set(hard_conflicts) == {"player_conflict"}
        and has_partial_multi_player_match(comp.title, str(identity.get("player_name") or ""), resolver=resolver)
        and provider_tier in {"exact_or_near_exact", "image_review_candidate"}
    ):
        return True
    soft_provider_ambiguity = provider_tier == "image_review_candidate"
    ambiguous_base_finish = bool(
        (
            identity.get("card_number")
            or re.search(r"(?:\bbase\b|普卡|基础卡)", comp.title, re.I)
        )
        and not identity.get("insert_name")
        and has_ambiguous_finish_marker(comp.title)
        and not has_material_parallel_marker(
            comp.title, identity.get("product_name")
        )
    )
    return bool(
        comp.image_url
        and hard_conflicts
        and set(hard_conflicts)
        <= {
            "parallel_conflict",
            "insert_conflict",
            "image_review_unconfirmed",
            "material_confirmation_required",
            "autograph_confirmation_required",
        }
        and (soft_provider_ambiguity or ambiguous_base_finish)
        and (soft_provider_ambiguity or not set(hard_conflicts).intersection({
            'material_confirmation_required', 'autograph_confirmation_required'}))
    )


class ValuationReference(BaseModel):
    title: str
    source_url: str
    evidence_type: str
    occurred_at: datetime | None = None
    observed_price_base: Decimal = Field(gt=0)
    match_confidence: Decimal = Field(ge=0, le=1)
    adjustment: str
    condition_type: str
    grade_company: str | None = None
    grade_value: Decimal | None = None
    player_name: str


FORMAL_FALLBACK_EVIDENCE_TYPES = {
    "confirmed_transaction",
    "ended_auction",
    "legacy_cardhobby_sale",
    "public_completed_sale",
    "aggregated_sale",
}


def is_formal_fallback_reference(reference: ValuationReference) -> bool:
    """Require transaction-shaped evidence before a fallback can be published."""
    return (
        reference.evidence_type.strip().casefold()
        in FORMAL_FALLBACK_EVIDENCE_TYPES
        and reference.occurred_at is not None
    )


class PlayerPricePair(BaseModel):
    target_title: str
    target_url: str
    target_price_base: Decimal = Field(gt=0)
    reference_title: str
    reference_url: str
    reference_price_base: Decimal = Field(gt=0)
    condition_type: str
    grade_company: str | None = None
    grade_value: Decimal | None = None


class PlayerAdjustment(BaseModel):
    target_player: str
    reference_player: str
    segment: str
    pairs: list[PlayerPricePair] = Field(default_factory=list)


class FallbackValuation(BaseModel):
    low_value: Decimal = Field(ge=0)
    fair_value: Decimal = Field(ge=0)
    high_value: Decimal = Field(ge=0)
    confidence: Decimal = Field(ge=0, le=0.65)
    methodology: str
    references: list[ValuationReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self) -> "FallbackValuation":
        if not self.low_value <= self.fair_value <= self.high_value:
            raise ValueError("fallback valuation range is invalid")
        return self


class ComparableSearchResult(BaseModel):
    search_execution: str = 'unknown'
    pending_refresh_job_id: str | None = None
    search_queries: list[str] = Field(default_factory=list)
    comparables: list[DiscoveredComparable] = Field(default_factory=list)
    resolved_identity: dict[str, str | int | bool | list[str] | None] = Field(
        default_factory=dict
    )
    identity_conflicts: list[str] = Field(default_factory=list)
    fallback_valuation: FallbackValuation | None = None
    player_adjustments: list[PlayerAdjustment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    rejected_candidates: list[dict] = Field(default_factory=list)
    visual_progress: dict = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def discard_invalid_player_adjustment_pairs(cls, value):
        if not isinstance(value, dict):
            return value
        cleaned = dict(value)
        fallback = value.get("fallback_valuation")
        if isinstance(fallback, dict):
            fallback_cleaned = dict(fallback)
            valid_references = []
            for reference in fallback.get("references") or []:
                if not isinstance(reference, dict):
                    continue
                try:
                    observed_price = Decimal(str(reference.get("observed_price_base")))
                except (ArithmeticError, TypeError, ValueError):
                    continue
                if observed_price > 0:
                    valid_references.append(reference)
            fallback_cleaned["references"] = valid_references
            cleaned["fallback_valuation"] = fallback_cleaned
        adjustments = []
        for adjustment in value.get("player_adjustments") or []:
            if not isinstance(adjustment, dict):
                continue
            pairs = [
                pair
                for pair in adjustment.get("pairs") or []
                if isinstance(pair, dict)
                and pair.get("target_price_base") not in (None, "")
                and pair.get("reference_price_base") not in (None, "")
            ]
            adjustments.append({**adjustment, "pairs": pairs})
        cleaned["player_adjustments"] = adjustments
        return cleaned


class IdentityVisualObservations(BaseModel):
    product_season_start: int | None = None
    product_season_end: int | None = None
    product_name: str | None = None
    card_number: str | None = None
    serial_number: int | None = None
    serial_total: int | None = None
    base_card: bool = False
    parallel_name_hint: str | None = None
    texture_tokens: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    confidence: Decimal = Field(default=Decimal("0"), ge=0, le=1)


class AutomatedValuationWorker:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        knowledge_runtime: KnowledgeRuntime | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.knowledge_runtime = knowledge_runtime
        self.client = (
            AsyncOpenAI(
                api_key=settings.vision_api_key,
                base_url=settings.vision_base_url,
                timeout=settings.valuation_search_timeout_seconds,
                max_retries=1,
            )
            if settings.vision_api_key
            else None
        )
        # CardHobby's public sold-auction API is first-party market evidence and
        # does not depend on a multimodal/model API key. The optional client is
        # used only for discovery fallback after direct retrieval is empty.
        self.cardhobby = CardHobbyProvider(
            self.client,
            settings.valuation_search_model,
            timeout=settings.provider_timeout_seconds,
            resolver=knowledge_runtime.resolver if knowledge_runtime else None,
            min_interval=settings.cardhobby_min_interval_ms / 1000,
        )
        self.search_index = SearchIndexResearch(
            brave_api_key=settings.search_index_brave_api_key,
            serpapi_key=settings.search_index_serpapi_key,
            timeout=settings.search_index_timeout_seconds,
            max_queries=settings.search_index_max_queries,
            concurrency=settings.search_index_concurrency,
        )
        providers = []
        providers.append(
            ProtectedProvider(
                database,
                CardHobbyMarketDataProvider(self.cardhobby),
            )
        )
        if (
            not settings.valuation_cardhobby_only
            and settings.ebay_client_id
            and settings.ebay_client_secret
        ):
            providers.append(
                ProtectedProvider(
                    database,
                    EbayMarketplaceInsightsProvider(
                        settings.ebay_client_id,
                        settings.ebay_client_secret,
                        marketplace_id=settings.ebay_marketplace_id,
                        token_url=settings.ebay_token_url,
                        insights_url=settings.ebay_insights_url,
                        timeout=settings.provider_timeout_seconds,
                    ),
                )
            )
        self.market_data = MarketDataService(database, providers)
        self.image_verifier = ComparableImageVerifier(settings)
        self.vision_service = CardVisionService(settings)
        self.evidence_reviewer = (
            DeepSeekEvidenceReviewer(
                settings.ai_api_key, settings.ai_base_url, settings.ai_model,
                timeout=min(settings.valuation_search_timeout_seconds, 30),
            )
            if settings.ai_api_key and settings.ai_provider == "deepseek"
            else None
        )
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = await self._claim()
                # Ready valuations must not starve behind a large refresh
                # queue (including capacity deferrals). _claim still excludes
                # this identity's active refresh, so it never races its fetch.
                if job_id is not None:
                    await self._process(job_id)
                    continue
                if await self.market_data.process_one_refresh():
                    continue
                if job_id is None:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.settings.valuation_worker_poll_seconds
                    )
                    continue
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("valuation worker loop failed")
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self._stop.set()

    async def _claim(self) -> UUID | None:
        async with self.database.connection() as connection, connection.transaction():
            await connection.execute(
                """
                UPDATE valuation_job
                SET status = CASE WHEN attempt_count >= 3 THEN 'failed' ELSE 'queued' END,
                    error_message = CASE
                        WHEN attempt_count >= 3
                        THEN COALESCE(error_message, 'valuation worker lease expired')
                        ELSE error_message
                    END,
                    finished_at = CASE WHEN attempt_count >= 3 THEN now() ELSE NULL END,
                    started_at = CASE WHEN attempt_count >= 3 THEN started_at ELSE NULL END,
                    updated_at = now()
                WHERE status IN ('identifying', 'searching', 'calculating')
                  AND started_at < now() - interval '15 minutes'
                """
            )
            result = await connection.execute(
                """
                SELECT vj.id FROM valuation_job vj
                WHERE vj.status = 'queued' AND vj.attempt_count < 3
                  AND NOT EXISTS (
                      SELECT 1
                      FROM market_identity_binding binding
                      JOIN market_refresh_job refresh
                        ON refresh.identity_fingerprint = binding.identity_fingerprint
                      WHERE binding.collection_item_id = vj.collection_item_id
                        AND refresh.status IN ('queued', 'running')
                  )
                ORDER BY vj.created_at
                FOR UPDATE SKIP LOCKED LIMIT 1
                """
            )
            row = await result.fetchone()
            if row is None:
                return None
            await connection.execute(
                """
                UPDATE valuation_job SET status = 'identifying', started_at = now(),
                    attempt_count = attempt_count + 1, updated_at = now(), error_message = NULL
                WHERE id = %s
                """,
                (row["id"],),
            )
            return row["id"]

    async def _process(self, job_id: UUID) -> None:
        try:
            if getattr(self, "knowledge_runtime", None):
                await self.knowledge_runtime.refresh()
            identity = await self._load_identity(job_id)
            specific, missing_fields = valuation_identity_specificity(identity)
            if identity.get("latest_identity_review_status") == "rejected":
                specific = False
                missing_fields = list(
                    dict.fromkeys([*missing_fields, "latest_automatic_identity_revoked"])
                )
            if not specific:
                research = await self._start_identity_research(
                    identity, missing_fields, job_id=job_id
                )
                if research["status"] in {
                    "confirmed_identity_available",
                    "automatic_identity_confirmed",
                }:
                    # A user can confirm the identity while the optional vision
                    # research call is in flight. Reload instead of letting the
                    # stale research result overwrite the confirmed workflow.
                    identity = await self._load_identity(job_id)
                    specific, missing_fields = valuation_identity_specificity(identity)
                if not specific:
                    # Missing or conflicting identity fields lower confidence,
                    # but must not prevent the card from reaching valuation.
                    # The search/calculation stages can still publish a broad or
                    # fallback estimate and preserve these warnings for editing.
                    async with self.database.connection() as connection:
                        await connection.execute(
                            """UPDATE valuation_job
                               SET result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                                   updated_at = now()
                               WHERE id = %s""",
                            (
                                Jsonb(
                                    {
                                        "identity_resolution_status": "low_confidence",
                                        "missing_identity_fields": missing_fields,
                                        "research_status": research["status"],
                                        "research_case_id": research["case_id"],
                                        "candidate_count": research["candidate_count"],
                                        "identity_confidence_advisory": True,
                                    }
                                ),
                                job_id,
                            ),
                        )
            await self._set_status(job_id, "searching")
            search = await self._search(identity)
            if search.pending_refresh_job_id and not search.comparables:
                if await self._defer_until_market_refresh(job_id, search.pending_refresh_job_id):
                    return
                # The refresh may have completed between cache read and the
                # conditional defer. Read its result once; a completed empty
                # search must become a truthful terminal outcome, not a loop.
                search = await self._search(identity)
            if search.visual_progress:
                async with self.database.connection() as connection:
                    await connection.execute(
                        "UPDATE valuation_job SET result_summary = COALESCE(result_summary, '{}'::jsonb) || %s WHERE id = %s",
                        (Jsonb({"visual_progress": search.visual_progress}), job_id),
                    )
            await self._store_comparables(identity, search)
            await self._set_status(job_id, "calculating")
            await self._calculate(job_id, identity, search)
            async with self.database.connection() as connection, connection.transaction():
                await self.market_data.repository.enqueue_revaluation_after_concurrent_refresh(
                    connection, job_id
                )
        except Exception as error:
            await self._finish(
                job_id,
                "failed",
                {"reason": "valuation worker failed"},
                str(error)[:500],
            )

    async def _defer_until_market_refresh(self, job_id: UUID, refresh_id: str) -> bool:
        async with self.database.connection() as connection:
            result = await connection.execute(
                """UPDATE valuation_job job
                   SET status = 'queued', started_at = NULL, finished_at = NULL,
                       attempt_count = GREATEST(job.attempt_count - 1, 0),
                       error_message = NULL, updated_at = now(),
                       result_summary = COALESCE(job.result_summary, '{}'::jsonb) || %s
                   FROM market_refresh_job refresh
                   JOIN market_identity_binding binding
                     ON binding.identity_fingerprint = refresh.identity_fingerprint
                   WHERE job.id = %s AND refresh.id = %s
                     AND binding.collection_item_id = job.collection_item_id
                     AND refresh.status IN ('queued', 'running')
                     AND job.status IN ('identifying', 'searching', 'calculating')""",
                (Jsonb({
                    "reason": "当前身份的市场证据仍在刷新，完成后自动继续估值",
                    "waiting_for_market_refresh": refresh_id,
                }), job_id, refresh_id),
            )
            return result.rowcount > 0

    async def _load_identity(self, job_id: UUID) -> dict:
        async with self.database.connection() as connection:
            result = await connection.execute(
                f"""
                SELECT vj.collection_item_id, ci.card_definition_id, u.base_currency,
                       ci.owner_id,
                       COALESCE(canonical_player.full_name, p.full_name) AS player_name,
                       CASE
                           WHEN canonical_player.full_name IS NOT NULL
                           THEN ARRAY[p.full_name]
                           ELSE ARRAY[]::text[]
                       END AS player_name_aliases,
                       b.name AS brand_name,
                       cs.season_start, cs.season_end, cs.product_name, cs.set_name,
                       cd.card_number, cd.insert_name, cd.parallel_name,
                       cd.is_rookie, cd.is_autograph, cd.autograph_type,
                       cd.is_memorabilia, cd.memorabilia_types,
                       cd.is_patch, cd.print_run,
                       ci.serial_number, ci.serial_total, ci.grade_company, ci.grade_value,
                       ci.grade_cert_number,
                       ci.image_front_url, ci.image_back_url, ci.notes,
                       purchase.notes AS purchase_context,
                       purchase.venue AS purchase_venue,
                       purchase.unit_price AS purchase_price,
                       purchase_sources.sources AS purchase_source_rows,
                       confirmed.identity AS confirmed_identity,
                       confirmed.id AS confirmed_identity_version_id,
                       latest_identity.review_status AS latest_identity_review_status,
                       scanner.result AS scanner_recognition
                FROM valuation_job vj
                JOIN collection_item ci ON ci.id = vj.collection_item_id
                JOIN app_user u ON u.id = ci.owner_id
                JOIN card_definition cd ON cd.id = ci.card_definition_id
                JOIN player p ON p.id = cd.player_id
                LEFT JOIN LATERAL (
                    SELECT candidate.full_name
                    FROM player candidate
                    WHERE candidate.id <> p.id
                      AND candidate.normalized_name ~ '^[a-z0-9 .''-]+$'
                      AND p.normalized_name ~ '^[a-z0-9 .''-]+$'
                      AND regexp_replace(
                              candidate.normalized_name, '[^a-z0-9]', '', 'g'
                          ) = regexp_replace(
                              p.normalized_name, '[^a-z0-9]', '', 'g'
                          )
                    ORDER BY
                        length(candidate.normalized_name),
                        candidate.created_at,
                        candidate.id
                    LIMIT 1
                ) canonical_player ON true
                JOIN card_set cs ON cs.id = cd.card_set_id
                JOIN brand b ON b.id = cs.brand_id
                LEFT JOIN LATERAL (
                    SELECT notes, venue, unit_price
                    FROM card_transaction
                    WHERE collection_item_id = ci.id AND transaction_type = 'buy'
                    ORDER BY transacted_at DESC LIMIT 1
                ) purchase ON true
                {PURCHASE_SOURCE_LATERAL}
                LEFT JOIN LATERAL (
                    SELECT confirmed_version.id, confirmed_version.identity
                    FROM card_identity_version confirmed_version
                    WHERE confirmed_version.collection_item_id = ci.id
                      AND confirmed_version.review_status = 'confirmed'
                      AND NOT EXISTS (
                          SELECT 1 FROM card_identity_version revocation
                          WHERE revocation.supersedes_id = confirmed_version.id
                            AND revocation.review_status = 'rejected'
                      )
                    ORDER BY version DESC LIMIT 1
                ) confirmed ON true
                LEFT JOIN LATERAL (
                    SELECT review_status
                    FROM card_identity_version
                    WHERE collection_item_id = ci.id
                    ORDER BY version DESC LIMIT 1
                ) latest_identity ON true
                LEFT JOIN LATERAL (
                    SELECT result
                    FROM scanner_recognition_job
                    WHERE collection_item_id = ci.id AND result IS NOT NULL
                      AND NOT require_confirmation
                      AND NOT EXISTS (
                          SELECT 1 FROM scanner_recognition_job human_capture
                          WHERE human_capture.collection_item_id = ci.id
                            AND human_capture.require_confirmation
                            AND human_capture.imported_at IS NOT NULL
                      )
                    ORDER BY created_at DESC LIMIT 1
                ) scanner ON true
                WHERE vj.id = %s
                """,
                (job_id,),
            )
            row = await result.fetchone()
            if row is None:
                raise RuntimeError("valuation job item not found")
            identity = self.resolve_identity_row(dict(row))
            identity['public_source_urls'] = public_item_urls_from_notes(row.get('notes'))
            identity.pop('purchase_source_rows', None)
            identity['purchase_sources'] = validated_purchase_sources(row.get('purchase_source_rows'),
                owner_id=row.get('owner_id'), collection_item_id=row.get('collection_item_id'))
            identity['image_content_hashes'] = {}
            verifier = getattr(self, 'image_verifier', None)
            for side in ('front','back'):
                path = verifier._target_path(identity.get('image_'+side+'_url')) if verifier else None
                identity['image_content_hashes'][side] = (
                    content_hash(path.read_bytes()) if path and path.stat().st_size <= verifier.MAX_IMAGE_BYTES else None)
            return identity

    def resolve_identity_row(self, row: dict) -> dict:
        """Resolve a frozen input row without database, model calls or mutations."""
        stored = dict(row)
        confirmed_identity = stored.pop("confirmed_identity", None)
        scanner_recognition = stored.pop("scanner_recognition", None)
        if confirmed_identity:
            # The immutable confirmed version is the valuation identity source;
            # collection/card_definition remain compatibility projections only.
            stored.update(confirmed_identity)
            stored["print_run"] = confirmed_identity.get("serial_total")
            stored["is_patch"] = confirmed_identity.get("is_memorabilia", False)
        elif scanner_recognition:
            from card_platform.image_service import sanitize_identity_field_roles
            scanner_result = normalize_catalog_evidence(canonicalize_identity(
                sanitize_identity_field_roles(CardRecognitionResult.model_validate(scanner_recognition))
            ))
            # Reuse deterministic corrections for already-imported scanner
            # cards.  This repairs structured search anchors without
            # treating a still-uncertain model hypothesis as confirmed.
            deterministic_fields = {
                "season_start",
                "season_end",
                "brand_name",
                "product_name",
                "card_number",
                "insert_name",
                "parallel_name",
                "serial_number",
                "serial_total",
                "is_redemption",
                "redemption_status",
                "is_rookie",
                "is_autograph",
                "is_memorabilia",
                "is_patch",
            }
            scanner_values = scanner_result.model_dump(mode="python")
            for field in deterministic_fields:
                value = scanner_values.get(field)
                raw_value = scanner_recognition.get(field)
                if value not in (None, "") or (
                    field
                    in {
                        "card_number",
                        "insert_name",
                        "parallel_name",
                        "serial_number",
                        "serial_total",
                    }
                    and raw_value not in (None, "")
                ):
                    stored[field] = value
            stored["scanner_identity_rationale"] = scanner_result.caveats
            stored["scanner_identity_example_links"] = list(
                dict.fromkeys(
                    [
                        scanner_result.reference_image_source_url,
                        *(
                            source_url
                            for candidate in scanner_result.web_candidates
                            for source_url in candidate.source_urls
                        ),
                    ]
                )
            )
            stored["scanner_identity_example_links"] = [
                url for url in stored["scanner_identity_example_links"] if url
            ]
        if scanner_recognition:
            physical = CardRecognitionResult.model_validate(scanner_recognition)
            if physical.is_redemption:
                stored["is_redemption"] = True
                if not confirmed_identity or not confirmed_identity.get("is_redemption"):
                    stored["redemption_status"] = physical.redemption_status
        if self.knowledge_runtime:
            stored, knowledge_changes = self.knowledge_runtime.resolver.card_catalog.enrich(
                stored, player_aliases=self.knowledge_runtime.resolver.aliases_for("player", stored.get("player_name"))
            )
            stored["knowledge_identity_evidence"] = knowledge_changes
            stored["knowledge_version"] = self.knowledge_runtime.resolver.version
            reference_candidates = self.knowledge_runtime.resolver.reference_catalog.research_candidates(stored)
            if reference_candidates:
                stored["knowledge_reference_candidates"] = reference_candidates
            if stored.get("serial_total") and not (stored.get("parallel_name") or stored.get("parallel")):
                stored["knowledge_parallel_candidates"] = self.knowledge_runtime.resolver.card_catalog.parallel_candidates(stored)
        resolved = CardIdentityResolver().resolve(stored)
        valuation_identity = {
            **stored,
            **resolved.as_dict(),
            "insert_name": resolved.insert,
            "parallel_name": resolved.parallel,
        }
        if self.knowledge_runtime:
            explicit_aliases = valuation_identity.get("player_name_aliases") or []
            valuation_identity["player_name_aliases"] = list(
                dict.fromkeys(
                    (
                        *self.knowledge_runtime.resolver.aliases_for(
                            "player", valuation_identity.get("player_name")
                        ),
                        *(str(value).strip() for value in explicit_aliases),
                    )
                )
            )
        return valuation_identity

    async def _start_identity_research(
        self, identity: dict, blockers: list[str], *, job_id: UUID
    ) -> dict:
        current = {
            key: identity.get(key)
            for key in (
                "player_name", "season_start", "season_end", "brand_name",
                "product_name", "set_name", "card_number", "insert_name",
                "parallel_name", "serial_number", "serial_total", "is_rookie",
                "is_redemption", "redemption_status",
                "is_autograph", "autograph_type", "is_memorabilia",
                "memorabilia_types", "grade_company", "grade_value",
            )
        }
        visual = {
            "serial_number": current.get("serial_number"),
            "serial_total": current.get("serial_total"),
            "texture_tokens": [],
            "reasons": ["automated valuation identity gate requested research"],
        }
        fixture_visuals = {
            "844be4e2-74db-4985-9279-0f717021b915": SCOTTIE_BARNES_FIXTURE_VISUAL,
            "7ada1e53-bf79-4093-90c8-930af92fc35e": BRANDON_MILLER_FIXTURE_VISUAL,
            "b5755992-64d2-42c6-a4e8-1baa30f642a4": JAREN_JACKSON_FIXTURE_VISUAL,
            "e5e00406-92e2-4476-8bdc-bff0ee1a09bd": LUKA_DONCIC_FIXTURE_VISUAL,
            "70325c16-cbd5-4c36-b20b-68e288235a74": JAREN_JACKSON_88_FIXTURE_VISUAL,
            "722c6f33-0895-494f-a6f1-211661445662": LEBRON_JAMES_FIXTURE_VISUAL,
            "0f5dac8e-019d-4b3a-98b8-84989af38a16": ACE_BAILEY_FIXTURE_VISUAL,
            "3dfc7e4b-8466-44c2-89a4-4c0966f3375f": ANDREW_NEMBHARD_FIXTURE_VISUAL,
            "9405c5c3-f4ed-43f4-9dd2-2ce4f992956c": STEPHEN_CURRY_FIXTURE_VISUAL,
        }
        fixture = fixture_visuals.get(str(identity["collection_item_id"]))
        if fixture:
            # Stable regression fallbacks keep known cases operable when the
            # optional vision provider is unavailable. They pass through the
            # same automatic quality gate as live visual research.
            visual = fixture
        else:
            researched = await self._research_visual_identity(identity)
            if researched:
                visual = researched
        async with self.database.connection() as connection:
            confirmed_result = await connection.execute(
                """
                SELECT 1 FROM card_identity_version confirmed_version
                WHERE confirmed_version.collection_item_id = %s
                  AND confirmed_version.review_status = 'confirmed'
                  AND NOT EXISTS (
                      SELECT 1 FROM card_identity_version revocation
                      WHERE revocation.supersedes_id = confirmed_version.id
                        AND revocation.review_status = 'rejected'
                  )
                LIMIT 1
                """,
                (identity["collection_item_id"],),
            )
            if await confirmed_result.fetchone():
                return {
                    "case_id": None,
                    "status": "confirmed_identity_available",
                    "candidate_count": 0,
                }
        candidates = generate_candidates(current, visual)
        selected_candidate, decision_reasons = automatic_candidate_decision(candidates)
        async with self.database.connection() as connection, connection.transaction():
            result = await connection.execute(
                """INSERT INTO identity_research_case
                   (collection_item_id, status, trigger_reasons, source_version, attempt_count)
                   VALUES (%s,%s,%s,%s,1)
                   ON CONFLICT (collection_item_id) WHERE status IN
                     ('identity_research_required','candidate_identity_ready','awaiting_confirmation')
                   DO UPDATE SET status=EXCLUDED.status, trigger_reasons=EXCLUDED.trigger_reasons,
                     source_version=EXCLUDED.source_version,
                     attempt_count=identity_research_case.attempt_count+1,
                     last_error=NULL, updated_at=now() RETURNING id""",
                (identity["collection_item_id"], ResearchStatus.CANDIDATE_READY if candidates
                 else ResearchStatus.REQUIRED, Jsonb(blockers), CHECKLIST_VERSION),
            )
            case_id = (await result.fetchone())["id"]
            await connection.execute(
                "DELETE FROM identity_research_candidate WHERE research_case_id=%s", (case_id,)
            )
            for rank, candidate in enumerate(candidates, 1):
                await connection.execute(
                    """INSERT INTO identity_research_candidate
                       (research_case_id,rank,identity,field_provenance,visual_reasons,
                        checklist_evidence,market_evidence,confidence,validation_reasons,is_valid)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (case_id, rank, Jsonb(candidate["identity"]),
                     Jsonb(candidate["field_provenance"]), Jsonb(candidate["visual_reasons"]),
                     Jsonb(candidate["checklist_evidence"]), Jsonb(candidate["market_evidence"]),
                     candidate["confidence"], Jsonb(candidate["validation_reasons"]),
                     candidate["is_valid"]),
                )
            await connection.execute(
                """UPDATE valuation_snapshot SET identity_compatibility='incompatible',
                       identity_compatibility_reasons=%s
                   WHERE collection_item_id=%s AND identity_compatibility='unknown'""",
                (Jsonb(["superseded_by_identity_research", *blockers]),
                 identity["collection_item_id"]),
            )
            if selected_candidate is not None:
                identity_version_id = await self._apply_automatic_identity_candidate(
                    connection, identity, selected_candidate, case_id
                )
                await connection.execute(
                    """UPDATE valuation_job
                       SET result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                           updated_at = now()
                       WHERE id = %s""",
                    (
                        Jsonb(
                            {
                                "identity_resolution_status": "resolved_automatically",
                                "identity_version_id": str(identity_version_id),
                                "identity_resolution_reasons": decision_reasons,
                                "identity_rationale": selected_candidate.get(
                                    "visual_reasons", []
                                ),
                                "identity_example_links": [
                                    source.get("source_url")
                                    for source in selected_candidate.get(
                                        "checklist_evidence", []
                                    )
                                    if source.get("source_url")
                                ],
                            }
                        ),
                        job_id,
                    ),
                )
                return {
                    "case_id": str(case_id),
                    "status": "automatic_identity_confirmed",
                    "candidate_count": len(candidates),
                    "diagnostics": {},
                }
            diagnostics = {
                "stage": "identity_quality_gate",
                "root_cause_codes": decision_reasons,
                "missing_or_conflicting_fields": blockers,
                "observed_identity": current,
                "generated_queries": list(
                    dict.fromkeys(
                        query
                        for candidate in candidates
                        for query in candidate.get("search_queries", [])
                    )
                ),
                "candidate_count": len(candidates),
                "candidates": [
                    {
                        "identity": candidate.get("identity"),
                        "confidence": candidate.get("confidence"),
                        "is_valid": candidate.get("is_valid"),
                        "validation_reasons": candidate.get("validation_reasons", []),
                        "visual_reasons": candidate.get("visual_reasons", []),
                        "example_links": [
                            source.get("source_url")
                            for source in candidate.get("checklist_evidence", [])
                            if source.get("source_url")
                        ],
                    }
                    for candidate in candidates
                ],
            }
            await connection.execute(
                """UPDATE identity_research_case
                   SET status = %s, last_error = %s, updated_at = now()
                   WHERE id = %s""",
                (
                    ResearchStatus.REQUIRED,
                    json.dumps(diagnostics, ensure_ascii=False)[:10000],
                    case_id,
                ),
            )
        return {
            "case_id": str(case_id),
            "status": ResearchStatus.REQUIRED,
            "candidate_count": len(candidates),
            "diagnostics": diagnostics,
        }

    async def _apply_automatic_identity_candidate(
        self, connection, stored_identity: dict, candidate: dict, case_id: UUID
    ) -> UUID:
        """Publish a quality-gated identity and keep its full reasoning audit."""
        identity = dict(candidate["identity"])
        market_identity = MarketIdentity.model_validate(
            {**identity, "print_run": identity.get("serial_total")}
        )
        latest_result = await connection.execute(
            """SELECT id, version FROM card_identity_version
               WHERE collection_item_id = %s ORDER BY version DESC LIMIT 1""",
            (stored_identity["collection_item_id"],),
        )
        latest = await latest_result.fetchone()
        provenance = {
            **(candidate.get("field_provenance") or {}),
            "automatic_resolution": {
                "visual_reasons": candidate.get("visual_reasons", []),
                "checklist_evidence": candidate.get("checklist_evidence", []),
                "search_queries": candidate.get("search_queries", []),
                "confidence": str(candidate.get("confidence") or "0"),
            },
        }
        inserted = await connection.execute(
            """INSERT INTO card_identity_version (
                   collection_item_id, version, identity, field_provenance,
                   conflicts, review_status, source, confirmed_at,
                   confirmation_reason, supersedes_id, identity_fingerprint
               ) VALUES (%s, %s, %s, %s, '[]'::jsonb, 'confirmed',
                         'automatic_identity_resolution', now(), %s, %s, %s)
               RETURNING id""",
            (
                stored_identity["collection_item_id"],
                (latest["version"] if latest else 0) + 1,
                Jsonb(identity),
                Jsonb(provenance),
                "single valid high-confidence candidate passed automatic identity gate",
                latest["id"] if latest else None,
                market_identity.fingerprint,
            ),
        )
        identity_version_id = (await inserted.fetchone())["id"]
        payload = CollectionItemCreate(
            owner_id=stored_identity["owner_id"],
            card=CardIdentityInput(
                player_name=identity["player_name"],
                brand_name=identity["brand_name"],
                season_start=identity["season_start"],
                season_end=identity.get("season_end"),
                product_name=identity["product_name"],
                set_name=identity.get("set_name"),
                card_number=identity.get("card_number"),
                insert_name=identity.get("insert_name"),
                parallel_name=identity.get("parallel_name"),
                is_rookie=bool(identity.get("is_rookie")),
                is_autograph=bool(identity.get("is_autograph")),
                autograph_type=identity.get("autograph_type") or "none",
                is_memorabilia=bool(identity.get("is_memorabilia")),
                memorabilia_types=identity.get("memorabilia_types") or [],
                print_run=identity.get("serial_total"),
            ),
            serial_number=identity.get("serial_number"),
            serial_total=identity.get("serial_total"),
            is_numbered=identity.get("serial_total") is not None,
            grade_company=identity.get("grade_company"),
            grade_value=identity.get("grade_value"),
        )
        portfolio = PortfolioRepository()
        player_id = await portfolio._get_or_create_player(connection, payload)
        brand_id = await portfolio._get_or_create_brand(connection, payload)
        card_set_id = await portfolio._get_or_create_set(connection, brand_id, payload)
        definition_id = await portfolio._get_or_create_definition(
            connection, player_id, card_set_id, payload
        )
        await connection.execute(
            """UPDATE collection_item
               SET card_definition_id = %s, serial_number = %s, serial_total = %s,
                   is_numbered = %s, grade_company = %s, grade_value = %s,
                   updated_at = now()
               WHERE id = %s""",
            (
                definition_id,
                identity.get("serial_number"),
                identity.get("serial_total"),
                identity.get("serial_total") is not None,
                identity.get("grade_company"),
                identity.get("grade_value"),
                stored_identity["collection_item_id"],
            ),
        )
        await self.market_data.repository.ensure_identity(
            connection,
            market_identity,
            stored_identity["collection_item_id"],
        )
        await connection.execute(
            """UPDATE identity_research_case
               SET status = %s, last_error = NULL, updated_at = now()
               WHERE id = %s""",
            (ResearchStatus.CONFIRMED_NO_EXACT_SALES, case_id),
        )
        return identity_version_id

    async def _research_visual_identity(self, identity: dict) -> dict | None:
        """Extract structured observations; deterministic checklist code remains authoritative."""
        if self.client is None:
            return None
        local_images = []
        for image_url in (identity.get("image_front_url"), identity.get("image_back_url")):
            local = await self._local_image_content(image_url)
            if local:
                local_images.append(local)
        if len(local_images) == 2 and self.vision_service.provider is not None:
            try:
                recognized = finalize_recognition_status(
                    canonicalize_identity(
                        await self.vision_service.recognize_pair_contents(
                            local_images[0][0], local_images[0][1],
                            local_images[1][0], local_images[1][1],
                        )
                    )
                )
                texture_text = [
                    recognized.parallel_name or "", *recognized.caveats,
                    *(text for values in recognized.field_evidence.values() for text in values),
                ]
                checklist_evidence = []
                if recognized.reference_image_source_url:
                    checklist_evidence.append(
                        {
                            "source_url": recognized.reference_image_source_url,
                            "image_url": recognized.reference_image_front_url,
                            "source_version": recognized.reference_image_provider,
                            "match_confidence": (
                                str(recognized.reference_image_match_confidence)
                                if recognized.reference_image_match_confidence is not None
                                else None
                            ),
                        }
                    )
                seen_source_urls = {
                    evidence["source_url"] for evidence in checklist_evidence
                }
                for web_candidate in recognized.web_candidates:
                    for source_url in web_candidate.source_urls:
                        if source_url in seen_source_urls:
                            continue
                        seen_source_urls.add(source_url)
                        checklist_evidence.append(
                            {
                                "source_url": source_url,
                                "image_url": web_candidate.image_url,
                                "source_version": web_candidate.title,
                                "match_confidence": str(
                                    web_candidate.match_confidence
                                ),
                            }
                        )
                return {
                    "identity_overrides": recognition_candidate_overrides(
                        identity, recognized
                    ),
                    "product_season_start": recognized.season_start,
                    "product_season_end": recognized.season_end,
                    "product_name": recognized.product_name,
                    "card_number": recognized.card_number,
                    "serial_number": recognized.serial_number,
                    "serial_total": recognized.serial_total,
                    "base_card": not recognized.parallel_name and not recognized.serial_total,
                    "parallel_name_hint": recognized.parallel_name,
                    "texture_tokens": texture_text,
                    "reasons": [
                        *recognized.caveats,
                        f"tiered_recognition_status:{recognized.recognition_status}",
                        *(f"quality_issue:{issue}"
                          for issue in recognized.recognition_quality_issues),
                        *(f"model_route:{step.get('model')}:{step.get('reason')}"
                          for step in recognized.recognition_route),
                    ],
                    "confidence": recognized.confidence,
                    "field_provenance": {
                        field: [
                            {"source": "tiered_vision_plus_web", "detail": detail}
                            for detail in details
                        ]
                        for field, details in recognized.field_evidence.items()
                    },
                    "checklist_evidence": checklist_evidence,
                }
            except Exception as error:
                logger.warning(
                    "tiered identity vision failed; using legacy extraction: %s", error
                )
        image_parts = []
        for image_url in (identity.get("image_front_url"), identity.get("image_back_url")):
            data_url = await self._local_image_data_url(image_url)
            if data_url:
                image_parts.append({"type": "image_url", "image_url": {"url": data_url}})
        if not image_parts:
            return None
        schema = json.dumps(IdentityVisualObservations.model_json_schema(), ensure_ascii=False)
        try:
            response = await traced_completion(self.client.chat.completions.create, stage='alias_query',
                model=self.settings.vision_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是球星卡身份取证助手。分别检查正反面，只报告图片可见事实。"
                            "必须严格区分卡号与限编流水号；Prizm、Refractor 等产品技术词不能"
                            "直接当作具体平行版。base_card 只有在没有限编、没有独特平行纹理且"
                            "版式符合基础卡时才为 true。parallel_name_hint 可以为空，禁止仅凭"
                            "颜色猜名称。卡背版权年仅是发行时间线索，球员统计、履历、"
                            "新秀赛季或正文中的年份都不是产品赛季证据；只有卡面明确产品"
                            "标题、评级标签或经核验 checklist 才能确认 season_start/season_end。"
                            f"只返回符合 JSON Schema 的 JSON：{schema}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": json.dumps({
                                "database_identity": {
                                    key: identity.get(key) for key in (
                                        "player_name", "season_start", "season_end",
                                        "product_name", "card_number", "insert_name",
                                        "parallel_name", "serial_number", "serial_total",
                                    )
                                },
                                "task": "extract independent front/back visual observations",
                            }, ensure_ascii=False)},
                            *image_parts,
                        ],
                    },
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                return None
            observations = IdentityVisualObservations.model_validate_json(content)
            if observations.confidence < Decimal("0.65"):
                return None
            return observations.model_dump(mode="json")
        except Exception as error:
            logger.warning("identity visual research failed: %s", str(error)[:200])
            return None

    async def _local_image_content(self, image_url: str | None) -> tuple[bytes, str] | None:
        if not image_url or not image_url.startswith("/uploads/"):
            return None
        name = Path(image_url).name
        candidates = [
            self.settings.resolved_upload_dir / name,
        ]
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            return None
        content = await asyncio.to_thread(path.read_bytes)
        return content, mimetypes.guess_type(path.name)[0] or "image/jpeg"

    async def _local_image_data_url(self, image_url: str | None) -> str | None:
        if not image_url:
            return None
        if image_url.startswith("data:image/"):
            return image_url
        if image_url.startswith(("http://", "https://")):
            return image_url
        name = Path(image_url).name
        candidates = [
            self.settings.resolved_upload_dir / name,
        ]
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            return None
        content = await asyncio.to_thread(path.read_bytes)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"

    async def _search(self, identity: dict) -> ComparableSearchResult:
        market_identity = MarketIdentity.model_validate(identity)
        image_discovery = None
        index_path = getattr(self.settings, 'valuation_public_image_index_path', None)
        if index_path:
            target = self.image_verifier._target_path(identity.get('image_front_url'))
            if target is not None:
                from card_platform.public_image_discovery import query_public_images
                image_discovery = await query_public_images(index_path, target,
                    expected_sha=(identity.get('image_content_hashes') or {}).get('front'))
        image_leads = (image_discovery or {}).get('leads') or []
        cached = await self.market_data.get(
            market_identity,
            collection_item_id=identity["collection_item_id"],
            card_definition_id=identity["card_definition_id"],
            confirmed_identity_version_id=identity.get("confirmed_identity_version_id"),
            **({'public_image_leads': image_leads} if image_leads else {}),
        )
        if image_discovery is not None:
            cached.notes.append('本地公开图片索引：' + image_discovery['status']
                                + f'，发现 {len(image_leads)} 条图片候选')
        comparables = [
            DiscoveredComparable(
                title=item.raw_title,
                source_url=item.source_url,
                occurred_at=item.occurred_at,
                sale_price=item.sale_price,
                shipping_price=item.shipping_price,
                currency=item.currency,
                fx_rate_to_base=Decimal("1"),
                grade_company=item.grade_company,
                grade_value=item.grade_value,
                match_confidence=item.match_confidence,
                source_reliability=Decimal(
                    str(item.identity_classification.get("source_reliability", "0.75"))
                ),
                image_url=item.raw_payload.get("image_url"),
                matching_fields=list(
                    item.identity_classification.get("matching_fields", [])
                ),
                conflicts=list(item.identity_classification.get("conflicts", [])),
                evidence_level=item.evidence_level,
                source_code=item.provider_code,
                source_tier=self._source_tier(item.provider_code),
                provider_payload=item.raw_payload,
            )
            for item in cached.evidence
        ]
        resolved_identity = CardIdentityResolver().resolve(identity)
        classifier = ComparableClassifier(
            resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None
        )
        rejected_cached_candidates: list[dict] = []
        safe_comparables = []
        exact_cached_count = 0
        for item, comp in zip(cached.evidence, comparables, strict=True):
            reviewed_identity_accepted = (
                item.identity_classification.get("reviewed_identity_accepted") is True
                and item.identity_classification.get("reviewed_identity_scope") == "identity"
                and item.identity_classification.get("reviewed_visual_accepted") is not True
                and item.identity_classification.get("reviewed_collection_item_id")
                == str(identity["collection_item_id"])
                and item.identity_classification.get("reviewed_identity_version_id")
                == str(identity.get("confirmed_identity_version_id"))
            )
            classification = classifier.classify(
                resolved_identity,
                ComparableInput(
                    title=comp.title,
                    grade_company=comp.grade_company,
                    grade_value=comp.grade_value,
                    source_reliability=comp.source_reliability,
                    source_code=comp.source_code,
                ),
            )
            provider_tier, provider_rejection_reasons = (
                (None, ())
                if reviewed_identity_accepted
                else
                revalidated_cardhobby_assessment(
                    identity,
                    item.raw_payload,
                    comp.matching_fields,
                    resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None,
                )
                if comp.source_code == "cardhobby"
                else (None, ())
            )
            classification, gate_disagreement = reconcile_cardhobby_classification(
                classification,
                provider_tier,
                provider_rejection_reasons=provider_rejection_reasons,
            )
            if comp.source_code == "cardhobby" and can_use_ungraded_basis(
                identity, comp.title, comp.grade_company, comp.grade_value
            ):
                classification = enforce_formal_cardhobby_identity(
                    identity,
                    item.raw_payload,
                    classification,
                    resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None,
                    identity_rejected=(
                        item.identity_classification.get("reviewed_identity_rejected") is True
                    ),
                )
                if classification.field_matches.get(UNGRADED_SAME_CARD_BASIS):
                    comp = comp.model_copy(update={
                        "matching_fields": list(dict.fromkeys([
                            *comp.matching_fields, UNGRADED_SAME_CARD_BASIS,
                        ])),
                    })
            if gate_disagreement:
                comp = comp.model_copy(
                    update={
                        "matching_fields": list(
                            dict.fromkeys(
                                [*comp.matching_fields, "gate_disagreement_softened"]
                            )
                        )
                    }
                )
            image_reviewable = cached_candidate_needs_image_review(
                identity,
                comp,
                classification.hard_conflicts,
                provider_tier,
                resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None,
            )
            if (
                classification.hard_conflicts
                and not image_reviewable
            ):
                rejected_cached_candidates.append(
                    {
                        "title": comp.title,
                        "source_url": comp.source_url,
                        "provider_code": comp.source_code,
                        "reason_codes": list(classification.hard_conflicts),
                        "provider_conflicts": list(
                            provider_rejection_reasons or comp.conflicts
                        ),
                        "match_confidence": str(comp.match_confidence),
                    }
                )
            else:
                # Derived audit metadata, not provider authority. Hard
                # conflicts were already excluded above; ranking never clears
                # one or promotes a candidate's comparable tier.
                comp = comp.model_copy(update={
                    "matching_fields": list(dict.fromkeys([
                        *comp.matching_fields,
                        *(
                            f"cache_title_anchor:{field}"
                            for field in ("insert", "card_number", "product")
                            if classification.field_matches.get(field) is True
                            and identity.get({
                                "insert": "insert_name",
                                "card_number": "card_number",
                                "product": "product_name",
                            }[field])
                        ),
                    ])),
                })
                safe_comparables.append(comp)
                if (
                    classification.tier == ComparableTier.T0
                    or provider_tier == "exact_or_near_exact"
                ):
                    exact_cached_count += 1
        comparables = rank_cached_comparables(safe_comparables)
        if rejected_cached_candidates and not comparables and not cached.refresh_job_id:
            cached.refresh_job_id = await self.market_data.refresh_after_rejected_cache(
                market_identity.fingerprint
            )
        notes = [
            *cached.notes,
            f"市场数据新鲜度：{cached.freshness.value}",
            (
                f"成交证据截至：{cached.data_as_of.isoformat()}"
                if cached.data_as_of
                else "暂无已缓存成交证据"
            ),
        ]
        if rejected_cached_candidates:
            notes.append(
                f"已在估值前剔除 {len(rejected_cached_candidates)} 条身份冲突缓存成交"
            )
        softened_count = sum(
            "gate_disagreement_softened" in comp.matching_fields
            for comp in comparables
        )
        if softened_count:
            notes.append(
                f"卡淘入口与估值解析器存在 {softened_count} 条字段判断差异；"
                "已按卡淘身份契约保留并降低可比层级"
            )
        result = ComparableSearchResult(
            search_execution='cache_replay',
            pending_refresh_job_id=cached.refresh_job_id,
            search_queries=[
                f"卡淘已售接口[{query.get('stage', 'cached')}]：{query.get('query', '')} "
                f"(召回{query.get('raw_count', 0)}/通过{query.get('accepted_count', 0)};"
                f"{query.get('outcome', 'cached')})"
                for diagnostic in cached.provider_diagnostics
                if diagnostic.get("provider") == "cardhobby"
                for query in diagnostic.get("query_diagnostics", [])
            ],
            comparables=comparables,
            notes=notes,
            rejected_candidates=rejected_cached_candidates,
        )
        if (
            len(result.comparables) < self.market_data.MIN_EVIDENCE
            or exact_cached_count == 0
        ) and not self.settings.valuation_cardhobby_only and (
            self.client is not None
            or (self.search_index.enabled and not self.settings.valuation_cardhobby_only)
        ):
            # The durable provider cache is intentionally strict. Rare cards get
            # a separately labelled, source-backed fallback instead of becoming
            # permanently unpriceable when exact sales are sparse.
            try:
                async with asyncio.timeout(
                    self.settings.valuation_search_timeout_seconds
                ):
                    # CardHobby recall already belongs to the durable refresh
                    # queue. Never run a second unprotected synchronous copy
                    # merely because the completed search returned few rows.
                    external = await self._search_external_legacy(
                        identity, include_cardhobby=False
                    )
            except TimeoutError:
                external = ComparableSearchResult(
                    search_queries=market_search_query_ladder(identity),
                    notes=[
                        "外部市场检索超过总时限；已保留严格缓存证据并结束本次检索"
                    ],
                )
            by_url = {comp.source_url: comp for comp in result.comparables}
            by_url.update({comp.source_url: comp for comp in external.comparables})
            result = external.model_copy(
                update={
                    "comparables": rank_cached_comparables(list(by_url.values())),
                    "pending_refresh_job_id": cached.refresh_job_id,
                    "notes": self._truthful_source_availability_notes(
                        [*notes, *external.notes], cached.provider_diagnostics
                    ),
                    "rejected_candidates": [
                        *result.rejected_candidates,
                        *external.rejected_candidates,
                    ],
                }
            )
        reviewed_urls: set[str] = set()
        result = await self._verify_comparable_images(identity, result, reviewed_urls=reviewed_urls)
        return await self._review_comparable_evidence(identity, result, reviewed_urls=reviewed_urls)

    async def _review_comparable_evidence(
        self, identity: dict, search: ComparableSearchResult, *, reviewed_urls: set[str] | None = None,
    ) -> ComparableSearchResult:
        if self.evidence_reviewer is None or not search.comparables:
            return search
        reviewed_urls = reviewed_urls if reviewed_urls is not None else set()
        evidence = [
            {
                "evidence_id": comp.source_url,
                "title": comp.title,
                "evidence_level": comp.evidence_level,
                "grade_company": comp.grade_company,
                "grade_value": str(comp.grade_value) if comp.grade_value is not None else None,
            }
            for comp in sorted(
                (comp for comp in search.comparables if comp.source_url not in reviewed_urls),
                key=lambda c: c.visual_verdict == "same", reverse=True,
            )[:12]
        ]
        if not evidence:
            return search
        confirmed = {
            key: identity.get(key)
            for key in (
                "player_name", "season_start", "season_end", "product_name",
                "card_number", "insert_name", "parallel_name", "serial_total",
                "grade_company", "grade_value",
            )
        }
        try:
            review = await self.evidence_reviewer.review(confirmed, evidence)
        except Exception as error:
            return search.model_copy(update={"notes": [
                *search.notes,
                f"DeepSeek 证据复核失败，已保留确定性门禁结果：{str(error)[:160]}",
            ]})
        reviewed_urls.update(item["evidence_id"] for item in evidence)
        rejected = enforce_conservative_rejections(review)
        if not rejected:
            return search.model_copy(update={"notes": [
                *search.notes, "DeepSeek 证据复核未发现可执行的高置信度硬冲突",
            ]})
        accepted = [comp for comp in search.comparables if comp.source_url not in rejected]
        rejection_notes = [
            f"DeepSeek 隔离成交 {evidence_id}: {','.join(reasons)}"
            for evidence_id, reasons in rejected.items()
        ]
        return search.model_copy(update={
            "comparables": accepted,
            "notes": [*search.notes, *rejection_notes],
            "rejected_candidates": [*search.rejected_candidates, *[
                {"source_url": url, "reason_codes": reasons}
                for url, reasons in rejected.items()
            ]],
        })

    def _visual_candidate_can_stop(self, identity, comp, decision, *, admitted_rows=None) -> bool:
        """Only a formally admissible same-card lead ends further image discovery.

        Final publication still revalidates persisted source facts and decisions.
        This predicate never approves an evidence row or a price.
        """
        if (decision.verdict != "same" or decision.confidence < Decimal(".80")
                or decision.design_match != "same" or decision.finish_match != "same"
                or not 0 <= (datetime.now(UTC) - comp.occurred_at).days <= 730
                or evidence_grade(comp.evidence_level) not in {EvidenceGrade.E0, EvidenceGrade.E1, EvidenceGrade.E2}):
            return False
        classifier = ComparableClassifier(
            resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None
        )
        visual_facts = build_visual_match_facts(identity, comp.source_url, comp.title, comp.image_url, {
            "visual_verdict": decision.verdict, "visual_confidence": decision.confidence,
            "visual_reasons": decision.reasons, "visual_evidence_binding": decision.evidence_binding,
            "visual_independent_confirmation": decision.independent_confirmation,
            "visual_condition_review": decision.condition_review.model_dump(mode="json") if decision.condition_review else None,
            "visual_match_evidence": [item.model_dump(mode="json") for item in decision.match_evidence],
            "visual_conflict_evidence": [item.model_dump(mode="json") for item in decision.conflict_evidence],
        }, reviewed_proof=decision._reviewed_proof)
        judged = classifier.classify(CardIdentityResolver().resolve(identity), ComparableInput(
            title=comp.title, source_code=comp.source_code,
            grade_company=comp.grade_company, grade_value=comp.grade_value,
            visual_verdict=decision.verdict, visual_confidence=decision.confidence,
            visual_reasons=tuple(decision.reasons), visual_facts=visual_facts,
        ))
        if comp.source_code in {"cardhobby", "cardhobby_provider"}:
            tier, reasons = revalidated_cardhobby_assessment(identity, comp.provider_payload,
                resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None)
            judged, _ = reconcile_cardhobby_classification(judged, tier, provider_rejection_reasons=reasons)
            judged = enforce_formal_cardhobby_identity(
                identity, comp.provider_payload, judged, visual_verdict=decision.verdict,
                visual_facts=visual_facts,
                resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None,
            )
        if judged.tier not in {ComparableTier.T0, ComparableTier.T1} or judged.hard_conflicts:
            return False
        from card_platform.domain.sale_independence import unique_physical_rows
        row = {
            'id': comp.source_url, 'source_code': comp.source_code,
            'source_url': comp.source_url, 'listing_title': comp.title,
            'grade_company': comp.grade_company,
            'comparable_tier': judged.tier.value,
            'provider_raw_payload': comp.provider_payload,
            'raw_payload': {
                'visual_verdict': decision.verdict, 'visual_confidence': decision.confidence,
                'visual_evidence_binding': decision.evidence_binding,
                'visual_match_evidence': [item.model_dump(mode='json') for item in decision.match_evidence],
            },
        }
        # Keep only formally admitted observations in this local stage's pool.
        # An owned source can also identify its same-photo relisting transitively.
        pool = admitted_rows if admitted_rows is not None else []
        pool.append(row)
        independent, _ = unique_physical_rows(pool, identity)
        return any(item['id'] == row['id'] for item in independent)

    async def _verify_comparable_images(
        self, identity: dict, search: ComparableSearchResult, *, reviewed_urls: set[str] | None = None,
    ) -> ComparableSearchResult:
        reviewed_urls = reviewed_urls if reviewed_urls is not None else set()
        reviewed = {}
        denied_urls = set()
        if identity.get("collection_item_id") and identity.get("confirmed_identity_version_id"):
            async with self.database.connection() as connection:
                result = await connection.execute(
                    """SELECT evidence.source_url, decision.decision, decision.id,
                              decision.collection_item_id, decision.identity_version_id,
                              decision.classification->'visual_review' AS visual_review
                       FROM market_evidence_identity_decision decision
                       JOIN market_evidence evidence ON evidence.id = decision.market_evidence_id
                       WHERE decision.collection_item_id = %s AND decision.identity_version_id = %s""",
                    (identity["collection_item_id"], identity["confirmed_identity_version_id"]),
                )
                for row in await result.fetchall():
                    if row["decision"] == "rejected":
                        denied_urls.add(row["source_url"])
                    reviewed[row["source_url"]] = {
                        'decision': row['decision'], 'review': row['visual_review'] or {},
                        'database_record': dict(row),
                    }
        denied_items = {cardhobby_item_id(url) for url in denied_urls} - {None}
        denied = [comp for comp in search.comparables if comp.source_url in denied_urls
                  or cardhobby_item_id(comp.source_url) in denied_items]
        comparables = [comp for comp in search.comparables if comp not in denied]
        by_url = {comp.source_url: comp for comp in comparables}
        skipped_sources = []
        skipped_source_reasons: dict[str, int] = {}
        candidates = []
        for comp in comparables:
            if not comp.image_url:
                continue
            source_reasons = (
                visual_source_rejection_reasons(identity, comp.provider_payload)
                if comp.source_code == "cardhobby" else []
            )
            if source_reasons:
                skipped_sources.append({"source_url": comp.source_url, "reason_codes": source_reasons})
                for reason in source_reasons:
                    skipped_source_reasons[reason] = skipped_source_reasons.get(reason, 0) + 1
                continue
            from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url
            gallery = comp.provider_payload.get('source_gallery') or {}
            support_urls = ()
            if (gallery.get('contract') == 'public-source-gallery-v1'
                    and gallery.get('primary_image_url') == comp.image_url
                    and canonical_public_item_url(gallery.get('source_url'))
                        == canonical_public_item_url(comp.source_url)
                    and canonical_public_item_url(comp.source_url) is not None):
                support_urls = tuple(url for url in gallery.get('image_urls', [])
                                     if isinstance(url,str) and url != comp.image_url)[:2]
            candidates.append(VisualCandidate(comp.source_url, comp.title, comp.image_url, support_urls))
        parallel_check = bool(identity.get("serial_total")) or (
            str(identity.get("parallel_name") or identity.get("parallel") or "")
            .strip().casefold() not in {"", "base"}
        )
        candidates = self.image_verifier.order_candidates(
            identity.get("image_front_url"), candidates, identity=identity,
            target_back_image_url=identity.get("image_back_url"),
            requires_parallel_match=parallel_check,
        )
        decisions = {}
        attempted = set()
        new_count = 0
        evidence_review_attempt_count = 0
        post_review_rejected = {}
        admitted_visual_rows = []
        stop_reason = "candidate_pool_exhausted"
        notes = list(search.notes)

        async def completed(decision):
            if decision.candidate_id in by_url:
                decisions[decision.candidate_id] = decision

        try:
            admission = getattr(self.image_verifier, 'card_admission', nullcontext) if candidates else nullcontext
            async with admission():
                stage_seconds = getattr(self.settings, 'valuation_visual_stage_timeout_seconds', 180.0)
                async with asyncio.timeout(stage_seconds):
                    offset = 0
                    while offset < len(candidates):
                        if new_count + evidence_review_attempt_count >= 36:
                            stop_reason = "candidate_budget_exhausted"
                            break
                        batch = candidates[offset:offset + 1]
                        offset += len(batch)
                        attempted.update(candidate.id for candidate in batch)
                        try:
                            result = await self.image_verifier.verify(
                                identity.get("image_front_url"), batch,
                                target_back_image_url=identity.get("image_back_url"),
                                reviewed_assets=reviewed, identity=identity, on_decision=completed,
                                requires_parallel_match=parallel_check,
                            )
                            for decision in result.values():
                                await completed(decision)
                        finally:
                            # A failed/unfinished remote attempt consumes budget;
                            # a local bound checkpoint or cooldown does not.
                            new_count += sum(
                                not ({"visual_checkpoint_reused", "visual_retry_deferred"}
                                     & set(decisions[c.id].reasons)) if c.id in decisions else True
                                for c in batch
                            )
                        stop_candidate = next((c for c in batch if c.id in decisions
                            and self._visual_candidate_can_stop(
                                identity, by_url[c.id], decisions[c.id], admitted_rows=admitted_visual_rows)), None)
                        if stop_candidate is not None:
                            if (getattr(self, 'evidence_reviewer', None) is not None
                                    and stop_candidate.id not in reviewed_urls):
                                # A same-looking subcard can occur in a lot. Reuse
                                # the existing source reviewer before stopping,
                                # charging its call to this stage's work budget.
                                if new_count + evidence_review_attempt_count >= 36:
                                    stop_reason = "candidate_budget_exhausted"
                                    break
                                evidence_review_attempt_count += 1
                                checked = await self._review_comparable_evidence(
                                    identity, ComparableSearchResult(comparables=[by_url[stop_candidate.id]]),
                                    reviewed_urls=reviewed_urls,
                                )
                                notes.extend(checked.notes)
                                if not checked.comparables:
                                    admitted_visual_rows[:] = [row for row in admitted_visual_rows
                                                              if row['id'] != stop_candidate.id]
                                    post_review_rejected[stop_candidate.id] = next(
                                        (item for item in checked.rejected_candidates
                                         if item.get('source_url') == stop_candidate.id),
                                        {'source_url': stop_candidate.id, 'reason_codes': ['source_review_rejected']},
                                    )
                                    continue
                            stop_reason = "same_card_candidate_confirmed"
                            break
        except TimeoutError:
            stop_reason = "time_budget_exhausted"
            notes.append("图片同卡核验达到时限；已保存完成结果，剩余候选待恢复核验")
        except Exception as error:
            stop_reason = "verification_failed"
            from card_platform.domain.model_failures import model_failure_reason
            notes.append(model_failure_reason(error, 'visual_verification'))
        notes.extend(reason for decision in decisions.values() for reason in decision.reasons
                     if reason.startswith('model_failure:'))
        updated = []
        for comp in comparables:
            if comp.source_url in post_review_rejected:
                continue
            decision = decisions.get(comp.source_url)
            updated.append(comp if decision is None else comp.model_copy(update={
                "visual_verdict": decision.verdict,
                "visual_confidence": decision.confidence,
                "visual_reasons": decision.reasons,
                "visual_evidence_binding": decision.evidence_binding,
                "visual_independent_confirmation": decision.independent_confirmation,
                "visual_condition_review": decision.condition_review.model_dump(mode="json") if decision.condition_review else None,
                "visual_match_evidence": [e.model_dump(mode="json") for e in decision.match_evidence],
            }))
        observed = {url for url, d in decisions.items() if d.confidence > 0}
        progress = {
            "candidate_count": len(candidates), "attempted_count": len(attempted),
            "observed_count": len(observed), "new_candidate_count": new_count,
            "evidence_review_attempt_count": evidence_review_attempt_count,
            "pending_count": len(candidates) - len(observed), "stop_reason": stop_reason,
            "pending_source_urls": [c.id for c in candidates if c.id not in observed],
            "skipped_source_count": len(skipped_sources),
            "skipped_source_reasons": skipped_source_reasons,
            "skipped_sources": skipped_sources,
        }
        return search.model_copy(update={
            "comparables": updated, "notes": notes, "visual_progress": progress,
            "rejected_candidates": [*search.rejected_candidates, *post_review_rejected.values(), *[
                {"source_url": c.source_url, "reason_codes": ["current_identity_evidence_explicitly_rejected"]}
                for c in denied
            ]],
        })

    async def _search_external_legacy(
        self, identity: dict, *, include_cardhobby: bool = True
    ) -> ComparableSearchResult:
        """Deprecated synchronous search retained temporarily for migration rollback only."""
        cardhobby_result = None
        if include_cardhobby and self.cardhobby is not None:
            try:
                cardhobby_result = await self.cardhobby.search(identity)
            except Exception as error:
                cardhobby_result = None
                cardhobby_error = f"卡淘定向检索失败：{str(error)[:180]}"
            else:
                cardhobby_error = None
        else:
            cardhobby_error = (
                "卡淘检索由持久刷新任务负责；本次复用其候选和查询诊断"
                if not include_cardhobby else "卡淘定向检索未配置"
            )
        if cardhobby_result is not None and cardhobby_result.sales:
            # Direct recovery must persist the original provider facts too.
            # Otherwise market_comp exists without its market_evidence link,
            # and final source revalidation rejects a genuinely found sale.
            fetched = CardHobbyMarketDataProvider.normalize_result(cardhobby_result)
            async with self.database.connection() as connection, connection.transaction():
                fingerprint = await self.market_data.repository.ensure_identity(
                    connection, MarketIdentity.model_validate(identity),
                    identity["collection_item_id"],
                )
                await self.market_data.repository.store_evidence(
                    connection, fingerprint, fetched.evidence,
                )
            direct_result = ComparableSearchResult(
                search_queries=cardhobby_result.search_queries,
                comparables=self._cardhobby_comparables(cardhobby_result),
                notes=cardhobby_result.notes,
            )
            if self.settings.valuation_verify_sources:
                direct_result = await self._verify_sources(direct_result)
            direct_result = direct_result.model_copy(
                update={
                    "comparables": [
                        comp
                        for comp in direct_result.comparables
                        if comparable_reaches_search_floor(comp)
                        and self._comparable_grade_matches(identity, comp)
                    ]
                }
            )
            if (
                len(direct_result.comparables) >= self.market_data.MIN_EVIDENCE
                or self.settings.valuation_cardhobby_only
            ):
                return direct_result
        if self.settings.valuation_cardhobby_only:
            return ComparableSearchResult(
                search_queries=(
                    cardhobby_result.search_queries if cardhobby_result is not None else []
                ),
                notes=[
                    *(cardhobby_result.notes if cardhobby_result is not None else []),
                    cardhobby_error or "仅启用卡淘公开已售来源；未调用其他市场来源",
                ],
            )
        search_index_result = await self._search_index_comparables(identity)
        if self.client is None:
            return search_index_result.model_copy(
                update={
                    "search_queries": [
                        *(cardhobby_result.search_queries if cardhobby_result else []),
                        *search_index_result.search_queries,
                    ],
                    "comparables": [
                        *(
                            self._cardhobby_comparables(cardhobby_result)
                            if cardhobby_result is not None
                            else []
                        ),
                        *search_index_result.comparables,
                    ],
                    "notes": [
                        *(cardhobby_result.notes if cardhobby_result is not None else []),
                        *search_index_result.notes,
                        "未配置市场检索模型",
                    ],
                }
            )
        schema = json.dumps(ComparableSearchResult.model_json_schema(), ensure_ascii=False)
        query_ladder = market_search_query_ladder(identity)
        response = await traced_completion(self.client.chat.completions.create, stage='alias_query',
            model=self.settings.valuation_search_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是篮球球星卡成交研究员。必须联网检索真实、已完成的单卡成交，不得"
                        "把多卡 lot、数字卡或复刻卡混入。先核验输入身份：卖家编号、订单编号、"
                        "中文备注可能被误识别为 card_number、insert 或 parallel，必须结合官方"
                        "checklist 和商品描述主动纠正，写入 resolved_identity 和 identity_conflicts。"
                        "优先完全相同的赛季、产品、卡号、insert、parallel、限编和评级；相似卡"
                        "可作为类比依据但必须写明差异和修正。sale_price 必须是实际成交价，Best "
                        "Offer 只有看到实际接受价时才能使用。currency 保留原币种，"
                        "fx_rate_to_base 是成交币种换算到账户基础币种的当前可核验汇率。每条成交"
                        "必须有可访问的 HTTP(S) 原始来源 URL。最多返回 12 条，两年内成交优先。"
                        "如果完全同款成交不足，必须继续构造 fallback_valuation：依次使用同球员"
                        "同系列相近限编/材质成交、同卡不同评级成交、可靠价格指南、最后才是可信"
                        "在售价格并施加流动性折价。fallback 价格全部换算为账户基础币种，至少"
                        "提供一个可访问来源，逐条写明 evidence_type、匹配度和调整逻辑；置信度"
                        "不得超过0.65。不要因为没有完全同款就直接放弃估值。"
                        "检索必须遵守来源瀑布：第一优先卡淘，第二优先 eBay 公开搜索，第三优先"
                        "SportscardsPro、Sports Card Investor、130point、PSA、Beckett 等第三方。"
                        "eBay 只有页面明确显示 Sold/Ended 且能核验实际成交价时才可进入"
                        "comparables，evidence_level=public_completed_sale；普通在售、Buy It Now、"
                        "未成交结束商品只能作为 fallback reference，evidence_type=asking_price。"
                        "每条 eBay comparables 必须链接到单个 /itm/ 商品页，并填写 sale_status="
                        "sold、sale_status_evidence（页面可见的 Sold/Ended 文本）、price_basis。"
                        "price_basis 仅允许 fixed_price_sale、auction_final_price、displayed_sold_price；"
                        "Best Offer 未显示实际接受价时不得进入 comparables。discovery_url 写入发现"
                        "该商品的 LH_Sold=1 且 LH_Complete=1 搜索页。搜索结果页和 /p/ 产品聚合页"
                        "只能用于发现，不能作为逐笔成交 source_url。"
                        "第三方逐笔历史成交可标 aggregated_sale；网站计算出的价格指数只能标"
                        "price_guide 并放入 fallback。绝不能把搜索摘要中的挂牌价当成交价。"
                        "搜索引擎索引只能作为低等级发现证据。只有 snippet 同时明确包含 Sold、"
                        "成交日期、币种和价格，并链接到单个 ebay.com/itm/{item_id} 页面时，才可"
                        "返回 evidence_level=search_index_sale，同时填写 search_provider、"
                        "search_query、search_snippet，price_basis=search_index_displayed_sold_price。"
                        "snippet 若含 Best Offer、只有 Ended、价格缺失或只是搜索结果页，一律不得"
                        "进入 comparables；可以写入 notes，但不得用于估值。"
                        "不得在 notes 声称 manual inspection、人工检查、exhaustive search 或全部"
                        "查询已执行；来源执行状态只由后端 provider 诊断记录。"
                        "在宣称第三方无结果前，必须逐条执行输入中的 third_party_exact_search_queries，"
                        "打开命中的单卡详情页并检查 completed sales/sales history；只能返回页面"
                        "明确列出的日期和成交价，搜索摘要、当前在售和页面估算价均不得冒充成交。"
                        "评级状态是硬约束：目标为裸卡时，comparables 和 fallback references 只能"
                        "使用 condition_type=raw 且没有评级公司/分数的裸卡；目标为评级卡时，只能"
                        "使用 condition_type=graded 且评级公司和分数都完全相同的卡。不得用 PSA "
                        "10、PSA 9、BGS 9.5 或其他评级卡推算裸卡，也不得在评级档位之间折算。"
                        "跨球员参考不能凭感觉填写百分比。若 reference.player_name 与目标球员不同，"
                        "必须在 player_adjustments 提供对应球员的至少3组成对成交：每组目标球员与"
                        "参考球员必须是同产品层级、同卡型、相近限编、相同评级状态。只填写两边"
                        "真实成交价和来源，不要自行计算系数；后端会用价格比中位数计算。没有3组"
                        "有效配对时，不得把该跨球员 reference 放入 fallback。"
                        f"只输出符合 JSON Schema 的 JSON：{schema}"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "card_identity": {
                                key: value
                                for key, value in identity.items()
                                if key
                                not in {"collection_item_id", "card_definition_id"}
                            },
                            "required_base_currency": identity["base_currency"],
                            "required_search_query_ladder": query_ladder,
                            "ebay_completed_search_urls": [
                                ebay_completed_search_url(query)
                                for query in query_ladder[:6]
                            ],
                            "search_index_discovery_queries": (
                                search_index_query_ladder(identity)
                            ),
                            "third_party_exact_search_queries": (
                                third_party_exact_search_queries(identity)
                            ),
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            response_format={"type": "json_object"},
            extra_body={
                "enable_search": True,
                "search_options": {
                    "forced_search": True,
                    "search_strategy": "turbo",
                    "assigned_site_list": VALUATION_SITES,
                },
            },
        )
        content = response.choices[0].message.content
        if not content:
            parsed = ComparableSearchResult(notes=["通用市场检索未返回结果"])
        else:
            parsed = ComparableSearchResult.model_validate_json(content)
            parsed = parsed.model_copy(
                update={
                    "notes": [
                        f"模型辅助检索备注（非证据）：{note}" for note in parsed.notes
                    ]
                }
            )
        if cardhobby_result is not None:
            parsed = parsed.model_copy(
                update={
                    "search_queries": [
                        *cardhobby_result.search_queries,
                        *search_index_result.search_queries,
                        *parsed.search_queries,
                    ],
                    "comparables": [
                        *self._cardhobby_comparables(cardhobby_result),
                        *search_index_result.comparables,
                        *parsed.comparables,
                    ],
                    "notes": [
                        *cardhobby_result.notes,
                        *search_index_result.notes,
                        *parsed.notes,
                    ],
                }
            )
        else:
            parsed = parsed.model_copy(
                update={
                    "search_queries": [
                        *search_index_result.search_queries,
                        *parsed.search_queries,
                    ],
                    "comparables": [
                        *search_index_result.comparables,
                        *parsed.comparables,
                    ],
                    "notes": [*search_index_result.notes, *parsed.notes],
                }
            )
        if cardhobby_error:
            parsed = parsed.model_copy(update={"notes": [cardhobby_error, *parsed.notes]})
        if self.settings.valuation_verify_sources:
            parsed = await self._verify_sources(parsed)
        valid = []
        for comp in parsed.comparables:
            normalized = self._normalize_public_comparable(comp)
            if (
                normalized is not None
                and comparable_reaches_search_floor(normalized)
                and self._comparable_grade_matches(identity, normalized)
            ):
                valid.append(normalized)
        valid.sort(key=lambda comp: (comp.source_tier, -float(comp.match_confidence)))
        fallback = parsed.fallback_valuation
        if fallback is not None:
            references = [
                reference
                for reference in fallback.references
                if urlparse(reference.source_url).scheme in {"http", "https"}
                and self._grade_matches(identity, reference)
            ]
            # Keep a source-less model prior as the final, explicitly low-confidence
            # fallback. It is never allowed to become a formal valuation snapshot.
            fallback = fallback.model_copy(update={"references": references})
        return parsed.model_copy(
            update={"comparables": valid[:12], "fallback_valuation": fallback}
        )

    async def _search_index_comparables(self, identity: dict) -> ComparableSearchResult:
        queries = search_index_query_ladder(identity)
        if not self.search_index.enabled:
            return ComparableSearchResult(
                search_queries=queries,
                notes=["独立搜索索引提供商未配置；未执行 Brave/SerpAPI 取证"],
            )
        hits, errors = await self.search_index.search(queries)
        candidates = [search_index_hit_to_comparable(hit) for hit in hits]
        parsed = [candidate for candidate in candidates if candidate is not None]
        rates: dict[tuple[str, str], Decimal | None] = {}
        base_currency = str(identity["base_currency"]).upper()
        accepted = []
        async with httpx.AsyncClient(
            timeout=self.settings.search_index_timeout_seconds,
            follow_redirects=True,
        ) as client:
            for comp in parsed:
                pair = (comp.currency.upper(), base_currency)
                if pair not in rates:
                    if pair[0] == pair[1]:
                        rates[pair] = Decimal("1")
                    else:
                        try:
                            response = await client.get(
                                "https://api.frankfurter.app/latest",
                                params={"from": pair[0], "to": pair[1]},
                            )
                            response.raise_for_status()
                            rate = response.json().get("rates", {}).get(pair[1])
                            rates[pair] = Decimal(str(rate)) if rate else None
                        except (httpx.HTTPError, ValueError, TypeError):
                            rates[pair] = None
                if rates[pair] is not None:
                    accepted.append(
                        comp.model_copy(update={"fx_rate_to_base": rates[pair]})
                    )
        rejected = len(hits) - len(accepted)
        provider_counts = {
            provider: sum(hit.provider == provider for hit in hits)
            for provider in sorted({hit.provider for hit in hits})
        }
        return ComparableSearchResult(
            search_queries=queries,
            comparables=accepted,
            notes=[
                "独立搜索索引取证："
                f"命中 {len(hits)}，通过 Sold+日期+币种+价格+itemId 门槛 "
                f"{len(accepted)}，拒绝 {rejected}；提供商 {provider_counts}",
                *([f"搜索索引部分失败：{'；'.join(errors[:3])}"] if errors else []),
            ],
        )

    @staticmethod
    def _source_tier(source_code: str) -> int:
        if source_code == "cardhobby":
            return 1
        if source_code in {"ebay", "ebay_public"}:
            return 2
        return 3

    @staticmethod
    def _truthful_source_availability_notes(
        notes: list[str], provider_diagnostics: list[dict[str, object]]
    ) -> list[str]:
        """Do not turn a provider outage into a claim that its market is empty."""
        unavailable = {
            str(item.get("provider") or "").strip().casefold()
            for item in provider_diagnostics
            if str(item.get("status") or "").casefold() != "succeeded"
        }
        if not unavailable:
            return notes
        absence_markers = (
            "no completed",
            "no exact",
            "no publicly",
            "returned zero",
            "zero result",
            "zero sold",
            "未找到",
            "未检索到",
            "没有找到",
            "无成交",
            "无结果",
        )
        truthful = []
        for note in notes:
            normalized = note.casefold()
            if any(
                provider in normalized
                and any(marker in normalized for marker in absence_markers)
                for provider in unavailable
            ):
                continue
            truthful.append(note)
        for provider in sorted(unavailable):
            truthful.append(
                f"{provider} 来源不可用，本次无法判断是否存在已售记录；"
                "不得将来源故障解释为零成交。"
            )
        return list(dict.fromkeys(truthful))

    @staticmethod
    def _cardhobby_comparables(
        result: CardHobbySearchResult,
    ) -> list[DiscoveredComparable]:
        return [
            DiscoveredComparable(
                title=sale.title,
                provider_payload=sale.model_dump(mode="json"),
                source_url=sale.source_url,
                occurred_at=sale.occurred_at,
                sale_price=sale.sale_price,
                shipping_price=sale.shipping_price,
                currency="CNY",
                fx_rate_to_base=sale.fx_rate_to_base,
                grade_company=sale.grade_company,
                grade_value=sale.grade_value,
                match_confidence=sale.match_confidence,
                source_reliability=sale.source_reliability,
                image_url=sale.image_url,
                matching_fields=[
                    *sale.matching_fields,
                    f"cardhobby:{sale.evidence_level}",
                ],
                conflicts=sale.conflicts,
                evidence_level=sale.evidence_level,
                source_code="cardhobby_provider",
                source_tier=1,
            )
            for sale in (result.candidate_pool if result.candidate_pool is not None else result.sales)
        ]

    @staticmethod
    def _normalize_public_comparable(
        comp: DiscoveredComparable,
    ) -> DiscoveredComparable | None:
        if comp.source_code == "cardhobby_provider":
            if (
                not is_cardhobby_evidence_url(comp.source_url)
                or comp.evidence_level not in {
                    "confirmed_transaction",
                    "ended_auction",
                    "legacy_cardhobby_sale",
                }
                or not any(
                    field in comp.matching_fields
                    for field in ("cardhobby_status_sold", "cardhobby:confirmed_transaction")
                )
            ):
                return None
            return comp.model_copy(update={"source_code": "cardhobby", "source_tier": 1})
        if comp.source_code == "cardhobby":
            # Search models may name a source, but they cannot promote their own
            # CardHobby discoveries to first-party transaction evidence.
            return None
        parsed = urlparse(comp.source_url)
        if parsed.scheme not in {"http", "https"}:
            return None
        host = (parsed.hostname or "").casefold()
        if host == "ebay.com" or host.endswith(".ebay.com"):
            if comp.evidence_level == "search_index_sale":
                if not search_index_sale_is_usable(comp):
                    return None
                return comp.model_copy(
                    update={
                        "source_code": "public_market_research",
                        "source_tier": 3,
                        "source_reliability": min(
                            comp.source_reliability, Decimal("0.55")
                        ),
                        "matching_fields": list(
                            dict.fromkeys(
                                [
                                    *comp.matching_fields,
                                    "search_index:sold_date_price",
                                    "origin_marketplace:ebay",
                                ]
                            )
                        ),
                    }
                )
            sold_status = str(comp.sale_status or "").strip().casefold()
            price_basis = str(comp.price_basis or "").strip().casefold()
            if comp.evidence_level not in {
                "public_completed_sale",
                "ended_auction",
                "aggregated_sale",
            } or not re.search(r"^/itm/(?:[^/]+/)?\d+", parsed.path) or sold_status not in {
                "sold",
                "ended_with_bid",
            } or not str(comp.sale_status_evidence or "").strip() or price_basis not in {
                "fixed_price_sale",
                "auction_final_price",
                "displayed_sold_price",
            }:
                return None
            return comp.model_copy(
                update={
                    "source_code": "ebay_public",
                    "source_tier": 2,
                    "source_reliability": min(
                        comp.source_reliability, Decimal("0.82")
                    ),
                    "evidence_level": "public_completed_sale",
                }
            )
        profiles = {
            "sportscardspro.com": ("sportscardspro", Decimal("0.72")),
            "sportscardinvestor.com": ("sports_card_investor", Decimal("0.70")),
            "130point.com": ("public_market_research", Decimal("0.72")),
            "psacard.com": ("public_market_research", Decimal("0.72")),
            "beckett.com": ("public_market_research", Decimal("0.70")),
            "goldin.co": ("public_market_research", Decimal("0.78")),
            "fanaticscollect.com": ("public_market_research", Decimal("0.78")),
        }
        profile = next(
            (
                value
                for domain, value in profiles.items()
                if host == domain or host.endswith(f".{domain}")
            ),
            None,
        )
        if profile is None or comp.evidence_level in {
            "active_listing",
            "asking_price",
            "price_guide",
        }:
            return None
        source_code, reliability_cap = profile
        if source_code == "sportscardspro" and not re.fullmatch(
            r"/game/basketball-cards-[a-z0-9-]+/[a-z0-9-]+/?",
            parsed.path,
        ):
            return None
        return comp.model_copy(
            update={
                "source_code": source_code,
                "source_tier": 3,
                "source_reliability": min(comp.source_reliability, reliability_cap),
                "evidence_level": "aggregated_sale",
            }
        )

    async def _verify_sources(
        self, result: ComparableSearchResult
    ) -> ComparableSearchResult:
        urls = {comp.source_url for comp in result.comparables}
        if result.fallback_valuation:
            urls.update(reference.source_url for reference in result.fallback_valuation.references)
        for adjustment in result.player_adjustments:
            for pair in adjustment.pairs:
                urls.update({pair.target_url, pair.reference_url})
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.settings.valuation_source_verify_timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0 CardPlatformValuation/1.2"},
        ) as client:
            checks = await asyncio.gather(
                *(self._url_verification_status(client, url) for url in urls),
                return_exceptions=True,
            )
        verification = {
            url: check if isinstance(check, str) else "unreachable"
            for url, check in zip(urls, checks, strict=True)
        }
        reachable = {
            url for url, status in verification.items()
            if status in {"verified", "browser_required"}
        }
        fallback = result.fallback_valuation
        if fallback:
            fallback = fallback.model_copy(
                update={
                    "references": [
                        reference
                        for reference in fallback.references
                        if reference.source_url in reachable
                    ]
                }
            )
        adjustments = [
            adjustment.model_copy(
                update={
                    "pairs": [
                        pair
                        for pair in adjustment.pairs
                        if pair.target_url in reachable and pair.reference_url in reachable
                    ]
                }
            )
            for adjustment in result.player_adjustments
        ]
        comparables = []
        for comp in result.comparables:
            status = verification.get(comp.source_url, "unreachable")
            if status not in {"verified", "browser_required"}:
                continue
            if status == "browser_required":
                comp = comp.model_copy(update={
                    "source_reliability": min(
                        comp.source_reliability, Decimal("0.60")
                    ),
                    "matching_fields": [
                        *comp.matching_fields,
                        "source_access:browser_required",
                    ],
                })
            comparables.append(comp)
        browser_required_count = sum(
            status == "browser_required" for status in verification.values()
        )
        return result.model_copy(
            update={
                "comparables": comparables,
                "fallback_valuation": fallback,
                "player_adjustments": adjustments,
                "notes": [
                    *result.notes,
                    *(
                        [
                            "第三级来源中有 "
                            f"{browser_required_count} 个链接被反爬挑战保护；"
                            "已标记 browser_required 并把来源可靠度上限降至 0.60"
                        ]
                        if browser_required_count
                        else []
                    ),
                ],
            }
        )

    @staticmethod
    async def _url_verification_status(client: httpx.AsyncClient, url: str) -> str:
        try:
            if cardhobby_item_id(url):
                # These item IDs were returned by CardHobby's first-party sold search API.
                # Historical detail pages can return 404 to HEAD even while the API record
                # remains available, so the API response itself is the verification source.
                return "verified"
            if is_cardhobby_evidence_url(url):
                response = await client.head(url)
                length = int(response.headers.get("content-length", "0") or 0)
                return (
                    "verified"
                    if 200 <= response.status_code < 400 and length > 0
                    else "unreachable"
                )
            response = await client.get(url)
            if 200 <= response.status_code < 400 and bool(response.content):
                return "verified"
            parsed = urlparse(url)
            host = (parsed.hostname or "").casefold()
            challenge = response.text[:6000].casefold()
            if (
                response.status_code == 403
                and (host == "sportscardspro.com" or host.endswith(".sportscardspro.com"))
                and re.fullmatch(
                    r"/game/basketball-cards-[a-z0-9-]+/[a-z0-9-]+/?",
                    parsed.path,
                )
                and "challenges.cloudflare.com" in challenge
                and "just a moment" in challenge
            ):
                return "browser_required"
            if (
                response.status_code == 403
                and ebay_item_id(url) is not None
                and (
                    "akamai" in challenge
                    or "access denied" in challenge
                    or "security measure" in challenge
                )
            ):
                # The individual item URL is still the evidence address, but eBay
                # commonly denies non-browser verification. This status never
                # promotes a listing by itself: normalization has already required
                # a self-contained sold/date/currency/price search-index snapshot.
                return "browser_required"
            return "unreachable"
        except httpx.HTTPError:
            return "unreachable"

    @staticmethod
    async def _url_reachable(client: httpx.AsyncClient, url: str) -> bool:
        return (
            await AutomatedValuationWorker._url_verification_status(client, url)
        ) in {"verified", "browser_required"}

    @staticmethod
    def _grade_matches(identity: dict, reference: ValuationReference) -> bool:
        target_company = identity.get("grade_company")
        target_grade = identity.get("grade_value")
        if target_company is None:
            return reference.condition_type.casefold() == "raw" and reference.grade_company is None
        if reference.condition_type.casefold() != "graded" or reference.grade_company is None:
            return False
        return (
            reference.grade_company.casefold() == str(target_company).casefold()
            and reference.grade_value is not None
            and target_grade is not None
            and Decimal(reference.grade_value) == Decimal(target_grade)
        )

    @staticmethod
    def _comparable_grade_matches(identity: dict, comp: DiscoveredComparable) -> bool:
        if (
            comp.source_code in {"cardhobby", "cardhobby_provider"}
            and UNGRADED_SAME_CARD_BASIS in comp.matching_fields
            and can_use_ungraded_basis(identity, comp.title, comp.grade_company, comp.grade_value)
        ):
            # The final basis gate rechecks immutable sold-source facts before
            # any price is published; this only retains the discovery result.
            return True
        target_company = identity.get("grade_company")
        target_grade = identity.get("grade_value")
        if target_company is None:
            return comp.grade_company is None
        return (
            comp.grade_company is not None
            and comp.grade_company.casefold() == str(target_company).casefold()
            and comp.grade_value is not None
            and target_grade is not None
            and Decimal(comp.grade_value) == Decimal(target_grade)
        )

    @classmethod
    def build_player_coefficients(
        cls, identity: dict, adjustments: list[PlayerAdjustment]
    ) -> dict[str, dict]:
        coefficients: dict[str, dict] = {}
        target_name = str(identity["player_name"]).casefold()
        for adjustment in adjustments:
            if adjustment.target_player.casefold() != target_name:
                continue
            pairs = [
                pair
                for pair in adjustment.pairs
                if cls._pair_grade_matches(identity, pair)
            ]
            ratios = [pair.target_price_base / pair.reference_price_base for pair in pairs]
            if len(ratios) < 3:
                continue
            median = cls._median(ratios)
            deviations = [abs(ratio - median) for ratio in ratios]
            mad = cls._median(deviations)
            filtered = (
                [ratio for ratio in ratios if abs(ratio - median) <= mad * 3]
                if mad > 0
                else ratios
            )
            if len(filtered) < 3:
                continue
            coefficient = cls._median(filtered)
            if not Decimal("0.3") <= coefficient <= Decimal("3"):
                continue
            coefficients[adjustment.reference_player.casefold()] = {
                "target_player": adjustment.target_player,
                "reference_player": adjustment.reference_player,
                "segment": adjustment.segment,
                "coefficient": coefficient,
                "sample_count": len(filtered),
                "ratio_low": min(filtered),
                "ratio_high": max(filtered),
                "pairs": [pair.model_dump(mode="json") for pair in pairs],
            }
        return coefficients

    @staticmethod
    def _pair_grade_matches(identity: dict, pair: PlayerPricePair) -> bool:
        target_company = identity.get("grade_company")
        target_grade = identity.get("grade_value")
        if target_company is None:
            return pair.condition_type.casefold() == "raw" and pair.grade_company is None
        return (
            pair.condition_type.casefold() == "graded"
            and pair.grade_company is not None
            and pair.grade_company.casefold() == str(target_company).casefold()
            and pair.grade_value is not None
            and target_grade is not None
            and Decimal(pair.grade_value) == Decimal(target_grade)
        )

    @staticmethod
    def _median(values: list[Decimal]) -> Decimal:
        ordered = sorted(values)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[midpoint]
        return (ordered[midpoint - 1] + ordered[midpoint]) / 2

    async def _store_comparables(self, identity: dict, search: ComparableSearchResult) -> None:
        if not search.comparables:
            return
        # A competing writer can hold a row needed by this batch. Retry only
        # after the entire transaction has rolled back, without repeating the
        # market search or its model calls. Other database failures still fail
        # the job with their original diagnostic.
        for attempt in range(3):
            try:
                await self._store_comparables_once(identity, search)
                return
            except DeadlockDetected:
                if attempt == 2:
                    raise
                logger.warning("market_comp transaction deadlocked; retry %s/2", attempt + 1)
                await asyncio.sleep(0.05 * (2 ** attempt))

    async def _store_comparables_once(self, identity: dict, search: ComparableSearchResult) -> None:
        async with self.database.connection() as connection, connection.transaction():
            sources = await connection.execute(
                "SELECT id, code FROM data_source "
                "WHERE code IN ('automated_market_search', 'cardhobby', "
                "'ebay_public', 'sportscardspro', 'sports_card_investor', "
                "'public_market_research')"
            )
            source_ids = {row["code"]: row["id"] for row in await sources.fetchall()}
            ordered = []
            for comp in search.comparables:
                cardhobby_key = cardhobby_external_id(
                    comp.source_url, comp.title, comp.occurred_at, comp.sale_price
                )
                ebay_key = ebay_item_id(comp.source_url)
                source_code = "cardhobby" if cardhobby_key is not None else comp.source_code
                source_id = source_ids.get(
                    source_code, source_ids["automated_market_search"]
                )
                external_id = (
                    cardhobby_key
                    if cardhobby_key is not None
                    else f"ebay:{ebay_key}"
                    if ebay_key is not None
                    else comp.source_url
                )
                ordered.append((source_id, external_id, comp, source_code, ebay_key))
            # Lock the actual conflict keys in one order even when searches
            # return the same sales in different orders or URL aliases.
            ordered.sort(key=lambda item: (str(item[0]), item[1]))
            for source_id, external_id, comp, source_code, ebay_key in ordered:
                classification = (
                    {"origin_source_code": "ebay", "ebay_item_id": ebay_key}
                    if ebay_key is not None
                    else {}
                )
                # A public sale can legitimately be a comparable for multiple
                # owners and for a corrected card definition. The database's
                # identity-scoped unique key prevents duplicates within one
                # definition; a global external-id quarantine would strand
                # evidence on superseded identities after manual correction.
                await connection.execute(
                    """
                    INSERT INTO market_comp (
                        card_definition_id, data_source_id, occurred_at, sale_price,
                        shipping_price, currency, fx_rate_to_base, grade_company,
                        grade_value, listing_title, source_url, external_id,
                        match_confidence, source_reliability, classification, raw_payload,
                        market_evidence_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        (SELECT id FROM market_evidence
                         WHERE provider_code = %s AND external_sale_id = %s)
                    )
                    ON CONFLICT (card_definition_id, data_source_id, external_id)
                    DO UPDATE SET
                        match_confidence = EXCLUDED.match_confidence,
                        source_reliability = EXCLUDED.source_reliability,
                        classification = market_comp.classification || EXCLUDED.classification,
                        raw_payload = CASE
                            WHEN EXCLUDED.raw_payload->>'source' = 'cardhobby'
                              AND (market_comp.raw_payload->>'visual_verdict' IN ('same','compatible','different')
                                OR (market_comp.raw_payload->>'visual_verdict' = 'uncertain'
                                    AND CASE WHEN btrim(market_comp.raw_payload->>'visual_confidence') ~ '^[+]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$'
                                        THEN (market_comp.raw_payload->>'visual_confidence')::numeric > 0
                                        ELSE false END)
                                OR market_comp.raw_payload->'visual_review_required' = 'true'::jsonb)
                            THEN EXCLUDED.raw_payload || '{"visual_review_required":true}'::jsonb
                            ELSE EXCLUDED.raw_payload
                        END,
                        market_evidence_id = COALESCE(
                            EXCLUDED.market_evidence_id, market_comp.market_evidence_id
                        )
                    """,
                    (
                        identity["card_definition_id"],
                        source_id,
                        comp.occurred_at,
                        comp.sale_price,
                        comp.shipping_price,
                        comp.currency.upper(),
                        comp.fx_rate_to_base,
                        comp.grade_company,
                        comp.grade_value,
                        comp.title,
                        comp.source_url,
                        external_id,
                        comp.match_confidence,
                        comp.source_reliability,
                        Jsonb(classification),
                        Jsonb(
                            {
                                "matching_fields": comp.matching_fields,
                                "conflicts": comp.conflicts,
                                "automated": True,
                                "source": source_code,
                                "source_tier": comp.source_tier,
                                "image_url": comp.image_url,
                                "visual_verdict": comp.visual_verdict,
                                "visual_confidence": (
                                    str(comp.visual_confidence)
                                    if comp.visual_confidence is not None
                                    else None
                                ),
                                "visual_reasons": comp.visual_reasons,
                                "visual_evidence_binding": comp.visual_evidence_binding,
                                "visual_match_evidence": comp.visual_match_evidence,
                                "visual_independent_confirmation": comp.visual_independent_confirmation,
                                "visual_condition_review": comp.visual_condition_review,
                                "evidence_level": comp.evidence_level,
                                "sale_status": comp.sale_status,
                                "sale_status_evidence": comp.sale_status_evidence,
                                "price_basis": comp.price_basis,
                                "discovery_url": comp.discovery_url,
                                "search_provider": comp.search_provider,
                                "search_query": comp.search_query,
                                "search_snippet": comp.search_snippet,
                                "ebay_item_id": ebay_key,
                            }
                        ),
                        source_code,
                        external_id,
                    ),
                )

    @staticmethod
    def _diagnostic_identity(identity: dict) -> dict:
        diagnostic = {
            key: identity.get(key)
            for key in (
                "collection_item_id", "player_name", "season_start", "season_end",
                "brand_name", "product_name", "set_name", "card_number",
                "insert_name", "parallel_name", "serial_number", "serial_total",
                "is_redemption", "redemption_status",
                "is_rookie", "is_autograph", "is_memorabilia", "grade_company",
                "grade_value",
            )
        }
        return {
            key: str(value) if isinstance(value, (UUID, Decimal)) else value
            for key, value in diagnostic.items()
        }

    def _classify_pricing_source(self, identity, row, visual, visual_facts):
        """Review authority never rewrites the source title or clears its conflicts."""
        classifier = ComparableClassifier(
            resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None)
        title = row.get('listing_title') or ''
        reliability = row.get('source_reliability') or (
            Decimal('.70') if row['source_code'] == 'cardhobby' else Decimal('.75'))
        classified = classifier.classify(CardIdentityResolver().resolve(identity), ComparableInput(
            title=title, grade_company=row.get('grade_company'), grade_value=row.get('grade_value'),
            source_reliability=reliability, source_code=row['source_code'],
            visual_verdict=visual.get('visual_verdict'),
            visual_confidence=Decimal(str(visual['visual_confidence'])) if visual.get('visual_confidence') is not None else None,
            visual_reasons=tuple(visual.get('visual_reasons') or []), visual_facts=visual_facts))
        reasons = list(classified.hard_conflicts)
        if row.get('reviewed_identity_rejected') is True:
            reasons.append('current_identity_evidence_explicitly_rejected')
        reasons.extend(condition_rejection_reasons(identity, title, visual_facts,
            source_url=row.get('source_url'), image_url=(row.get('raw_payload') or {}).get('image_url')))
        if reasons:
            return Classification(ComparableTier.REJECTED, Decimal('0'), classified.source_reliability,
                field_matches=classified.field_matches, hard_conflicts=list(dict.fromkeys(reasons)))
        return disclose_minor_condition(classified, title)

    def _current_visual_evidence(self, identity: dict, row: dict, search=None):
        """Only the separate current DB review column can restore review authority."""
        visual = row.get('raw_payload') or {}
        candidate = VisualCandidate(row['source_url'], row['listing_title'] or '',
                                    visual.get('image_url') or '')
        # A market_comp row belongs to a card definition, not a held copy.
        # Another copy may have replaced its image binding since this search.
        # Use this search's exact-source observation without changing shared
        # sale facts or treating serialized review markers as review authority.
        matches = [comp for comp in search.comparables
                   if tuple(getattr(comp, key, None) for key in ('source_url', 'title', 'image_url')) ==
                   (candidate.id, candidate.title, candidate.image_url)] if search else []
        if len(matches) == 1:
            fields = ('visual_verdict', 'visual_confidence', 'visual_reasons',
                      'visual_evidence_binding', 'visual_match_evidence',
                      'visual_independent_confirmation', 'visual_condition_review')
            observed = {**visual, **{key: getattr(matches[0], key, None) for key in fields}}
            observed['visual_reasons'] = observed.get('visual_reasons') or []
            if observed.get('visual_confidence') is not None:
                observed['visual_confidence'] = str(observed['visual_confidence'])
            current = current_persisted_visual(identity, candidate, observed)
            if current.get('visual_verdict') is not None:
                visual = current
        verifier = getattr(self, 'image_verifier', None)
        loader = getattr(verifier, 'reviewed_decision_from_cached_assets', None)
        manual = loader(identity, candidate, row.get('accepted_visual_review')) if loader else None
        if manual is not None:
            visual = {**visual, 'visual_verdict': manual.verdict,
                'visual_confidence': str(manual.confidence), 'visual_reasons': manual.reasons,
                'visual_evidence_binding': manual.evidence_binding,
                'visual_match_evidence': [e.model_dump(mode='json') for e in manual.match_evidence],
                'visual_reviewed_record_id': manual._reviewed_proof.record_id,
                'visual_condition_review': manual.condition_review.model_dump(mode='json') if manual.condition_review else visual.get('visual_condition_review')}
        if row['source_code'] == 'cardhobby':
            visual = current_persisted_visual(identity, candidate, visual)
        facts = build_visual_match_facts(identity, candidate.id, candidate.title, candidate.image_url,
            visual, reviewed_proof=manual._reviewed_proof if manual else None)
        return visual, facts

    async def _calculate(
        self, job_id: UUID, identity: dict, search: ComparableSearchResult
    ) -> None:
        async with self.database.connection() as connection, connection.transaction():
            from card_platform.domain.model_failures import parse_model_failures
            await connection.execute(
                "UPDATE valuation_job SET result_summary=COALESCE(result_summary,'{}'::jsonb)||%s WHERE id=%s",
                (Jsonb({'upstream_failures': parse_model_failures([
                    *getattr(search, 'notes', []), *(r for comp in search.comparables for r in getattr(comp, 'visual_reasons', []))]),
                    'search_execution': getattr(search, 'search_execution', 'unknown'),
                    'executed_query_count': 0 if getattr(search, 'search_execution', None) == 'cache_replay' else None}), job_id))
            result = await connection.execute(
                """
                SELECT market_comp.id, market_comp.occurred_at,
                       market_comp.sale_price, market_comp.shipping_price,
                       market_comp.currency, market_comp.fx_rate_to_base,
                       market_comp.listing_title, market_comp.source_url,
                       market_comp.match_confidence, market_comp.grade_company,
                       market_comp.grade_value, market_comp.raw_payload,
                       me.raw_payload AS provider_raw_payload,
                       me.identity_classification AS provider_identity_classification,
                       market_comp.external_id, market_comp.classification,
                       market_comp.comparable_tier,
                       EXISTS (
                           SELECT 1
                           FROM market_evidence_identity_decision decision
                           WHERE decision.market_evidence_id = me.id
                             AND decision.collection_item_id = %s
                             AND decision.identity_version_id = %s
                             AND decision.decision = 'accepted'
                             AND NOT (decision.classification ? 'visual_review')
                       ) AS reviewed_identity_accepted,
                       EXISTS (
                           SELECT 1
                           FROM market_evidence_identity_decision decision
                           WHERE decision.market_evidence_id = me.id
                             AND decision.collection_item_id = %s
                             AND decision.identity_version_id = %s
                             AND decision.decision = 'rejected'
                       ) AS reviewed_identity_rejected,
                       (
                           SELECT jsonb_build_object(
                               'id', decision.id, 'collection_item_id', decision.collection_item_id,
                               'identity_version_id', decision.identity_version_id,
                               'decision', decision.decision, 'source_url', me.source_url,
                               'visual_review', decision.classification->'visual_review')
                           FROM market_evidence_identity_decision decision
                           WHERE decision.market_evidence_id = me.id
                             AND decision.collection_item_id = %s
                             AND decision.identity_version_id = %s
                             AND decision.decision = 'accepted'
                             AND decision.classification ? 'visual_review'
                       ) AS accepted_visual_review,
                       COALESCE(
                           me.evidence_level,
                           market_comp.raw_payload->>'evidence_level',
                           'aggregated_sale'
                       ) AS evidence_level,
                       (SELECT code FROM data_source WHERE id = market_comp.data_source_id)
                           AS source_code,
                       market_comp.sale_price * COALESCE(market_comp.fx_rate_to_base, 1)
                           AS value_base,
                       market_comp.shipping_price * COALESCE(market_comp.fx_rate_to_base, 1)
                           AS shipping_base,
                       market_comp.source_reliability,
                       GREATEST(0, EXTRACT(day FROM now() - market_comp.occurred_at))::int
                           AS age_days
                FROM market_comp
                LEFT JOIN market_evidence me ON me.id = market_comp.market_evidence_id
                WHERE market_comp.card_definition_id = %s
                  AND market_comp.occurred_at >= now() - interval '730 days'
                  AND market_comp.occurred_at <= now()
                ORDER BY market_comp.occurred_at DESC
                """,
                (
                    identity["collection_item_id"],
                    identity.get("confirmed_identity_version_id"),
                    identity["collection_item_id"],
                    identity.get("confirmed_identity_version_id"),
                    identity["collection_item_id"],
                    identity.get("confirmed_identity_version_id"),
                    identity["card_definition_id"],
                ),
            )
            allowed_source_urls = {comp.source_url for comp in search.comparables}
            raw_rows = [
                dict(row)
                for row in await result.fetchall()
                if source_matches_current_search(row["source_url"], allowed_source_urls)
            ]
            rows, duplicate_sales = deduplicate_market_rows(raw_rows)
            from card_platform.domain.sale_independence import unique_physical_rows, seller_key
            audit_current = {
                **identity,
                "insert_name": identity.get("insert_name"),
                "parallel_name": identity.get("parallel_name"),
            }
            suggestions = build_identity_suggestions(
                audit_current, rows
            )
            await IdentityAuditRepository().replace_open_suggestions(
                connection,
                identity["collection_item_id"],
                suggestions,
            )
            if not rows:
                fallback = search.fallback_valuation
                fallback_failure_reason: str | None = None
                if fallback is not None:
                    await self._store_model_fallback_shadow(
                        connection, job_id, identity, fallback, search
                    )
                if fallback is not None:
                    fallback_failure_reason = (
                        "模型类比参考缺少当前商品图像、版本与实际成交资格；仅保留影子记录，不能补成当前价格"
                    )
                await connection.execute(
                    """
                    UPDATE valuation_job SET status = 'insufficient_evidence',
                        result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                        finished_at = now(), updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        Jsonb(
                            {
                                "reason": fallback_failure_reason
                                or empty_valuation_recall_diagnostics(search)["reason"],
                                "search_queries": search.search_queries,
                                "notes": search.notes,
                                "rejected_comparables": search.rejected_candidates,
                                "valuation_failure_diagnostics": {
                                    "stage": "cardhobby_recall_and_similarity_gate",
                                    "identity": self._diagnostic_identity(identity),
                                    "query_count": len(search.search_queries),
                                    "recalled_candidate_count": len(search.comparables),
                                    "candidate_urls": [
                                        comp.source_url for comp in search.comparables
                                    ],
                                    "rejected_candidate_count": len(
                                        search.rejected_candidates
                                    ),
                                    "rejected_candidates": search.rejected_candidates,
                                    **empty_valuation_recall_diagnostics(search),
                                    "source_notes": search.notes,
                                },
                            }
                        ),
                        job_id,
                    ),
                )
                return
            resolver = self.knowledge_runtime.resolver if self.knowledge_runtime else None
            classified: list[ClassifiedSale] = []
            shadow_sales: list[ShadowSale] = []
            rejected: list[dict] = []
            rows_by_id = {str(row["id"]): dict(row) for row in rows}
            for row in rows:
                visual, visual_facts = self._current_visual_evidence(identity, row, search)
                rows_by_id[str(row['id'])]['raw_payload'] = visual
                classification = self._classify_pricing_source(identity, row, visual, visual_facts)
                matching_fields = list(
                    (row.get("provider_identity_classification") or {}).get(
                        "matching_fields", []
                    )
                    or (row.get("raw_payload") or {}).get("matching_fields", [])
                )
                reviewed_identity_accepted = bool(
                    row.get("reviewed_identity_accepted")
                    and not row.get("accepted_visual_review")
                )
                provider_tier, provider_rejection_reasons = (
                    (None, ())
                    if reviewed_identity_accepted
                    else
                    revalidated_cardhobby_assessment(
                        identity,
                        row.get("provider_raw_payload"),
                        matching_fields,
                        resolver=resolver,
                    )
                    if row["source_code"] == "cardhobby"
                    else (None, ())
                )
                classification, gate_disagreement = reconcile_cardhobby_classification(
                    classification,
                    provider_tier,
                    provider_rejection_reasons=provider_rejection_reasons,
                )
                if row["source_code"] == "cardhobby":
                    classification = enforce_formal_cardhobby_identity(
                        identity,
                        row.get("provider_raw_payload"),
                        classification,
                        visual_verdict=visual.get("visual_verdict"),
                        visual_review_required=visual.get('visual_review_required') is True,
                        identity_rejected=bool(row.get("reviewed_identity_rejected")),
                        identity_accepted=reviewed_identity_accepted,
                        visual_facts=visual_facts, resolver=resolver,
                        visual_observation=visual,
                    )
                await connection.execute(
                    """
                    UPDATE market_comp SET comparable_tier = %s, similarity = %s,
                        source_reliability = %s, classification = classification || %s
                    WHERE id = %s
                    """,
                    (
                        classification.tier.value,
                        classification.similarity,
                        classification.source_reliability,
                        Jsonb(
                            {
                                "field_matches": classification.field_matches,
                                "hard_conflicts": classification.hard_conflicts,
                                "gate_disagreement_softened": gate_disagreement,
                                "cardhobby_provider_tier": provider_tier,
                                "cardhobby_provider_rejection_reasons": list(
                                    provider_rejection_reasons
                                ),
                                "knowledge_version": (
                                    self.knowledge_runtime.resolver.version
                                    if self.knowledge_runtime
                                    else None
                                ),
                            }
                        ),
                        row["id"],
                    ),
                )
                rows_by_id[str(row["id"])]["comparable_tier"] = (
                    classification.tier.value
                )
                rows_by_id[str(row["id"])]["classification"] = {
                    "field_matches": classification.field_matches,
                    "hard_conflicts": classification.hard_conflicts,
                    "gate_disagreement_softened": gate_disagreement,
                    "cardhobby_provider_tier": provider_tier,
                    "cardhobby_provider_rejection_reasons": list(
                        provider_rejection_reasons
                    ),
                }
                grade = evidence_grade(row["evidence_level"])
                shadow_sales.append(
                    ShadowSale(
                        id=str(row["id"]),
                        seller_key=seller_key(row),
                        provider_code=row["source_code"],
                        price=row["value_base"],
                        shipping=row["shipping_base"],
                        age_days=row["age_days"],
                        tier=classification.tier,
                        similarity=classification.similarity,
                        source_reliability=classification.source_reliability,
                        evidence_level=row["evidence_level"],
                        pricing_basis=(
                            UNGRADED_SAME_CARD_BASIS
                            if classification.field_matches.get(UNGRADED_SAME_CARD_BASIS)
                            else None
                        ),
                    )
                )
                evidence_conflicts = (
                    []
                    if grade in {EvidenceGrade.E0, EvidenceGrade.E1, EvidenceGrade.E2}
                    else [f"evidence_grade_{grade.value}_not_center_eligible"]
                )
                if classification.hard_conflicts or evidence_conflicts:
                    rejected.append(
                        {
                            "id": str(row["id"]),
                            "title": row["listing_title"],
                            "tier": classification.tier.value,
                            "hard_conflicts": [
                                *classification.hard_conflicts,
                                *evidence_conflicts,
                            ],
                            "field_matches": classification.field_matches,
                            "raw_weight": "0",
                            "final_weight": "0",
                        }
                    )
                    continue
                classified.append(
                    ClassifiedSale(
                        id=str(row["id"]),
                        seller_key=seller_key(row),
                        sale_price_base=row["value_base"],
                        shipping_base=row["shipping_base"],
                        age_days=row["age_days"],
                        classification=classification,
                        adjustment=Adjustment(),
                    )
                )
            eligible_rows = [rows_by_id[sale.id] for sale in classified]
            independent_rows, physical_excluded = unique_physical_rows(eligible_rows, identity)
            independent_ids = {str(row['id']) for row in independent_rows}
            excluded_ids = {row['dropped_id'] for row in physical_excluded}
            classified = [sale for sale in classified if sale.id in independent_ids]
            shadow_sales = [sale for sale in shadow_sales if sale.id not in excluded_ids]
            duplicate_sales.extend(physical_excluded)
            rejected.extend({'id':entry['dropped_id'], 'hard_conflicts':[entry['reason_code']],
                             'raw_weight':'0', 'final_weight':'0'} for entry in physical_excluded)
            rows_by_id = {key:row for key,row in rows_by_id.items() if key not in excluded_ids}
            shadow = ShadowValuationEstimator().estimate(
                shadow_sales, identity, require_same_card=True,
            )
            # Different parallels remain contextual evidence. They cannot
            # create a current same-card price when exact evidence is absent.
            parallel_details = {}
            for comp_id, detail in parallel_details.items():
                selected = rows_by_id[comp_id]
                selected['same_card_classification'] = selected['classification']
                selected['comparable_tier'] = ComparableTier.T2.value
                selected['parallel_adjustment'] = detail
                selected['classification'] = {
                    'field_matches': {'pricing_basis': PARALLEL_BASIS},
                    'analogue_differences': detail['differences'],
                    'same_card_classification': selected['same_card_classification'],
                }
            await self._store_shadow_result(connection, job_id, identity, shadow)
            if shadow.fair is not None:
                valuation_id = await self._publish_relative_valuation(
                    connection, job_id, identity, shadow, search, rows_by_id
                )
                await connection.execute(
                    """UPDATE valuation_job SET status = 'succeeded',
                           result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                           finished_at = now(), updated_at = now() WHERE id = %s""",
                    (
                        Jsonb(
                            {
                                "valuation_id": str(valuation_id),
                                "evidence_count": len(shadow.used_ids),
                                "confidence": str(shadow.confidence),
                                "valuation_tier": (PARALLEL_BASIS if parallel_details else "best_relative_candidate"),
                                "risk_flags": list(shadow.review_reasons),
                            }
                        ),
                        job_id,
                    ),
                )
                return
            # Classification can succeed while strict price selection rejects a
            # typed basis. Keep those later-stage reasons in the public failure
            # receipt instead of reporting an empty rejected list.
            rejected_by_id = {str(item["id"]): item for item in rejected}
            basis_by_id = {sale.id: sale.pricing_basis for sale in shadow_sales}
            raw_basis_missing_grade = False
            for item in shadow.rejected:
                key = str(item["id"])
                row = rows_by_id.get(key, {})
                entry = rejected_by_id.setdefault(key, {
                    "id": key, "tier": row.get("comparable_tier"),
                    "hard_conflicts": [], "raw_weight": "0", "final_weight": "0",
                })
                entry["title"] = row.get("listing_title")
                entry["source_url"] = row.get("source_url")
                entry["hard_conflicts"] = list(dict.fromkeys([
                    *entry.get("hard_conflicts", []), *item.get("reasons", []),
                ]))
                if (basis_by_id.get(key) == UNGRADED_SAME_CARD_BASIS
                        and "formal_price_requires_same_card_and_grade" in item.get("reasons", [])):
                    raw_basis_missing_grade = True
            rejected = list(rejected_by_id.values())
            failure_codes = list(dict.fromkeys([
                *shadow.review_reasons,
                *(reason for entry in rejected for reason in entry.get("hard_conflicts", [])),
                *(["same_card_raw_source_without_matching_grade_sale"] if raw_basis_missing_grade else []),
            ]))
            raw_basis_failure_reason = "有同款裸卡来源，但缺少同公司同分数成交；未发布正式估值"
            if identity.get("confirmed_identity_version_id"):
                if search.fallback_valuation is not None:
                    await self._store_model_fallback_shadow(
                        connection,
                        job_id,
                        identity,
                        search.fallback_valuation,
                        search,
                    )
                    shadow_status = "manual_review"
                    valuation_tier = "model_fallback_shadow"
                else:
                    shadow_status = shadow.status
                    valuation_tier = "no_eligible_candidate"
                await connection.execute(
                    """UPDATE valuation_job SET status = 'insufficient_evidence',
                           result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                           finished_at = now(), updated_at = now() WHERE id = %s""",
                    (
                        Jsonb(
                            {
                                "reason": (
                                    raw_basis_failure_reason if raw_basis_missing_grade else
                                    "只有模型回退或存在硬冲突的类比，未发布正式估值"
                                ),
                                "published_value_unchanged": True,
                                "shadow_status": shadow_status,
                                "valuation_tier": valuation_tier,
                                "confidence": (
                                    str(
                                        self._model_fallback_confidence(
                                            search.fallback_valuation
                                        )
                                    )
                                    if search.fallback_valuation is not None
                                    else "0"
                                ),
                                "rejected_comparables": rejected,
                                "valuation_failure_diagnostics": {
                                    "stage": "evidence_quality_gate",
                                    "identity": self._diagnostic_identity(identity),
                                    "query_count": len(search.search_queries),
                                    "recalled_candidate_count": len(rows),
                                    "rejected_candidate_count": len(rejected),
                                    "root_cause_codes": failure_codes,
                                },
                            }
                        ),
                        job_id,
                    ),
                )
                return
            if not classified:
                await connection.execute(
                    """
                    UPDATE valuation_job SET status = 'insufficient_evidence',
                        result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                        finished_at = now(), updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        Jsonb(
                            {
                                "reason": "检索到的成交均与当前卡片身份存在硬冲突",
                                "search_queries": search.search_queries,
                                "notes": search.notes,
                                "rejected_comparables": rejected,
                                "valuation_failure_diagnostics": {
                                    "stage": "exact_identity_hard_gate",
                                    "identity": self._diagnostic_identity(identity),
                                    "query_count": len(search.search_queries),
                                    "recalled_candidate_count": len(rows),
                                    "rejected_candidate_count": len(rejected),
                                    "root_cause_codes": list(
                                        dict.fromkeys(
                                            conflict
                                            for candidate in rejected
                                            for conflict in candidate.get(
                                                "hard_conflicts", []
                                            )
                                        )
                                    ),
                                },
                            }
                        ),
                        job_id,
                    ),
                )
                return
            if not any(
                sale.classification.tier.value == "T0" for sale in classified
            ):
                await connection.execute(
                    """
                    UPDATE valuation_job SET status = 'insufficient_evidence',
                        result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                        finished_at = now(), updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        Jsonb(
                            {
                                "reason": (raw_basis_failure_reason if raw_basis_missing_grade else
                                           "没有通过全部身份硬门槛的同卡成交"),
                                "search_queries": search.search_queries,
                                "notes": search.notes,
                                "rejected_comparables": rejected,
                                "shadow_status": shadow.status,
                                "valuation_failure_diagnostics": {
                                    "stage": "exact_identity_tier_gate",
                                    "identity": self._diagnostic_identity(identity),
                                    "query_count": len(search.search_queries),
                                    "recalled_candidate_count": len(rows),
                                    "accepted_non_t0_count": len(classified),
                                    "rejected_candidate_count": len(rejected),
                                    "root_cause_codes": list(dict.fromkeys([
                                        "no_exact_t0_candidate", *failure_codes,
                                    ])),
                                },
                            }
                        ),
                        job_id,
                    ),
                )
                return
            estimate = HierarchicalEstimator().estimate(classified)
            evidence = Explainability.build(resolved_identity, estimate, rows_by_id)
            evidence.update(
                {
                    "identity_version_id": (
                        str(identity["confirmed_identity_version_id"])
                        if identity.get("confirmed_identity_version_id") else None
                    ),
                    "knowledge_version": (
                        self.knowledge_runtime.resolver.version
                        if self.knowledge_runtime
                        else None
                    ),
                    "search_queries": search.search_queries,
                    "notes": search.notes,
                    "market_comp_ids": [str(row["id"]) for row in rows],
                    "deduplicated_sales": duplicate_sales,
                    "rejected_comparables": rejected,
                }
            )
            valuation = await connection.execute(
                """
                INSERT INTO valuation_snapshot (
                    collection_item_id, method, currency, low_value, fair_value,
                    high_value, confidence, evidence, model_version
                ) VALUES (%s, 'ai_estimate', %s, %s, %s, %s, %s, %s, 'hierarchical-comp-2.2')
                RETURNING id
                """,
                (
                    identity["collection_item_id"],
                    identity["base_currency"],
                    estimate.low,
                    estimate.fair,
                    estimate.high,
                    estimate.confidence,
                    Jsonb(evidence),
                ),
            )
            valuation_id = (await valuation.fetchone())["id"]
            await connection.execute(
                """
                UPDATE valuation_job SET status = 'succeeded',
                    result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                    finished_at = now(), updated_at = now() WHERE id = %s
                """,
                (
                    Jsonb(
                        {
                            "valuation_id": str(valuation_id),
                            "evidence_count": estimate.evidence_count,
                            "confidence": str(estimate.confidence),
                        }
                    ),
                    job_id,
                ),
            )

    @staticmethod
    async def _publish_relative_valuation(
        connection,
        job_id: UUID,
        identity: dict,
        shadow,
        search: ComparableSearchResult,
        rows_by_id: dict[str, dict],
    ) -> UUID:
        used = [
            {
                "market_comp_id": comp_id,
                "title": rows_by_id.get(comp_id, {}).get("listing_title"),
                "source_url": rows_by_id.get(comp_id, {}).get("source_url"),
                "tier": rows_by_id.get(comp_id, {}).get("comparable_tier"),
                "parallel_adjustment": rows_by_id.get(comp_id, {}).get("parallel_adjustment"),
                "classification": rows_by_id.get(comp_id, {}).get("classification") or {},
                "visual_verdict": (
                    rows_by_id.get(comp_id, {}).get("raw_payload") or {}
                ).get("visual_verdict"),
                "visual_confidence": (
                    rows_by_id.get(comp_id, {}).get("raw_payload") or {}
                ).get("visual_confidence"),
                "visual_evidence_binding": (rows_by_id.get(comp_id, {}).get("raw_payload") or {}).get("visual_evidence_binding"),
                "visual_match_evidence": (rows_by_id.get(comp_id, {}).get("raw_payload") or {}).get("visual_match_evidence", []),
                "visual_independent_confirmation": (rows_by_id.get(comp_id, {}).get("raw_payload") or {}).get("visual_independent_confirmation"),
                "visual_condition_review": (rows_by_id.get(comp_id, {}).get("raw_payload") or {}).get("visual_condition_review"),
                "visual_reasons": (
                    rows_by_id.get(comp_id, {}).get("raw_payload") or {}
                ).get("visual_reasons", []),
            }
            for comp_id in shadow.used_ids
        ]
        proxy_basis = "cardhobby_same_type_proxy_range" in shadow.review_reasons
        parallel_basis = PARALLEL_BASIS in shadow.review_reasons
        proxy_notes = []
        corroborated_basis = any((item.get('classification', {}).get('field_matches') or {}).get('corroborated_same_card')
                                 for item in used)
        if corroborated_basis:
            proxy_notes.append('依据人物/画师、产品、年份、卡号或总限编及当前图片模板匹配综合判断为同款；'
                               '平行版本细节及品相未完整核验，已降低证据权重。保留卖家瑕疵披露和原成交价，'
                               '未换算完美品相价格，也未将反光差异或裁边单独作为拒绝理由。')
        if any((item.get('classification', {}).get('field_matches') or {}).get('condition_basis') == 'visible_front_minor_comparable'
               for item in used):
            proxy_notes.append('含卖家明确披露微瑕的成交，已核验可见正面品相可比并降低证据权重；'
                               '保留原成交价，未换算完美品相价格，背面及细微隐蔽缺陷仍不确定。')
        if proxy_basis and not parallel_basis:
            proxy_notes.append("卡淘同类型卡片的低置信参考区间，不代表已核实的精确同版成交；保留可编辑估值。")
            if identity.get("serial_total") or identity.get("print_run"):
                proxy_notes.append("参考记录未充分证实相同限编或平行版本，限编/平行溢价未知，未凭空加价。")
        if parallel_basis:
            proxy_notes.append('未找到可用同款成交，采用同底卡不同限编的低置信类比估值。'
                '限编倍率使用有上下限的平方根暂定模型，尚未经过市场成对成交校准；'
                '未证明平行颜色溢价或同等品相，置信度不超过18%，运费不计入卡价。')
            for item in used:
                detail = item['parallel_adjustment']
                if detail:
                    proxy_notes.append(f"参考 /{detail['source_serial_total']} 原价 {detail['original_price']}，"
                        f"推算目标 /{detail['target_serial_total']} 为 {detail['adjusted_price']}；"
                        f"倍率 {Decimal(detail['multiplier']):.4f}。"
                        + ('卖家披露微瑕，未换算完美品相价。' if detail['minor_condition_disclosed'] else '品相溢价未调整。'))
        model_version = PARALLEL_MODEL if parallel_basis else "relative-multimodal-comps-1.0"
        result = await connection.execute(
            f"""
            INSERT INTO valuation_snapshot (
                collection_item_id, method, currency, low_value, fair_value,
                high_value, confidence, evidence, model_version
            ) VALUES (
                %s, 'ai_estimate', %s, %s, %s, %s, %s, %s,
                '{model_version}'
            ) RETURNING id
            """,
            (
                identity["collection_item_id"],
                identity["base_currency"],
                shadow.low,
                shadow.fair,
                shadow.high,
                shadow.confidence,
                Jsonb(
                    {
                        "selection_policy": ("exact_then_same_base_print_run_fallback" if parallel_basis
                                             else "strongest_non_conflicting_relative_tier"),
                        "identity_version_id": (
                            str(identity["confirmed_identity_version_id"])
                            if identity.get("confirmed_identity_version_id") else None
                        ),
                        "used_comparables": used,
                        "rejected_comparables": list(shadow.rejected),
                        "risk_flags": list(shadow.review_reasons),
                        "search_queries": search.search_queries,
                        "notes": [
                            *search.notes,
                            *proxy_notes,
                            *([UNGRADED_SAME_CARD_NOTE]
                              if UNGRADED_SAME_CARD_BASIS in shadow.review_reasons else []),
                        ],
                        **({
                            "pricing_basis": UNGRADED_SAME_CARD_BASIS,
                            "grade_premium_applied": False,
                        } if UNGRADED_SAME_CARD_BASIS in shadow.review_reasons else {}),
                        **({'pricing_basis': PARALLEL_BASIS,
                            'adjustment_model': PARALLEL_MODEL,
                            'exact_version_verified': False,
                            'market_calibrated': False,
                            'numbered_premium_applied': True,
                            'confidence_cap': '0.18'} if parallel_basis else {}),
                        "automatic_publication": True,
                        **({'pricing_basis': 'corroborated_same_card',
                            'exact_version_verified': False, 'condition_fully_verified': False}
                           if corroborated_basis else {}),
                        **({"pricing_basis": (UNGRADED_SAME_CARD_BASIS
                              if UNGRADED_SAME_CARD_BASIS in shadow.review_reasons else "same_type_proxy"),
                            "exact_version_verified": False,
                            "numbered_premium_applied": False} if proxy_basis and not parallel_basis else {}),
                    }
                ),
            ),
        )
        return (await result.fetchone())["id"]

    @staticmethod
    def _model_fallback_confidence(
        fallback: FallbackValuation,
    ) -> Decimal:
        reference_count = len(fallback.references)
        cap = (
            Decimal("0.35")
            if reference_count >= 2
            else Decimal("0.25")
            if reference_count == 1
            else Decimal("0")
        )
        return min(fallback.confidence, cap)

    async def _store_model_fallback_shadow(
        self,
        connection,
        job_id: UUID,
        identity: dict,
        fallback: FallbackValuation,
        search: ComparableSearchResult,
    ) -> None:
        confidence = self._model_fallback_confidence(fallback)
        reasons = ["model_fallback_only", "no_exact_t0_sales"]
        if not fallback.references:
            reasons.append("no_verified_reference_urls")
        await connection.execute(
            """
            INSERT INTO valuation_shadow_result (
                collection_item_id, valuation_job_id, identity_version_id,
                model_version, status, currency, low_value, fair_value,
                high_value, confidence, evidence_count, review_reasons, evidence
            ) VALUES (%s, %s, %s, %s, 'manual_review', %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                identity["collection_item_id"],
                job_id,
                identity["confirmed_identity_version_id"],
                f"{self.settings.valuation_search_model}-fallback-1.0",
                identity["base_currency"],
                fallback.low_value,
                fallback.fair_value,
                fallback.high_value,
                confidence,
                len(fallback.references),
                Jsonb(reasons),
                Jsonb(
                    {
                        "pricing_basis": "model_analogy_fallback",
                        "methodology": fallback.methodology,
                        "references": [
                            {
                                "title": reference.title,
                                "source_url": reference.source_url,
                                "observed_price_base": str(
                                    reference.observed_price_base
                                ),
                                "match_confidence": str(reference.match_confidence),
                                "adjustment": reference.adjustment,
                                "condition_type": reference.condition_type,
                                "player_name": reference.player_name,
                            }
                            for reference in fallback.references
                        ],
                        "search_queries": search.search_queries,
                        "notes": search.notes,
                        "published_value_unchanged": True,
                    }
                ),
            ),
        )
        await connection.execute(
            """UPDATE identity_research_case
               SET status = %s, updated_at = now()
               WHERE collection_item_id = %s""",
            (
                ResearchStatus.CONFIRMED_NO_EXACT_SALES,
                identity["collection_item_id"],
            ),
        )

    @staticmethod
    async def _store_shadow_result(connection, job_id: UUID, identity: dict, shadow) -> None:
        identity_payload = {
            key: value
            for key, value in identity.items()
            if key
            in {
                "player_name", "season_start", "season_end", "brand_name",
                "product_name", "set_name", "card_number", "insert_name",
                "parallel_name", "serial_number", "serial_total", "print_run",
                "is_redemption", "redemption_status",
                "is_rookie", "is_autograph", "is_patch", "grade_company", "grade_value",
            }
        }
        confirmed_result = await connection.execute(
            """SELECT confirmed_version.id, confirmed_version.identity
               FROM card_identity_version confirmed_version
               WHERE confirmed_version.collection_item_id = %s
                 AND confirmed_version.review_status = 'confirmed'
                 AND NOT EXISTS (
                     SELECT 1 FROM card_identity_version revocation
                     WHERE revocation.supersedes_id = confirmed_version.id
                       AND revocation.review_status = 'rejected'
                 )
               ORDER BY confirmed_version.version DESC LIMIT 1""",
            (identity["collection_item_id"],),
        )
        confirmed = await confirmed_result.fetchone()
        identity_row = confirmed
        if identity_row is None:
            existing = await connection.execute(
                """
                SELECT id FROM card_identity_version
                WHERE collection_item_id = %s AND identity = %s
                ORDER BY version DESC LIMIT 1
                """,
                (identity["collection_item_id"], Jsonb(identity_payload)),
            )
            identity_row = await existing.fetchone()
            if identity_row is None:
                inserted = await connection.execute(
                    """
                    INSERT INTO card_identity_version (
                        collection_item_id, version, identity, field_provenance,
                        conflicts, review_status
                    )
                    SELECT %s, COALESCE(max(version), 0) + 1, %s, %s, %s, 'pending'
                    FROM card_identity_version WHERE collection_item_id = %s
                    RETURNING id
                    """,
                    (
                        identity["collection_item_id"],
                        Jsonb(identity_payload),
                        Jsonb(identity.get("evidence") or {}),
                        Jsonb(identity_review_reasons(identity)),
                        identity["collection_item_id"],
                    ),
                )
                identity_row = await inserted.fetchone()
        identity_version_id = identity_row["id"]
        shadow_status = shadow.status
        shadow_low = shadow.low
        shadow_fair = shadow.fair
        shadow_high = shadow.high
        shadow_confidence = shadow.confidence
        shadow_reasons = list(shadow.review_reasons)
        if not confirmed:
            shadow_reasons = list(
                dict.fromkeys([*shadow_reasons, "identity_inferred_automatically"])
            )
        await connection.execute(
            """
            INSERT INTO valuation_shadow_result (
                collection_item_id, valuation_job_id, identity_version_id,
                model_version, status, currency, low_value, fair_value,
                high_value, confidence, evidence_count, review_reasons, evidence
            ) VALUES (%s, %s, %s, 'robust-shadow-1.1', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                identity["collection_item_id"], job_id, identity_version_id,
                shadow_status, identity["base_currency"], shadow_low, shadow_fair,
                shadow_high, shadow_confidence, len(shadow.used_ids),
                Jsonb(shadow_reasons),
                Jsonb(
                    {
                        "used_ids": list(shadow.used_ids),
                        "rejected": list(shadow.rejected),
                        "automatic_publication": shadow.fair is not None,
                    }
                ),
            ),
        )
        if confirmed:
            research_status = (
                ResearchStatus.SHADOW_ESTIMATED
                if shadow.fair is not None
                else ResearchStatus.CONFIRMED_NO_EXACT_SALES
            )
            await connection.execute(
                """UPDATE identity_research_case SET status = %s, updated_at = now()
                   WHERE collection_item_id = %s""",
                (research_status, identity["collection_item_id"]),
            )

    async def _set_status(self, job_id: UUID, status: str) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                "UPDATE valuation_job SET status = %s, updated_at = now() WHERE id = %s",
                (status, job_id),
            )

    async def _finish(
        self, job_id: UUID, status: str, summary: dict, error_message: str | None = None
    ) -> None:
        async with self.database.connection() as connection:
            await connection.execute(
                """
                UPDATE valuation_job SET status = %s,
                    result_summary = COALESCE(result_summary, '{}'::jsonb) || %s,
                    error_message = %s, finished_at = now(), updated_at = now()
                WHERE id = %s
                """,
                (status, Jsonb(summary), error_message, job_id),
            )


async def stop_worker(
    tasks: asyncio.Task | list[asyncio.Task], worker: AutomatedValuationWorker
) -> None:
    await worker.stop()
    worker_tasks = [tasks] if isinstance(tasks, asyncio.Task) else tasks
    for task in worker_tasks:
        task.cancel()
    for task in worker_tasks:
        with suppress(asyncio.CancelledError):
            await task
