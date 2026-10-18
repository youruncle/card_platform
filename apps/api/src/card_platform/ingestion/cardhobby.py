from card_platform.ingestion.query_feedback import BoundedAliasFeedback, discovery_subject, short_insert_query_term
from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url, parse_public_item_html, public_gallery_for_primary
from card_platform.valuation_trace import traced_completion, emit, content_hash
from time import perf_counter
import asyncio
import hashlib
import json
import re
import time
import unicodedata
from datetime import datetime
from decimal import Decimal
from card_platform.domain.finish_evidence import finish_state
from card_platform.domain.scoped_parallel_names import parallel_match_text
from card_platform.domain.comparables import shortened_refractor_color_claim
from card_platform.domain.collector_number import (
    ORDINAL_COLLECTOR_PATTERN, ordinal_collector_number_matches, ordinal_collector_number_conflicts, numeric_card_number_key,
)
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, PrivateAttr

from card_platform.domain.redemption import redemption_conflict, redemption_identity
from card_platform.domain.card_identity import (
    INSERT_ALIASES,
    has_misplaced_season_numbering,
    is_intrinsic_product_finish,
)
from card_platform.domain.comparables import (
    parallel_matches_title,
    parallel_title_has_conflicting_specificity,
)
from card_platform.domain.grade_basis import (
    UNGRADED_SAME_CARD_BASIS,
    can_use_ungraded_basis,
)
from card_platform.domain.listing_title import (
    GRADING_COMPANY_PATTERN,
    GRADING_DETAIL_PATTERN,
    explicit_print_runs,
    has_explicit_named_card_variant_claim,
    has_material_parallel_marker,
    has_named_card_variant_marker,
    is_explicit_base_listing,
    is_explicit_nonparallel_listing,
    is_broad_three_color_visual_lead,
    has_three_color_pattern_claim,
    is_graded_listing,
    is_multi_card_listing,
)
from card_platform.domain.product_identity import (
    ProductMatchStatus,
    aliases_from_registry,
    assess_identity_product_match,
    normalize_product_text,
    product_label_variants,
    product_matches_title,
    named_child_product_for_parent,
    has_ambiguous_child_product_shorthand,
)
from card_platform.domain.player_aliases import (
    PLAYER_MATCH_ALIAS_EXCLUSIONS,
    PLAYER_SEARCH_ALIASES,
    player_roster_components,
    query_player_aliases,
)
from card_platform.domain.season import explicit_seasons as parse_explicit_seasons, matches_abbreviated_season
from card_platform.domain.listing_attributes import autograph_claim, memorabilia_claim
from card_platform.knowledge import KnowledgeResolver
from card_platform.domain.subject_aliases import published_subject_aliases, published_subject_matches
from card_platform.domain.entity_roles import (
    identity_has_entity_role_collision as _identity_has_entity_role_collision,
    official_cover_work_subject,
    official_work_query_title,
)
from card_platform.marketplace_aliases import seller_aliases


CARDHOBBY_DOMAIN = "cardhobby.com.cn"
# The apex host and www host resolve to different frontends. The www TLS
# tunnel is unreliable behind common macOS proxy/VPN clients, while the
# official apex host serves the same public endpoint with a valid handshake.
CARDHOBBY_SEARCH_URL = "https://cardhobby.com.cn/NewCommodity/SearchCommodity"
CARDHOBBY_SINGLE_CARD_CATEGORY_ID = "08d83279-bba1-4ffe-88c6-b13c22576d96"
CARDHOBBY_ITEM_PATH = re.compile(r"^/market/item/(?P<item_id>\d+)/?$", re.IGNORECASE)
CARDHOBBY_FEEDBACK_PATH = re.compile(
    r"^/market/feedback(?:/(?P<account_id>\d+))?/?$", re.IGNORECASE
)
GRADE_PATTERN = GRADING_COMPANY_PATTERN
AUTOGRAPH_PATTERN = re.compile(r"签字|卡签|贴签|签名|\bauto(?:graph)?\b|\brpa\b", re.I)
PATCH_PATTERN = re.compile(r"球衣|物料|切割|patch|jersey|relic|\brpa\b", re.I)
MULTI_PLAYER_PATTERN = re.compile(
    r"双人|三人|四人|多人|双签|三签|四签|各年份|等(?:\s|，|,|特卡|折射|卡|base|新秀|$)|\bdual\b|\btriple\b|\bquad\b",
    re.I,
)
CROSS_SELL_PATTERN = re.compile(r"(?:主页|另有|同版).{0,60}(?:在拍|可搜|可配|同出)", re.I)
NON_BASE_VARIANT_PATTERN = re.compile(
    r"特卡|\binsert\b|\bssp\b|\bsp\b|(?:/\s*\d+|\d+\s*[编遍])|"
    r"(?:金|银|红|蓝|绿|紫|橙|粉|黑|白|黄|棋盘|格子|熔岩|碎点|波纹|泡泡|hyper)\s*折|"
    r"\b(?:cosmic|update|tcu)\b|stars\s+of\s+(?:the\s+)?nba|power\s+players",
    re.I,
)
CARDHOBBY_IMAGE_PATTERN = re.compile(
    r"https://images\.cardhobby\.com/[^\"'<>?\s]+\.(?:jpe?g|png|webp)",
    re.IGNORECASE,
)
PRODUCT_SEARCH_ALIASES = {
    # Sellers almost universally omit the catalog suffix "Basketball".  CardHobby's
    # search is token-AND, so keeping it in either discovery or matching silently
    # hides otherwise exact NBA Hoops sales.
    "nba hoops basketball": ("NBA Hoops", "Hoops"),
    "nba hoops": ("NBA Hoops", "Hoops"),
    "prizm basketball": ("Prizm", "Panini Prizm"),
    "panini prizm basketball": ("Prizm", "Panini Prizm"),
    "donruss basketball": ("Donruss", "Panini Donruss"),
    "panini donruss basketball": ("Donruss", "Panini Donruss"),
    "national treasures": ("国宝", "National Treasures"),
    "one and one": ("One and One", "One & One", "OAO", "壹和壹"),
    "one & one": ("One and One", "One & One", "OAO", "壹和壹"),
    "topps three": ("Topps 3",),
    "topps 3": ("Topps 3",),
    "topps basketball": ("Topps Basketball", "Topps Flagship", "Topps"),
    "flagship": ("Topps Basketball", "Topps Flagship", "Topps"),
    "topps chrome": ("Topps Chrome", "Topps Chrome Basketball", "TC"),
    "topps chrome updates basketball": (
        "Topps Chrome Update",
        "Topps Chrome Updates",
        "TCU",
        "Topps Chrome",
    ),
    "topps chrome update": ("Topps Chrome Update", "TCU", "Topps Chrome"),
    "upper deck draft edition": ("Draft Edition", "UD Draft Edition"),
    "2009-10 draft edition": ("Draft Edition", "UD Draft Edition"),
    "ud choice": ("UD Choice", "Upper Deck Choice"),
    "upper deck reserve": ("Upper Deck Reserve", "UD Reserve"),
    "hardcourt": ("Upper Deck Hardcourt", "Hardcourt"),
    "revolution basketball": ("Revolution", "革命"),
    "preferred basketball": ("Preferred", "Panini Preferred"),
    "origins basketball": ("Origins", "Panini Origins", "起源"),
    "topps chrome mcdonald's all american": (
        "Topps Chrome",
        "McDonald's All American",
        "麦当劳",
        "全美",
    ),
    "panini signature series": ("Signature Series", "签字系列"),
    "signature series": ("Signature Series", "签字系列"),
    "topps signature class": ("Topps Signature Class", "Signature Class"),
    "topps signature class basketball": ("Topps Signature Class", "Signature Class"),
    "court kings": ("油画", "Court Kings"),
    "premium stock basketball": (
        "Hoops Premium Stock",
        "NBA Hoops Premium Stock",
        "Premium Stock",
    ),
    "photogenic basketball": ("Photogenic", "上镜"),
    "complete basketball": ("Complete",),
    "immaculate collection": ("Immaculate Collection", "Immaculate", "爱咪咪"),
    # CardHobby tokenizes search terms as AND; Latin and Chinese synonyms must
    # be separate queries, never the impossible combined phrase "Mosaic 马赛克".
    "mosaic": ("Mosaic", "马赛克"),
    "fleer showcase": ("Fleer Showcase", "Flair Showcase", "Showcase"),
    "flair showcase": ("Fleer Showcase", "Flair Showcase", "Showcase"),
    "showcase": ("Fleer Showcase", "Flair Showcase", "Showcase"),
    "chronicles draft picks": ("Panini Chronicles", "Chronicles", "编年史"),
    # The paper flagship is catalogued as "Bowman Rookies and Stars", while
    # Chinese-market sellers overwhelmingly shorten it to just "Bowman" and
    # use the release year rather than the full NBA season.  Keep the short
    # form first so CardHobby's token-AND search can discover those sales.
    "bowman rookies and stars": ("Bowman", "Bowman Rookies and Stars"),
    "bowman rookies & stars": ("Bowman", "Bowman Rookies and Stars"),
    # The 2025-26 release mixes NBA cards and an NCAA Chrome prospect section.
    # Sellers variously call it Bowman, Bowman Chrome, or Bowman University.
    "bowman basketball": ("Bowman", "Bowman Chrome", "Bowman University"),
    # CardHobby sellers commonly call cards from this silver-foil 90-card set
    # "银标" and often shorten the printed 2001-02 season to 2001.
    # Put the narrow Chinese nickname first: broad MJ's Back queries can span
    # hundreds of rows and exhaust CardHobby's request budget before reaching
    # the locally common title form.
    "mj's back": ("银标", "MJ's Back", "MJs Back"),
}
RELEASE_YEAR_SEARCH_PRODUCTS = {
    "mj s back",
    "bowman rookies and stars",
    "bowman basketball",
}
def product_search_aliases(value: object) -> tuple[str, ...]:
    """Resolve catalog labels such as `Mosaic Basketball` to seller vocabulary."""
    product = str(value or "").strip()
    normalized = product.casefold()
    exact = PRODUCT_SEARCH_ALIASES.get(normalized) or aliases_from_registry(
        value, PRODUCT_SEARCH_ALIASES
    )
    if exact:
        return exact
    for family, aliases in PRODUCT_SEARCH_ALIASES.items():
        if family in normalized:
            return aliases
    # Unknown catalog families still need marketplace-friendly discovery.
    # Strip only optional maker/sport/release wrappers; internal qualifiers
    # remain in the candidate and the shared product matcher validates every
    # recalled row before it can enter evidence.
    generic_wrappers = {"basketball", "panini", "topps", "upper deck", "fleer", "skybox"}
    variants = [
        variant for variant in product_label_variants(product) if variant not in generic_wrappers
    ]
    if not variants:
        return (product,) if product else ()
    ordered = sorted(
        dict.fromkeys(variants),
        key=lambda candidate: (len(candidate.split()), len(candidate)),
    )
    normalized_original = normalize_product_text(product)
    return tuple(
        product if candidate == normalized_original else candidate for candidate in ordered
    )


def _has_explicit_nonbasketball_scope(identity: dict) -> bool:
    """Use product/checklist scope, never a person's or character's name."""
    context = ' '.join(str(identity.get(key) or '') for key in
                       ('brand_name', 'product_name', 'set_name', 'insert_name', 'insert')).casefold()
    other_scope = bool(re.search(
        r'\b(?:soccer|football|baseball|hockey|formula\s*(?:1|one)|f1|'
        r'pok[eé]mon|tcg|marvel|disney|ufc|wwe)\b|'
        r'宝可梦|寶可夢|精灵宝可梦|迪士尼|漫威|足球|棒球|冰球|'
        r'\bcombat anthology\b', context))
    # This is a published Disney checklist subset, not a player-name guess.
    disney_subset = any(str(identity.get(key) or '').strip().casefold() == 'stitch in costume'
                        for key in ('insert_name', 'insert', 'set_name'))
    return other_scope or disney_subset


def _query_with_product_scope(identity: dict, query: str) -> str:
    """Keep discovery terms within the explicitly identified product scope."""
    if _has_explicit_basketball_scope(identity):
        return query
    return re.sub(r'\s+', ' ', re.sub(
        r'(?<![a-z0-9])basketball(?![a-z0-9])', ' ', query, flags=re.I)).strip()


def _has_explicit_basketball_scope(identity: dict) -> bool:
    """Unknown product scope does not justify a restrictive sports filter."""
    if _has_explicit_nonbasketball_scope(identity):
        return False
    sport = str(identity.get('sport') or identity.get('sport_name') or '').strip().casefold()
    if sport:
        return sport in {'basketball', 'nba', 'wnba', '篮球', '籃球'}
    context = ' '.join(str(identity.get(key) or '') for key in ('product_name', 'set_name'))
    return bool(re.search(r'\b(?:basketball|nba|wnba)\b|篮球|籃球', context, re.I))


