import asyncio
import json
import logging
import re
from contextlib import suppress
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse
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
from card_platform.market_data.cardhobby_provider import CardHobbyMarketDataProvider
from card_platform.market_data.ebay_provider import EbayMarketplaceInsightsProvider
from card_platform.market_data.models import MarketIdentity
from card_platform.market_data.protection import ProtectedProvider
from card_platform.market_data.service import MarketDataService
from card_platform.knowledge_runtime import KnowledgeRuntime


logger = logging.getLogger(__name__)


VALUATION_SITES = [
    "ebay.com",
    "psacard.com",
    "130point.com",
    "goldin.co",
    "fanaticscollect.com",
    "beckett.com",
]


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
    resolved_identity: dict[str, str | int | bool | None] = Field(default_factory=dict)
    identity_conflicts: list[str] = Field(default_factory=list)
    fallback_valuation: FallbackValuation | None = None
    player_adjustments: list[PlayerAdjustment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
            result = await connection.execute(
                """
                SELECT id FROM valuation_job
                WHERE status = 'queued' AND attempt_count < 3
                ORDER BY created_at
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
                await self._finish(
                    job_id,
                    "insufficient_evidence",
                    {
                        "reason": "卡片身份不够具体，禁止将同球员同产品的不同卡混合估值",
                        "missing_identity_fields": missing_fields,
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
                       p.full_name AS player_name, b.name AS brand_name,
                       cs.season_start, cs.season_end, cs.product_name, cs.set_name,
                       cd.card_number, cd.insert_name, cd.parallel_name,
                       cd.is_rookie, cd.is_autograph, cd.is_patch, cd.print_run,
                       ci.serial_number, ci.serial_total, ci.grade_company, ci.grade_value,
                       ci.image_front_url, ci.image_back_url, ci.notes,
                       purchase.notes AS purchase_context,
                       purchase.venue AS purchase_venue,
                       purchase.unit_price AS purchase_price
                FROM valuation_job vj
                JOIN collection_item ci ON ci.id = vj.collection_item_id
                JOIN app_user u ON u.id = ci.owner_id
                JOIN card_definition cd ON cd.id = ci.card_definition_id
                JOIN player p ON p.id = cd.player_id
                JOIN card_set cs ON cs.id = cd.card_set_id
                JOIN brand b ON b.id = cs.brand_id
                LEFT JOIN LATERAL (
                    SELECT notes, venue, unit_price
                    FROM card_transaction
                    WHERE collection_item_id = ci.id AND transaction_type = 'buy'
                    ORDER BY transacted_at DESC LIMIT 1
                ) purchase ON true
                WHERE vj.id = %s
                """,
                (job_id,),
            )
            row = await result.fetchone()
            if row is None:
                raise RuntimeError("valuation job item not found")
            stored = dict(row)
            resolved = CardIdentityResolver().resolve(stored)
            return {
                **stored,
                **resolved.as_dict(),
                "insert_name": resolved.insert,
                "parallel_name": resolved.parallel,
            }

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
            )
            for item in cached.evidence
        ]
        notes = [
            *cached.notes,
            f"市场数据新鲜度：{cached.freshness.value}",
            (
                f"成交证据截至：{cached.data_as_of.isoformat()}"
                if cached.data_as_of
                else "暂无已缓存成交证据"
            ),
        ]
        result = ComparableSearchResult(comparables=comparables[:12], notes=notes)
        return await self._verify_comparable_images(identity, result)

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
                min(self.settings.valuation_search_timeout_seconds, 30.0)
            ):
                decisions = await self.image_verifier.verify(
                    identity.get("image_front_url"),
                    candidates,
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
        if self.client is None:
            return ComparableSearchResult(notes=["未配置市场检索模型"])
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
            return direct_result.model_copy(
                update={
                    "comparables": [
                        comp
                        for comp in direct_result.comparables
                        if comp.match_confidence >= Decimal("0.5")
                        and self._comparable_grade_matches(identity, comp)
                    ][:12]
                }
            )
        schema = json.dumps(ComparableSearchResult.model_json_schema(), ensure_ascii=False)
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
        if cardhobby_result is not None:
            parsed = parsed.model_copy(
                update={
                    "search_queries": [
                        *cardhobby_result.search_queries,
                        *parsed.search_queries,
                    ],
                    "comparables": [
                        *self._cardhobby_comparables(cardhobby_result),
                        *parsed.comparables,
                    ],
                    "notes": [*cardhobby_result.notes, *parsed.notes],
                }
            )
        if cardhobby_error:
            parsed = parsed.model_copy(update={"notes": [cardhobby_error, *parsed.notes]})
        if self.settings.valuation_verify_sources:
            parsed = await self._verify_sources(parsed)
        valid = [
            comp
            for comp in parsed.comparables
            if urlparse(comp.source_url).scheme in {"http", "https"}
            and comp.match_confidence >= Decimal("0.5")
            and self._comparable_grade_matches(identity, comp)
        ]
        fallback = parsed.fallback_valuation
        if fallback is not None:
            references = [
                reference
                for reference in fallback.references
                if urlparse(reference.source_url).scheme in {"http", "https"}
                and self._grade_matches(identity, reference)
            ]
            fallback = (
                fallback.model_copy(update={"references": references}) if references else None
            )
        return parsed.model_copy(
            update={"comparables": valid[:12], "fallback_valuation": fallback}
        )

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
            )
            for sale in result.sales
        ]

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
                *(self._url_reachable(client, url) for url in urls),
                return_exceptions=True,
            )
        reachable = {url for url, check in zip(urls, checks, strict=True) if check is True}
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
        return result.model_copy(
            update={
                "comparables": [
                    comp for comp in result.comparables if comp.source_url in reachable
                ],
                "fallback_valuation": fallback,
                "player_adjustments": adjustments,
            }
        )

    @staticmethod
    async def _url_reachable(client: httpx.AsyncClient, url: str) -> bool:
        try:
            if cardhobby_item_id(url):
                # These item IDs were returned by CardHobby's first-party sold search API.
                # Historical detail pages can return 404 to HEAD even while the API record
                # remains available, so the API response itself is the verification source.
                return True
            if is_cardhobby_evidence_url(url):
                response = await client.head(url)
                length = int(response.headers.get("content-length", "0") or 0)
                return 200 <= response.status_code < 400 and length > 0
            response = await client.get(url)
            return 200 <= response.status_code < 400 and bool(response.content)
        except httpx.HTTPError:
            return False

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
                "WHERE code IN ('automated_market_search', 'cardhobby')"
            )
            source_ids = {row["code"]: row["id"] for row in await sources.fetchall()}
            for comp in search.comparables:
                cardhobby_key = cardhobby_external_id(
                    comp.source_url, comp.title, comp.occurred_at, comp.sale_price
                )
                source_code = (
                    "cardhobby" if cardhobby_key is not None else "automated_market_search"
                )
                source_id = source_ids[source_code]
                external_id = (
                    cardhobby_key if cardhobby_key is not None else comp.source_url
                )
                existing_identity = await connection.execute(
                    """
                    SELECT card_definition_id
                    FROM market_comp
                    WHERE data_source_id = %s AND external_id = %s
                    LIMIT 1
                    """,
                    (source_id, external_id),
                )
                existing_identity_row = await existing_identity.fetchone()
                if (
                    existing_identity_row is not None
                    and str(existing_identity_row["card_definition_id"])
                    != str(identity["card_definition_id"])
                ):
                    logger.warning(
                        "quarantined market comparable %s because it is already "
                        "bound to card definition %s instead of %s",
                        external_id,
                        existing_identity_row["card_definition_id"],
                        identity["card_definition_id"],
                    )
                    continue
                await connection.execute(
                    """
                    INSERT INTO market_comp (
                        card_definition_id, data_source_id, occurred_at, sale_price,
                        shipping_price, currency, fx_rate_to_base, grade_company,
                        grade_value, listing_title, source_url, external_id,
                        match_confidence, source_reliability, raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (card_definition_id, data_source_id, external_id)
                    DO UPDATE SET
                        match_confidence = EXCLUDED.match_confidence,
                        source_reliability = EXCLUDED.source_reliability,
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
                        Jsonb(
                            {
                                "matching_fields": comp.matching_fields,
                                "conflicts": comp.conflicts,
                                "automated": True,
                                "source": source_code,
                                "image_url": comp.image_url,
                                "visual_verdict": comp.visual_verdict,
                                "visual_confidence": (
                                    str(comp.visual_confidence)
                                    if comp.visual_confidence is not None
                                    else None
                                ),
                                "visual_reasons": comp.visual_reasons,
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
                SELECT id, occurred_at, sale_price, shipping_price, currency,
                       fx_rate_to_base, listing_title, source_url, match_confidence,
                       grade_company, grade_value, raw_payload, external_id,
                       classification,
                       (SELECT code FROM data_source WHERE id = market_comp.data_source_id)
                           AS source_code,
                       sale_price * COALESCE(fx_rate_to_base, 1) AS value_base,
                       shipping_price * COALESCE(fx_rate_to_base, 1) AS shipping_base,
                       source_reliability,
                       GREATEST(0, EXTRACT(day FROM now() - occurred_at))::int AS age_days
                FROM market_comp
                WHERE card_definition_id = %s
                  AND occurred_at >= now() - interval '730 days'
                ORDER BY occurred_at DESC
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
                if classification.hard_conflicts:
                    rejected.append(
                        {
                            "id": str(row["id"]),
                            "title": row["listing_title"],
                            "tier": classification.tier.value,
                            "hard_conflicts": classification.hard_conflicts,
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


async def stop_worker(task: asyncio.Task, worker: AutomatedValuationWorker) -> None:
    await worker.stop()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
