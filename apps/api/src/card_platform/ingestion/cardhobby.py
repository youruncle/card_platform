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


CARDHOBBY_DOMAIN = "cardhobby.com.cn"
CARDHOBBY_SEARCH_URL = "https://www.cardhobby.com.cn/NewCommodity/SearchCommodity"
CARDHOBBY_SINGLE_CARD_CATEGORY_ID = "08d83279-bba1-4ffe-88c6-b13c22576d96"
CARDHOBBY_ITEM_PATH = re.compile(r"^/market/item/(?P<item_id>\d+)/?$", re.IGNORECASE)
CARDHOBBY_FEEDBACK_PATH = re.compile(
    r"^/market/feedback(?:/(?P<account_id>\d+))?/?$", re.IGNORECASE
)
LOT_PATTERN = re.compile(
    r"(?:\blot\b|打包|多卡|套卡|整套|未拆|卡盒|盒卡|卡包|箱卡|\bpack\b|\bbox\b)",
    re.IGNORECASE,
)
GRADE_PATTERN = re.compile(
    r"(?:\b(?:PSA|BGS|SGC|CGC|RFE|BEM|CBA|GMA)\s*(?:10|9(?:\.5)?|8(?:\.5)?)\b|评级)",
    re.IGNORECASE,
)
AUTOGRAPH_PATTERN = re.compile(r"签字|卡签|贴签|签名|\bauto(?:graph)?\b|\brpa\b", re.I)
PATCH_PATTERN = re.compile(r"球衣|物料|切割|patch|jersey|relic|\brpa\b", re.I)
MULTI_PLAYER_PATTERN = re.compile(
    r"双人|三人|四人|多人|双签|三签|四签|\bdual\b|\btriple\b|\bquad\b", re.I
)


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


class CardHobbySearchResult(BaseModel):
    search_queries: list[str] = Field(default_factory=list)
    sales: list[CardHobbySale] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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
        score_cap = Decimal("0.95")
    elif (
        sale.evidence_level == "ended_auction"
        and item_id is not None
        and (sale.bid_count or 0) > 0
    ):
        score_cap = Decimal("0.70")
    else:
        conflicts.append("未证明为已确认交易或有出价的结束拍卖")
        return Decimal("0"), conflicts
    if LOT_PATTERN.search(title):
        conflicts.append("标题疑似多卡、套卡或未拆产品")
        return Decimal("0"), conflicts
    if MULTI_PLAYER_PATTERN.search(title):
        conflicts.append("标题疑似多人卡，不能作为单球员同款成交")
        return Decimal("0"), conflicts

    title_is_autograph = bool(AUTOGRAPH_PATTERN.search(title))
    if bool(identity.get("is_autograph")) != title_is_autograph:
        conflicts.append("签字属性与目标卡不一致")
        return Decimal("0"), conflicts
    title_is_patch = bool(PATCH_PATTERN.search(title))
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
    player_name = identity.get("player_name")
    if not _contains_phrase(title, player_name):
        conflicts.append("标题未命中目标球员")
        return Decimal("0"), conflicts
    score += Decimal("0.35")

    season_start = identity.get("season_start")
    season_end = identity.get("season_end")
    season_tokens = {str(season_start)} if season_start else set()
    if season_start and season_end and season_end != season_start:
        season_tokens.update(
            {
                f"{season_start}-{str(season_end)[-2:]}",
                f"{season_start}-{season_end}",
            }
        )
    if season_tokens and not any(token in title for token in season_tokens):
        conflicts.append("产品赛季与目标卡不一致或无法确认")
        return Decimal("0"), conflicts
    if season_tokens:
        score += Decimal("0.15")

    if _contains_phrase(title, identity.get("brand_name")):
        score += Decimal("0.10")
    if _contains_phrase(title, identity.get("product_name")):
        score += Decimal("0.15")
    if _contains_phrase(title, identity.get("card_number")):
        score += Decimal("0.08")
    if _contains_phrase(title, identity.get("insert_name")):
        score += Decimal("0.07")
    if _contains_phrase(title, identity.get("parallel_name")):
        score += Decimal("0.07")

    print_run = identity.get("serial_total") or identity.get("print_run")
    explicit_print_runs = {
        int(value)
        for groups in re.findall(r"(?:/\s*(\d+)\b|\b(\d+)\s*编)", title)
        for value in groups
        if value
    }
    if print_run and explicit_print_runs and int(print_run) not in explicit_print_runs:
        conflicts.append("明确限编与目标卡不一致")
        return Decimal("0"), conflicts
    if print_run and re.search(
        rf"(?:/\s*{int(print_run)}\b|\b{int(print_run)}\s*编)", title, re.IGNORECASE
    ):
        score += Decimal("0.08")
    if identity.get("is_autograph") and title_is_autograph:
        score += Decimal("0.05")
    if identity.get("is_patch") and title_is_patch:
        score += Decimal("0.05")

    return min(score, sale.match_confidence, score_cap), conflicts


