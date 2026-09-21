import asyncio
import json
import re
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from openai import AsyncOpenAI

from card_platform.config import Settings
from card_platform.domain.serial_evidence import (
    serial_field_evidence,
    visible_serial_evidence,
)
from card_platform.ingestion.cardhobby import CardHobbyProvider
from card_platform.schemas import CardRecognitionResult, CardSearchEnrichment, CardWebCandidate

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
    "has_inscription",
    "inscription_text",
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

GENERIC_CARD_TOKENS = {
    "base",
    "basketball",
    "card",
    "cards",
    "insert",
    "parallel",
    "set",
    "the",
    "trading",
}
GENERIC_INSERT_TOKENS = {
    *GENERIC_CARD_TOKENS,
    "autograph",
    "autographs",
    "black",
    "blue",
    "gold",
    "green",
    "orange",
    "prizm",
    "purple",
    "red",
    "rookie",
    "silver",
    "white",
}

STRING_FIELDS = {
    "player_name",
    "brand_name",
    "product_name",
    "insert_name",
    "parallel_name",
    "card_number",
    "inscription_text",
}
INTEGER_FIELDS = {"season_start", "season_end", "serial_total"}
BOOLEAN_FIELDS = {"is_rookie", "is_autograph", "has_inscription", "is_patch"}


class _PageImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.image_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta" or self.image_url:
            return
        values = {key.lower(): value for key, value in attrs if value}
        key = (values.get("property") or values.get("name") or "").lower()
        if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
            self.image_url = values.get("content")


def extract_page_image(html: str, page_url: str) -> str | None:
    parser = _PageImageParser()
    parser.feed(html)
    if not parser.image_url:
        return None
    image_url = urljoin(page_url, parser.image_url)
    return image_url if urlparse(image_url).scheme in {"http", "https"} else None


def is_trusted_catalog_url(url: str) -> bool:
    return _url_belongs_to_domains(url, TRUSTED_SITES)


def _url_belongs_to_domains(url: str, domains) -> bool:
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(hostname == site or hostname.endswith(f".{site}") for site in domains)


def _identity_tokens(value: object, *, ignored: set[str] | None = None) -> list[str]:
    ignored = ignored or set()
    return [
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if token not in ignored
    ]


def _page_contains_tokens(page_tokens: set[str], value: object, *, ignored: set[str]) -> bool:
    expected = _identity_tokens(value, ignored=ignored)
    return bool(expected) and all(token in page_tokens for token in expected)


def _distinctive_insert_tokens(candidate: CardWebCandidate) -> list[str]:
    return _identity_tokens(candidate.identity.insert_name, ignored=GENERIC_INSERT_TOKENS)


def _candidate_has_joint_anchor(candidate: CardWebCandidate) -> bool:
    identity = candidate.identity
    matched = set(candidate.matching_fields)
    if not identity.player_name or "player_name" not in matched:
        return False
    if identity.card_number and "card_number" in matched:
        return True
    return bool(
        identity.insert_name and "insert_name" in matched and _distinctive_insert_tokens(candidate)
    )


def _page_supports_season(page_text: str, start: int | None, end: int | None) -> bool:
    if start is None and end is None:
        return True
    if start is None:
        return bool(re.search(rf"(?<!\d){end}(?!\d)", page_text))
    if end is None:
        return bool(re.search(rf"(?<!\d){start}(?!\d)", page_text))
    short_end = str(end)[-2:]
    return bool(
        re.search(
            rf"(?<!\d){start}\s*(?:-|/|\u2013|\u2014)\s*(?:{end}|{short_end})(?!\d)",
            page_text,
        )
    )


