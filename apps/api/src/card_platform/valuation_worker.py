import asyncio
import json
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
from card_platform.db import Database
from card_platform.domain.valuation import ComparableSale, estimate_from_comps
from card_platform.ingestion.cardhobby import (
    CardHobbyProvider,
    CardHobbySearchResult,
    cardhobby_external_id,
    cardhobby_item_id,
    is_cardhobby_evidence_url,
)


VALUATION_SITES = [
    "ebay.com",
    "psacard.com",
    "130point.com",
    "goldin.co",
    "fanaticscollect.com",
    "beckett.com",
]


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
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
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
            )
            if self.client is not None
            else None
        )
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
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
                       ci.serial_total, ci.grade_company, ci.grade_value, ci.notes,
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
            return dict(row)

    async def _search(self, identity: dict) -> ComparableSearchResult:
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
                await connection.execute(
                    """
                    INSERT INTO market_comp (
                        card_definition_id, data_source_id, occurred_at, sale_price,
                        shipping_price, currency, fx_rate_to_base, grade_company,
                        grade_value, listing_title, source_url, external_id,
                        match_confidence, raw_payload
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (data_source_id, external_id) DO UPDATE SET
                        match_confidence = EXCLUDED.match_confidence,
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
                        Jsonb(
                            {
                                "matching_fields": comp.matching_fields,
                                "conflicts": comp.conflicts,
                                "automated": True,
                                "source": source_code,
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
                       grade_company, grade_value, raw_payload,
                       (SELECT code FROM data_source WHERE id = market_comp.data_source_id)
                           AS source_code,
                       (sale_price + shipping_price) * COALESCE(fx_rate_to_base, 1) AS value_base,
                       GREATEST(0, EXTRACT(day FROM now() - occurred_at))::int AS age_days
                FROM market_comp
                WHERE card_definition_id = %s
                  AND occurred_at >= now() - interval '730 days'
                  AND COALESCE(match_confidence, 0) >= 0.5
                  AND (
                    (%s::text IS NULL AND grade_company IS NULL)
                    OR
                    (%s::text IS NOT NULL AND UPPER(grade_company) = UPPER(%s::text)
                     AND grade_value = %s::numeric)
                  )
                ORDER BY occurred_at DESC
                """,
                (
                    identity["card_definition_id"],
                    identity["grade_company"],
                    identity["grade_company"],
                    identity["grade_company"],
                    identity["grade_value"],
                ),
            )
            rows = await result.fetchall()
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
            estimate = estimate_from_comps(
                [
                    ComparableSale(
                        value_base=row["value_base"],
                        age_days=row["age_days"],
                        match_confidence=row["match_confidence"],
                    )
                    for row in rows
                ]
            )
            evidence = {
                "strategy": "可比成交的匹配度与时间衰减加权分位数",
                "formula": "P25=低位，P50=合理价，P75=高位；单一成交使用 ±15% 区间",
                "search_queries": search.search_queries,
                "notes": search.notes,
                "market_comp_ids": [str(row["id"]) for row in rows],
                "comparables": [
                    {
                        "id": str(row["id"]),
                        "title": row["listing_title"],
                        "source_url": row["source_url"],
                        "occurred_at": row["occurred_at"].isoformat(),
                        "original_price": str(row["sale_price"] + row["shipping_price"]),
                        "currency": row["currency"],
                        "fx_rate_to_base": str(row["fx_rate_to_base"] or 1),
                        "normalized_price": str(row["value_base"]),
                        "match_confidence": str(row["match_confidence"]),
                        "age_days": row["age_days"],
                        "source": row["source_code"],
                        "evidence_level": next(
                            (
                                field.split(":", 1)[1]
                                for field in (row["raw_payload"] or {}).get(
                                    "matching_fields", []
                                )
                                if field.startswith("cardhobby:")
                            ),
                            None,
                        ),
                        "condition_type": (
                            "graded" if row["grade_company"] is not None else "raw"
                        ),
                        "grade_company": row["grade_company"],
                        "grade_value": (
                            str(row["grade_value"])
                            if row["grade_value"] is not None
                            else None
                        ),
                    }
                    for row in rows
                ],
            }
            valuation = await connection.execute(
                """
                INSERT INTO valuation_snapshot (
                    collection_item_id, method, currency, low_value, fair_value,
                    high_value, confidence, evidence, model_version
                ) VALUES (%s, 'ai_estimate', %s, %s, %s, %s, %s, %s, 'auto-comp-1.0')
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
