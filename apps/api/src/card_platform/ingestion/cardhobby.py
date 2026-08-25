import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from card_platform.domain.listing_title import is_memorabilia_listing, is_multi_card_listing
from card_platform.knowledge import KnowledgeResolver
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
GRADE_PATTERN = re.compile(
    r"(?:\b(?:PSA|BGS|SGC|CGC|RFE|BEM|CBA|GMA|BGN|GBTC)\s*(?:10|9(?:\.5)?|8(?:\.5)?)\b|评级)",
    re.IGNORECASE,
)
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
    "topps chrome": ("Topps Chrome", "Topps Chrome Basketball", "TC"),
    "topps chrome mcdonald's all american": (
        "Topps Chrome",
        "McDonald's All American",
        "麦当劳",
        "全美",
    ),
    "panini signature series": ("Signature Series", "签字系列"),
    "signature series": ("Signature Series", "签字系列"),
    "court kings": ("油画", "Court Kings"),
    "immaculate collection": ("Immaculate Collection", "Immaculate", "爱咪咪"),
    "mosaic": ("Mosaic 马赛克", "Mosaic", "马赛克"),
    "fleer showcase": ("Fleer Showcase", "Flair Showcase", "Showcase"),
    "flair showcase": ("Fleer Showcase", "Flair Showcase", "Showcase"),
    "showcase": ("Fleer Showcase", "Flair Showcase", "Showcase"),
    # The paper flagship is catalogued as "Bowman Rookies and Stars", while
    # Chinese-market sellers overwhelmingly shorten it to just "Bowman" and
    # use the release year rather than the full NBA season.  Keep the short
    # form first so CardHobby's token-AND search can discover those sales.
    "bowman rookies and stars": ("Bowman", "Bowman Rookies and Stars"),
    "bowman rookies & stars": ("Bowman", "Bowman Rookies and Stars"),
    # CardHobby sellers commonly call cards from this silver-foil 90-card set
    # "银标" and often shorten the printed 2001-02 season to 2001.
    # Put the narrow Chinese nickname first: broad MJ's Back queries can span
    # hundreds of rows and exhaust CardHobby's request budget before reaching
    # the locally common title form.
    "mj's back": ("银标", "MJ's Back", "MJs Back"),
}
PRODUCT_MATCH_ALIASES = {
    # A lone “马赛克” often describes a refractor texture in another product.
    # Discovery may use it, but deterministic identity matching may not.
    "mosaic": ("Panini Mosaic", "Mosaic Basketball", "Mosaic 马赛克"),
    "mosaic basketball": ("Panini Mosaic", "Mosaic Basketball", "Mosaic 马赛克"),
    "topps chrome basketball": ("Topps Chrome", "Topps Chrome Basketball"),
    "panini signature series basketball": (
        "Panini Signature Series",
        "Signature Series Basketball",
        "Signature Series",
        "签字系列",
    ),
    "panini prizm basketball": ("Panini Prizm", "Prizm Basketball"),
    "panini donruss basketball": ("Panini Donruss", "Donruss Basketball"),
    # Keep the Chinese nickname discovery-only: "银标" also describes grading
    # labels and unrelated parallels, so deterministic matching still requires
    # the independently printed product name.
    "mj's back": ("MJ's Back", "MJs Back"),
    "bowman rookies and stars": ("Bowman", "Bowman Rookies and Stars"),
    "bowman rookies & stars": ("Bowman", "Bowman Rookies and Stars"),
}
RELEASE_YEAR_SEARCH_PRODUCTS = {
    "mj s back",
    "bowman rookies and stars",
}
PLAYER_SEARCH_ALIASES = {
    "jaren jackson jr": ("小贾伦 杰克逊", "贾伦 杰克逊", "JJJ"),
    "lebron james": ("勒布朗 詹姆斯", "詹姆斯"),
    "stephen curry": ("斯蒂芬 库里", "库里"),
    "scottie barnes": ("斯科蒂 巴恩斯", "巴恩斯"),
    "brandon miller": ("布兰登 米勒",),
    "luka dončić": ("卢卡 东契奇", "东契奇"),
    "luka doncic": ("卢卡 东契奇", "东契奇"),
    "kevin garnett": ("凯文 加内特", "加内特"),
    "matas buzelis": ("马塔斯 布泽利斯", "布泽利斯"),
    "tristan da silva": ("特里斯坦 达席尔瓦", "达席尔瓦"),
    "matas buzelis tristan da silva": (
        "Matas Buzelis",
        "Tristan da Silva",
        "马塔斯 布泽利斯 特里斯坦 达席尔瓦",
        "布泽利斯 达席尔瓦",
    ),
    "victor wembanyama": ("维克托 文班亚马", "文班亚马"),
    "scoot henderson": ("斯库特 亨德森", "亨德森"),
    "zach lavine": ("扎克 拉文", "拉文"),
    "jalen duren": ("杰伦 杜伦", "杜伦"),
    "brandon ingram": ("布兰登 英格拉姆", "英格拉姆"),
    "shaedon sharpe": ("谢登 夏普", "夏普"),
    "paolo banchero": ("保罗 班凯罗", "班凯罗"),
    "shareef abdur rahim": ("谢里夫 阿卜杜 拉希姆", "拉希姆"),
    "grant hill": ("格兰特 希尔",),
    "toni kukoc": ("托尼 库科奇", "托尼 库克奇", "库科奇"),
    "jerome williams": ("杰罗姆 威廉姆斯",),
    "tim thomas": ("蒂姆 托马斯",),
    "tony delk": ("托尼 德尔克",),
    "john stockton": ("约翰 斯托克顿", "斯托克顿"),
    "glenn robinson": ("格伦 罗宾逊", "大狗"),
}


