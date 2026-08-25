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
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, model_validator

from card_platform.config import Settings
from card_platform.comparable_image_verifier import (
    ComparableImageVerifier,
    VisualCandidate,
)
from card_platform.db import Database
from card_platform.domain.adjustments import Adjustment
from card_platform.domain.card_identity import CardIdentityResolver, normalize_text
from card_platform.domain.comparables import ComparableClassifier, ComparableInput
from card_platform.domain.explainability import Explainability
from card_platform.domain.hierarchical_estimator import ClassifiedSale, HierarchicalEstimator
from card_platform.domain.valuation import ComparableSale, estimate_from_comps
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
    generate_candidates,
)
from card_platform.ingestion.cardhobby import (
    CardHobbyProvider,
    CardHobbySearchResult,
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
from card_platform.market_data.models import MarketIdentity
from card_platform.market_data.protection import ProtectedProvider
from card_platform.market_data.service import MarketDataService
from card_platform.search_index import SearchIndexHit, SearchIndexResearch
from card_platform.knowledge_runtime import KnowledgeRuntime


logger = logging.getLogger(__name__)


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
        # OCR/catalog imports often use the copyright year as season_start.
        # Search the adjacent label for unconfirmed identities, but keep the
        # stored identity unchanged until authoritative review.
        if not identity.get("confirmed_identity_version_id"):
            seasons.append(f"{end}-{str(int(end) + 1)[-2:]}")
            seasons.append(str(end))
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
    if re.fullmatch(r"0*\d+\s*/\s*0*\d+", str(card_number or "").strip()):
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
            else current.get("parallel_name")
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
        "grade_company": recognized.grade_company or current.get("grade_company"),
        "grade_value": (
            recognized.grade_value
            if recognized.grade_value is not None
            else current.get("grade_value")
        ),
    }