PARALLEL_SEARCH_ALIASES = {
    "hyper red and green": "红绿折",
    "silver": "银折",
    "silver parallel": "银折",
    "red": "红折",
    "red parallel": "红折",
    "blue": "蓝折",
    "blue parallel": "蓝折",
    "gold": "金折",
    "gold parallel": "金折",
    "green": "绿折",
    "green parallel": "绿折",
    "purple": "紫折",
    "purple parallel": "紫折",
    "green mosaic": "绿马赛克",
    "fusion red and yellow": "红黄马赛克",
    "red, white, and blue refractor": "红白蓝",
    "x-fractor": "X折",
    "plaid": "格子折",
    "aqua wave": "青波纹折",
    "row 1": "Row 1",
    "fast break purple": "快攻紫折",
    "status": "Status",
    "red lava refractor": "红熔岩折",
}
PARALLEL_MATCH_ALIASES = {
    "hyper red and green": ("hyper red and green", "hyper red & green", "红绿折", "红绿双折"),
    "silver": ("silver", "silver parallel", "银平行", "银折", "银折射", "正银折"),
    "silver parallel": (
        "silver",
        "silver parallel",
        "银平行",
        "银折",
        "银折射",
        "正银折",
    ),
    "silver refractor": (
        "silver refractor",
        "silver",
        "银平行",
        "银折",
        "银折射",
        "正银折",
    ),
    "red": ("red", "red parallel", "红平行", "红折"),
    "red parallel": ("red", "red parallel", "红平行", "红折"),
    "blue": ("blue", "blue parallel", "蓝平行", "蓝折"),
    "blue parallel": ("blue", "blue parallel", "蓝平行", "蓝折"),
    "gold": ("gold", "gold parallel", "金平行", "金折"),
    "gold parallel": ("gold", "gold parallel", "金平行", "金折"),
    "green": ("green", "green parallel", "绿平行", "绿折"),
    "green parallel": ("green", "green parallel", "绿平行", "绿折"),
    "purple": ("purple", "purple parallel", "紫平行", "紫折"),
    "purple parallel": ("purple", "purple parallel", "紫平行", "紫折"),
    "red lava refractor": (
        "red lava refractor",
        "red lava",
        "红熔岩折",
        "红熔岩",
    ),
    "green mosaic": ("green mosaic", "绿马赛克", "绿折"),
    "fusion red and yellow": (
        "fusion red and yellow",
        "red and yellow fusion",
        "红黄马赛克",
        "红黄融合",
        "红黄折",
    ),
    "red, white, and blue refractor": (
        "red white and blue refractor",
        "red white blue refractor",
        "rwb refractor",
        "红白蓝三色平行折射",
        "红白蓝",
    ),
    "x-fractor": ("x-fractor", "xfractor", "x折", "x碎冰"),
    "plaid": ("plaid", "格子折", "格子"),
    "aqua wave": ("aqua wave", "aqua", "teal wave", "青波纹折", "青折"),
    "row 1": ("row 1", "row1", "第一排"),
    "fast break purple": (
        "fast break purple",
        "purple fast break",
        "快攻紫折",
        "紫泡泡折",
        "紫圈圈折",
    ),
    "status": ("status", "status parallel", "状态折"),
}
INSERT_MATCH_ALIASES = {
    "hoopnotic": ("hoopnotic", "hoop-notic", "迷幻特卡"),
    "retrofractor variations": (
        "retrofractor variations",
        "retrofractor",
        "retro refractor",
        "复古银折",
        "复古",
        "变化版",
    ),
    "team checklist": ("team checklist", "team checklits"),
    "award winner holograms": (
        "award winner holograms",
        "ward winner holograms",
        "award winner",
        "ward winner",
        "mvp全息",
        "ud 全息",
    ),
    "finals mvps": (
        "finals mvps",
        "finals mvp",
        "fmvp",
        "总决赛mvp",
        "总决赛 mvp",
        "捧杯",
        "双捧杯",
        "奖杯",
    ),
    "bounce house": ("bounce house", "跳跳屋", "弹跳屋", "扣篮板报"),
    "stained glass": ("stained glass", "教堂", "教堂玻璃", "彩色玻璃"),
    "jam masters": ("jam masters", "扣篮大师", "大头特卡", "大头"),
    "bomb squad": ("bomb squad", "爆炸小队"),
    "bomb squad!": ("bomb squad", "爆炸小队"),
    "magicians": ("magicians", "魔术师"),
    "penmanship": ("penmanship", "书法签"),
    "jumbo jersey autographs": (
        "jumbo jersey autographs",
        "jumbo jersey auto",
        "大窗球衣签字",
        "大窗 签字 球衣",
        "ja 签字 卡签 球衣",
    ),
    "fanatical": ("fanatical", "狂热"),
    "starquest": (
        "starquest",
        "star quest",
        "星际探索",
        "星际迷航",
        "一星",
    ),
    "stratospheric stars": ("stratospheric stars", "stratospheric", "平流层之星"),
    "winning tags": ("winning tags", "winning tag"),
    "principal powers": ("principal powers", "principal power"),
    "colossal materials": ("colossal materials", "colossal", "大窗"),
    "nucleus": ("nucleus", "核心三人"),
    "moment in time": ("moment in time", "mit"),
    "topps chrome autographs": (
        "topps chrome autographs",
        "tca",
        "新秀签",
        "base签",
    ),
    "ud game floor": ("ud game floor", "game floor", "比赛地板", "地板"),
    "silhouettes": ("silhouettes", "silhouette", "剪影", "剪影签"),
    "treasured threads": ("treasured threads", "球衣物料", "球衣", "物料"),
    # Global marketplace ontology. These labels describe card kinds across
    # every player; they are not collection-item exceptions.
    "calligraphy": ("calligraphy", "书法签", "书法签字"),
    "hall of fame": ("hall of fame", "名人堂", "名人堂签"),
    "serenity": ("serenity", "宁静特卡"),
    "cactus jack": ("cactus jack", "蟑螂盒"),
    "image variation": ("image variation", "变化版", "变体版"),
    "passport": ("passport", "护照特卡", "护照"),
    "illuminators": ("illuminators", "辐射源特卡", "辐射源"),
    "nba 2k17": ("nba2k17", "nba 2k17"),
    "modern strokes": (
        "modern strokes",
        "现代进攻艺术",
        "进攻艺术",
        "笔刷特卡",
    ),
    "maestros": ("maestros", "大师特卡"),
    "contemporaries": ("contemporaries", "contemporary", "当代特卡"),
    "buzz factor": ("buzz factor",),
    "hurricane": ("hurricane", "破风龙驹"),
    "frostbite": ("frostbite", "fbostbite", "冰霜"),
    "thunder road": ("thunder road", "雷霆之路"),
    "epic performers": ("epic performers",),
    "elevate": ("elevate", "升华特卡", "升华"),
    "city edition": ("city edition",),
    "road to finals": (
        "road to finals",
        "road to the finals",
        "总决赛之路",
        "总冠军之路",
        "季后赛特卡",
    ),
}

INSERT_SEARCH_ALIASES = {
    "hoopnotic": ("Hoopnotic", "Hoop-Notic", "迷幻特卡"),
    "retrofractor variations": (
        "Retrofractor",
        "Retro Refractor",
        "复古",
        "变化版",
    ),
    # Chinese sellers overwhelmingly call Mosaic's Stained Glass insert
    # “教堂” (and sometimes “彩色玻璃”), not its English checklist name.
    # Keep both seller terms in the query ladder: CardHobby token search cannot
    # recover them from “Stained Glass” alone.
    "stained glass": ("Stained Glass", "教堂", "彩色玻璃"),
    "finals mvps": ("捧杯", "总决赛MVP", "FMVP", "Finals MVPs"),
    "bounce house": ("Bounce House", "跳跳屋", "弹跳屋"),
    "jam masters": ("Jam Masters", "扣篮大师", "大头特卡"),
    "bomb squad": ("Bomb Squad", "爆炸小队"),
    "bomb squad!": ("Bomb Squad", "爆炸小队"),
    "magicians": ("Magicians", "魔术师"),
    "penmanship": ("Penmanship", "书法签"),
    "extraterrestrial talent": (
        "Extraterrestrial Talent",
        "Talent",
        "外星人",
        "外星天才",
    ),
    "jumbo jersey autographs": (
        "Jumbo Jersey Autographs",
        "大窗球衣签字",
        "JA 球衣签字",
    ),
    "fanatical": ("Fanatical", "狂热"),
    "starquest": ("Starquest", "一星", "星际迷航"),
    "stratospheric stars": ("Stratospheric", "Stratospheric Stars"),
    "winning tags": ("Winning Tags", "Winning Tag"),
    "principal powers": ("Principal Powers",),
    "colossal materials": ("Colossal", "大窗"),
    "nucleus": ("Nucleus", "核心三人"),
    "moment in time": ("Moment in Time", "MIT"),
    # Official checklist labels are frequently absent from CardHobby titles.
    # These short seller terms are only discovery/identity-gate vocabulary;
    # image verification still decides whether a recalled row is the same card.
    "topps chrome autographs": ("新秀签", "TCA", "Topps Chrome Autographs"),
    "ud game floor": ("地板", "Game Floor", "UD Game Floor"),
    "silhouettes": ("剪影", "Silhouettes", "剪影签"),
    "treasured threads": ("球衣", "Treasured Threads", "物料"),
    "star cast": ("Star Cast", "胶片特卡", "胶片", "星星"),
}


class CardHobbySale(BaseModel):
    item_id: str | None = None
    seller_id: str | None = None
    consignment_code: str | None = None
    source_metadata: dict = Field(default_factory=dict)
    source_gallery: dict = Field(default_factory=dict)
    title: str
    source_url: str
    occurred_at: datetime
    sale_price: Decimal = Field(gt=0)
    shipping_price: Decimal = Field(default=Decimal("0"), ge=0)
    fx_rate_to_base: Decimal = Field(default=Decimal("1"), gt=0)
    bid_count: int | None = Field(default=None, ge=0)
    sale_type: str
    evidence_level: str
    sold_status_evidence: str
    grade_company: str | None = None
    grade_value: Decimal | None = None
    matching_fields: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    match_confidence: Decimal = Field(ge=0, le=1)
    source_reliability: Decimal = Field(default=Decimal("0.70"), ge=0, le=1)
    image_url: str | None = None


class CardHobbyQueryDiagnostic(BaseModel):
    query: str
    stage: str
    raw_count: int = Field(default=0, ge=0)
    eligible_count: int = Field(default=0, ge=0)
    accepted_count: int = Field(default=0, ge=0)
    new_source_count: int = Field(default=0, ge=0)
    new_candidate_count: int = Field(default=0, ge=0)
    rejected_count: int = Field(default=0, ge=0)
    pages_fetched: int = Field(default=0, ge=0)
    outcome: str
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    rejected_examples: list[dict] = Field(default_factory=list, max_length=5)
    error: str | None = None
    error_type: str | None = None
    http_status: int | None = None
    retryable: bool = False


def append_rejected_example(
    examples: list[dict], sale: CardHobbySale, reasons: list[str], source_status: object = None
) -> None:
    """Bounded diagnostic excerpts, never candidate evidence or price inputs."""
    if len(examples) >= 5:
        return
    counts: dict[str, int] = {}
    for example in examples:
        for reason in example["reasons"]:
            counts[reason] = counts.get(reason, 0) + 1
    available = [
        reason[:200]
        for reason in dict.fromkeys(reasons or ["身份门禁分数不足"])
        if counts.get(reason[:200], 0) < 2
    ][:5]
    if not available:
        return
    examples.append(
        {
            "item_id": sale.item_id,
            "source_url": sale.source_url,
            "title": sale.title[:300],
            "reasons": available,
            "additional_reason_count": max(0, len(reasons) - len(available)),
            "source_status": str(source_status)[:40] if source_status is not None else None,
            "occurred_at": sale.occurred_at.isoformat(),
            "sale_price": str(sale.sale_price),
            "currency": "CNY",
            "evidence_level": sale.evidence_level,
            "bid_count": sale.bid_count,
            **({"source_metadata": sale.source_metadata} if getattr(sale, "source_metadata", None) else {}),
            "audit_only": True,
        }
    )


class CardHobbySearchResult(BaseModel):
    # Never a model-output field: only direct public API observations populate it.
    _public_source_pool: list[dict] = PrivateAttr(default_factory=list)

    @property
    def public_source_pool(self) -> list[dict]:
        return self._public_source_pool

    search_queries: list[str] = Field(default_factory=list)
    sales: list[CardHobbySale] = Field(default_factory=list)
    # None preserves the contract for older clients; [] is an explicitly empty
    # public-source pool. Display sales remain limited independently.
    candidate_pool: list[CardHobbySale] | None = None
    notes: list[str] = Field(default_factory=list)
    query_diagnostics: list[CardHobbyQueryDiagnostic] = Field(default_factory=list)


class CardHobbySearchError(RuntimeError):
    """All bounded public queries failed; retain the causes for durable retry."""

    def __init__(self, diagnostics: list[CardHobbyQueryDiagnostic]) -> None:
        self.query_diagnostics = [item.model_copy(deep=True) for item in diagnostics[:12]]
        self.retryable = any(item.retryable for item in self.query_diagnostics)
        # Canonical metadata, never query/error prose, drives the legacy durable
        # string retry policy. Keep the marker first so truncation cannot lose it.
        marker = "transient_source_failure" if self.retryable else "permanent_source_failure"
        causes = list(dict.fromkeys(
            f"HTTP {item.http_status}" if item.http_status else (item.error_type or "unknown")
            for item in self.query_diagnostics
        ))
        super().__init__(f"{marker}: 卡淘全部查询失败，不能作为零成交结果; {', '.join(causes)}")


class CardHobbyReferenceImages(BaseModel):
    title: str
    source_url: str
    front_url: str
    back_url: str | None = None
    match_confidence: Decimal = Field(ge=0, le=1)


def extract_cardhobby_images(html: str) -> list[str]:
    return list(dict.fromkeys(CARDHOBBY_IMAGE_PATTERN.findall(html)))


def is_exact_cardhobby_reference(
    identity: dict, sale: CardHobbySale, *, resolver: KnowledgeResolver | None = None
) -> bool:
    title = sale.title
    required = (
        identity.get("player_name"),
        identity.get("product_name"),
        identity.get("card_number"),
    )
    if any(value and not _contains_phrase(title, value) for value in required):
        return False
    season_start = identity.get("season_start")
    season_end = identity.get("season_end")
    season_tokens = {str(season_start)} if season_start else set()
    if season_start and season_end:
        season_tokens.add(f"{season_start}-{str(season_end)[-2:]}")
    if season_tokens and not any(token in title for token in season_tokens):
        return False
    score, conflicts = assess_cardhobby_match(identity, sale, resolver=resolver)
    return score >= Decimal("0.65") and not conflicts and bool(sale.image_url)


def cardhobby_item_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host not in {CARDHOBBY_DOMAIN, f"www.{CARDHOBBY_DOMAIN}"}:
        return None
    match = CARDHOBBY_ITEM_PATH.match(parsed.path)
    return match.group("item_id") if match else None


def cardhobby_feedback_account_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host not in {CARDHOBBY_DOMAIN, f"www.{CARDHOBBY_DOMAIN}"}:
        return None
    match = CARDHOBBY_FEEDBACK_PATH.match(parsed.path)
    if not match:
        return None
    if match.group("account_id"):
        return match.group("account_id")
    query_id = (parse_qs(parsed.query).get("id") or [None])[0]
    return query_id if query_id and query_id.isdigit() else None


def is_cardhobby_evidence_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if host not in {CARDHOBBY_DOMAIN, f"www.{CARDHOBBY_DOMAIN}"}:
        return False
    return bool(CARDHOBBY_ITEM_PATH.match(parsed.path) or cardhobby_feedback_account_id(url))


