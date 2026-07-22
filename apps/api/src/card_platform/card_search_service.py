import json
from urllib.parse import urlparse

from openai import AsyncOpenAI

from card_platform.config import Settings
from card_platform.schemas import CardRecognitionResult, CardSearchEnrichment

SEARCHABLE_FIELDS = {
    "player_name",
    "season_start",
    "season_end",
    "brand_name",
    "product_name",
    "insert_name",
    "parallel_name",
    "card_number",
    "serial_total",
    "is_rookie",
    "is_autograph",
    "is_patch",
}

TRUSTED_SITES = [
    "paniniamerica.net",
    "topps.com",
    "beckett.com",
    "cardboardconnection.com",
    "tcdb.com",
    "psacard.com",
    "ebay.com",
    "130point.com",
]

STRONG_IDENTITY_SITES = {
    "paniniamerica.net",
    "topps.com",
    "beckett.com",
    "cardboardconnection.com",
    "tcdb.com",
    "psacard.com",
}

STRING_FIELDS = {
    "player_name",
    "brand_name",
    "product_name",
    "insert_name",
    "parallel_name",
    "card_number",
}
INTEGER_FIELDS = {"season_start", "season_end", "serial_total"}
BOOLEAN_FIELDS = {"is_rookie", "is_autograph", "is_patch"}


def normalize_resolved_field(key: str, value: str | int | bool) -> str | int | bool | None:
    try:
        if key in STRING_FIELDS:
            return str(value)
        if key in INTEGER_FIELDS and not isinstance(value, bool):
            return int(value)
        if key in BOOLEAN_FIELDS:
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"true", "yes", "1"}:
                return True
            if normalized in {"false", "no", "0"}:
                return False
    except (TypeError, ValueError):
        return None
    return None


def build_card_search_queries(
    result: CardRecognitionResult, context: str | None = None
) -> list[str]:
    parts = [
        result.player_name,
        str(result.season_start) if result.season_start else None,
        result.brand_name,
        result.product_name,
        result.insert_name,
        result.parallel_name,
        f"#{result.card_number}" if result.card_number else None,
    ]
    identity = " ".join(str(value) for value in parts if value)
    if not identity and context:
        identity = context[:300]
    if not identity:
        return []
    queries = [f"{identity} basketball card checklist"]
    if result.card_number:
        queries.append(f'"{result.player_name or ""}" "{result.card_number}" card checklist')
    if result.serial_total or result.parallel_name:
        queries.append(f"{identity} parallel print run")
    return queries[:3]


class CardSearchService:
    def __init__(self, settings: Settings) -> None:
        self.enabled = settings.card_search_enabled and bool(settings.vision_api_key)
        self.model = settings.card_search_model
        self.client = (
            AsyncOpenAI(
                api_key=settings.vision_api_key,
                base_url=settings.vision_base_url,
                timeout=settings.card_search_timeout_seconds,
                max_retries=1,
            )
            if self.enabled
            else None
        )

    async def enrich(
        self, visual: CardRecognitionResult, context: str | None = None
    ) -> CardRecognitionResult:
        queries = build_card_search_queries(visual, context)
        if not self.client or not queries:
            return visual.model_copy(update={"search_queries": queries})
        schema = json.dumps(CardSearchEnrichment.model_json_schema(), ensure_ascii=False)
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是篮球球星卡目录核验器。必须联网搜索，不得依赖记忆补全。"
                            "优先厂商 checklist，其次 Beckett、Cardboard Connection、TCDB、PSA；"
                            "成交平台标题只能作为弱证据。只有至少两个关键字段一致，或官方 checklist"
                            "明确对应时，才能写 resolved_fields。序列编号、签字和 RC 不得仅凭销售标题"
                            "确认。source_urls 只能填写搜索中实际看到的 HTTP(S) URL。"
                            "field_evidence 必须填写简短的人类可读依据，例如‘卡号与官方 checklist"
                            "一致’，禁止把 URL 填入 field_evidence；URL 只能放在 candidates 的"
                            "source_urls。eBay、COMC、130point 等销售标题属于弱证据，不能单独"
                            "证明年份、RC、签字、平行版本或限编。"
                            "listing_or_order_context 是截图 OCR 的商品标题和描述，可用于生成更精确"
                            "的搜索词和候选匹配，但它本身仍是销售方描述，必须经过强来源核验后才能"
                            "修正字段。若视觉字段为空，应主动尝试用该文字定位具体 checklist 条目。"
                            f"只输出符合以下 JSON Schema 的 JSON：{schema}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "visual_extraction": visual.model_dump(mode="json"),
                                "listing_or_order_context": context,
                                "search_queries": queries,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                response_format={"type": "json_object"},
                extra_body={
                    "enable_search": True,
                    "search_options": {
                        "forced_search": True,
                        "search_strategy": "turbo",
                        "assigned_site_list": TRUSTED_SITES,
                    },
                },
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("web search returned an empty response")
            enrichment = CardSearchEnrichment.model_validate_json(content)
        except Exception as error:
            return visual.model_copy(
                update={
                    "search_queries": queries,
                    "caveats": [*visual.caveats, f"联网目录核验失败：{str(error)[:180]}"],
                }
            )

        candidates = []
        for candidate in enrichment.candidates[:5]:
            valid_urls = [
                url
                for url in candidate.source_urls
                if urlparse(url).scheme in {"http", "https"}
            ]
            candidates.append(candidate.model_copy(update={"source_urls": valid_urls}))
        has_strong_sources = any(
            any(
                (urlparse(url).hostname or "").removeprefix("www.") in STRONG_IDENTITY_SITES
                for url in candidate.source_urls
            )
            for candidate in candidates
        )
        updates = {
            key: normalized
            for key, value in enrichment.resolved_fields.items()
            if (normalized := normalize_resolved_field(key, value)) is not None
            if key in SEARCHABLE_FIELDS
            and value is not None
            and enrichment.field_evidence.get(key)
            and has_strong_sources
        }
        return visual.model_copy(
            update={
                **updates,
                "search_queries": queries,
                "field_evidence": {
                    **visual.field_evidence,
                    **enrichment.field_evidence,
                },
                "web_candidates": candidates,
                "caveats": [*visual.caveats, *enrichment.caveats],
            }
        )