def deduplicate_market_rows(
    rows: list[dict],
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
    if re.fullmatch(r"0*\d+\s*/\s*0*\d+", card_number):
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
    matching_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    evidence_level: str = "aggregated_sale"
    source_code: str = "public_market_research"
    source_tier: int = Field(default=3, ge=1, le=3)
    sale_status: str | None = None
    sale_status_evidence: str | None = None
    price_basis: str | None = None
    discovery_url: str | None = None
    search_provider: str | None = None
    search_query: str | None = None
    search_snippet: str | None = None


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
    search_queries: list[str] = Field(default_factory=list)
    comparables: list[DiscoveredComparable] = Field(default_factory=list)
    resolved_identity: dict[str, str | int | bool | list[str] | None] = Field(
        default_factory=dict
    )
    identity_conflicts: list[str] = Field(default_factory=list)
    fallback_valuation: FallbackValuation | None = None
    player_adjustments: list[PlayerAdjustment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

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
        self.cardhobby = (
            CardHobbyProvider(
                self.client,
                settings.valuation_search_model,
                timeout=settings.provider_timeout_seconds,
                resolver=knowledge_runtime.resolver if knowledge_runtime else None,
            )
            if self.client is not None
            else None
        )
        self.search_index = SearchIndexResearch(
            brave_api_key=settings.search_index_brave_api_key,
            serpapi_key=settings.search_index_serpapi_key,
            timeout=settings.search_index_timeout_seconds,
            max_queries=settings.search_index_max_queries,
            concurrency=settings.search_index_concurrency,
        )
        providers = []
        if self.cardhobby is not None:
            providers.append(
                ProtectedProvider(
                    database,
                    CardHobbyMarketDataProvider(self.cardhobby),
                )
            )
        if settings.ebay_client_id and settings.ebay_client_secret:
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
                if await self.market_data.process_one_refresh():
                    continue
                job_id = await self._claim()
                if job_id is None:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.settings.valuation_worker_poll_seconds
                    )
                    continue
                await self._process(job_id)
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
            identity = await self._load_identity(job_id)
            specific, missing_fields = valuation_identity_specificity(identity)
            if not specific:
                research = await self._start_identity_research(identity, missing_fields)
                await self._finish(
                    job_id,
                    "insufficient_evidence",
                    {
                        "reason": "身份冲突已转入研究，确认候选身份后再生成影子估值",
                        "missing_identity_fields": missing_fields,
                        "research_status": research["status"],
                        "research_case_id": research["case_id"],
                        "candidate_count": research["candidate_count"],
                    },
                )
                return
            await self._set_status(job_id, "searching")
            search = await self._search(identity)
            await self._store_comparables(identity, search)
            await self._set_status(job_id, "calculating")
            await self._calculate(job_id, identity, search)
        except Exception as error:
            await self._finish(
                job_id,
                "failed",
                {"reason": "valuation worker failed"},
                str(error)[:500],
            )

    async def _load_identity(self, job_id: UUID) -> dict:
        async with self.database.connection() as connection:
            result = await connection.execute(
                """
                SELECT vj.collection_item_id, ci.card_definition_id, u.base_currency,
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
                       ci.image_front_url, ci.image_back_url, ci.notes,
                       purchase.notes AS purchase_context,
                       purchase.venue AS purchase_venue,
                       purchase.unit_price AS purchase_price,
                       confirmed.identity AS confirmed_identity,
                       confirmed.id AS confirmed_identity_version_id,
                       latest_identity.review_status AS latest_identity_review_status
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
                LEFT JOIN LATERAL (
                    SELECT id, identity FROM card_identity_version
                    WHERE collection_item_id = ci.id AND review_status = 'confirmed'
                    ORDER BY version DESC LIMIT 1
                ) confirmed ON true
                LEFT JOIN LATERAL (
                    SELECT review_status
                    FROM card_identity_version
                    WHERE collection_item_id = ci.id
                    ORDER BY version DESC LIMIT 1
                ) latest_identity ON true
                WHERE vj.id = %s
                """,
                (job_id,),
            )
            row = await result.fetchone()
            if row is None:
                raise RuntimeError("valuation job item not found")
            stored = dict(row)
            confirmed_identity = stored.pop("confirmed_identity", None)
            if confirmed_identity:
                # The immutable confirmed version is the valuation identity source;
                # collection/card_definition remain compatibility projections only.
                stored.update(confirmed_identity)
                stored["print_run"] = confirmed_identity.get("serial_total")
                stored["is_patch"] = confirmed_identity.get("is_memorabilia", False)
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

    async def _start_identity_research(self, identity: dict, blockers: list[str]) -> dict:
        current = {
            key: identity.get(key)
            for key in (
                "player_name", "season_start", "season_end", "brand_name",
                "product_name", "set_name", "card_number", "insert_name",
                "parallel_name", "serial_number", "serial_total", "is_rookie",
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
            # optional vision provider is unavailable. Prefer them for these
            # exact image-backed items so a provider timeout does not delay the
            # review queue; they still require explicit user confirmation.
            visual = fixture
        else:
            researched = await self._research_visual_identity(identity)
            if researched:
                visual = researched
        candidates = generate_candidates(current, visual)
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
                (identity["collection_item_id"], ResearchStatus.AWAITING_CONFIRMATION if candidates
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
        return {"case_id": str(case_id), "status": ResearchStatus.AWAITING_CONFIRMATION
                if candidates else ResearchStatus.REQUIRED, "candidate_count": len(candidates)}

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
                recognized = await self.vision_service.recognize_pair_contents(
                    local_images[0][0], local_images[0][1],
                    local_images[1][0], local_images[1][1],
                )
                texture_text = [
                    recognized.parallel_name or "", *recognized.caveats,
                    *(text for values in recognized.field_evidence.values() for text in values),
                ]
                checklist_evidence = [
                    {
                        "source_url": recognized.reference_image_source_url,
                        "image_url": recognized.reference_image_front_url,
                        "match_confidence": (
                            str(recognized.reference_image_match_confidence)
                            if recognized.reference_image_match_confidence is not None
                            else None
                        ),
                    }
                ] if recognized.reference_image_source_url else []
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
            response = await self.client.chat.completions.create(
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
        cached = await self.market_data.get(
            market_identity,
            collection_item_id=identity["collection_item_id"],
            card_definition_id=identity["card_definition_id"],
        )
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
            )
            for item in cached.evidence
        ]
        resolved_identity = CardIdentityResolver().resolve(identity)
        classifier = ComparableClassifier(
            resolver=self.knowledge_runtime.resolver if self.knowledge_runtime else None
        )
        rejected_cached = 0
        safe_comparables = []
        for comp in comparables:
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
            if classification.hard_conflicts:
                rejected_cached += 1
            else:
                safe_comparables.append(comp)
        comparables = safe_comparables
        notes = [
            *cached.notes,
            f"市场数据新鲜度：{cached.freshness.value}",
            (
                f"成交证据截至：{cached.data_as_of.isoformat()}"
                if cached.data_as_of
                else "暂无已缓存成交证据"
            ),
        ]
        if rejected_cached:
            notes.append(f"已在估值前剔除 {rejected_cached} 条身份冲突缓存成交")
        result = ComparableSearchResult(
            comparables=comparables[:12], notes=notes
        )
        if len(result.comparables) < self.market_data.MIN_EVIDENCE and (
            self.client is not None or self.search_index.enabled
        ):
            # The durable provider cache is intentionally strict. Rare cards get
            # a separately labelled, source-backed fallback instead of becoming
            # permanently unpriceable when exact sales are sparse.
            try:
                async with asyncio.timeout(
                    self.settings.valuation_search_timeout_seconds
                ):
                    external = await self._search_external_legacy(identity)
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
                    "comparables": list(by_url.values())[:12],
                    "notes": self._truthful_source_availability_notes(
                        [*notes, *external.notes], cached.provider_diagnostics
                    ),
                }
            )
        result = await self._verify_comparable_images(identity, result)
        return await self._review_comparable_evidence(identity, result)

    async def _review_comparable_evidence(
        self, identity: dict, search: ComparableSearchResult
    ) -> ComparableSearchResult:
        if self.evidence_reviewer is None or not search.comparables:
            return search
        evidence = [
            {
                "evidence_id": comp.source_url,
                "title": comp.title,
                "evidence_level": comp.evidence_level,
                "grade_company": comp.grade_company,
                "grade_value": str(comp.grade_value) if comp.grade_value is not None else None,
            }
            for comp in search.comparables
        ]
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
        })

    async def _verify_comparable_images(
        self,
        identity: dict,
        search: ComparableSearchResult,
    ) -> ComparableSearchResult:
        candidates = [
            VisualCandidate(
                id=comp.source_url,
                title=comp.title,
                image_url=comp.image_url,
            )
            for comp in search.comparables
            if comp.image_url
        ]
        if not candidates:
            return search
        try:
            # Image hosts and the vision provider are both remote dependencies.
            # Do not let an optional visual cross-check hold an otherwise valid
            # valuation job in `searching` indefinitely.
            async with asyncio.timeout(
                min(self.settings.valuation_search_timeout_seconds, 65.0)
            ):
                decisions = await self.image_verifier.verify(
                    identity.get("image_front_url"),
                    candidates,
                    requires_parallel_match=(
                        str(identity.get("parallel_name") or identity.get("parallel") or "")
                        .strip()
                        .casefold()
                        not in {"", "base"}
                    ),
                )
        except TimeoutError:
            return search.model_copy(
                update={
                    "notes": [
                        *search.notes,
                        "图片同卡核验超时，已使用严格文字身份匹配继续估值",
                    ]
                }
            )
        except Exception as error:
            return search.model_copy(
                update={
                    "notes": [
                        *search.notes,
                        f"图片同卡核验失败，已降级为文字分层：{str(error)[:160]}",
                    ]
                }
            )
        if not decisions:
            return search.model_copy(
                update={
                    "notes": [
                        *search.notes,
                        "图片同卡核验未返回可用结论，候选仅按文字分层",
                    ]
                }
            )
        comparables = []
        for comp in search.comparables:
            decision = decisions.get(comp.source_url)
            if decision is None:
                comparables.append(comp)
                continue
            comparables.append(
                comp.model_copy(
                    update={
                        "visual_verdict": decision.verdict,
                        "visual_confidence": decision.confidence,
                        "visual_reasons": decision.reasons,
                    }
                )
            )
        return search.model_copy(update={"comparables": comparables})

    async def _search_external_legacy(self, identity: dict) -> ComparableSearchResult:
        """Deprecated synchronous search retained temporarily for migration rollback only."""
        cardhobby_result = None
        if self.cardhobby is not None:
            try:
                cardhobby_result = await self.cardhobby.search(identity)
            except Exception as error:
                cardhobby_result = None
                cardhobby_error = f"卡淘定向检索失败：{str(error)[:180]}"
            else:
                cardhobby_error = None
        else:
            cardhobby_error = "卡淘定向检索未配置"
        if cardhobby_result is not None and cardhobby_result.sales:
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
                        if comp.match_confidence >= Decimal("0.5")
                        and self._comparable_grade_matches(identity, comp)
                    ][:12]
                }
            )
            if len(direct_result.comparables) >= self.market_data.MIN_EVIDENCE:
                return direct_result
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
        response = await self.client.chat.completions.create(
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
                and normalized.match_confidence >= Decimal("0.5")
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
            for sale in result.sales
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
        async with self.database.connection() as connection, connection.transaction():
            sources = await connection.execute(
                "SELECT id, code FROM data_source "
                "WHERE code IN ('automated_market_search', 'cardhobby', "
                "'ebay_public', 'sportscardspro', 'sports_card_investor', "
                "'public_market_research')"
            )
            source_ids = {row["code"]: row["id"] for row in await sources.fetchall()}
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
                        match_confidence, source_reliability, classification, raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (card_definition_id, data_source_id, external_id)
                    DO UPDATE SET
                        match_confidence = EXCLUDED.match_confidence,
                        source_reliability = EXCLUDED.source_reliability,
                        classification = market_comp.classification || EXCLUDED.classification,
                        raw_payload = EXCLUDED.raw_payload
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
                    ),
                )

    async def _calculate(
        self, job_id: UUID, identity: dict, search: ComparableSearchResult
    ) -> None:
        async with self.database.connection() as connection, connection.transaction():
            result = await connection.execute(
                """
                SELECT market_comp.id, market_comp.occurred_at,
                       market_comp.sale_price, market_comp.shipping_price,
                       market_comp.currency, market_comp.fx_rate_to_base,
                       market_comp.listing_title, market_comp.source_url,
                       market_comp.match_confidence, market_comp.grade_company,
                       market_comp.grade_value, market_comp.raw_payload,
                       market_comp.external_id, market_comp.classification,
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
                ORDER BY market_comp.occurred_at DESC
                """,
                (identity["card_definition_id"],),
            )
            allowed_source_urls = {comp.source_url for comp in search.comparables}
            raw_rows = [
                dict(row)
                for row in await result.fetchall()
                if row["source_url"] in allowed_source_urls
            ]
            rows, duplicate_sales = deduplicate_market_rows(raw_rows)
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
                    coefficients = self.build_player_coefficients(
                        identity, search.player_adjustments
                    )
                    adjusted_references: list[tuple[ValuationReference, Decimal, dict | None]] = []
                    for reference in fallback.references:
                        if reference.player_name.casefold() == str(identity["player_name"]).casefold():
                            adjusted_references.append(
                                (reference, reference.observed_price_base, None)
                            )
                            continue
                        coefficient = coefficients.get(reference.player_name.casefold())
                        if coefficient is not None:
                            adjusted_references.append(
                                (
                                    reference,
                                    reference.observed_price_base * coefficient["coefficient"],
                                    coefficient,
                                )
                            )
                    if not adjusted_references:
                        fallback_failure_reason = (
                            "跨球员参考未满足至少3组来源可访问、卡型相近且评级状态一致的"
                            "成对成交，无法计算可复算的球员价格系数"
                        )
                        fallback = None
                if fallback is not None:
                    fallback_estimate = estimate_from_comps(
                        [
                            ComparableSale(
                                value_base=adjusted_value,
                                age_days=0,
                                match_confidence=reference.match_confidence,
                            )
                            for reference, adjusted_value, _ in adjusted_references
                        ]
                    )
                    confidence = min(fallback.confidence, fallback_estimate.confidence)
                    evidence = {
                        "knowledge_version": (
                            self.knowledge_runtime.resolver.version
                            if self.knowledge_runtime
                            else None
                        ),
                        "strategy": "分层类比估值（含可复算的跨球员价格系数）",
                        "formula": (
                            "后端先用至少3组成对成交的目标/参考球员价格比中位数计算系数，"
                            "再折算参考价格并计算加权分位数。"
                        ),
                        "search_queries": search.search_queries,
                        "notes": [*search.identity_conflicts, *search.notes],
                        "resolved_identity": search.resolved_identity,
                        "player_adjustments": [
                            {
                                **coefficient,
                                "coefficient": str(coefficient["coefficient"]),
                                "ratio_low": str(coefficient["ratio_low"]),
                                "ratio_high": str(coefficient["ratio_high"]),
                            }
                            for coefficient in coefficients.values()
                        ],
                        "comparables": [
                            {
                                "id": f"fallback-{index}",
                                "title": reference.title,
                                "source_url": reference.source_url,
                                "occurred_at": (
                                    reference.occurred_at.isoformat()
                                    if reference.occurred_at
                                    else datetime.now().isoformat()
                                ),
                                "original_price": str(reference.observed_price_base),
                                "currency": identity["base_currency"],
                                "fx_rate_to_base": "1",
                                "normalized_price": str(adjusted_value),
                                "match_confidence": str(reference.match_confidence),
                                "age_days": 0,
                                "evidence_type": reference.evidence_type,
                                "adjustment": reference.adjustment,
                                "condition_type": reference.condition_type,
                                "grade_company": reference.grade_company,
                                "grade_value": (
                                    str(reference.grade_value)
                                    if reference.grade_value is not None
                                    else None
                                ),
                                "player_name": reference.player_name,
                                "player_coefficient": (
                                    str(coefficient["coefficient"])
                                    if coefficient is not None
                                    else "1"
                                ),
                            }
                            for index, (reference, adjusted_value, coefficient) in enumerate(
                                adjusted_references
                            )
                        ],
                    }
                    valuation = await connection.execute(
                        """
                        INSERT INTO valuation_snapshot (
                            collection_item_id, method, currency, low_value, fair_value,
                            high_value, confidence, evidence, model_version
                        ) VALUES (%s, 'ai_estimate', %s, %s, %s, %s, %s, %s, 'player-index-comp-1.2')
                        RETURNING id
                        """,
                        (
                            identity["collection_item_id"],
                            identity["base_currency"],
                            fallback_estimate.low,
                            fallback_estimate.fair,
                            fallback_estimate.high,
                            confidence,
                            Jsonb(evidence),
                        ),
                    )
                    valuation_id = (await valuation.fetchone())["id"]
                    await connection.execute(
                        """
                        UPDATE valuation_job SET status = 'succeeded', result_summary = %s,
                            finished_at = now(), updated_at = now() WHERE id = %s
                        """,
                        (
                            Jsonb(
                                {
                                    "valuation_id": str(valuation_id),
                                    "evidence_count": len(adjusted_references),
                                    "confidence": str(confidence),
                                    "valuation_tier": "fallback",
                                }
                            ),
                            job_id,
                        ),
                    )
                    return
                await connection.execute(
                    """
                    UPDATE valuation_job SET status = 'insufficient_evidence',
                        result_summary = %s, finished_at = now(), updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        Jsonb(
                            {
                                "reason": fallback_failure_reason
                                or "未找到匹配度达到 50% 的真实成交",
                                "search_queries": search.search_queries,
                                "notes": search.notes,
                            }
                        ),
                        job_id,
                    ),
                )
                return
            resolved_identity = CardIdentityResolver().resolve(identity)
            resolver = self.knowledge_runtime.resolver if self.knowledge_runtime else None
            classifier = ComparableClassifier(resolver=resolver)
            classified: list[ClassifiedSale] = []
            shadow_sales: list[ShadowSale] = []
            rejected: list[dict] = []
            rows_by_id = {str(row["id"]): dict(row) for row in rows}
            for row in rows:
                source_reliability = row["source_reliability"] or (
                    Decimal("0.70") if row["source_code"] == "cardhobby" else Decimal("0.75")
                )
                classification = classifier.classify(
                    resolved_identity,
                    ComparableInput(
                        title=row["listing_title"] or "",
                        grade_company=row["grade_company"],
                        grade_value=row["grade_value"],
                        source_reliability=source_reliability,
                        source_code=row["source_code"],
                        visual_verdict=(row["raw_payload"] or {}).get(
                            "visual_verdict"
                        ),
                        visual_confidence=(
                            Decimal(
                                str(
                                    (row["raw_payload"] or {}).get(
                                        "visual_confidence"
                                    )
                                )
                            )
                            if (row["raw_payload"] or {}).get("visual_confidence")
                            is not None
                            else None
                        ),
                        visual_reasons=tuple(
                            (row["raw_payload"] or {}).get("visual_reasons") or []
                        ),
                    ),
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
                }
                grade = evidence_grade(row["evidence_level"])
                shadow_sales.append(
                    ShadowSale(
                        id=str(row["id"]),
                        provider_code=row["source_code"],
                        price=row["value_base"],
                        shipping=row["shipping_base"],
                        age_days=row["age_days"],
                        tier=classification.tier,
                        similarity=classification.similarity,
                        source_reliability=classification.source_reliability,
                        evidence_level=row["evidence_level"],
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
                        sale_price_base=row["value_base"],
                        shipping_base=row["shipping_base"],
                        age_days=row["age_days"],
                        classification=classification,
                        adjustment=Adjustment(),
                    )
                )
            shadow = ShadowValuationEstimator().estimate(shadow_sales, identity)
            await self._store_shadow_result(connection, job_id, identity, shadow)
            if shadow.fair is not None:
                valuation_id = await self._publish_relative_valuation(
                    connection, job_id, identity, shadow, search, rows_by_id
                )
                await connection.execute(
                    """UPDATE valuation_job SET status = 'succeeded', result_summary = %s,
                           finished_at = now(), updated_at = now() WHERE id = %s""",
                    (
                        Jsonb(
                            {
                                "valuation_id": str(valuation_id),
                                "evidence_count": len(shadow.used_ids),
                                "confidence": str(shadow.confidence),
                                "valuation_tier": "best_relative_candidate",
                                "risk_flags": list(shadow.review_reasons),
                            }
                        ),
                        job_id,
                    ),
                )
                return
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
                           result_summary = %s,
                           finished_at = now(), updated_at = now() WHERE id = %s""",
                    (
                        Jsonb(
                            {
                                "reason": (
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
                        result_summary = %s, finished_at = now(), updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        Jsonb(
                            {
                                "reason": "检索到的成交均与当前卡片身份存在硬冲突",
                                "search_queries": search.search_queries,
                                "notes": search.notes,
                                "rejected_comparables": rejected,
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
                        result_summary = %s, finished_at = now(), updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        Jsonb(
                            {
                                "reason": "没有通过全部身份硬门槛的同卡成交",
                                "search_queries": search.search_queries,
                                "notes": search.notes,
                                "rejected_comparables": rejected,
                                "shadow_status": shadow.status,
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
                UPDATE valuation_job SET status = 'succeeded', result_summary = %s,
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
                "classification": rows_by_id.get(comp_id, {}).get("classification") or {},
                "visual_verdict": (
                    rows_by_id.get(comp_id, {}).get("raw_payload") or {}
                ).get("visual_verdict"),
                "visual_confidence": (
                    rows_by_id.get(comp_id, {}).get("raw_payload") or {}
                ).get("visual_confidence"),
            }
            for comp_id in shadow.used_ids
        ]
        result = await connection.execute(
            """
            INSERT INTO valuation_snapshot (
                collection_item_id, method, currency, low_value, fair_value,
                high_value, confidence, evidence, model_version
            ) VALUES (
                %s, 'ai_estimate', %s, %s, %s, %s, %s, %s,
                'relative-multimodal-comps-1.0'
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
                        "selection_policy": "strongest_non_conflicting_relative_tier",
                        "used_comparables": used,
                        "rejected_comparables": list(shadow.rejected),
                        "risk_flags": list(shadow.review_reasons),
                        "search_queries": search.search_queries,
                        "notes": search.notes,
                        "automatic_publication": True,
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
                "is_rookie", "is_autograph", "is_patch", "grade_company", "grade_value",
            }
        }
        confirmed_result = await connection.execute(
            """SELECT id, identity FROM card_identity_version
               WHERE collection_item_id = %s AND review_status = 'confirmed'
               ORDER BY version DESC LIMIT 1""",
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
                UPDATE valuation_job SET status = %s, result_summary = %s,
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