class CardHobbyProvider:
    """Fetches CardHobby's public sold-item search, with model search as fallback."""

    def __init__(self, client: AsyncOpenAI, model: str, timeout: float = 15.0) -> None:
        self.client = client
        self.model = model
        self.timeout = timeout

    async def search(self, identity: dict) -> CardHobbySearchResult:
        try:
            direct = await self._search_direct(identity)
        except (httpx.HTTPError, ValueError, TypeError) as error:
            direct = CardHobbySearchResult(notes=[f"卡淘公开接口调用失败：{str(error)[:180]}"])
        if direct.sales:
            return direct
        fallback = await self._search_model(identity)
        return fallback.model_copy(update={"notes": [*direct.notes, *fallback.notes]})

    async def _search_direct(self, identity: dict) -> CardHobbySearchResult:
        queries = self.build_queries(identity)
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
        async with httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=True, headers=headers
        ) as client:
            for query in queries:
                for page_index in (1, 2):
                    response = await client.get(
                        CARDHOBBY_SEARCH_URL,
                        params=self.search_params(query, page_index),
                    )
                    response.raise_for_status()
                    payload = response.json()
                    rows = ((payload.get("data") or {}).get("PagedMarketItemList") or [])
                    for row in rows:
                        sale = self.parse_market_item(row)
                        if sale is None:
                            continue
                        score, conflicts = assess_cardhobby_match(identity, sale)
                        if score < Decimal("0.5"):
                            continue
                        found[sale.item_id or sale.source_url] = sale.model_copy(
                            update={"match_confidence": score, "conflicts": conflicts}
                        )
                    if len(rows) < 60:
                        break
                if len(found) >= 12:
                    break
        return CardHobbySearchResult(
            search_queries=[f"卡淘已售接口：{query}" for query in queries],
            sales=sorted(found.values(), key=lambda sale: sale.occurred_at, reverse=True)[:20],
            notes=[
                "数据来自卡淘公开 SearchCommodity 接口，Status=-2；有出价的结束拍卖"
                "作为卡淘结束价参考，未宣称已付款。"
            ],
        )

    @staticmethod
    def build_queries(identity: dict) -> list[str]:
        player = str(identity.get("player_name") or "").strip()
        product = str(identity.get("product_name") or "").strip()
        specific = " ".join(part for part in (player, product) if part)
        queries = [specific, player]
        return list(dict.fromkeys(query for query in queries if query))

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
            source_url=f"https://www.cardhobby.com.cn/market/item/{item_id}",
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
            score, conflicts = assess_cardhobby_match(identity, sale)
            if score < Decimal("0.5"):
                continue
            accepted.append(
                sale.model_copy(
                    update={"match_confidence": score, "conflicts": conflicts}
                )
            )
        return parsed.model_copy(update={"sales": accepted[:20]})