def product_search_aliases(value: object) -> tuple[str, ...]:
    """Resolve catalog labels such as `Mosaic Basketball` to seller vocabulary."""
    product = str(value or "").strip()
    normalized = product.casefold()
    exact = PRODUCT_SEARCH_ALIASES.get(normalized)
    if exact:
        return exact
    for family, aliases in PRODUCT_SEARCH_ALIASES.items():
        if family in normalized:
            return aliases
    return (product,) if product else ()
PARALLEL_SEARCH_ALIASES = {
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
    "red lava refractor": "红熔岩折",
}
PARALLEL_MATCH_ALIASES = {
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
}
INSERT_MATCH_ALIASES = {
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
}

INSERT_SEARCH_ALIASES = {
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
    "jumbo jersey autographs": (
        "Jumbo Jersey Autographs",
        "大窗球衣签字",
        "JA 球衣签字",
    ),
}


class CardHobbySale(BaseModel):
    item_id: str | None = None
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


class CardHobbySearchResult(BaseModel):
    search_queries: list[str] = Field(default_factory=list)
    sales: list[CardHobbySale] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CardHobbyReferenceImages(BaseModel):
    title: str
    source_url: str
    front_url: str
    back_url: str | None = None
    match_confidence: Decimal = Field(ge=0, le=1)


def extract_cardhobby_images(html: str) -> list[str]:
    return list(dict.fromkeys(CARDHOBBY_IMAGE_PATTERN.findall(html)))


def is_exact_cardhobby_reference(identity: dict, sale: CardHobbySale) -> bool:
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
    score, conflicts = assess_cardhobby_match(identity, sale)
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
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(value or "").casefold()).strip()


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