def candidate_is_exact_image_match(
    candidate: CardWebCandidate,
    target: CardRecognitionResult,
) -> bool:
    if candidate.match_confidence < Decimal("0.92") or candidate.conflicts:
        return False
    matched = set(candidate.matching_fields)
    required = {"player_name", "season_start", "brand_name", "product_name"}
    if target.season_end:
        required.add("season_end")
    if target.serial_total:
        required.add("serial_total")
    for field in ("card_number", "insert_name", "parallel_name"):
        if getattr(target, field):
            required.add(field)
    if target.is_rookie:
        required.add("is_rookie")
    if target.is_autograph:
        required.add("is_autograph")
    if target.has_inscription:
        required.add("has_inscription")
    if target.is_patch:
        required.add("is_patch")
    return required.issubset(matched)


def normalize_resolved_field(key: str, value: str | int | bool) -> str | int | bool | None:
    try:
        if key in STRING_FIELDS:
            normalized = str(value).strip()
            return normalized or None
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


def resolved_update_is_safe(
    visual: CardRecognitionResult,
    key: str,
    value: str | int | bool,
) -> bool:
    if key == "card_number" and visual.card_number:
        explicit_local_number = any(
            text.startswith("Apple Vision OCR：")
            and re.search(r"(?:\bNo\.?\s|N[º°]\s*|#\s*)[A-Z0-9-]+", text, re.I)
            for text in visual.field_evidence.get("card_number", [])
        )
        if explicit_local_number:
            return str(value).strip().upper() == visual.card_number.strip().upper()
    if key == "serial_total":
        # A catalog describes editions, not the stamp on this owned copy.
        if visual.serial_number is not None and visual.serial_total is not None:
            return value == visual.serial_total
        candidates = visible_serial_evidence(serial_field_evidence(visual.field_evidence))
        physical_pairs = {(candidate.number, candidate.total) for candidate in candidates}
        if physical_pairs:
            return len(physical_pairs) == 1 and value == next(iter(physical_pairs))[1]
        # A catalogue print run may be retained as an editable candidate, but it
        # is not evidence for the stamp on this particular owned copy.
        return False
    if key in {
        "is_rookie",
        "is_autograph",
        "has_inscription",
        "is_patch",
    }:
        return not (getattr(visual, key) is True and value is False)
    return True


def merge_search_evidence(
    visual: CardRecognitionResult,
    enrichment: CardSearchEnrichment,
    applied_fields: dict,
) -> dict[str, list[str]]:
    """Append explicitly attributed catalog evidence; never replace pixels/OCR."""
    merged = {field: list(texts) for field, texts in visual.field_evidence.items()}
    for field, texts in enrichment.field_evidence.items():
        prefix = "目录核验" if field in applied_fields else "联网候选"
        merged[field] = list(
            dict.fromkeys(
                [
                    *merged.get(field, []),
                    *(f"{prefix}：{text}" for text in texts),
                ]
            )
        )
    return merged


def candidate_supports_resolved_field(
    candidate: CardWebCandidate, key: str, value: str | int | bool
) -> bool:
    if candidate.match_confidence < Decimal("0.85") or candidate.conflicts:
        return False
    if not _candidate_has_joint_anchor(candidate):
        return False
    if key not in candidate.matching_fields:
        return False
    if not any(
        _url_belongs_to_domains(url, STRONG_IDENTITY_SITES) for url in candidate.source_urls
    ):
        return False
    candidate_value = getattr(candidate.identity, key, None)
    if candidate_value is None:
        return False
    if isinstance(value, str):
        return str(candidate_value).strip().casefold() == value.strip().casefold()
    return candidate_value == value