def _normalized(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_marks = "".join(
        character for character in folded if not unicodedata.combining(character)
    )
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", without_marks).strip()


def cardhobby_external_id(
    url: str, title: str, occurred_at: datetime, sale_price: Decimal
) -> str | None:
    if not is_cardhobby_evidence_url(url):
        return None
    item_id = cardhobby_item_id(url)
    if item_id:
        return f"item:{item_id}"
    payload = f"{url}|{_normalized(title)}|{occurred_at.isoformat()}|{sale_price}"
    return f"feedback:{hashlib.sha256(payload.encode()).hexdigest()}"


def _contains_phrase(title: str, value: object) -> bool:
    target = _normalized(value)
    return bool(target) and target in _normalized(title)


def _matches_product(
    title: str,
    product_name: object,
    identity: dict | None = None,
) -> bool:
    identity = identity or {}
    return product_matches_title(
        title,
        product_name,
        target_set=identity.get("set_name"),
        target_insert=identity.get("insert_name") or identity.get("insert"),
        target_parallel=identity.get("parallel_name") or identity.get("parallel"),
    )


def _has_non_base_variant(title: str, product_name: object) -> bool:
    """Do not mistake a product's own SP acronym for a short-print variant."""
    candidate_text = title
    if "sp authentic" in _normalized(product_name):
        candidate_text = re.sub(r"\bsp\s+authentic\b", "authentic", title, flags=re.I)
    return bool(
        NON_BASE_VARIANT_PATTERN.search(candidate_text)
        or has_material_parallel_marker(candidate_text, product_name)
        or has_named_card_variant_marker(candidate_text, product_name)
    )


def _contains_card_number(title: str, value: object) -> bool:
    ordinal_match = ordinal_collector_number_matches(title, value)
    if ordinal_match is not None:
        return ordinal_match
    target = numeric_card_number_key(_normalized(value))
    if not target:
        return False
    pattern = "0*" + re.escape(target) if target.isascii() and target.isdigit() else re.escape(target)
    return bool(
        re.search(
            rf"(?<![0-9a-z]){pattern}(?![0-9a-z])",
            _normalized(title),
        )
    )


def _identity_aliases(identity: dict, field: str) -> tuple[str, ...]:
    """Return canonical identity text plus explicitly audited seller aliases."""
    canonical = str(identity.get(field) or "").strip()
    raw_aliases = identity.get(f"{field}_aliases") or []
    if isinstance(raw_aliases, str):
        raw_aliases = [raw_aliases]
    curated_aliases = (
        PLAYER_SEARCH_ALIASES.get(_normalized(canonical), ()) if field == "player_name" else ()
    )
    aliases: list[str] = []
    seen: set[str] = set()
    for value in (
        canonical,
        *curated_aliases,
        *(str(value).strip() for value in raw_aliases),
    ):
        normalized = _normalized(value)
        if value and normalized not in seen:
            aliases.append(value)
            seen.add(normalized)
    return tuple(aliases)


def _player_components(identity: dict, *, resolver=None) -> tuple[tuple[str, ...], ...]:
    """Return independently searchable alias groups for multi-player cards."""
    canonical = str(identity.get("player_name") or "").strip()
    parts = player_roster_components(canonical)
    if len(parts) <= 1:
        return (_matching_player_aliases(identity),)
    groups: list[tuple[str, ...]] = []
    for part in parts:
        aliases = (part, *published_subject_aliases(part, resolver), *(alias for alias in PLAYER_SEARCH_ALIASES.get(_normalized(part), ())
                            if alias not in PLAYER_MATCH_ALIAS_EXCLUSIONS.get(_normalized(part), set())))
        groups.append(tuple(dict.fromkeys(value for value in aliases if value)))
    return tuple(groups)


def _matching_player_aliases(identity: dict) -> tuple[str, ...]:
    aliases = _identity_aliases(identity, "player_name")
    canonical = _normalized(identity.get("player_name"))
    excluded = PLAYER_MATCH_ALIAS_EXCLUSIONS.get(canonical, set())
    return tuple(alias for alias in aliases if alias not in excluded)


def _player_discovery_aliases(identity: dict) -> tuple[str, ...]:
    """Derive query-only surname/initial forms without weakening identity gates."""
    canonical = str(identity.get("player_name") or "").strip()
    if not canonical or re.search(r"[,&/]|和", canonical):
        return ()
    words = re.findall(r"[A-Za-z]+", canonical)
    if len(words) < 2:
        return ()
    significant = [word for word in words if word.casefold() not in {"jr", "sr", "ii", "iii", "iv"}]
    if len(significant) < 2:
        return ()
    initials = "".join(word[0] for word in significant).upper()
    surname_match = re.search(r"([A-Za-z]+(?:-[A-Za-z]+)*)\s*$", canonical)
    surname = surname_match.group(1) if surname_match else significant[-1]
    candidates = (
        initials if 2 <= len(initials) <= 5 else "",
        surname if len(surname) >= 4 else "",
        surname.replace("-", " ") if "-" in surname else "",
    )
    return tuple(dict.fromkeys(value for value in candidates if value))


def _unique_given_name_discovery_alias(identity: dict) -> str:
    """Return a long canonical given name for discovery, never identity proof.

    Catalog aliases are additional spellings, not evidence that a seller uses
    them. A specific catalog code leaves room for an independent English-name
    query even when a large catalog supplies many alternate spellings.
    """
    if (len(_identity_aliases(identity, "player_name")) > 1
            and not _is_strong_catalog_code(identity.get("card_number"))):
        return ""
    canonical = str(identity.get("player_name") or "").strip()
    if re.search(r"[,&/]|和", canonical):
        return ""
    match = re.match(r"([A-Za-z]+)(?:\s|$)", canonical)
    given_name = match.group(1) if match else ""
    return given_name if len(given_name) >= 5 else ""


def _is_strong_catalog_code(value: object) -> bool:
    code = str(value or "").strip().lstrip("#")
    if not code or len(code) > 20:
        return False
    if re.fullmatch(r"[A-Za-z]{1,8}-[A-Za-z0-9]{2,10}", code):
        return True
    return bool(
        re.fullmatch(r"[A-Za-z0-9]{2,12}", code)
        and re.search(r"[A-Za-z]", code)
        and re.search(r"\d", code)
    )


def _title_matches_player(
    identity: dict, title: str, *, allow_partial_roster: bool = False, resolver=None
) -> bool:
    parts = player_roster_components(identity.get('player_name'))
    # A published complete roster can shadow its own component entities.
    if len(parts) > 1 and published_subject_matches(title, identity.get('player_name'), resolver):
        return True
    groups = _player_components(identity)
    def matches(part, aliases):
        published = {_normalized(a) for a in published_subject_aliases(part, resolver)}
        return (any(_contains_phrase(title, alias) for alias in aliases
                    if _normalized(alias) not in published)
                or published_subject_matches(title, part, resolver))
    if len(groups) <= 1:
        return matches(identity.get('player_name'), groups[0])
    # A multi-player insert must name at least two distinct subjects. Requiring
    # the entire database concatenation would make normal seller titles (for
    # example "詹姆斯父子 + 浓眉") impossible to recall, while a one-name hit
    # is too weak and crosses into unrelated cards for the same star.
    matched_groups = sum(
        matches(part, aliases)
        for part, aliases in zip(parts, groups, strict=True)
    )
    return matched_groups >= (1 if allow_partial_roster else min(2, len(groups)))


def _explicit_release_years(title: str) -> set[int]:
    """Return seller-declared release years, excluding serial totals.

    Season ranges were already checked strictly, but many CardHobby sellers use
    a single calendar year (``2001 Upper Deck``). Treating that as no season
    evidence let distant releases enter the same-product analogue tier.
    """
    return {
        int(match.group(1))
        for match in re.finditer(
            r"(?<![\d/])((?:19|20)\d{2})(?!\d|\s*[编遍])",
            title,
        )
    }


def _identity_is_multi_player(identity: dict) -> bool:
    return len(player_roster_components(identity.get("player_name"))) > 1


def _explicit_print_runs(title: str) -> set[int]:
    """Compatibility wrapper around the shared marketplace parser."""
    return explicit_print_runs(title)


def _normalized_card_number(value: object) -> str:
    return numeric_card_number_key(re.sub(r"[^0-9a-z]+", "", str(value or "").casefold().lstrip("#")))


def _explicit_hash_card_numbers(title: str) -> set[str]:
    """Return only seller-declared ``#number`` claims, never inventory tails."""
    from card_platform.domain.listing_title import title_without_inventory_hash_tail

    title = title_without_inventory_hash_tail(title)
    numbers: set[str] = set()
    for match in re.finditer(
        rf"(?<![0-9A-Za-z])#\s*({ORDINAL_COLLECTOR_PATTERN}|[A-Za-z0-9][A-Za-z0-9-]{{0,19}})",
        title,
        re.IGNORECASE,
    ):
        # ``#WGY2#`` and similar balanced-hash tails are seller inventory
        # labels, not checklist numbers. Checking after the greedy match avoids
        # regex backtracking that could otherwise return a truncated token.
        if title[match.end() :].lstrip().startswith("#"):
            continue
        normalized = _normalized_card_number(match.group(1))
        if normalized:
            numbers.add(normalized)
    return numbers


def _insert_aliases(identity: dict) -> tuple[str, ...]:
    from card_platform.domain.sketch_identity import is_sketch_card, SKETCH_ALIASES
    target = str(identity.get("insert_name") or "").casefold()
    return tuple(
        dict.fromkeys(
            (
                *seller_aliases("insert", identity.get("insert_name")),
                *(SKETCH_ALIASES if is_sketch_card(target) else ()),
                *INSERT_MATCH_ALIASES.get(
                    target,
                    (str(identity.get("insert_name") or ""),),
                ),
            )
        )
    )


def _title_has_known_other_insert(
    identity: dict,
    title: str,
    target_aliases: tuple[str, ...],
) -> bool:
    """Detect a reviewed insert name different from the requested card kind.

    Recall remains permissive, but a known *other* card kind is an explicit
    contradiction rather than a missing-title-field signal. Short Chinese
    aliases are ignored here because two-character nicknames are too likely to
    occur inside seller prose or a player's nickname.
    """
    target = _normalized(identity.get("insert_name"))
    target_alias_tokens = {_normalized(alias) for alias in target_aliases if _normalized(alias)}
    player_alias_tokens = {
        _normalized(alias) for alias in _matching_player_aliases(identity) if _normalized(alias)
    }
    target_component_tokens = {
        _normalized(value)
        for value in (
            identity.get("brand_name"),
            identity.get("product_name"),
            identity.get("set_name"),
            identity.get("insert_name"),
        )
        if _normalized(value)
    }
    registries = (INSERT_ALIASES, INSERT_MATCH_ALIASES)
    for registry in registries:
        for canonical, aliases in registry.items():
            canonical_token = _normalized(canonical)
            if canonical_token in {
                "gu jersey",
                "treasured threads",
                "topps chrome autographs",
            }:
                # Their shortest aliases are material attributes (球衣/物料),
                # not proof of a particular insert family.
                continue
            candidate_aliases = tuple(dict.fromkeys((canonical, *aliases)))
            if (
                canonical_token == target
                or canonical_token in target_alias_tokens
                or canonical_token in target_component_tokens
            ):
                continue
            for alias in candidate_aliases:
                alias_token = _normalized(alias)
                if (
                    not alias_token
                    or alias_token in target_alias_tokens
                    or alias_token in target_component_tokens
                ):
                    continue
                if alias_token in player_alias_tokens:
                    continue
                if len(str(alias)) <= 2 and re.fullmatch(r"[\u3400-\u9fff]+", str(alias)):
                    continue
                # An English insert abbreviation inside a player surname is
                # not a competing insert claim (for example MIT in Mitchell).
                if (re.search(r'[a-z0-9]', alias_token)
                        and not re.search(r'(?<![a-z0-9])' + re.escape(alias_token)
                                          + r'(?![a-z0-9])', _normalized(title))):
                    continue
                if _contains_phrase(title, alias):
                    return True
    return False


def _player_only_appears_in_promotional_tail(identity: dict, title: str, *, resolver=None) -> bool:
    """Reject another card whose seller tail merely advertises the target player."""
    marker = re.search(r"(?:凑套必备|专收必备|同系列可配|相关球星|另有同系列)", title, re.I)
    if not marker or not _title_matches_player(identity, title, resolver=resolver):
        return False
    return not _title_matches_player(identity, title[: marker.start()], resolver=resolver)


def assess_cardhobby_match(
    identity: dict, sale: CardHobbySale, *, allow_partial_roster: bool = False,
    resolver: KnowledgeResolver | None = None
) -> tuple[Decimal, list[str]]:
    """Apply deterministic safety gates after model-assisted CardHobby discovery."""
    return _assess_cardhobby_match(identity, sale, allow_partial_roster=allow_partial_roster,
                                  resolver=resolver)


def _assess_cardhobby_match(
    identity: dict, sale: CardHobbySale, *, allow_partial_roster: bool = False,
    resolver: KnowledgeResolver | None = None, allow_work_subject_omission: bool = False,
) -> tuple[Decimal, list[str]]:
    title = sale.title
    conflicts = list(sale.conflicts)
    item_id = cardhobby_item_id(sale.source_url)
    if not is_cardhobby_evidence_url(sale.source_url):
        conflicts.append("不是受支持的卡淘商品或交易评价链接")
        return Decimal("0"), conflicts
    if item_id is not None and item_id != sale.item_id:
        conflicts.append("卡淘商品链接与商品 ID 不一致")
        return Decimal("0"), conflicts
    if sale.evidence_level == "confirmed_transaction":
        if item_id is not None:
            conflicts.append("商品详情页只能证明结束拍卖，不能证明交易完成")
            return Decimal("0"), conflicts
        source_reliability = Decimal("0.95")
    elif (
        sale.evidence_level == "ended_auction" and item_id is not None and (sale.bid_count or 0) > 0
    ):
        source_reliability = Decimal("0.70")
    else:
        conflicts.append("未证明为已确认交易或有出价的结束拍卖")
        return Decimal("0"), conflicts
    if is_multi_card_listing(title, identity.get("product_name")):
        conflicts.append("标题疑似多卡、套卡或未拆产品")
        return Decimal("0"), conflicts
    if MULTI_PLAYER_PATTERN.search(title) and not _identity_is_multi_player(identity):
        conflicts.append("标题疑似多人卡，不能作为单球员同款成交")
        return Decimal("0"), conflicts
    if CROSS_SELL_PATTERN.search(title):
        conflicts.append("标题含其他在拍卡的交叉推广，无法确认目标球员是本商品主体")
        return Decimal("0"), conflicts
    if _player_only_appears_in_promotional_tail(identity, title, resolver=resolver):
        conflicts.append("目标球员仅出现在卖家推广尾词中，不是本商品主体")
        return Decimal("0"), conflicts

    voucher_conflict = redemption_conflict(identity, title)
    if voucher_conflict:
        return Decimal("0"), [*conflicts, voucher_conflict]

    from card_platform.domain.sketch_identity import is_sketch_card, without_artist_signature
    autograph_state = autograph_claim(without_artist_signature(title)
        if is_sketch_card(identity.get('insert_name') or identity.get('insert')) else title)
    title_is_autograph = autograph_state is True
    autograph_omitted = bool(identity.get("is_autograph")) and autograph_state is None
    if autograph_state is not None and bool(identity.get("is_autograph")) != autograph_state:
        conflicts.append("签字属性与目标卡不一致")
        return Decimal("0"), conflicts
    material_state = memorabilia_claim(title)
    title_is_patch = material_state is True
    target_is_memorabilia = bool(identity.get("is_patch") or identity.get("is_memorabilia"))
    material_omitted = target_is_memorabilia and material_state is None
    if material_state is not None and target_is_memorabilia != material_state:
        conflicts.append("物料/球衣属性与目标卡不一致")
        return Decimal("0"), conflicts
    if autograph_omitted:
        conflicts.append("标题未提及签字属性，需图像确认")
    if material_omitted:
        conflicts.append("标题未提及物料属性，需图像确认")

    target_company = identity.get("grade_company")
    title_is_graded = bool(is_graded_listing(title) or sale.grade_company)
    if target_company is None and title_is_graded:
        conflicts.append("目标为裸卡，但卡淘成交疑似评级卡")
        return Decimal("0"), conflicts
    if target_company is not None:
        if identity.get("grade_value") is None:
            conflicts.append("目标评级卡缺少评级分数")
            return Decimal("0"), conflicts
        if sale.grade_company is None or sale.grade_value is None:
            conflicts.append("目标为评级卡，但成交缺少评级公司或分数")
            return Decimal("0"), conflicts
        if sale.grade_company.casefold() != str(target_company).casefold():
            conflicts.append("评级公司不一致")
            return Decimal("0"), conflicts
        if Decimal(sale.grade_value) != Decimal(identity.get("grade_value")):
            conflicts.append("评级分数不一致")
            return Decimal("0"), conflicts

    score = Decimal("0")
    if _identity_has_entity_role_collision(identity, resolver=resolver):
        conflicts.append("目标球员字段与产品、卡种或系列字段重叠，需先修正身份")
        return Decimal("0"), conflicts
    if not allow_work_subject_omission and not _title_matches_player(identity, title, allow_partial_roster=allow_partial_roster, resolver=resolver):
        conflicts.append("标题未命中目标球员")
        return Decimal("0"), conflicts
    score += Decimal("0.35")

    from card_platform.domain.entity_roles import title_without_catalog_subject
    attribute_title = title_without_catalog_subject(title, identity, resolver=resolver)
    season_start = identity.get("season_start")
    season_end = identity.get("season_end")
    explicit_seasons = parse_explicit_seasons(attribute_title)
    season_hard_conflict = False
    if (
        explicit_seasons
        and season_start
        and season_end
        and (int(season_start), int(season_end)) not in explicit_seasons
    ):
        conflicts.append("产品赛季与目标卡不一致或无法确认")
        season_hard_conflict = True
    explicit_release_years = _explicit_release_years(attribute_title)
    if (
        explicit_release_years
        and season_start
        and min(abs(year - int(season_start)) for year in explicit_release_years) > 2
    ):
        if "产品赛季与目标卡不一致或无法确认" not in conflicts:
            conflicts.append("产品赛季与目标卡不一致或无法确认")
        season_hard_conflict = True
    season_tokens = {str(season_start)} if season_start else set()
    if season_start and season_end and season_end != season_start:
        season_tokens.update(
            {
                str(season_end),
                f"{season_start}-{str(season_end)[-2:]}",
                f"{season_start}-{season_end}",
            }
        )
    season_matches = bool(season_tokens and any(token in attribute_title for token in season_tokens)) or matches_abbreviated_season(attribute_title, season_start, season_end)
    if season_matches:
        score += Decimal("0.15")

    if _contains_phrase(title, identity.get("brand_name")):
        score += Decimal("0.10")
    product_name = str(identity.get("product_name") or "").strip()
    product_status = assess_identity_product_match(title, identity, resolver=resolver)
    product_matches = product_status == ProductMatchStatus.MATCH
    if product_status == ProductMatchStatus.EXPLICIT_CONFLICT:
        conflicts.append("标题未命中目标产品系列")
        return Decimal("0"), conflicts
    if product_status == ProductMatchStatus.UNKNOWN:
        conflicts.append(
            "目标产品字段缺失或信息不足" if not product_name else "标题未提供可验证的目标产品信息"
        )
    if season_hard_conflict:
        return Decimal("0"), conflicts
    if product_matches:
        # Player + product alone is intentionally below the acceptance floor.
        # A season, card/insert, parallel or print-run anchor must establish
        # that a long seller title is about this card rather than a lot/list.
        score += Decimal("0.10")
    # Parse every version-bearing claim before applying any soft title-
    # completeness rule.  Previously, a missing card number/insert returned
    # here and hid a later explicit wrong parallel, serial total, grade, or
    # child product. The candidate layer then saw only a soft reason and
    # incorrectly promoted the sale to T2.
    hard_conflict = False
    misplaced_season_numbering = has_misplaced_season_numbering(identity)
    target_card_number_value = None if misplaced_season_numbering else identity.get("card_number")
    card_number_matches = _contains_card_number(attribute_title, target_card_number_value)
    target_card_number = str(target_card_number_value or "").strip()
    target_card_number_token = _normalized_card_number(target_card_number)
    if ordinal_collector_number_conflicts(attribute_title, target_card_number_value):
        conflicts.append("标题明确卡号与目标卡号不一致")
        hard_conflict = True
    explicit_hash_card_numbers = _explicit_hash_card_numbers(attribute_title)
    prefixed_number = re.fullmatch(r"([A-Za-z]+)[#\s-]*(\d+)", target_card_number)
    if prefixed_number:
        prefix, target_number = prefixed_number.groups()
        explicit_same_family = set(
            re.findall(rf"#?{re.escape(prefix)}[\s-]*(\d+)", attribute_title, re.IGNORECASE)
        )
        if explicit_same_family and target_number not in explicit_same_family:
            conflicts.append("标题明确卡号与目标卡号不一致")
            hard_conflict = True
    if (
        target_card_number_token
        and explicit_hash_card_numbers
        and target_card_number_token not in explicit_hash_card_numbers
    ):
        if "标题明确卡号与目标卡号不一致" not in conflicts:
            conflicts.append("标题明确卡号与目标卡号不一致")
        hard_conflict = True

    target_insert = str(identity.get("insert_name") or "").casefold()
    insert_aliases = _insert_aliases(identity)
    insert_matches = bool(target_insert) and any(
        _contains_phrase(title, alias) for alias in insert_aliases
    )
    if (
        target_insert
        and _identity_is_multi_player(identity)
        and bool(identity.get("is_patch"))
        and title_is_patch
        and re.search(r"双人|双窗|\bdual\b", title, re.I)
    ):
        insert_matches = True

    target_parallel_value = identity.get("parallel_name")
    if is_intrinsic_product_finish(target_parallel_value, product_name):
        target_parallel_value = None
    target_parallel = str(target_parallel_value or "").casefold()
    target_print_run = (
        None
        if misplaced_season_numbering
        else identity.get("serial_total") or identity.get("print_run")
    )
    explicit_print_runs = _explicit_print_runs(title)
    shortened_parallel = bool(
        target_print_run and explicit_print_runs == {int(target_print_run)}
        and (identity.get('card_number') or identity.get('insert_name'))
        and shortened_refractor_color_claim(title, target_parallel_value)
    )
    strict_parallel_aliases = PARALLEL_MATCH_ALIASES.get(target_parallel)
    reviewed_parallel_aliases = seller_aliases("parallel", target_parallel_value)
    parallel_aliases = tuple(
        dict.fromkeys(
            (
                *reviewed_parallel_aliases,
                *(strict_parallel_aliases or (str(target_parallel_value or ""),)),
            )
        )
    )
    parallel_text = parallel_match_text(title, identity, resolver=resolver)
    parallel_matches = not target_parallel or parallel_matches_title(
        parallel_text,
        target_parallel_value,
        parallel_aliases,
    )
    explicitly_described_base = is_explicit_base_listing(title)
    material_parallel = has_material_parallel_marker(
        title,
        product_name,
        insert_aliases if insert_matches else (),
    )
    known_other_insert = _title_has_known_other_insert(identity, title, insert_aliases)

    if known_other_insert and not insert_matches:
        conflicts.append("标题明确命中其他卡种，与目标卡种不一致")
        hard_conflict = True
    if (
        not target_insert
        and (target_card_number_value or target_parallel == "base")
        and not target_print_run
        and has_explicit_named_card_variant_claim(title, product_name, identity.get("player_name"))
    ):
        conflicts.append("目标为基础卡/未识别卡种，但标题明确为命名特卡或变体")
        hard_conflict = True
    if target_insert and not insert_matches and explicitly_described_base:
        conflicts.append("标题明确为基础卡，与目标卡种不一致")
        hard_conflict = True
    if (
        target_insert
        and not insert_matches
        and has_explicit_named_card_variant_claim(title, product_name, identity.get("player_name"))
    ):
        conflicts.append("标题明确为其他命名卡种，与目标卡种不一致")
        hard_conflict = True

    if target_parallel == "base":
        if not target_print_run and material_parallel:
            conflicts.append("目标为基础/未识别平行版，但标题明确为其他平行")
            hard_conflict = True
    elif (not target_parallel and material_parallel and not target_print_run
          and not any(identity.get(key) for key in ('card_number', 'insert_name', 'set_name'))):
        conflicts.append('目标身份缺少卡片锚点，不能核验显式平行')
        hard_conflict = True
    elif target_parallel:
        if (parallel_title_has_conflicting_specificity(
            parallel_text, target_parallel_value, parallel_aliases
        ) or (normalize_product_text(target_parallel_value) in {
            'red white and blue refractor', 'red white blue refractor', 'rwb refractor'
        } and has_three_color_pattern_claim(title))):
            conflicts.append("标题为比目标更具体的其他平行版本")
            hard_conflict = True
        if (
            is_explicit_nonparallel_listing(title)
            and not parallel_matches
            and not (target_print_run and int(target_print_run) in explicit_print_runs)
        ):
            conflicts.append("标题明确为基础卡，与目标平行版本不一致")
            hard_conflict = True
        if material_parallel and not parallel_matches and not shortened_parallel:
            conflicts.append("标题明确为其他平行版本，与目标平行不一致")
            hard_conflict = True

    original_art_singleton = (is_sketch_card(target_insert) and is_sketch_card(title)
                              and explicit_print_runs == {1})
    if not target_print_run and explicit_print_runs and not original_art_singleton:
        season_years = {
            int(year) for year in (identity.get("season_start"), identity.get("season_end")) if year
        }
        compatible_unstored_total = bool(
            target_parallel not in {"", "base"}
            and not hard_conflict
            and not target_insert
            and explicit_print_runs.issubset(season_years)
        )
        if compatible_unstored_total:
            # A named target parallel may itself be numbered even when the
            # recognition/catalog payload omitted its checklist total. Keep a
            # same-player/product sale available as T2; the missing total is a
            # confidence penalty, not proof that this is another version.
            conflicts.append("目标平行版缺少限编总数，卡淘限编成交按低置信类比")
        else:
            conflicts.append("目标未识别为限编版本，但标题明确为限编平行")
            hard_conflict = True
    if (
        target_print_run
        and explicit_print_runs
        and int(target_print_run) not in explicit_print_runs
    ):
        conflicts.append("明确限编与目标卡不一致")
        hard_conflict = True
    print_run_matches = bool(target_print_run and int(target_print_run) in explicit_print_runs)
    if print_run_matches and target_parallel not in {"", "base"} and not material_parallel:
        # A checklist total may uniquely define the named parallel even when a
        # seller writes “Base /15” to describe the design rather than the
        # checklist tier.
        parallel_matches = True

    # Only now classify missing title fields as soft. These rows stay available
    # for a transparent low-confidence estimate when no explicit contradiction
    # exists, which is the intended behavior for terse CardHobby seller titles.
    if target_insert and not insert_matches and not known_other_insert:
        exact_numbered_autograph = bool(
            identity.get("is_autograph")
            and target_print_run
            and title_is_autograph
            and print_run_matches
        )
        image_reviewable_omitted_insert = bool(
            product_matches
            and season_matches
            # A bare "insert/特卡" agrees with an insert target; only a
            # concrete competing label prevents the existing image gate.
            and not has_explicit_named_card_variant_claim(
                title, product_name, identity.get("player_name")
            )
            and not explicitly_described_base
            and (
                exact_numbered_autograph
                or (identity.get("is_autograph") and (title_is_autograph or autograph_omitted))
                or (target_is_memorabilia and (title_is_patch or material_omitted))
                or not identity.get("is_autograph")
                and not target_is_memorabilia
            )
        )
        conflicts.append(
            "标题未命中目标卡种，需图像确认"
            if image_reviewable_omitted_insert
            else "标题未命中目标卡号或卡种"
        )

    target_missing_card_anchor = bool(
        not target_card_number_value
        and not target_insert
        and target_parallel in {"", "base"}
        and not target_print_run
    )
    if target_missing_card_anchor and not material_parallel and not explicit_print_runs:
        conflicts.append(
            "目标缺少卡片锚点，但标题明确为命名特卡或变体"
            if has_named_card_variant_marker(title, product_name)
            else "目标身份缺少卡号、卡种、平行或限编锚点"
        )

    if (
        target_card_number_value
        and not target_insert
        and target_parallel in {"", "base"}
        and not target_print_run
        and not card_number_matches
    ):
        explicit_named_variant = has_explicit_named_card_variant_claim(
            title, product_name, identity.get("player_name")
        )
        if (material_parallel and finish_state(identity) != 'unknown'
                or explicit_named_variant or known_other_insert):
            if "目标为基础卡/未识别卡种，但标题明确为命名特卡或变体" not in conflicts:
                conflicts.append("目标为基础卡，但标题显示为其他特卡、产品变体、平行或限编")
            hard_conflict = True
        else:
            intrinsic_holofoil_base = bool(
                _normalized(product_name) in {"encore", "upper deck encore"}
                and identity.get("season_start") == 1998
                and identity.get("season_end") == 1999
                and re.search(r"折射|foil|holo", title, re.I)
                and not material_parallel
            )
            conflicts.append(
                "标题未命中目标卡号，基础/原生闪卡需图像确认"
                if product_matches
                and season_matches
                and (explicitly_described_base or intrinsic_holofoil_base or sale.image_url)
                else "标题未命中目标卡号或卡种"
            )

    if card_number_matches:
        score += Decimal("0.08")
    if insert_matches:
        score += Decimal("0.07")
    if target_parallel and parallel_matches:
        score += Decimal("0.07")
    elif (
        target_parallel not in {"", "base"}
        and not hard_conflict
        and (strict_parallel_aliases or reviewed_parallel_aliases or shortened_parallel)
    ):
        chrome_autograph_base_finish_needs_image = bool(
            target_parallel in {"silver", "silver refractor", "refractor"}
            and identity.get("is_autograph")
            and "chrome" in _normalized(product_name)
            and title_is_autograph
            and not material_parallel
            and not explicit_print_runs
        )
        conflicts.append(
            "标题未写银折基础签版本，需图像确认"
            if chrome_autograph_base_finish_needs_image
            else "标题未命中目标平行版本"
        )

    if print_run_matches:
        score += Decimal("0.08")
    elif target_print_run and not explicit_print_runs:
        conflicts.append("目标为限编卡，但标题未命中目标总限编")
    if identity.get("is_autograph") and title_is_autograph:
        score += Decimal("0.05")
    if target_is_memorabilia and title_is_patch:
        score += Decimal("0.05")

    if not any(
        (
            season_matches,
            card_number_matches,
            insert_matches,
            bool(target_parallel and parallel_matches),
            print_run_matches,
        )
    ):
        anchor_conflict = "标题只命中球员和产品，缺少赛季、卡号、版本或限编锚点"
        if anchor_conflict not in conflicts:
            conflicts.append(anchor_conflict)

    sale.source_reliability = source_reliability
    if hard_conflict:
        return Decimal("0"), conflicts
    if finish_state(identity) == 'unknown':
        conflicts.append('目标表面版本未知，需图像确认')
    image_review_only = bool(
        conflicts
        and any("需图像确认" in conflict for conflict in conflicts)
        and all(
            conflict.startswith(
                (
                    "标题未命中目标卡种，需图像确认",
                    "标题未写银折基础签版本，需图像确认",
                    "标题未命中目标卡号，基础/原生闪卡需图像确认",
                    "标题未命中目标平行版本",
                    "目标表面版本未知，需图像确认",
                )
            )
            for conflict in conflicts
        )
    )
    if conflicts and not image_review_only:
        return Decimal("0"), conflicts
    return min(score, Decimal("1")), conflicts


ANALOG_SOFT_CONFLICTS = (
    "目标表面版本未知，需图像确认",
    "标题未命中目标卡号或卡种",
    "标题未命中目标卡号，基础/原生闪卡需图像确认",
    "标题未命中目标平行版本",
    "目标为限编卡，但标题未命中目标总限编",
    "标题只命中球员和产品",
    "目标身份缺少卡号、卡种、平行或限编锚点",
    "目标缺少卡片锚点，但标题明确为命名特卡或变体",
    "目标产品字段缺失或信息不足",
    "标题未提供可验证的目标产品信息",
    "目标平行版缺少限编总数，卡淘限编成交按低置信类比",
)
IMAGE_REVIEW_CONFLICTS = (
    "标题未提及签字属性，需图像确认",
    "标题未提及物料属性，需图像确认",
    "目标表面版本未知，需图像确认",
    "标题未命中目标卡种，需图像确认",
    "标题未写银折基础签版本，需图像确认",
    "标题未命中目标卡号，基础/原生闪卡需图像确认",
)
IMAGE_REVIEW_COMPANION_CONFLICTS = ("标题未命中目标平行版本",)


def assess_cardhobby_candidate(
    identity: dict, sale: CardHobbySale, *, resolver: KnowledgeResolver | None = None
) -> tuple[Decimal, list[str], str | None]:
    """Classify a safe sale as exact-ish evidence or a bounded product analog.

    The strict matcher remains the authority for T0-like evidence.  A failed
    strict match may enter the downstream comparable classifier only when the
    failure is a value-bearing card variant difference, never a source,
    player, product, lot, autograph, memorabilia or grade conflict.
    """
    from card_platform.domain.card_identity import is_placeholder_identity_value
    subject = identity.get('player_name')
    work = identity.get('insert_name') or identity.get('set_name')
    if (not subject or is_placeholder_identity_value(subject)) and work:
        # With a missing person, require all independent work anchors and an
        # image. This is discovery only; the formal gate still needs current
        # identity/image confirmation and every version/condition constraint.
        if (sale.image_url and identity.get('season_start')
                and _contains_phrase(sale.title, work)
                and _contains_card_number(sale.title, identity.get('card_number'))):
            score, conflicts = _assess_cardhobby_match(
                identity, sale, resolver=resolver, allow_work_subject_omission=True)
            if score >= Decimal('.5') and set(conflicts) <= {'目标表面版本未知，需图像确认'}:
                sale.matching_fields = list(dict.fromkeys([
                    *sale.matching_fields, 'cardhobby_work_subject_image_required']))
                return Decimal('.45'), conflicts, 'image_review_candidate'
    if can_use_ungraded_basis(identity, sale.title, sale.grade_company, sale.grade_value):
        # Preserve the printed-card identity while checking grading uncertainty
        # separately; all source, player and version checks still apply.
        underlying = {**identity, "grade_company": None, "grade_value": None}
        score, conflicts = assess_cardhobby_match(underlying, sale, resolver=resolver)
        if score >= Decimal("0.5") and not conflicts:
            sale.matching_fields = list(
                dict.fromkeys(
                    [
                        *sale.matching_fields,
                        UNGRADED_SAME_CARD_BASIS,
                    ]
                )
            )
            return min(score, Decimal("0.55")), [], UNGRADED_SAME_CARD_BASIS
        return Decimal("0"), conflicts or ["未确认同款卡片，不能作为未标评级基础价"], None
    score, conflicts = assess_cardhobby_match(identity, sale, resolver=resolver)
    if score >= Decimal("0.5") and not conflicts:
        if parallel_match_text(sale.title, identity, resolver=resolver) != sale.title:
            sale.matching_fields = list(dict.fromkeys([
                *sale.matching_fields, 'cardhobby_image_review_required',
                'cardhobby_scoped_parallel_visual_required',
            ]))
            return Decimal('.45'), [], 'image_review_candidate'
        return score, conflicts, "exact_or_near_exact"
    if (sale.image_url and conflicts == ['标题未命中目标平行版本']
            and (identity.get('serial_total') or identity.get('print_run'))
            and _explicit_print_runs(sale.title) == {int(identity.get('serial_total') or identity['print_run'])}
            and (identity.get('card_number') or identity.get('insert_name'))
            and shortened_refractor_color_claim(sale.title, identity.get('parallel_name'))):
        sale.matching_fields = list(dict.fromkeys([
            *sale.matching_fields, 'cardhobby_image_review_required',
            'cardhobby_shortened_parallel_description',
        ]))
        return Decimal('.45'), conflicts, 'image_review_candidate'
    from card_platform.domain.parallel_valuation import assess_parallel_analogue
    if assess_parallel_analogue(identity, sale, resolver=resolver):
        return Decimal('.40'), conflicts, 'numbered_parallel_analogue'
    # Missing title fields can request inspection of the original card back.
    # This is discovery only: a same-front verdict cannot supply a serial stamp.
    if (sale.image_url and identity.get('serial_total') and identity.get('card_number')
            and (identity.get('season_start'), identity.get('season_end')) in parse_explicit_seasons(sale.title)
            and assess_identity_product_match(sale.title, identity, resolver=resolver) == ProductMatchStatus.MATCH
            and conflicts and set(conflicts) <= {
                '标题未命中目标卡号或卡种', '标题未命中目标卡号，基础/原生闪卡需图像确认',
                '标题未命中目标卡种，需图像确认', '目标为限编卡，但标题未命中目标总限编',
                '标题未命中目标平行版本', '目标表面版本未知，需图像确认',
            }):
        sale.matching_fields = list(dict.fromkeys([
            *sale.matching_fields, 'cardhobby_image_review_required',
            'cardhobby_source_back_identity_required',
        ]))
        return Decimal('.45'), conflicts, 'image_review_candidate'
    # A checklist cover title is a work, not a player's name. Sellers often
    # omit it. Re-run all remaining safety gates without inventing an alias or
    # changing the immutable sale title. Even a complete text pass stays a
    # visual lead; the formal gate independently enforces year and paired proof.
    if (conflicts == ['标题未命中目标球员'] and sale.image_url
            and official_cover_work_subject(identity, resolver=resolver)
            and assess_identity_product_match(sale.title, identity, resolver=resolver) == ProductMatchStatus.MATCH
            and any(_contains_phrase(sale.title, alias) for alias in _insert_aliases(identity))):
        _, remaining = _assess_cardhobby_match(
            identity, sale, resolver=resolver, allow_work_subject_omission=True)
        if not remaining or all(reason == '目标表面版本未知，需图像确认' for reason in remaining):
            sale.matching_fields = list(dict.fromkeys([
                *sale.matching_fields, 'cardhobby_image_review_required',
                'cardhobby_checklist_work_subject_image_required',
            ]))
            return Decimal('.45'), ['清单作品名称未在标题中完整出现，需图像确认', *remaining], 'image_review_candidate'
    # Broad three-color wording is not evidence of a named RWB finish. In the
    # explicitly identified Updates product it may request visual review only.
    if (
        sale.image_url
        and normalize_product_text(identity.get('product_name')) in {
            'topps chrome updates basketball', 'topps chrome updates', 'topps chrome update'
        }
        and normalize_product_text(identity.get('parallel_name') or identity.get('parallel')) in {
            'red white and blue refractor', 'red white blue refractor', 'rwb refractor'
        }
        and is_broad_three_color_visual_lead(sale.title)
        and (identity.get('season_start'), identity.get('season_end')) in parse_explicit_seasons(sale.title)
        and conflicts
        and all(reason in {'标题明确为其他平行版本，与目标平行不一致',
                           '标题未命中目标平行版本'} for reason in conflicts)
    ):
        sale.matching_fields = list(dict.fromkeys([
            *sale.matching_fields, 'cardhobby_image_review_required',
            'cardhobby_broad_three_color_image_required',
        ]))
        return Decimal('0.45'), ['三色描述未确定具体平行版本，需图像确认'], 'image_review_candidate'
    # An audited child shorthand is a visual lead, never text-only equivalence
    # with a separately released product of the same name. All other source,
    # roster, grade and version conflicts from the strict matcher still apply.
    if (
        sale.image_url
        and str(identity.get("brand_name") or "").casefold() == "panini"
        and has_ambiguous_child_product_shorthand(sale.title, identity.get("product_name"))
        and (identity.get("season_start"), identity.get("season_end")) in parse_explicit_seasons(sale.title)
        and (identity.get("card_number") and _contains_card_number(sale.title, identity["card_number"])
             or identity.get("serial_total") and int(identity["serial_total"]) in _explicit_print_runs(sale.title))
        and conflicts
        and all(reason in {"标题未提供可验证的目标产品信息", "标题未命中目标平行版本"} for reason in conflicts)
    ):
        return Decimal("0.45"), ["子系列简写需图像确认"], "image_review_candidate"
    if (
        sale.image_url
        and _identity_is_multi_player(identity)
        and not identity.get("confirmed_identity_version_id")
        and re.search(
            r"双人|三人|四人|多人|\bduals?\b|\bduos?\b|\btriple\b",
            sale.title,
            re.I,
        )
        and (identity.get("insert_name") or identity.get("insert"))
        and any(_contains_phrase(sale.title, alias) for alias in _insert_aliases(identity))
        and not _title_matches_player(identity, sale.title, resolver=resolver)
        and _title_matches_player(identity, sale.title, allow_partial_roster=True, resolver=resolver)
    ):
        partial_score, partial_conflicts = assess_cardhobby_match(
            identity, sale, allow_partial_roster=True, resolver=resolver
        )
        if partial_score >= Decimal(".5") and not partial_conflicts:
            sale.matching_fields = list(
                dict.fromkeys([*sale.matching_fields, "cardhobby_partial_roster_image_required"])
            )
            return (
                Decimal(".45"),
                ["多人卡部分姓名翻译尚未核实，需图像确认"],
                "image_review_candidate",
            )
    child_product = named_child_product_for_parent(sale.title, identity.get("product_name"))
    # A recognition-only parent label may omit the child line printed in a
    # checklist. Keep a tightly anchored discovery candidate for an image
    # check; confirmed identities and plain shared-name cards stay strict.
    if (
        child_product
        and sale.image_url
        and not identity.get("confirmed_identity_version_id")
        and identity.get("card_number")
        and _contains_card_number(sale.title, identity["card_number"])
        and (identity.get("insert_name") or identity.get("insert"))
        and any(_contains_phrase(sale.title, alias) for alias in _insert_aliases(identity))
    ):
        child_sale = sale.model_copy(deep=True)
        child_score, child_conflicts, child_tier = assess_cardhobby_candidate(
            {**identity, "product_name": child_product}, child_sale, resolver=resolver
        )
        if child_tier in {"exact_or_near_exact", "same_player_product_analog"} and child_score > 0:
            sale.matching_fields = list(
                dict.fromkeys([*sale.matching_fields, "cardhobby_parent_child_image_required"])
            )
            return Decimal(".45"), ["父子产品线尚未核实，需图像确认"], "image_review_candidate"
    has_image_review_reason = any(
        any(conflict.startswith(prefix) for prefix in IMAGE_REVIEW_CONFLICTS)
        for conflict in conflicts
    )
    image_review_only = bool(
        has_image_review_reason
        and sale.image_url
        and all(
            any(
                conflict.startswith(prefix)
                for prefix in (
                    *IMAGE_REVIEW_CONFLICTS,
                    *IMAGE_REVIEW_COMPANION_CONFLICTS,
                )
            )
            for conflict in conflicts
        )
        and not (
            any(
                any(conflict.startswith(prefix) for prefix in IMAGE_REVIEW_COMPANION_CONFLICTS)
                for conflict in conflicts
            )
            and has_material_parallel_marker(sale.title, identity.get("product_name"))
        )
    )
    if image_review_only:
        sale.matching_fields = list(
            dict.fromkeys([*sale.matching_fields, "cardhobby_image_review_required"])
        )
        return Decimal("0.45"), conflicts, "image_review_candidate"
    if not conflicts or not all(
        any(conflict.startswith(prefix) for prefix in ANALOG_SOFT_CONFLICTS)
        for conflict in conflicts
    ):
        return Decimal("0"), conflicts, None
    title = sale.title
    if any(
        conflict.startswith("标题未命中目标平行版本") for conflict in conflicts
    ) and has_material_parallel_marker(title, identity.get("product_name")):
        return Decimal("0"), conflicts, None
    if not _title_matches_player(identity, title, resolver=resolver):
        return Decimal("0"), conflicts, None
    product_name = str(identity.get("product_name") or "").strip()
    if (
        assess_identity_product_match(title, identity, resolver=resolver)
        == ProductMatchStatus.EXPLICIT_CONFLICT
    ):
        return Decimal("0"), conflicts, None
    explicit_seasons = _explicit_release_years(title)
    target_season = identity.get("season_start")
    if (
        explicit_seasons
        and target_season
        and min(abs(value - int(target_season)) for value in explicit_seasons) > 2
    ):
        return Decimal("0"), [*conflicts, "类比赛季与目标相距超过两年"], None
    sale.matching_fields = list(
        dict.fromkeys([*sale.matching_fields, "analog_same_player_product"])
    )
    return Decimal("0.40"), conflicts, "same_player_product_analog"


class CardHobbyProvider:
    """Fetch CardHobby sold items; model search is discovery-only fallback."""

    def __init__(
        self,
        client: AsyncOpenAI | None,
        model: str,
        timeout: float = 15.0,
        resolver: KnowledgeResolver | None = None,
        min_interval: float = 0.2,
        max_search_pages: int = 12,
    ) -> None:
        if not 1 <= max_search_pages <= 12:
            raise ValueError('search page budget must be between 1 and 12')
        self.max_search_pages = max_search_pages
        self.client = client
        self.model = model
        self.timeout = timeout
        self.resolver = resolver
        self.min_interval = max(min_interval, 0.0)
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._public_item_cache: dict[str, tuple[float, dict]] = {}

    async def _get_search_payload(
        self,
        client: httpx.AsyncClient,
        query: str,
        page_index: int,
        *,
        identity: dict | None = None,
    ) -> dict:
        """Pace shared-worker requests and retry transient CardHobby throttles."""
        async with self._request_lock:
            last_error: Exception | None = None
            for attempt in range(3):
                wait_seconds = self.min_interval - (time.monotonic() - self._last_request_at)
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
                try:
                    response = await client.get(
                        CARDHOBBY_SEARCH_URL,
                        params=self.search_params(query, page_index, identity=identity),
                    )
                    self._last_request_at = time.monotonic()
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict) or (
                        "result" in payload and payload["result"] not in (1, "1", True)
                    ):
                        raise ValueError("卡淘公开接口返回业务失败，不能作为零成交")
                    data = payload.get("data")
                    if not isinstance(data, dict) or not isinstance(data.get("PagedMarketItemList"), list):
                        raise ValueError("卡淘公开接口缺少成交列表，不能作为零成交")
                    if (not data['PagedMarketItemList']
                            and all(key in data and data[key] is None
                                    for key in ('Keyword', 'Total', 'TotalCount', 'PageIndex'))
                            and data.get('StatusType') == 0):
                        # A public request without the expected browser headers
                        # can return result=1 with an uninitialized search model.
                        # Real responses can also have Keyword=null/StatusType=0,
                        # so pagination/counts, not these two fields alone, are
                        # the distinguishing evidence. Keep the working request
                        # parameters and classify this shell as transport failure.
                        raise ValueError('unbound_search_response: 卡淘查询未绑定有效分页，不能作为零成交')
                    return payload
                except httpx.HTTPStatusError as error:
                    last_error = error
                    if error.response.status_code not in {400, 429, 502, 503, 504}:
                        raise
                    if attempt < 2:
                        await asyncio.sleep(0.25 * (attempt + 1))
                except (ValueError, TypeError) as error:
                    last_error = error
                    break
                except httpx.TransportError as error:
                    last_error = error
                    if attempt < 2:
                        await asyncio.sleep(0.25 * (attempt + 1))
            if last_error is not None:
                raise last_error
            raise ValueError("卡淘公开接口未返回可解析响应")

    async def _get_public_item_payload(self, client: httpx.AsyncClient, url: str) -> dict:
        """An optional seed request inside the same protected fetch and budget."""
        canonical = canonical_public_item_url(url)
        if canonical is None:
            raise ValueError("unapproved public item URL")
        async with self._request_lock:
            cached = self._public_item_cache.get(canonical)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
            wait_seconds = self.min_interval - (time.monotonic() - self._last_request_at)
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            body = bytearray()
            try:
                async with client.stream("GET", canonical, follow_redirects=False) as response:
                    if response.is_redirect:
                        raise ValueError("public item redirect rejected")
                    response.raise_for_status()
                    if "text/html" not in response.headers.get("content-type", "text/html"):
                        raise ValueError("public item is not HTML")
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 8_000_000:
                            raise ValueError("public item response exceeds size limit")
            finally:
                self._last_request_at = time.monotonic()
            html = body.decode("utf-8", errors="replace")
            if re.search(r"cf-chl-|<title>[^<]*(?:captcha|challenge)", html, re.I):
                raise ValueError("captcha challenge on public item source")
            parsed = parse_public_item_html(html, canonical)
            metadata = (parsed["sale"]["source_metadata"] if parsed.get("sale")
                        else parsed["source_metadata"])
            metadata["raw_html_sha256"] = hashlib.sha256(body).hexdigest()
            self._public_item_cache[canonical] = (time.monotonic(), parsed)
            return parsed

    async def search(self, identity: dict) -> CardHobbySearchResult:
        try:
            direct = await self._search_direct(identity)
        except (httpx.HTTPError, ValueError, TypeError) as error:
            if ((isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {403, 429})
                    or "captcha" in str(error).casefold()):
                raise  # Let the outer ProtectedProvider record the source circuit event.
            direct = CardHobbySearchResult(notes=[f"卡淘公开接口调用失败：{str(error)[:180]}"])
        if direct.sales:
            return direct
        if self.client is None:
            return direct.model_copy(
                update={
                    "notes": [
                        *direct.notes,
                        "未配置模型搜索；卡淘公开成交接口仍已独立执行",
                    ]
                }
            )
        fallback = await self._search_model(identity)
        return fallback.model_copy(
            update={
                "sales": [],
                "candidate_pool": [],
                "search_queries": list(
                    dict.fromkeys(
                        [
                            *direct.search_queries,
                            *fallback.search_queries,
                        ]
                    )
                ),
                "query_diagnostics": direct.query_diagnostics,
                "notes": [
                    *direct.notes,
                    # Unverified discovery prose must not become durable market
                    # facts or override the actual source's rejected search hits.
                    (
                        f"模型检索发现 {len(fallback.sales)} 条卡淘候选，但公开接口未能独立确认"
                        " Status=-2 与真实出价；候选仅作发现线索，未进入估值。"
                    ),
                ],
            }
        )

    async def reference_images(self, identity: dict) -> CardHobbyReferenceImages | None:
        direct = await self._search_direct(identity)
        exact = [sale for sale in direct.sales if is_exact_cardhobby_reference(identity, sale, resolver=self.resolver)]
        if not exact:
            return None
        exact.sort(
            key=lambda sale: (
                _contains_phrase(sale.title, identity.get("card_number")),
                sale.match_confidence,
                sale.occurred_at,
            ),
            reverse=True,
        )
        sale = exact[0]
        headers = {
            "Referer": "https://www.cardhobby.com.cn/",
            "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/139 Safari/537.36",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, headers=headers
        ) as client:
            response = await client.get(sale.source_url)
            response.raise_for_status()
        images = extract_cardhobby_images(response.text)
        front_url = sale.image_url or (images[0] if images else None)
        if not front_url:
            return None
        ordered = [front_url, *(url for url in images if url != front_url)]
        return CardHobbyReferenceImages(
            title=sale.title,
            source_url=sale.source_url,
            front_url=ordered[0],
            back_url=ordered[1] if len(ordered) > 1 else None,
            match_confidence=Decimal("0.98"),
        )

    async def _search_direct(self, identity: dict) -> CardHobbySearchResult:
        from card_platform.domain.card_identity import is_placeholder_identity_value
        if is_placeholder_identity_value(identity.get('player_name')):
            identity = {**identity, 'player_name': '', 'player_name_aliases': []}
        if self.resolver and getattr(self.resolver, "card_catalog", None):
            identity, _ = self.resolver.card_catalog.enrich(
                identity, player_aliases=self.resolver.aliases_for("player", identity.get("player_name"))
            )
        query_identity = {
            **identity,
            "player_name_aliases": [
                *list(_identity_aliases(identity, "player_name"))[1:],
                *(self.resolver.aliases_for("player", identity.get("player_name")) if self.resolver else ()),
            ],
        }
        query_plan = (self.build_query_plan(query_identity, resolver=self.resolver)
                      if self.resolver else self.build_query_plan(query_identity))
        if self.resolver and self.resolver.entities:
            knowledge_queries = list(self.resolver.build_query_plan(query_identity))
            # Generic knowledge aliases and provider-specific seller vocabulary
            # are complementary. Never let the generic plan replace CardHobby
            # aliases such as Chinese product nicknames or source-specific year
            # forms. Reserve two of the bounded remote-query slots before the
            # provider plan fills all 12; appending then truncating made every
            # reviewed KnowledgeResolver alias unreachable in production.
            alternate_alias_groups: list[tuple[str, ...]] = []
            catalog = getattr(self.resolver, 'card_catalog', None)
            if catalog:
                vocabulary = catalog.discovery_vocabulary(query_identity)
                alternate_alias_groups.extend(values for values in vocabulary.values() if values)
            # Discovery-only vocabulary competes for query slots, but must not
            # enter query_identity's trusted player aliases or matching indexes.
            query_aliases_for = getattr(self.resolver, "query_aliases_for", lambda *_: ())
            for entity_type, raw_value in (
                ("player", query_identity.get("player_name")),
                ("product", query_identity.get("product_name")),
                (
                    "insert",
                    query_identity.get("insert_name") or query_identity.get("insert"),
                ),
                (
                    "parallel",
                    query_identity.get("parallel_name") or query_identity.get("parallel"),
                ),
            ):
                raw_normalized = _normalized(raw_value)
                alternatives = tuple(
                    alias
                    for alias in dict.fromkeys((
                        *self.resolver.aliases_for(entity_type, raw_value),
                        *query_aliases_for(entity_type, raw_value),
                    ))
                    if _normalized(alias) != raw_normalized
                )
                if alternatives:
                    alternate_alias_groups.append(alternatives)

            provider_queries: list[tuple[str, str]] = []
            seen_queries: set[str] = set()
            for stage, query in query_plan:
                normalized_query = _normalized(query)
                if normalized_query and normalized_query not in seen_queries:
                    provider_queries.append((stage, query))
                    seen_queries.add(normalized_query)

            unique_knowledge_queries = []
            for original_index, stage in enumerate(knowledge_queries):
                scoped_query = _query_with_product_scope(query_identity, stage.query)
                normalized_query = _normalized(scoped_query)
                if not normalized_query or normalized_query in seen_queries:
                    continue
                seen_queries.add(normalized_query)
                alias_coverage = sum(
                    any(_contains_phrase(scoped_query, alias) for alias in aliases)
                    for aliases in alternate_alias_groups
                )
                unique_knowledge_queries.append((alias_coverage, original_index, scoped_query))
            unique_knowledge_queries.sort(key=lambda item: (-item[0], item[1]))

            # Knowledge expansion is a second budget allocation, not a second
            # blind truncation. Preserve the independent provider families
            # already chosen above before reserving optional alias requests.
            protected = set(provider_queries[:2])
            for protected_stage in (
                "subject_print_run",
                "collector_prefix",
                "short_insert",
                "season_insert",
                "release_parallel",
                "brand_release_product",
                "relax_product",
                "product_spelling_rotation",
                "product_alias_rotation",
                "refine_product",
                "explicit_base",
                "refine_print_run",
                "player_release_recovery",
                "player_alias_rotation",
            ):
                chosen = next(
                    (item for item in provider_queries if item[0] == protected_stage), None
                )
                if chosen is not None:
                    protected.add(chosen)
            knowledge_reserve = min(2, len(unique_knowledge_queries), max(0, 12 - len(protected)))
            provider_limit = max(12 - knowledge_reserve, 0)
            selected_provider = set(protected)
            for item in provider_queries:
                if len(selected_provider) >= provider_limit:
                    break
                selected_provider.add(item)
            query_plan = [
                *(item for item in provider_queries if item in selected_provider),
                *(
                    ("knowledge_alias", query)
                    for _coverage, _index, query in unique_knowledge_queries[:knowledge_reserve]
                ),
            ]
            if len(query_plan) < 12:
                query_plan.extend(
                    item for item in provider_queries if item not in selected_provider
                )
            if len(query_plan) < 12:
                query_plan.extend(
                    ("knowledge_alias", query)
                    for _coverage, _index, query in unique_knowledge_queries[
                        knowledge_reserve : knowledge_reserve + 12 - len(query_plan)
                    ]
                )
        query_plan = query_plan[:12]
        source_urls = list(dict.fromkeys(
            url for value in identity.get("public_source_urls", [])
            if (url := canonical_public_item_url(value))
        ))[:min(2, self.max_search_pages)]
        if source_urls:
            # Share the twelve-request budget. Preserve independent recall
            # families before redundant aliases when seeds consume a slot.
            remaining = self.max_search_pages - len(source_urls)
            preferred = list(query_plan[:2])
            for stage in ("subject_print_run", "collector_prefix", "short_insert", "relax_product", "brand_release_product", "refine_print_run", "player_release_recovery", "player_alias_rotation",
                          "product_alias_rotation", "product_spelling_rotation", "refine_product",
                          "season_insert", "release_parallel", "explicit_base"):
                candidate = next((item for item in query_plan if item[0] == stage), None)
                if candidate and candidate not in preferred:
                    preferred.append(candidate)
            selected = set([*preferred, *(item for item in query_plan if item not in preferred)][:remaining])
            query_plan = [item for item in query_plan if item in selected]
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.cardhobby.com.cn/market/search?ckey=0&keyword=",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/139.0 Safari/537.36"
            ),
        }
        page_requests = 0
        found: dict[str, CardHobbySale] = {}
        public_sources: dict[str, dict] = {}
        public_pages: dict[str, dict] = {}
        failed_queries: list[str] = []
        query_failures: list[Exception] = []
        diagnostics: list[CardHobbyQueryDiagnostic] = []
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, headers=headers
        ) as client:
            page_plan = BoundedAliasFeedback(
                query_plan, canonical=query_identity.get('player_name'),
                trusted_aliases=(*_identity_aliases(query_identity, 'player_name'),
                                 *query_player_aliases(query_identity.get('player_name'))),
                recovery=[('legacy_recovery', value) for value in self.build_queries(query_identity)],
                seeds=source_urls, limit=self.max_search_pages,
                enabled=not _identity_is_multi_player(query_identity),
            )
            for stage, query, requested_page in page_plan:
                if page_requests >= self.max_search_pages:
                    break
                raw_count = 0
                eligible_count = 0
                accepted_count = 0
                query_source_ids: set[str] = set()
                query_candidate_ids: set[str] = set()
                rejected_reasons: dict[str, int] = {}
                rejected_examples: list[dict] = []
                pages_fetched = 0
                query_error: str | None = None
                error_type: str | None = None
                http_status: int | None = None
                retryable = False
                for page_index in (requested_page,):
                    if page_requests >= self.max_search_pages:
                        break
                    page_requests += 1
                    page_started = perf_counter()
                    try:
                        payload = (await self._get_public_item_payload(client, query)
                                   if stage == "public_item_source"
                                   else await self._get_search_payload(client, query, page_index,
                                       **({'identity': query_identity}
                                          if not _has_explicit_basketball_scope(query_identity) else {})))
                    except (httpx.HTTPError, ValueError, TypeError) as error:
                        if stage == "public_item_source" and (
                            (isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {403, 429})
                            or "captcha" in str(error).casefold()
                        ):
                            raise
                        emit('retrieval', 'failed', event_kind='search_page', query=query,
                             page=page_index, elapsed_ms=(perf_counter()-page_started)*1000,
                             error_type=type(error).__name__, reason_codes=['source_request_failed'])
                        error_type = type(error).__name__
                        http_status = (
                            error.response.status_code if isinstance(error, httpx.HTTPStatusError)
                            else None
                        )
                        retryable = (
                            isinstance(error, (httpx.TimeoutException, httpx.NetworkError))
                            or http_status in {429, 502, 503, 504}
                        )
                        query_error = (str(error) or error_type)[:120]
                        query_failures.append(error)
                        failed_queries.append(f"{query}: {query_error}")
                        break
                    if stage == "public_item_source":
                        public_pages[canonical_public_item_url(query)] = payload
                        rows = [payload["sale"]] if payload.get("sale") else []
                        if not rows:
                            for reason in payload.get("reasons", []):
                                rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
                            rejected_examples.append({"source_url": query, "audit_only": True,
                                "reasons": payload.get("reasons", []),
                                "source_metadata": payload.get("source_metadata", {})})
                    else:
                        rows = (payload.get("data") or {}).get("PagedMarketItemList") or []
                    emit('retrieval', 'succeeded' if rows else 'empty', event_kind='search_page',
                         query=query, page=page_index, raw_payload=payload,
                         payload_hash=content_hash(payload), elapsed_ms=(perf_counter()-page_started)*1000)
                    pages_fetched += 1
                    raw_count += len(rows)
                    for row in rows:
                        row_id = (row.get('ID') or row.get('item_id')) if isinstance(row, dict) else None
                        if row_id is not None:
                            query_source_ids.add(str(row_id))
                        sale = (CardHobbySale.model_validate(row) if stage == "public_item_source"
                                else self.parse_market_item(row))
                        if sale is None:
                            emit('sale_status', 'rejected', event_kind='candidate', query=query,
                                 page=page_index, raw_payload=row, source_hash=content_hash(row),
                                 reason_codes=['unverifiable_public_auction_row'])
                            continue
                        eligible_count += 1
                        if stage != 'public_item_source':
                            from card_platform.public_source_catalog import observe_public_sale
                            observation = observe_public_sale(row)
                            if observation is not None:
                                public_sources[sale.item_id] = observation
                        score, conflicts, tier = assess_cardhobby_candidate(identity, sale, resolver=self.resolver)
                        emit('same_card', 'rejected' if score < Decimal('0.35') else 'candidate',
                             event_kind='candidate', query=query, page=page_index,
                             item_id=sale.item_id, source_hash=content_hash(row),
                             reason_codes=list(conflicts), discovery_tier=tier,
                             final_eligibility='not_yet_verified')
                        if score < Decimal("0.35"):
                            append_rejected_example(
                                rejected_examples, sale, conflicts, row.get("Status")
                            )
                            for reason in conflicts or ["身份门禁分数不足"]:
                                rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
                            continue
                        accepted_count += 1
                        key = sale.item_id or sale.source_url
                        query_candidate_ids.add(str(key))
                        if key in found and found[key].source_metadata.get("kind") == "public_item_html":
                            continue
                        found[key] = sale.model_copy(
                            update={
                                "match_confidence": score,
                                "conflicts": conflicts,
                                "matching_fields": list(
                                    dict.fromkeys([*sale.matching_fields, f"cardhobby_tier:{tier}"])
                                ),
                            }
                        )
                    # Cover every planned query family before spending spare
                    # page budget on a broad query's continuation.
                    if page_index == 1 and len(rows) >= 60:
                        page_plan.append((stage, query, 2))
                if query_error is not None:
                    outcome = "error"
                elif stage == "public_item_source" and rejected_reasons and raw_count == 0:
                    outcome = "source_facts_rejected"
                elif raw_count == 0:
                    outcome = "zero_results_relax"
                elif raw_count >= 60 and accepted_count == 0:
                    outcome = "too_broad_refine"
                elif accepted_count:
                    outcome = "accepted"
                else:
                    outcome = "identity_gate_rejected"
                new_source_count, new_candidate_count = page_plan.observe(
                    query, raw_ids=query_source_ids, candidate_ids=query_candidate_ids,
                    raw_count=raw_count, errored=query_error is not None,
                )
                diagnostics.append(
                    CardHobbyQueryDiagnostic(
                        query=query,
                        stage=stage,
                        raw_count=raw_count,
                        eligible_count=eligible_count,
                        accepted_count=accepted_count,
                        new_source_count=new_source_count,
                        new_candidate_count=new_candidate_count,
                        rejected_count=max(eligible_count - accepted_count, 0),
                        pages_fetched=pages_fetched,
                        outcome=outcome,
                        rejection_reasons=dict(
                            sorted(
                                rejected_reasons.items(),
                                key=lambda item: (-item[1], item[0]),
                            )[:8]
                        ),
                        error=query_error,
                        error_type=error_type,
                        http_status=http_status,
                        retryable=retryable,
                        rejected_examples=rejected_examples,
                    )
                )
        if diagnostics and all(item.outcome == "error" for item in diagnostics):
            emit('retrieval', 'failed', event_kind='search_stop', page_requests=page_requests,
                 candidate_count=0, returned_count=0, reason_codes=['all_queries_failed'])
            raise CardHobbySearchError(diagnostics) from query_failures[-1]
        # A page may lack independently verifiable currency/shipping while the
        # sold API provides valid sale facts. Preserve its gallery separately;
        # never turn a rejected HTML price into a sale or overwrite API facts.
        for key, sale in list(found.items()):
            payload = public_pages.get(canonical_public_item_url(sale.source_url))
            if payload:
                gallery = public_gallery_for_primary(payload, sale.source_url, sale.image_url)
                if gallery:
                    found[key] = sale.model_copy(update={'source_gallery': gallery})
        ranked_candidates = sorted(
            found.values(),
            key=lambda sale: (
                sale.match_confidence,
                not sale.conflicts,
                bool(sale.conflicts) and all(
                    conflict.startswith("标题未命中目标卡号或卡种")
                    or conflict.startswith("标题未命中目标卡种，需图像确认")
                    for conflict in sale.conflicts
                ),
                sale.occurred_at,
            ),
            reverse=True,
        )
        emit('retrieval', 'completed', event_kind='search_stop',
             page_requests=page_requests, candidate_count=len(found), returned_count=len(found),
             reason_codes=['page_budget_exhausted' if page_requests >= self.max_search_pages else 'query_plan_exhausted'],
             truncated_ids=[])
        result = CardHobbySearchResult(
            search_queries=[
                f"卡淘已售接口[{item.stage}]：{item.query} "
                f"(召回{item.raw_count}/通过{item.accepted_count};{item.outcome})"
                for item in diagnostics
            ],
            # Keep the legacy display shortlist while retaining every discovered
            # candidate for persistence and independent verification selection. Prefer an
            # otherwise exact base-card candidate that merely omits its card
            # number over a newer title that explicitly calls itself a
            # different insert/parallel. Recency remains the tie-breaker.
            sales=ranked_candidates[:20],
            candidate_pool=ranked_candidates,
            notes=[
                "数据来自卡淘公开 SearchCommodity 接口（Status=-2）或有sold标记的公开商品详情；"
                "有出价的结束拍卖作为卡淘结束价参考，未宣称已付款。",
                *(
                    [f"部分查询失败但已保留其他查询结果：{'；'.join(failed_queries[:3])}"]
                    if failed_queries
                    else []
                ),
                *[
                    "查询反馈："
                    f"{item.query} → 原始{item.raw_count}、可解析{item.eligible_count}、"
                    f"通过{item.accepted_count}、拒绝{item.rejected_count}；"
                    f"结论={item.outcome}"
                    + (
                        "；主要拒绝="
                        + "、".join(
                            f"{reason}×{count}"
                            for reason, count in list(item.rejection_reasons.items())[:3]
                        )
                        if item.rejection_reasons
                        else ""
                    )
                    for item in diagnostics
                ],
            ],
            query_diagnostics=diagnostics,
        )
        result._public_source_pool = list(public_sources.values())
        return result

    @staticmethod
    def build_query_plan(identity: dict, *, resolver=None) -> list[tuple[str, str]]:
        """Build short-anchor queries with bounded feedback refinements."""
        from card_platform.domain.card_identity import is_placeholder_identity_value
        if is_placeholder_identity_value(identity.get('player_name')) or not identity.get('player_name'):
            # A work/scene is a retrieval anchor, never an invented person.
            subject = str(identity.get('insert_name') or identity.get('set_name') or '').strip()
            product = str(identity.get('product_name') or '').strip()
            number = str(identity.get('card_number') or '').strip()
            return [(stage, query) for stage, query in [
                ('work_number', f'{subject} {number}'.strip()),
                ('work_product', f'{subject} {product}'.strip()),
                ('product_number', f'{product} {number}'.strip()),
            ] if subject and product and number and query]
        parts = player_roster_components(identity.get('player_name'))
        player_groups = _player_components(identity, resolver=resolver)
        if len(player_groups) > 1:
            # Search each subject independently. CardHobby's token matcher is
            # effectively AND-based, so concatenating every printed name is
            # much less reliable than one recognizable player + insert/serial.
            ordered_players = list(
                dict.fromkeys(
                    min(
                        group,
                        key=lambda value: (
                            not bool(re.search(r"[\u4e00-\u9fff]", value)),
                            len(value),
                        ),
                    )
                    for group in player_groups
                    if group
                )
            )
        else:
            players = (*_identity_aliases(identity, "player_name"), *query_player_aliases(identity.get("player_name")))
            chinese_players = [
                value for value in players[1:] if re.search(r"[\u4e00-\u9fff]", value)
            ]
            # Reviewed query vocabulary is ordered by preference. Choosing
            # the shortest alias first discards that ranking (e.g. Brunson's
            # preferred 布伦森 loses to the ambiguous shorter 布朗).
            from card_platform.domain import player_aliases as player_vocabulary
            preferred_aliases = getattr(
                player_vocabulary, "NBA_PREFERRED_QUERY_ALIASES", {}
            ).get(_normalized(identity.get("player_name")), ())
            preferred_chinese = next((value for value in preferred_aliases
                if re.search(r"[\u4e00-\u9fff]", value)), None)
            shortest_chinese = ([preferred_chinese] if preferred_chinese
                                else sorted(chinese_players, key=len)[:1])
            canonical_player = players[:1]
            ordered_players = list(
                dict.fromkeys(
                    [*shortest_chinese, *canonical_player, *chinese_players, *players[1:]]
                )
            )
        product_aliases = product_search_aliases(identity.get("product_name"))
        product = product_aliases[0] if product_aliases else ""
        primary_product_scripts = (
            bool(re.search(r"[A-Za-z]", product)),
            bool(re.search(r"[\u3400-\u9fff]", product)),
        )
        same_script_product = next(
            (
                alias
                for alias in (
                    str(identity.get("product_name") or "").strip(),
                    *product_aliases[1:],
                )
                if _normalized(alias) != _normalized(product)
                and (
                    bool(re.search(r"[A-Za-z]", alias)),
                    bool(re.search(r"[\u3400-\u9fff]", alias)),
                )
                == primary_product_scripts
            ),
            "",
        )
        alternate_product = next(
            (
                alias
                for alias in product_aliases[1:]
                if (
                    bool(re.search(r"[A-Za-z]", alias)),
                    bool(re.search(r"[\u3400-\u9fff]", alias)),
                )
                != primary_product_scripts
            ),
            "",
        )
        card_number = str(identity.get("card_number") or "").strip().lstrip("#")
        if re.fullmatch(r"CMP\d+|\d{6,}", card_number, re.I):
            card_number = ""
        insert = str(identity.get("insert") or identity.get("insert_name") or "").strip()
        insert_aliases = tuple(
            dict.fromkeys(
                [
                    *seller_aliases("insert", insert),
                    *INSERT_SEARCH_ALIASES.get(insert.casefold(), (insert,) if insert else ()),
                ]
            )
        )
        primary_insert = insert_aliases[0] if insert_aliases else ""
        alternate_inserts = insert_aliases[1:3]
        print_run = identity.get("serial_total") or identity.get("print_run")
        serial = f"{int(print_run)}编" if print_run else ""
        season_start = identity.get("season_start")
        season_end = identity.get("season_end")
        season = (
            f"{season_start}-{str(season_end)[-2:]}"
            if season_start and season_end and season_start != season_end
            else str(season_start or "")
        )
        parallel_name = str(identity.get("parallel_name") or "").strip()
        parallel_aliases = tuple(
            dict.fromkeys(
                [
                    *seller_aliases("parallel", parallel_name),
                    *PARALLEL_MATCH_ALIASES.get(
                        parallel_name.casefold(),
                        (parallel_name,) if parallel_name else (),
                    ),
                ]
            )
        )
        primary_parallel = (
            min(
                parallel_aliases,
                key=lambda value: (
                    not bool(re.search(r"[\u4e00-\u9fff]", value)),
                    len(value),
                ),
            )
            if parallel_aliases
            else ""
        )

        def query(*parts: object) -> str:
            return _query_with_product_scope(identity, " ".join(
                str(part).strip() for part in parts if str(part or "").strip()))

        plan: list[tuple[str, str]] = []
        work_title = official_work_query_title(identity, resolver=resolver)
        if work_title:
            plan.append(('official_work_title', work_title))
            if season_start:
                plan.append(('official_work_release', f'{work_title} {season_start}'))
        # Rotate beyond the shortest Chinese alias and canonical name. These
        # aliases come from reviewed vocabulary only and remain query-only.
        player_rotation = None
        if len(player_groups) <= 1 and len(ordered_players) > 2:
            # Punctuation-only spellings do not provide an independent alias.
            # Scan the trusted pool beyond the old five-name cap, without
            # adding these query aliases to matching/identity evidence.
            primary_names = {
                "".join(char for char in name.casefold() if char.isalnum())
                for name in ordered_players[:2]
            }
            alternate_player = next(
                (
                    name for name in ordered_players[2:]
                    if "".join(char for char in name.casefold() if char.isalnum()) not in primary_names
                ),
                None,
            )
            if alternate_player:
                player_rotation = (
                    "player_alias_rotation",
                    query(alternate_player, product or season_start or card_number),
                )
        if redemption_identity(identity)[0]:
            for player in ordered_players[:2]:
                plan.extend([
                    ("redemption", query(player, product, "交换")),
                    ("redemption", query(player, "交换")),
                    ("redemption", query(player, product, "redemption")),
                ])
        # Start with only two strong anchors. This works with CardHobby's AND
        # tokenization and keeps weak OCR fields out of initial discovery.
        for player in ordered_players[:2]:
            if card_number:
                plan.append(("strong_anchor", query(player, card_number)))
            if primary_insert:
                plan.append(("strong_anchor", query(player, primary_insert)))
        # Descriptive wrappers occur on card backs but may be absent from
        # seller titles. Preserve the full label, plus a bounded subject/type
        # lane independent of year/product. This never adds matching aliases.
        short_insert = short_insert_query_term(insert)
        if short_insert:
            for player in ordered_players[:2]:
                plan.append(("short_insert", query(player, short_insert)))
        # A catalog product/insert label must not constrain every numbered
        # query. Keep this independent lane inside the existing request budget;
        # classification still verifies the actual product and variant.
        if serial and len(player_groups) <= 1 and identity.get("player_name"):
            plan.append(("subject_print_run", query(
                discovery_subject(identity["player_name"]), serial)))
        if serial and identity.get('player_name') and season_start:
            subject = re.sub(r'[&,，]+', ' ', str(identity['player_name']))
            plan.append(('player_release_recovery', query(subject, season_start)))
        # Seller shorthand often retains only the alphabetic checklist prefix
        # (e.g. RSI签 / GN). It is a discovery token, never a matching alias.
        prefix = re.match(r'^([A-Za-z]{2,5})[-/][A-Za-z0-9]', card_number)
        if prefix and not serial and len(player_groups) <= 1 and not work_title and identity.get("player_name"):
            plan.append(("collector_prefix", query(
                discovery_subject(identity["player_name"]), prefix[1])))
        # Reserve this high-signal form before product/alias expansions consume
        # the 12-request budget. It is the common seller query shape for inserts
        # across every brand (player + season + insert), not a product exception.
        if season and primary_insert:
            for player in ordered_players[:2] or [""]:
                plan.append(("season_insert", query(player, season, primary_insert)))
        # Sellers often omit the catalog product spelling. Reserve a visual
        # seller-language query independent of product, card number and serial.
        # This is discovery only: product/variant/image gates remain unchanged.
        if season_start and primary_parallel and ordered_players and not primary_insert:
            plan.append(("release_parallel", query(ordered_players[0], season_start, primary_parallel)))
        if ordered_players and product:
            # Sellers may omit both card number and insert name. Reserve one
            # product-only query even when no same-script synonym exists.
            plan.append(("relax_product", query(ordered_players[0], product)))
        # Same product names can span makers and years. Keep one joint
        # discovery lane without removing the broader recovery queries: an
        # uncertain identity field must not hard-filter the entire search.
        brand = str(identity.get("brand_name") or "").strip()
        # A structured catalog code already has dedicated product/name-free
        # lanes. Spend the joint maker/year slot on weak or absent numbers,
        # where ordinary numbers are shared by many unrelated products.
        joint_scope = bool(ordered_players and product and season_start and brand
                           and not _is_strong_catalog_code(card_number))
        if joint_scope:
            maker = "" if _contains_phrase(product, brand) else brand
            plan.append(("brand_release_product",
                         query(ordered_players[0], season_start, maker, product)))
        if ordered_players and same_script_product:
            # Same-language spellings need their own request in token-AND
            # search too. Keep one broad alternative before refinements fill
            # the budget, preserving the original catalog wording when it
            # differs from the preferred seller alias.
            plan.append(
                ("product_spelling_rotation", query(ordered_players[0], same_script_product))
            )
        if ordered_players and alternate_product:
            # Reserve one script-diverse product synonym inside the bounded
            # request budget (e.g. 国宝/National Treasures, Mosaic/马赛克).
            # Search aliases are OR alternatives, not one token-AND query.
            plan.append(
                (
                    "product_alias_rotation",
                    query(
                        ordered_players[0],
                        alternate_product,
                        primary_insert or card_number or season,
                    ),
                )
            )
        if ordered_players:
            for alternate_insert in alternate_inserts:
                plan.append(("alias_rotation", query(ordered_players[0], alternate_insert)))
                if product:
                    plan.append(
                        (
                            "refine_product_alias",
                            query(ordered_players[0], product, alternate_insert),
                        )
                    )
            if product and card_number and not insert and not parallel_name and not print_run:
                plan.append(("explicit_base", query(ordered_players[0], product, "base")))
            if product and primary_parallel:
                plan.append(
                    ("refine_parallel", query(ordered_players[0], product, primary_parallel))
                )

        primary_players = ordered_players[:2] or [""]
        # Structured checklist codes and named inserts are safe *discovery*
        # anchors even when a seller omits the player's canonical English name.
        # Every recalled row still passes `_title_matches_player` below, so the
        # query-only aliases never become identity evidence by themselves.
        strong_catalog_code = card_number if _is_strong_catalog_code(card_number) else ""
        if strong_catalog_code and product:
            plan.append(("catalog_code_product", query(strong_catalog_code, product)))
        if primary_insert and product:
            plan.append(("insert_product_no_player", query(primary_insert, product)))
        given_name = None if work_title else _unique_given_name_discovery_alias(identity)
        if given_name and product:
            plan.append(("player_given_product", query(given_name, product)))
        discovery_anchor = strong_catalog_code or primary_insert
        for player_alias in ([] if work_title else _player_discovery_aliases(identity)[:2]):
            if discovery_anchor:
                plan.append(
                    (
                        "player_discovery_alias",
                        query(player_alias, discovery_anchor, product),
                    )
                )
        # Full-page feedback means “too broad”: add exactly one discriminator.
        for player in primary_players:
            if product and card_number:
                plan.append(("refine_product", query(player, product, card_number)))
            if product and primary_insert:
                plan.append(("refine_product", query(player, product, primary_insert)))
            for alternate_insert in alternate_inserts:
                if product:
                    plan.append(("refine_product_alias", query(player, product, alternate_insert)))
            if primary_insert and serial:
                plan.append(("refine_print_run", query(player, primary_insert, serial)))
            if product and serial:
                plan.append(("refine_print_run", query(player, product, serial)))

        # Zero-result feedback means “too strict”: drop code/serial and rotate
        # seller product/release-year wording.
        for player in primary_players:
            if product:
                plan.append(("relax_product", query(player, product)))
            if product and season_start:
                plan.append(("release_year", query(player, season_start, product)))
        if ordered_players:
            plan.append(("broad_player", ordered_players[0]))

        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for stage, value in plan:
            normalized_query = _normalized(value)
            if value and normalized_query not in seen:
                unique.append((stage, value))
                seen.add(normalized_query)
        # Catalog-specific legacy variants remain a final recovery pool.
        for value in CardHobbyProvider.build_queries(identity):
            normalized_query = _normalized(value)
            if value and normalized_query not in seen:
                unique.append(("legacy_recovery", value))
                seen.add(normalized_query)
        # Allocate a bounded budget across independent query families before
        # filling it with aliases. Tail truncation alone can silently eliminate
        # the sole release-only, primary-product or numbered-product request.
        independent = None
        if ordered_players and season_start:
            candidate = ("player_release_recovery", query(ordered_players[0], season_start))
            independent = next(
                (item for item in unique if _normalized(item[1]) == _normalized(candidate[1])),
                None,
            )
            if independent is None:
                independent = candidate
                unique.append(independent)
        # Spend at most one extra short lane per identity. A prefix query can
        # replace a redundant subject/full-insert anchor, while preserving the
        # complete collector number and the established catalog recovery lanes.
        prefix_lane = next((item for item in unique if item[0] == "collector_prefix"), None)
        required = set(unique[:1] if prefix_lane else unique[:2])
        for stage in (
            "subject_print_run",
            "collector_prefix",
            "short_insert",
            "brand_release_product",
            "season_insert",
            "release_parallel",
            "product_spelling_rotation",
            "product_alias_rotation",
            "refine_product",
            "explicit_base",
        ):
            candidate = next((item for item in unique if item[0] == stage), None)
            if candidate is not None:
                required.add(candidate)
        numbered = next((item for item in reversed(unique) if item[0] == "refine_print_run"), None)
        if numbered is not None:
            required.add(numbered)
        if independent is not None:
            required.add(independent)
        if ordered_players and product:
            primary = next(
                (item for item in unique if item[1] == query(ordered_players[0], product)),
                None,
            )
            if primary is not None:
                required.add(primary)
        if player_rotation is not None:
            existing = next(
                (item for item in unique if _normalized(item[1]) == _normalized(player_rotation[1])),
                None,
            )
            if existing is not None:
                unique.remove(existing)
                required.discard(existing)
            unique.append(player_rotation)
            required.add(player_rotation)
        # Preserve seller insert wording before filling with duplicate primary
        # name/season forms. Player rotation must not starve existing short
        # insert aliases that are more usable than long catalog phrases.
        for stage in (("alias_rotation", "refine_product_alias") if player_rotation else ()):
            candidate = next((item for item in unique if item[0] == stage), None)
            if candidate is not None and len(required) < 12:
                required.add(candidate)
        # Only rebalance when the extra joint lane is actually present.
        # Structured-code and unknown-scope plans retain their original order.
        if joint_scope:
            # Keep both primary subject/name spellings for the year+insert lane
            # before filling with duplicate player+number anchors. Multi-player
            # cards need the second subject too, not just the first name.
            for candidate in [item for item in unique if item[0] == 'season_insert'][:2]:
                if len(required) < 12:
                    required.add(candidate)
            if not strong_catalog_code:
                # Preserve a named-insert query that does not depend on a seller
                # spelling the player correctly; replace duplicate anchor aliases.
                candidate = next((item for item in unique if item[0] == 'insert_product_no_player'), None)
                if candidate is not None and len(required) < 12:
                    required.add(candidate)
        # Catalog discovery is useful but must not evict the only independent
        # player alias, print-run, release-year or product query.
        if strong_catalog_code and not alternate_inserts:
            for stage in ('catalog_code_product', 'player_given_product', 'player_discovery_alias', 'insert_product_no_player'):
                candidate = next((item for item in unique if item[0] == stage), None)
                if candidate is not None and len(required) < 12:
                    required.add(candidate)
        selected = set(required)
        for item in unique:
            if len(selected) >= 12:
                break
            selected.add(item)
        return [item for item in unique if item in selected][:12]

    @staticmethod
    def build_queries(identity: dict) -> list[str]:
        players = (*_identity_aliases(identity, "player_name"), *query_player_aliases(identity.get("player_name")))
        player = players[0].replace(",", " ") if players else ""
        product = str(identity.get("product_name") or "").strip()
        product_aliases = product_search_aliases(product)
        print_run = identity.get("serial_total") or identity.get("print_run")
        insert = str(identity.get("insert") or identity.get("insert_name") or "").strip()
        card_number = str(identity.get("card_number") or "").strip().lstrip("#")
        card_type = (
            "RPA"
            if identity.get("is_autograph") and identity.get("is_patch")
            else "AUTO"
            if identity.get("is_autograph")
            else "RC"
            if identity.get("is_rookie")
            else ""
        )
        season_start = identity.get("season_start")
        season_end = identity.get("season_end")
        season = (
            f"{season_start}-{str(season_end)[-2:]}"
            if season_start and season_end and season_start != season_end
            else str(season_start or "")
        )
        serial = f"{int(print_run)}编" if print_run else ""
        parallel_name = str(identity.get("parallel_name") or "")
        parallel = next(
            iter(seller_aliases("parallel", parallel_name)[1:]),
            PARALLEL_SEARCH_ALIASES.get(parallel_name.casefold(), ""),
        )
        # The central marketplace catalog is the source of truth for reviewed
        # seller vocabulary.  Keep the legacy map as a temporary fallback for
        # entries that have not yet been migrated into the reviewable catalog.
        insert_search_aliases = tuple(
            dict.fromkeys(
                [
                    *seller_aliases("insert", insert),
                    *INSERT_SEARCH_ALIASES.get(insert.casefold(), (insert,) if insert else ()),
                ]
            )
        )

        def query(*parts: object) -> str:
            return _query_with_product_scope(identity, " ".join(
                str(part).strip() for part in parts if part is not None and str(part).strip()
            ))

        queries: list[str] = []
        for alias_index, alias in enumerate(product_aliases):
            release_years = (
                list(dict.fromkeys((season_end, season_start)))
                if season_start and season_end and season_start != season_end
                else []
            )
            release_year_queries = [query(player, year, alias) for year in release_years]
            release_year_card_queries = [
                query(player, year, alias, card_number) for year in release_years if card_number
            ]
            if release_year_queries and _normalized(product) in RELEASE_YEAR_SEARCH_PRODUCTS:
                # Preserve the historically strongest curated seller query as
                # the first request, then add end-year/card-number variants.
                curated_primary = query(player, season_start, alias)
                queries.append(curated_primary)
                queries.extend(
                    [
                        *release_year_card_queries,
                        *(value for value in release_year_queries if value != curated_primary),
                    ]
                )
            for insert_alias in insert_search_aliases:
                queries.extend(
                    [
                        query(
                            player,
                            season,
                            identity.get("brand_name"),
                            alias,
                            insert_alias,
                            parallel,
                        ),
                        query(player, season, alias, insert_alias, parallel),
                        # Seller search often works best with the insert's
                        # short English name plus release year, without the
                        # catalog product suffix (e.g. “Wembanyama Talent
                        # 2025”).  Keep this before the 12-query cap.
                        query(player, insert_alias, season_start),
                    ]
                )
            # CardHobby behaves like an AND search. Start with four durable seller
            # tokens, then progressively remove the least reliable constraints.
            if insert:
                queries.extend(
                    [
                        query(player, alias, insert, parallel, serial),
                        query(player, insert, parallel, serial),
                        query(player, alias, insert, serial),
                        query(player, insert, serial),
                        query(player, insert),
                    ]
                )
            queries.extend(
                [
                    query(player, season, serial, alias),
                    query(player, alias, card_type, serial),
                    # CardHobby tokenization does not always equate “10编”
                    # with the slash-number form “07/10”.  Keep a plain numeric
                    # print-run query for sparse modern products, after the
                    # more discriminating autograph/card-type form.
                    query(player, season, alias, print_run),
                    query(player, season, alias),
                    query(player, alias, parallel, serial),
                    query(player, alias, serial),
                    query(player, alias),
                ]
            )
            if (
                release_year_queries
                and _normalized(product) not in RELEASE_YEAR_SEARCH_PRODUCTS
                and alias_index == 0
            ):
                # Sellers often print only the copyright/release year ("2001")
                # while the catalog identity uses an NBA season ("2001-02").
                # Keep exact serial/insert queries first, then add this bounded
                # product-level recall query for every cross-year identity.
                queries.extend([*release_year_card_queries, *release_year_queries])
            if card_number:
                queries.extend(
                    [
                        query(player, season, alias, card_number),
                        query(player, alias, card_number),
                    ]
                )
        queries.extend(
            [
                query(player, season, serial),
                query(player, card_type, serial),
                player,
            ]
        )
        unique = list(dict.fromkeys(value for value in queries if value))
        if serial:
            # Preserve the strongest curated product query at position zero,
            # then reserve an early slot for a short print-run discovery query.
            # The strict identity gate still prevents a different card entering.
            short_serial_queries = [query(player, insert, serial), query(player, serial)]
            unique = [*unique[:2], *short_serial_queries, *unique[2:]]
            unique = list(dict.fromkeys(value for value in unique if value))
        alias_queries = [
            query(player_alias, product_aliases[0])
            for player_alias in players[1:3]
            if product_aliases
        ]
        season_insert_anchor = query(player, season, insert)
        if season and insert and season_insert_anchor:
            # Keep the two historically strongest curated requests, then pin
            # the canonical season+insert recovery form ahead of truncation.
            unique = [*unique[:2], season_insert_anchor, *unique[2:]]
            unique = list(dict.fromkeys(value for value in unique if value))
        if len(product_aliases) > 1:
            primary_scripts = (
                bool(re.search(r"[A-Za-z]", product_aliases[0])),
                bool(re.search(r"[\u3400-\u9fff]", product_aliases[0])),
            )
            script_diverse_alias = next(
                (
                    alias
                    for alias in product_aliases[1:]
                    if (
                        bool(re.search(r"[A-Za-z]", alias)),
                        bool(re.search(r"[\u3400-\u9fff]", alias)),
                    )
                    != primary_scripts
                ),
                "",
            )
            if script_diverse_alias:
                product_alias_query = query(
                    player,
                    script_diverse_alias,
                    insert or serial or card_number or season_start,
                )
                unique = [*unique[:2], product_alias_query, *unique[2:]]
                unique = list(dict.fromkeys(value for value in unique if value))
        if len(players) > 1 and insert_search_aliases:
            shortest_insert_alias = min(insert_search_aliases, key=len)
            alias_queries.insert(
                0,
                query(players[1], shortest_insert_alias, season_start),
            )
        if alias_queries:
            # Preserve exact-first ordering while reserving bounded stages for
            # audited seller-language aliases and each name on multi-player cards.
            provider_budget = max(12 - len(alias_queries), 0)
            primary_product_relaxed = query(player, product_aliases[0]) if product_aliases else ""
            if (
                primary_product_relaxed
                and not insert
                and primary_product_relaxed not in unique[:provider_budget]
                and provider_budget
            ):
                # A script-diverse product query must not push the primary
                # product's broad fallback beyond the same bounded budget.
                unique = [
                    *unique[: provider_budget - 1],
                    primary_product_relaxed,
                    *unique[provider_budget - 1 :],
                ]
            unique = [*unique[:provider_budget], *alias_queries, *unique[10:]]
        bounded = list(dict.fromkeys(unique))[:12]
        if player and player not in bounded and not alias_queries:
            bounded = [*bounded[:11], player]
        return bounded

    @staticmethod
    def search_params(
        search_key: str, page_index: int, *, identity: dict | None = None
    ) -> dict[str, str | int]:
        filters = [
            {"Key": "ByWay", "Value": ""},
            {"Key": "Bargain", "Value": ""},
            {"Key": "Status", "Value": -2},
            {"Key": "Sports", "Value": "Basketball"},
            {"Key": "ThirdCategoryId", "Value": CARDHOBBY_SINGLE_CARD_CATEGORY_ID},
            {"Key": "MinPrice", "Value": ""},
            {"Key": "MaxPrice", "Value": ""},
            {"Key": "IsValut", "Value": ""},
        ]
        # The legacy category also excludes valid non-sports singles (the
        # provider has separate sports, non-sports, TCG and sketch categories).
        # Without positive basketball scope, search across categories and let
        # the existing single-card/identity gates reject bundles and wrong cards.
        # Keep the no-identity legacy API contract unchanged.
        if identity is not None and not _has_explicit_basketball_scope(identity):
            filters = [item for item in filters if item['Key'] not in {'Sports', 'ThirdCategoryId'}]
        return {
            "userId": "",
            "pageIndex": page_index,
            "pageSize": 60,
            "searchKey": search_key,
            "searchJson": json.dumps(filters, ensure_ascii=False, separators=(",", ":")),
            "sort": "EffectiveTimeStamp",
            "sortType": "DESC",
        }

    @staticmethod
    def parse_market_item(row: dict) -> CardHobbySale | None:
        if not isinstance(row, dict):
            return None
        try:
            status = int(row.get("Status") or 0)
            # An explicit zero is authoritative; do not replace it with a
            # secondary display counter or a stale asking/starting price.
            bid_raw = row.get("OutCount")
            if bid_raw is None:
                bid_raw = row.get("PriceCount")
            bid_count = int(bid_raw or 0)
            sale_type = int(row.get("ByWay") or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        if status != -2 or sale_type != 2:
            return None
        if bid_count <= 0:
            return None
        item_id = str(row.get("ID") or "").strip()
        title = str(row.get("Title") or "").strip()
        occurred_raw = str(row.get("EffectiveDate") or "").strip()
        if not item_id or not title or not occurred_raw:
            return None
        try:
            occurred_at = datetime.strptime(occurred_raw, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=ZoneInfo("Asia/Shanghai")
            )
            sale_price = Decimal(str(row.get("LowestPrice") or "0"))
        except (TypeError, ValueError, ArithmeticError):
            return None
        if (
            not sale_price.is_finite() or sale_price <= 0
            or occurred_at > datetime.now(ZoneInfo("Asia/Shanghai"))
        ):
            return None
        postage_match = re.search(r"\d+(?:\.\d+)?", str(row.get("NewPostageMoney") or ""))
        shipping_price = Decimal(postage_match.group()) if postage_match else Decimal("0")
        grade_match = GRADE_PATTERN.search(title)
        grade_company = None
        grade_value = None
        if grade_match:
            detail = GRADING_DETAIL_PATTERN.search(title)
            if detail:
                grade_company = detail.group(1).upper()
                grade_value = Decimal(detail.group(2))
        return CardHobbySale(
            item_id=item_id,
            title=title,
            source_url=f"https://cardhobby.com.cn/Market/Item/{item_id}",
            occurred_at=occurred_at,
            sale_price=sale_price,
            shipping_price=shipping_price,
            bid_count=bid_count,
            sale_type="auction" if int(row.get("ByWay") or 0) == 2 else "fixed_price",
            evidence_level="ended_auction",
            sold_status_evidence=f"卡淘已售接口 Status=-2，出价 {bid_count} 次",
            grade_company=grade_company,
            grade_value=grade_value,
            matching_fields=["cardhobby_status_sold", "cardhobby_bid_count"],
            match_confidence=Decimal("0.95"),
            image_url=str(row.get("TitImg") or "").strip() or None,
        )

    async def _search_model(self, identity: dict) -> CardHobbySearchResult:
        schema = json.dumps(CardHobbySearchResult.model_json_schema(), ensure_ascii=False)
        response = await traced_completion(self.client.chat.completions.create, stage='retrieval',
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是卡淘（CardHobby）近期成交检索器。只检索 cardhobby.com.cn 的公开"
                        "页面。优先检索卡淘交易评价页（/market/feedback），评价记录中明确出现"
                        "标题、成交价和评价时间的记录填 evidence_level=confirmed_transaction。"
                        "其次允许使用已经结束且 bid_count>0 的商品详情页（/market/item/数字），"
                        "填 evidence_level=ended_auction；这只表示结束拍卖价，不得声称已付款。"
                        "在售价格、尚未结束竞价、无出价流拍、取消交易、多卡 lot、套卡、卡盒、"
                        "卡包和复刻卡一律不能返回。商品页 item_id 必须与链接一致；评价页没有"
                        "商品 ID 时 item_id 留空。sale_price 填成交价或结束拍卖价，"
                        "shipping_price 单独填写，fx_rate_to_base 填人民币换算到账户基础币种的"
                        "可核验汇率；occurred_at 使用页面结束时间。sold_status_evidence"
                        "必须说明为何确认它来自已售列表或已成交状态，不能确认就省略该记录。"
                        "优先相同球员、赛季、产品、卡号、平行、限编、签字/物料及相同评级状态。"
                        "完全同款不足时，不要返回空结果；继续返回同球员同产品的相邻赛季、相近"
                        "限编或相同材质卡，以及同球员同档次产品的近期成交，并把所有差异写进"
                        "conflicts、实际命中项写进 matching_fields。类比卡仍必须是评价确认交易"
                        "或有出价的结束拍卖。检索时明确使用 site:cardhobby.com.cn/market/item 和"
                        "site:cardhobby.com.cn/market/feedback。"
                        "产品赛季代表卡片发行年份，与球员当时是否现役完全无关；退役传奇球员"
                        "可以出现在新赛季产品中。不得根据球员退役年份或记忆中的 checklist 推断"
                        "卡片不存在，也不得用这种推断停止检索。card_identity 中的 purchase_context"
                        "和 notes 可能包含卡淘卖家原始中文标题，必须把其中的中文球员昵称、系列"
                        "俗称、限编和材质词加入查询。你只负责返回实际找到的页面，不负责否定"
                        "卡片存在。必须先执行至少两条宽查询：英文球员名+产品名、中文球员名/"
                        "昵称+产品俗称，不附加赛季、卡号、平行或限编；再逐步增加材质、赛季和"
                        "限编。purchase_context 末尾的货位号、卖家库存号、订单号（例如2745、"
                        "A68、V75A）不得当成卡号或强制搜索条件。完全同款为空时，宽查询找到的"
                        "同球员同产品类比成交仍应返回。裸卡和评级卡严禁混用。最多返回最近的"
                        "20 条。"
                        f"只输出符合 JSON Schema 的 JSON：{schema}"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            key: value
                            for key, value in identity.items()
                            if key not in {"collection_item_id", "card_definition_id"}
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
                },
            },
        )
        content = response.choices[0].message.content
        if not content:
            return CardHobbySearchResult(notes=["卡淘定向检索未返回结果"])
        parsed = CardHobbySearchResult.model_validate_json(content)
        accepted: list[CardHobbySale] = []
        for sale in parsed.sales:
            score, conflicts, tier = assess_cardhobby_candidate(identity, sale, resolver=self.resolver)
            if score < Decimal("0.35"):
                continue
            accepted.append(
                sale.model_copy(
                    update={
                        "match_confidence": score,
                        "conflicts": conflicts,
                        "matching_fields": list(
                            dict.fromkeys([*sale.matching_fields, f"cardhobby_tier:{tier}"])
                        ),
                    }
                )
            )
        return parsed.model_copy(update={"sales": accepted[:20]})