def _matches_product(title: str, product_name: object) -> bool:
    normalized_product = _normalized(product_name)
    normalized_title = _normalized(title)
    if "topps chrome" in normalized_product and any(
        token in normalized_product
        for token in ("mcdonald", "all american", "麦当劳", "全美")
    ):
        return "topps chrome" in normalized_title and any(
            token in normalized_title
            for token in ("mcdonald", "all american", "麦当劳", "全美")
        )
    if normalized_product == "bowman rookies and stars":
        # Plain "Bowman" is the common seller name for the paper flagship, but
        # the same token also prefixes several genuinely different products.
        # Discover broadly while retaining deterministic product boundaries.
        conflicting_families = (
            "bowman s best",
            "bowman best",
            "bowman chrome",
            "bowman draft",
            "bowman signature",
            "bowman sterling",
        )
        return "bowman" in normalized_title and not any(
            family in normalized_title for family in conflicting_families
        )
    aliases = PRODUCT_MATCH_ALIASES.get(
        str(product_name or "").strip().casefold(),
        product_search_aliases(product_name),
    )
    return any(_contains_phrase(title, alias) for alias in aliases)


def _contains_card_number(title: str, value: object) -> bool:
    target = _normalized(value)
    if not target:
        return False
    return bool(
        re.search(
            rf"(?<![0-9a-z]){re.escape(target)}(?![0-9a-z])",
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
        PLAYER_SEARCH_ALIASES.get(_normalized(canonical), ())
        if field == "player_name"
        else ()
    )
    return tuple(
        dict.fromkeys(
            value
            for value in (
                canonical,
                *curated_aliases,
                *(str(value).strip() for value in raw_aliases),
            )
            if value
        )
    )


def _identity_is_multi_player(identity: dict) -> bool:
    player = str(identity.get("player_name") or "")
    return bool(re.search(r",|\s+&\s+|\s+and\s+|\s+/\s+", player, re.I))


def assess_cardhobby_match(identity: dict, sale: CardHobbySale) -> tuple[Decimal, list[str]]:
    """Apply deterministic safety gates after model-assisted CardHobby discovery."""
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
        sale.evidence_level == "ended_auction"
        and item_id is not None
        and (sale.bid_count or 0) > 0
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

    title_is_autograph = bool(AUTOGRAPH_PATTERN.search(title))
    if bool(identity.get("is_autograph")) != title_is_autograph:
        conflicts.append("签字属性与目标卡不一致")
        return Decimal("0"), conflicts
    title_is_patch = is_memorabilia_listing(title)
    if bool(identity.get("is_patch")) != title_is_patch:
        conflicts.append("物料/球衣属性与目标卡不一致")
        return Decimal("0"), conflicts

    target_company = identity.get("grade_company")
    title_is_graded = bool(GRADE_PATTERN.search(title) or sale.grade_company)
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
    player_aliases = _identity_aliases(identity, "player_name")
    if not any(_contains_phrase(title, alias) for alias in player_aliases):
        conflicts.append("标题未命中目标球员")
        return Decimal("0"), conflicts
    score += Decimal("0.35")

    season_start = identity.get("season_start")
    season_end = identity.get("season_end")
    explicit_seasons: set[tuple[int, int]] = set()
    for start_text, end_text in re.findall(
        r"(?<!\d)((?:19|20)\d{2})\s*[-–/]\s*((?:(?:19|20)\d{2})|\d{2})(?!\d)",
        title,
    ):
        start_value = int(start_text)
        end_value = int(end_text)
        if len(end_text) == 2:
            end_value = (start_value // 100) * 100 + end_value
        explicit_seasons.add((start_value, end_value))
    if (
        explicit_seasons
        and season_start
        and season_end
        and (int(season_start), int(season_end)) not in explicit_seasons
    ):
        conflicts.append("产品赛季与目标卡不一致或无法确认")
        return Decimal("0"), conflicts
    season_tokens = {str(season_start)} if season_start else set()
    if season_start and season_end and season_end != season_start:
        season_tokens.update(
            {
                str(season_end),
                f"{season_start}-{str(season_end)[-2:]}",
                f"{season_start}-{season_end}",
            }
        )
    season_matches = bool(season_tokens and any(token in title for token in season_tokens))
    if season_matches:
        score += Decimal("0.15")

    if _contains_phrase(title, identity.get("brand_name")):
        score += Decimal("0.10")
    product_name = str(identity.get("product_name") or "").strip()
    product_matches = _matches_product(title, product_name)
    if product_name and not product_matches:
        conflicts.append("标题未命中目标产品系列")
        return Decimal("0"), conflicts
    if product_matches:
        # Player + product alone is intentionally below the acceptance floor.
        # A season, card/insert, parallel or print-run anchor must establish
        # that a long seller title is about this card rather than a lot/list.
        score += Decimal("0.10")
    card_number_matches = _contains_card_number(title, identity.get("card_number"))
    target_card_number = str(identity.get("card_number") or "").strip()
    prefixed_number = re.fullmatch(r"([A-Za-z]+)[#\s-]*(\d+)", target_card_number)
    if prefixed_number:
        prefix, target_number = prefixed_number.groups()
        explicit_same_family = set(
            re.findall(rf"#?{re.escape(prefix)}[\s-]*(\d+)", title, re.IGNORECASE)
        )
        if explicit_same_family and target_number not in explicit_same_family:
            conflicts.append("标题明确卡号与目标卡号不一致")
            return Decimal("0"), conflicts
    if target_card_number.isdigit():
        explicit_hash_numbers = set(re.findall(r"#\s*(\d+)", title))
        if explicit_hash_numbers and target_card_number not in explicit_hash_numbers:
            conflicts.append("标题明确卡号与目标卡号不一致")
            return Decimal("0"), conflicts
    target_insert = str(identity.get("insert_name") or "").casefold()
    insert_aliases = INSERT_MATCH_ALIASES.get(
        target_insert,
        (str(identity.get("insert_name") or ""),),
    )
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
    # Numeric card numbers are especially unsafe in long marketplace titles:
    # seller inventory suffixes such as “(1)” commonly create false matches.
    # An insert target therefore always needs its canonical name or a reviewed
    # seller alias; a card-number token alone cannot establish the card kind.
    if identity.get("insert_name") and not insert_matches:
        conflicts.append("标题未命中目标卡号或卡种")
        return Decimal("0"), conflicts
    if (
        identity.get("card_number")
        and not identity.get("insert_name")
        and not identity.get("parallel_name")
        and not (identity.get("serial_total") or identity.get("print_run"))
        and not card_number_matches
        and NON_BASE_VARIANT_PATTERN.search(title)
    ):
        conflicts.append("目标为基础卡，但标题显示为其他特卡、产品变体、平行或限编")
        return Decimal("0"), conflicts
    if (
        identity.get("card_number")
        and not identity.get("insert_name")
        and not card_number_matches
        and not (identity.get("serial_total") or identity.get("print_run"))
    ):
        conflicts.append("标题未命中目标卡号或卡种")
        return Decimal("0"), conflicts
    if card_number_matches:
        score += Decimal("0.08")
    if insert_matches:
        score += Decimal("0.07")

    target_parallel = str(identity.get("parallel_name") or "").casefold()
    strict_parallel_aliases = PARALLEL_MATCH_ALIASES.get(target_parallel)
    parallel_aliases = strict_parallel_aliases or (
        str(identity.get("parallel_name") or ""),
    )
    parallel_matches = not target_parallel or any(
        _contains_phrase(title, alias) for alias in parallel_aliases
    )
    if (
        strict_parallel_aliases
        and not parallel_matches
        and not (identity.get("serial_total") or identity.get("print_run"))
    ):
        conflicts.append("标题未命中目标平行版本")
        return Decimal("0"), conflicts
    if target_parallel and parallel_matches:
        score += Decimal("0.07")

    print_run = identity.get("serial_total") or identity.get("print_run")
    explicit_print_runs = {
        int(value)
        for groups in re.findall(
            r"(?:/\s*(\d+)(?!\d)|(?<!\d)(\d+)\s*[编遍])",
            title,
        )
        for value in groups
        if value
    }
    if print_run and explicit_print_runs and int(print_run) not in explicit_print_runs:
        conflicts.append("明确限编与目标卡不一致")
        return Decimal("0"), conflicts
    print_run_matches = bool(print_run and re.search(
        rf"(?:/\s*{int(print_run)}(?!\d)|(?<!\d){int(print_run)}\s*[编遍])",
        title,
        re.IGNORECASE,
    ))
    if print_run_matches:
        score += Decimal("0.08")
    if print_run and not print_run_matches:
        conflicts.append("目标为限编卡，但标题未命中目标总限编")
        return Decimal("0"), conflicts
    if identity.get("is_autograph") and title_is_autograph:
        score += Decimal("0.05")
    if identity.get("is_patch") and title_is_patch:
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
        conflicts.append("标题只命中球员和产品，缺少赛季、卡号、版本或限编锚点")
        return Decimal("0"), conflicts

    sale.source_reliability = source_reliability
    return min(score, Decimal("1")), conflicts


ANALOG_SOFT_CONFLICTS = (
    "产品赛季与目标卡不一致或无法确认",
    "标题未命中目标卡号或卡种",
    "标题未命中目标平行版本",
    "明确限编与目标卡不一致",
    "目标为限编卡，但标题未命中目标总限编",
    "标题只命中球员和产品",
    "目标为基础卡，但标题显示为其他特卡",
)


def assess_cardhobby_candidate(
    identity: dict, sale: CardHobbySale
) -> tuple[Decimal, list[str], str | None]:
    """Classify a safe sale as exact-ish evidence or a bounded product analog.

    The strict matcher remains the authority for T0-like evidence.  A failed
    strict match may enter the downstream comparable classifier only when the
    failure is a value-bearing card variant difference, never a source,
    player, product, lot, autograph, memorabilia or grade conflict.
    """
    score, conflicts = assess_cardhobby_match(identity, sale)
    if score >= Decimal("0.5"):
        return score, conflicts, "exact_or_near_exact"
    if not conflicts or not all(
        any(conflict.startswith(prefix) for prefix in ANALOG_SOFT_CONFLICTS)
        for conflict in conflicts
    ):
        return Decimal("0"), conflicts, None
    title = sale.title
    if not any(
        _contains_phrase(title, alias)
        for alias in _identity_aliases(identity, "player_name")
    ):
        return Decimal("0"), conflicts, None
    product_name = str(identity.get("product_name") or "").strip()
    if product_name and not _matches_product(title, product_name):
        return Decimal("0"), conflicts, None
    explicit_seasons = [
        int(start)
        for start, _end in re.findall(
            r"(?<!\d)((?:19|20)\d{2})\s*[-–/]\s*((?:(?:19|20)\d{2})|\d{2})(?!\d)",
            title,
        )
    ]
    target_season = identity.get("season_start")
    if explicit_seasons and target_season and min(
        abs(value - int(target_season)) for value in explicit_seasons
    ) > 2:
        return Decimal("0"), [*conflicts, "类比赛季与目标相距超过两年"], None
    sale.matching_fields = list(
        dict.fromkeys([*sale.matching_fields, "analog_same_player_product"])
    )
    return Decimal("0.40"), conflicts, "same_player_product_analog"


class CardHobbyProvider:
    """Fetch CardHobby sold items; model search is discovery-only fallback."""

    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        timeout: float = 15.0,
        resolver: KnowledgeResolver | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.timeout = timeout
        self.resolver = resolver

    async def search(self, identity: dict) -> CardHobbySearchResult:
        try:
            direct = await self._search_direct(identity)
        except (httpx.HTTPError, ValueError, TypeError) as error:
            direct = CardHobbySearchResult(notes=[f"卡淘公开接口调用失败：{str(error)[:180]}"])
        if direct.sales:
            return direct
        fallback = await self._search_model(identity)
        return fallback.model_copy(update={
            "sales": [],
            "notes": [
                *direct.notes,
                *fallback.notes,
                (
                    f"模型检索发现 {len(fallback.sales)} 条卡淘候选，但公开接口未能独立确认"
                    " Status=-2 与真实出价；候选仅作发现线索，未进入估值。"
                ),
            ],
        })

    async def reference_images(self, identity: dict) -> CardHobbyReferenceImages | None:
        direct = await self._search_direct(identity)
        exact = [sale for sale in direct.sales if is_exact_cardhobby_reference(identity, sale)]
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
        query_identity = {
            **identity,
            "player_name_aliases": list(_identity_aliases(identity, "player_name"))[1:],
        }
        provider_queries = self.build_queries(query_identity)
        if self.resolver and self.resolver.entities:
            knowledge_queries = [
                stage.query for stage in self.resolver.build_query_plan(query_identity)
            ]
            # Generic knowledge aliases and provider-specific seller vocabulary
            # are complementary. Never let the generic plan replace CardHobby
            # aliases such as Chinese product nicknames or source-specific year
            # forms. Keep the remote-query budget bounded and deterministic.
            queries = list(dict.fromkeys([*provider_queries, *knowledge_queries]))[:12]
        else:
            queries = provider_queries
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.cardhobby.com.cn/market/search?ckey=0&keyword=",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/139.0 Safari/537.36"
            ),
        }
        found: dict[str, CardHobbySale] = {}
        failed_queries: list[str] = []
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, headers=headers
        ) as client:
            for query in queries:
                for page_index in (1, 2):
                    try:
                        response = await client.get(
                            CARDHOBBY_SEARCH_URL,
                            params=self.search_params(query, page_index),
                        )
                        response.raise_for_status()
                        payload = response.json()
                    except (httpx.HTTPError, ValueError, TypeError) as error:
                        failed_queries.append(f"{query}: {str(error)[:120]}")
                        break
                    rows = ((payload.get("data") or {}).get("PagedMarketItemList") or [])
                    for row in rows:
                        sale = self.parse_market_item(row)
                        if sale is None:
                            continue
                        score, conflicts, tier = assess_cardhobby_candidate(identity, sale)
                        if score < Decimal("0.35"):
                            continue
                        found[sale.item_id or sale.source_url] = sale.model_copy(
                            update={
                                "match_confidence": score,
                                "conflicts": conflicts,
                                "matching_fields": list(
                                    dict.fromkeys(
                                        [*sale.matching_fields, f"cardhobby_tier:{tier}"]
                                    )
                                ),
                            }
                        )
                    if len(rows) < 60:
                        break
        return CardHobbySearchResult(
            search_queries=[f"卡淘已售接口：{query}" for query in queries],
            # The image verifier has a bounded candidate budget. Prefer an
            # otherwise exact base-card candidate that merely omits its card
            # number over a newer title that explicitly calls itself a
            # different insert/parallel. Recency remains the tie-breaker.
            sales=sorted(
                found.values(),
                key=lambda sale: (
                    not sale.conflicts,
                    bool(sale.conflicts)
                    and all(
                        conflict.startswith("标题未命中目标卡号或卡种")
                        for conflict in sale.conflicts
                    ),
                    sale.match_confidence,
                    sale.occurred_at,
                ),
                reverse=True,
            )[:20],
            notes=[
                "数据来自卡淘公开 SearchCommodity 接口，Status=-2；有出价的结束拍卖"
                "作为卡淘结束价参考，未宣称已付款。",
                *(
                    [f"部分查询失败但已保留其他查询结果：{'；'.join(failed_queries[:3])}"]
                    if failed_queries
                    else []
                ),
            ],
        )

    @staticmethod
    def build_queries(identity: dict) -> list[str]:
        players = _identity_aliases(identity, "player_name")
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
        parallel = PARALLEL_SEARCH_ALIASES.get(
            str(identity.get("parallel_name") or "").casefold(),
            "",
        )
        # The central marketplace catalog is the source of truth for reviewed
        # seller vocabulary.  Keep the legacy map as a temporary fallback for
        # entries that have not yet been migrated into the reviewable catalog.
        insert_search_aliases = seller_aliases("insert", insert) or INSERT_SEARCH_ALIASES.get(
            insert.casefold(), (insert,) if insert else ()
        )

        def query(*parts: object) -> str:
            return " ".join(
                str(part).strip()
                for part in parts
                if part is not None and str(part).strip()
            )

        queries: list[str] = []
        for alias_index, alias in enumerate(product_aliases):
            release_year_query = (
                query(player, season_start, alias)
                if season_start and season_end and season_start != season_end
                else ""
            )
            if release_year_query and _normalized(product) in RELEASE_YEAR_SEARCH_PRODUCTS:
                queries.append(release_year_query)
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
                        query(player, alias, insert_alias, parallel),
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
                    query(player, season, alias),
                    query(player, alias, parallel, serial),
                    query(player, alias, serial),
                    query(player, alias),
                ]
            )
            if (
                release_year_query
                and _normalized(product) not in RELEASE_YEAR_SEARCH_PRODUCTS
                and alias_index == 0
            ):
                # Sellers often print only the copyright/release year ("2001")
                # while the catalog identity uses an NBA season ("2001-02").
                # Keep exact serial/insert queries first, then add this bounded
                # product-level recall query for every cross-year identity.
                queries.append(release_year_query)
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
        alias_queries = [
            query(player_alias, product_aliases[0])
            for player_alias in players[1:5]
            if product_aliases
        ]
        if alias_queries:
            # Preserve exact-first ordering while reserving bounded stages for
            # audited seller-language aliases and each name on multi-player cards.
            unique = [*unique[: 12 - len(alias_queries)], *alias_queries, *unique[10:]]
        bounded = list(dict.fromkeys(unique))[:12]
        if player and player not in bounded and not alias_queries:
            bounded = [*bounded[:11], player]
        return bounded

    @staticmethod
    def search_params(search_key: str, page_index: int) -> dict[str, str | int]:
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
        if int(row.get("Status") or 0) != -2:
            return None
        bid_count = int(row.get("OutCount") or row.get("PriceCount") or 0)
        if bid_count <= 0:
            return None
        item_id = str(row.get("ID") or "").strip()
        title = str(row.get("Title") or "").strip()
        occurred_raw = str(row.get("EffectiveDate") or "").strip()
        if not item_id or not title or not occurred_raw:
            return None
        occurred_at = datetime.strptime(occurred_raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
        )
        sale_price = Decimal(str(row.get("LowestPrice") or row.get("Price") or "0"))
        if sale_price <= 0:
            return None
        postage_match = re.search(r"\d+(?:\.\d+)?", str(row.get("NewPostageMoney") or ""))
        shipping_price = Decimal(postage_match.group()) if postage_match else Decimal("0")
        grade_match = GRADE_PATTERN.search(title)
        grade_company = None
        grade_value = None
        if grade_match:
            detail = re.search(
                r"\b(PSA|BGS|SGC|CGC|RFE|BEM|CBA|GMA)\s*(10|9(?:\.5)?|8(?:\.5)?)\b",
                title,
                re.IGNORECASE,
            )
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
        response = await self.client.chat.completions.create(
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
            score, conflicts, tier = assess_cardhobby_candidate(identity, sale)
            if score < Decimal("0.35"):
                continue
            accepted.append(
                sale.model_copy(
                    update={
                        "match_confidence": score,
                        "conflicts": conflicts,
                        "matching_fields": list(
                            dict.fromkeys(
                                [*sale.matching_fields, f"cardhobby_tier:{tier}"]
                            )
                        ),
                    }
                )
            )
        return parsed.model_copy(update={"sales": accepted[:20]})