def source_page_supports_candidate(page_text: str, candidate: CardWebCandidate) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", page_text.casefold())
    page_tokens = set(text.split())
    identity = candidate.identity
    if not _candidate_has_joint_anchor(candidate):
        return False
    player_tokens = _identity_tokens(identity.player_name)
    if not player_tokens or not all(token in page_tokens for token in player_tokens):
        return False
    card_number = str(identity.card_number or "").strip().casefold()
    card_number_supported = bool(
        card_number
        and re.search(
            rf"(?:^|[^a-z0-9]){re.escape(card_number)}(?:[^a-z0-9]|$)",
            page_text.casefold(),
        )
    )
    insert_tokens = _distinctive_insert_tokens(candidate)
    insert_supported = bool(insert_tokens) and all(token in page_tokens for token in insert_tokens)
    if card_number and not card_number_supported:
        return False
    if insert_tokens and not insert_supported:
        return False
    if not card_number_supported and not insert_supported:
        return False
    if identity.brand_name and not _page_contains_tokens(
        page_tokens, identity.brand_name, ignored=GENERIC_CARD_TOKENS
    ):
        return False
    if identity.product_name and not _page_contains_tokens(
        page_tokens, identity.product_name, ignored=GENERIC_CARD_TOKENS
    ):
        return False
    if not _page_supports_season(page_text.casefold(), identity.season_start, identity.season_end):
        return False
    parallel_tokens = _identity_tokens(identity.parallel_name, ignored=GENERIC_CARD_TOKENS)
    return not parallel_tokens or all(token in page_tokens for token in parallel_tokens)


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
    if result.player_name and result.insert_name:
        # Product subseries and OCR card numbers can both be incomplete. Keep
        # one catalogue route anchored in the independent player/insert pair.
        queries.append(f'"{result.player_name}" "{result.insert_name}" card checklist')
    if result.serial_total or result.parallel_name:
        queries.append(f"{identity} parallel print run")
    return list(dict.fromkeys(queries))[:4]


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
                            "视觉产品名可能只读出母系列，卡号也可能误读；必须交叉核对独立的"
                            "球员+卡号、球员+特卡名查询。若官方目录将这些实卡锚点唯一对应到"
                            "更具体的子系列，可补全产品和赛季，并说明原识别缺失部分；不能将"
                            "母系列与所有子系列全局视为同款，也不能仅凭版权年认定赛季。"
                            "仅当搜索结果明确给出该精确图鉴条目的原始高清图片直链时，才填写候选"
                            "image_url；不得填写搜索结果缩略图、站点 Logo、占位图、相似卡或同球员"
                            "其他卡片图片。无法确认精确图片时必须返回 null。"
                            "每个 candidates 条目必须尽量填写 identity，identity 只能包含"
                            "player_name、season_start、season_end、brand_name、product_name、"
                            "insert_name、parallel_name、card_number、serial_number、"
                            "serial_total、is_numbered、grade_company、grade_value、"
                            "is_rookie、rookie_evidence、is_autograph、autograph_type、"
                            "has_inscription、inscription_text、is_memorabilia、"
                            "memorabilia_types、memorabilia_usage、memorabilia_player、"
                            "is_patch；布尔属性无法确认时填 null，不得把未知写成 false；"
                            "候选之间必须是"
                            "可独立评分的具体卡片身份，不能只换一种标题措辞。"
                            "candidates 必须包含搜索证据最强的具体卡片，即使它与"
                            "resolved_fields 一致；若视觉结果缺少背面、年份、产品、特卡名"
                            "或卡号，或视觉 caveats 表明某一面识别失败，必须主动搜索同球员、"
                            "同卡号或相同版式可能对应的产品，并在确有搜索结果支持时返回最多"
                            "3 个互斥的具体候选。不要为了凑数编造候选。若搜索结果中确实看见"
                            "具体图鉴身份，但无法获得稳定直链，可将 source_urls 留空；仍需"
                            "填写完整 identity、matching_fields、conflicts 和保守置信度。"
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
                if urlparse(url).scheme in {"http", "https"} and is_trusted_catalog_url(url)
            ]
            candidates.append(candidate.model_copy(update={"source_urls": valid_urls}))
        verified_url_groups = await asyncio.gather(
            *(self._verified_source_urls(candidate) for candidate in candidates)
        )
        candidates = [
            candidate.model_copy(update={"source_urls": urls})
            for candidate, urls in zip(candidates, verified_url_groups, strict=True)
        ]
        image_urls = await asyncio.gather(
            *(
                self._candidate_image(
                    candidate.source_urls,
                    candidate.image_url,
                    candidate_is_exact_image_match(candidate, visual),
                )
                for candidate in candidates
            )
        )
        candidates = [
            candidate.model_copy(update={"image_url": image_url})
            for candidate, image_url in zip(candidates, image_urls, strict=True)
        ]
        updates = {
            key: normalized
            for key, value in enrichment.resolved_fields.items()
            if (normalized := normalize_resolved_field(key, value)) is not None
            if key in SEARCHABLE_FIELDS
            and value is not None
            and enrichment.field_evidence.get(key)
            and any(
                candidate_supports_resolved_field(candidate, key, normalized)
                for candidate in candidates
            )
            and resolved_update_is_safe(visual, key, normalized)
        }
        reference_updates: dict = {}
        try:
            reference = await CardHobbyProvider(
                self.client, self.model, timeout=15
            ).reference_images(visual.model_dump(mode="json"))
            if reference:
                reference_updates = {
                    "reference_image_front_url": reference.front_url,
                    "reference_image_back_url": reference.back_url,
                    "reference_image_source_url": reference.source_url,
                    "reference_image_provider": "cardhobby",
                    "reference_image_match_confidence": reference.match_confidence,
                }
        except (httpx.HTTPError, ValueError, TypeError):
            reference_updates = {}
        return visual.model_copy(
            update={
                **updates,
                **reference_updates,
                "search_queries": queries,
                "field_evidence": merge_search_evidence(visual, enrichment, updates),
                "web_candidates": candidates,
                "caveats": [*visual.caveats, *enrichment.caveats],
            }
        )

    async def _verified_source_urls(self, candidate: CardWebCandidate) -> list[str]:
        """Keep only source pages whose response text supports this exact candidate."""
        urls = candidate.source_urls
        if not urls:
            return []
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            )
        }
        verified: list[str] = []
        async with httpx.AsyncClient(
            timeout=8,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for url in urls[:3]:
                try:
                    response = await client.get(url)
                    if response.status_code < 400 and source_page_supports_candidate(
                        response.text[:2_000_000], candidate
                    ):
                        verified.append(str(response.url))
                except httpx.HTTPError:
                    continue
        return verified

    async def _candidate_image(
        self,
        source_urls: list[str],
        suggested_image_url: str | None,
        exact_identity_match: bool,
    ) -> str | None:
        if not exact_identity_match or not source_urls:
            return None
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(
            timeout=8,
            follow_redirects=True,
            headers=headers,
        ) as client:
            if suggested_image_url and is_trusted_catalog_url(suggested_image_url):
                if await self._is_real_image(client, suggested_image_url):
                    return suggested_image_url
            for source_url in source_urls[:2]:
                try:
                    response = await client.get(source_url)
                    if (
                        response.status_code != 200
                        or not is_trusted_catalog_url(str(response.url))
                        or "text/html" not in response.headers.get("content-type", "")
                    ):
                        continue
                    image_url = extract_page_image(response.text[:2_000_000], str(response.url))
                    if (
                        image_url
                        and is_trusted_catalog_url(image_url)
                        and not any(
                            marker in image_url.lower()
                            for marker in ("logo", "default", "placeholder", "social-share")
                        )
                        and await self._is_real_image(client, image_url)
                    ):
                        return image_url
                except httpx.HTTPError:
                    continue
        return None

    @staticmethod
    async def _is_real_image(client: httpx.AsyncClient, image_url: str) -> bool:
        try:
            response = await client.get(
                image_url,
                headers={"Range": "bytes=0-65535"},
            )
            content_type = response.headers.get("content-type", "").lower()
            return (
                response.status_code in {200, 206}
                and content_type.startswith("image/")
                and len(response.content) >= 512
            )
        except httpx.HTTPError:
            return False
