"""CardHobby order evidence and conservative purchase matching. Never handles credentials."""

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from card_platform.domain.card_identity import (
    CardIdentityResolver,
    INSERT_ALIASES,
    canonical_insert,
    normalize_text,
)
from card_platform.purchase_cost_matching import (
    parallel_matches_title,
    parallel_title_has_conflicting_specificity,
    has_ambiguous_finish_marker,
    has_material_parallel_marker,
    has_named_card_variant_marker,
    is_explicit_base_listing,
)
from card_platform.domain.listing_title import is_memorabilia_listing
from card_platform.domain.product_identity import (
    LOW_INFORMATION_PRODUCT_LABELS,
    PRODUCT_CHILD_FAMILIES,
    PRODUCT_PARENT_SHORTHANDS,
    ProductMatchStatus,
    assess_product_match,
    product_label_variants,
)
from card_platform.knowledge import KnowledgeResolver
from card_platform.domain.sketch_identity import SKETCH_ALIASES, is_sketch_card, without_artist_signature
from card_platform.marketplace_aliases import requires_visual_confirmation, seller_aliases

Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OrderLine(StrictModel):
    item_id: str = Field(pattern=r"^\d{1,40}$")
    title: str = Field(min_length=1, max_length=2000)
    item_url: str = Field(max_length=300)
    image_urls: list[str] = Field(default_factory=list, max_length=10)
    unit_price: Money
    quantity: int = Field(ge=1, le=500)

    @field_validator("image_urls")
    @classmethod
    def images(cls, values):
        for value in values:
            p = urlsplit(value)
            if (
                p.scheme != "https"
                or p.hostname != "images.cardhobby.com"
                or p.username
                or p.query
                or p.fragment
            ):
                raise ValueError(
                    "Only public CardHobby image URLs without query strings are allowed"
                )
        return values

    @model_validator(mode="after")
    def item_link(self):
        # CardHobby's public 商品编号 differs from the internal /market/item/ ID.
        if not re.fullmatch(
            r"https://(?:www\.)?cardhobby\.com\.cn/market/item/\d{1,40}/?",
            self.item_url,
            re.I,
        ):
            raise ValueError("无效的卡淘商品链接")
        return self


class OrderCapture(StrictModel):
    parser_version: Literal["cardhobby-orders-v1"] = "cardhobby-orders-v1"
    external_order_id: str = Field(pattern=r"^\d{1,40}$")
    ordered_at: date | None
    seller: str = Field(min_length=1, max_length=200)
    status: str = Field(min_length=1, max_length=100)
    source_url: str = Field(max_length=500)
    fetched_at: datetime
    currency: Literal["CNY"] = "CNY"
    shipping: Money | None = None
    displayed_total: Money | None = None
    paid_total: Money | None = None
    discount: Money | None = None
    refund: Money | None = None
    extra_fee: Money | None = None
    complete: bool = False
    parser_errors: list[str] = Field(default_factory=list, max_length=30)
    lines: list[OrderLine] = Field(min_length=1, max_length=500)

    @field_validator("source_url")
    @classmethod
    def source(cls, value):
        # Canonical evidence links only; no arbitrary query parameters or credentials.
        if not re.fullmatch(
            r"https://(?:www\.)?cardhobby\.com\.cn/[Oo]rder/(?:[Mm]y[Oo]rders(?:\?page=\d+)?|[Dd]etail/\d+)/?",
            value,
        ):
            raise ValueError("无效的订单来源链接")
        return value

    @field_validator("fetched_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value > datetime.now(timezone.utc):
            raise ValueError("抓取时间必须含时区且不晚于当前时间")
        return value

    @model_validator(mode="after")
    def unique_lines(self):
        if len({line.item_id for line in self.lines}) != len(self.lines):
            raise ValueError("订单中有重复商品编号；请检查解析边界")
        if sum(line.quantity for line in self.lines) > 2000:
            raise ValueError("订单件数超过安全上限")
        return self


def order_errors(order: OrderCapture) -> list[str]:
    errors = list(order.parser_errors)
    if order.ordered_at is None:
        errors.append("订单日期缺失")
    if not order.complete:
        errors.append("订单行未完整读取")
    if order.status not in {
        "已完成",
        "完成",
        "等待卖家发货",
        "待发货",
        "待收货",
        "待评价",
        "等待买家收货",
    }:
        errors.append("订单未付款、已关闭、退款中或状态不明确")
    if order.shipping is None:
        errors.append("运费未确认")
    if order.displayed_total is None and order.paid_total is None:
        errors.append("缺少订单总额，无法校验商品完整性")
    if any(value for value in (order.discount, order.refund, order.extra_fee)):
        errors.append("存在优惠、退款或额外费用，需人工核算；未分配差额")
    gross = sum((line.unit_price * line.quantity for line in order.lines), Decimal(0))
    if order.shipping is not None:
        expected = gross + order.shipping
        for label, value in [("订单总额", order.displayed_total), ("实付总额", order.paid_total)]:
            if value is not None and value != expected:
                errors.append(f"{label}与卡价加运费不一致，需人工核算")
    return list(dict.fromkeys(errors))


def allocate_shipping(order: OrderCapture) -> list[dict]:
    """Integer cents; remainder cents go to item-id then unit-index order, never floats."""
    if order.shipping is None:
        return []
    count = sum(line.quantity for line in order.lines)
    cents, remainder = divmod(int(order.shipping * 100), count)
    units = []
    for line in sorted(order.lines, key=lambda x: x.item_id):
        for index in range(line.quantity):
            fee = Decimal(cents + (len(units) < remainder)) / 100
            units.append(
                dict(
                    item_id=line.item_id,
                    unit_index=index,
                    title=line.title,
                    unit_price=line.unit_price,
                    shipping=fee,
                )
            )
    return units


def evidence_hash(order: OrderCapture) -> str:
    value = order.model_dump(mode="json", exclude={"fetched_at"})
    value["lines"] = sorted(value["lines"], key=lambda line: line["item_id"])
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


BUNDLE = re.compile(r"打包|\blot\s*\d*|[2-9]\d*\s*张|一图|组合", re.I)


def _phrase_in_title(value: object, title: str) -> bool:
    """Allow Latin/Han boundaries without matching names inside longer words."""
    phrase = normalize_text(value)
    return bool(phrase) and bool(
        re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", title)
    )


def _without_aliases(title: str, aliases) -> str:
    for alias in sorted({normalize_text(a) for a in aliases if a}, key=len, reverse=True):
        title = re.sub(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", " ", title)
    return title


def _source_item_id(url: str) -> str | None:
    match = re.fullmatch(
        r"https://(?:www\.)?cardhobby\.com\.cn/market/item/(\d{1,40})/?", url, re.I
    )
    return match[1] if match else None


def _same_source_item(card: dict, line: dict) -> bool:
    item_id = _source_item_id(line.get("item_url") or "")
    urls = re.findall(r"https://[^\s<>\"'()，。；）]+", card.get("notes") or "", re.I)
    return bool(item_id) and any(_source_item_id(url) == item_id for url in urls)


def _specific_product_alias(alias: str, product: str | None) -> bool:
    """A published discovery alias cannot erase a checklisted child product."""
    if normalize_text(alias) in LOW_INFORMATION_PRODUCT_LABELS:
        return False
    variants = set(product_label_variants(product))
    return not any(
        any(child in variants for child in children)
        and (
            parent in product_label_variants(alias)
            or normalize_text(alias) in {
                normalize_text(a) for a in PRODUCT_PARENT_SHORTHANDS.get(parent, ())
            }
        )
        for parent, children in PRODUCT_CHILD_FAMILIES.items()
    )


def score_match(card: dict, line: dict, knowledge: KnowledgeResolver | None = None, *, visual_evidence: dict | None = None) -> dict:
    knowledge = knowledge or KnowledgeResolver()
    normalized_card, _ = knowledge.normalize_identity(card)
    identity = CardIdentityResolver().resolve(normalized_card)
    sketch = is_sketch_card(identity.insert)
    title = normalize_text(line["title"])
    raw_title = unicodedata.normalize("NFKC", line["title"]).casefold()
    reasons, conflicts, missing = [], [], []
    visual_same = bool(visual_evidence and visual_evidence.get('verdict') == 'same')
    if visual_evidence and visual_evidence.get('verdict') == 'different':
        conflicts.append('逐图复核确认非同款')
    score = 0
    soft_differences = []
    if BUNDLE.search(title):
        conflicts.append("多卡打包商品须人工拆分，不能当成单卡")
    aliases = knowledge.aliases_for("player", identity.player_name) or (identity.player_name,)
    player_match = any(_phrase_in_title(a, title) for a in aliases)
    if player_match:
        score += 25
        reasons.append("球员姓名或知识库别名一致")
    else:
        missing.append("球员")
    # A player's own nickname/surname must not become another player's name or
    # an insert/parallel (for example Magic Johnson, Jalen Green or Jaylen Brown).
    identity_title = _without_aliases(title, aliases)
    mentioned_players = knowledge.find_in_text("player", identity_title)
    expected_player = knowledge.resolve("player", identity.player_name).canonical_value
    if any(
        mention.canonical_value != expected_player
        and _phrase_in_title(mention.matched_alias, identity_title)
        for mention in mentioned_players
    ):
        conflicts.append("标题包含其他球员，需核对多人卡或打包商品")
    season = re.search(r"(?<!\d)((?:19|20)\d{2})(?:\s*[-/]\s*(\d{2,4}))?(?!\d)", raw_title)
    if season and identity.season_start:
        if not season[2] and int(season[1]) == identity.season_end and identity.season_end == identity.season_start + 1:
            score += 10
            reasons.append('标题使用赛季结束年份')
            soft_differences.append('卖家单年与库存跨年赛季写法不同')
        elif int(season[1]) != identity.season_start:
            conflicts.append("年份冲突")
        else:
            score += 15
            reasons.append("年份一致")
            if season[2] and identity.season_end:
                end = int(season[2])
                end = (int(season[1]) // 100) * 100 + end if len(season[2]) == 2 else end
                if end != identity.season_end:
                    conflicts.append("赛季结束年份冲突")
    else:
        missing.append("年份")
    brands = [b for b in ("panini", "topps", "upper deck", "fleer", "skybox", "leaf") if _phrase_in_title(b, title)]
    brands.extend(normalize_text(m.canonical_value) for m in knowledge.find_in_text("brand", title)
                  if _phrase_in_title(m.matched_alias, title))
    brand = normalize_text(identity.brand_name)
    brand_match = bool(brand) and any(
        _phrase_in_title(a, title) for a in knowledge.aliases_for("brand", identity.brand_name)
    )
    if brand_match:
        score += 10
        reasons.append("品牌一致")
    elif brands and brand:
        conflicts.append("品牌冲突")
    else:
        missing.append("品牌")
    product = assess_product_match(
        title,
        identity.product_name,
        target_set=identity.set_name,
        target_insert=identity.insert,
        target_parallel=identity.parallel,
    )
    if product == ProductMatchStatus.UNKNOWN and any(
        _phrase_in_title(a, title) and _specific_product_alias(a, identity.product_name)
        for a in knowledge.aliases_for("product", identity.product_name or "")
        if knowledge.resolve("product", identity.product_name).entity_id
        and normalize_text(identity.product_name) not in LOW_INFORMATION_PRODUCT_LABELS
    ):
        product = ProductMatchStatus.MATCH
    if product == ProductMatchStatus.EXPLICIT_CONFLICT:
        conflicts.append("产品或子系列冲突")
    elif product == ProductMatchStatus.MATCH:
        score += 15
        reasons.append("产品一致")
    else:
        missing.append("产品")
    insert_aliases = tuple(dict.fromkeys((
        *knowledge.aliases_for("insert", identity.insert or ""),
        *INSERT_ALIASES.get(normalize_text(identity.insert), ()),
        *(SKETCH_ALIASES if sketch else ()),
    )))
    insert_match = bool(identity.insert) and any(_phrase_in_title(a, identity_title) for a in insert_aliases)
    # Remove the proven target name first; overlapping aliases in the shared
    # vocabulary must not turn one long insert name into two different inserts.
    other_insert_title = _without_aliases(identity_title, insert_aliases) if insert_match else identity_title
    other_inserts = {
        normalize_text(canonical_insert(canonical))
        for canonical, names in INSERT_ALIASES.items()
        if any(_phrase_in_title(name, other_insert_title) for name in names)
    }
    other_inserts.update(
        normalize_text(canonical_insert(m.canonical_value))
        for m in knowledge.find_in_text("insert", other_insert_title)
        if _phrase_in_title(m.matched_alias, other_insert_title)
    )
    other_inserts.discard(normalize_text(canonical_insert(identity.insert)))
    if identity.insert and other_inserts:
        conflicts.append("Insert 冲突")
    elif identity.insert:
        if insert_match:
            score += 10
            reasons.append("Insert 一致")
        else:
            missing.append("Insert")
    elif other_inserts or has_named_card_variant_marker(identity_title, identity.product_name):
        missing.append("库存未确认 Insert")
    number = re.search(r"(?:卡号|card\s*(?:no\.?|number)|no\.|(?<![a-z0-9])#)\s*[:：#]?\s*([a-z0-9-]+)", raw_title)
    if number and identity.card_number:
        if normalize_text(identity.card_number) != normalize_text(number[1]):
            conflicts.append("卡号冲突")
        else:
            score += 10
            reasons.append("卡号一致")
    # Checklist numbers strengthen a match and explicit mismatches veto it,
    # but sellers routinely omit them. They are not a mandatory identity field.
    # Remove years before examining serial numbers: 2023/24 is a season, not /24.
    serial_text = re.sub(r"(?<!\d)(?:19|20)\d{2}\s*[-/]\s*\d{2,4}(?!\d)", "", raw_title)
    serial = re.search(r"(?:(\d+)\s*(?:/|of)\s*(\d+)|/\s*(\d+)|(\d+)\s*编(?!年史))", serial_text)
    total = int(next(v for v in serial.groups()[1:] if v)) if serial else None
    if total is not None and identity.serial_total is not None:
        if total != identity.serial_total:
            conflicts.append("限编总数冲突")
        elif (
            serial[1]
            and identity.serial_number is not None
            and int(serial[1]) != identity.serial_number
        ):
            conflicts.append("限编分子冲突")
        else:
            score += 10
            reasons.append("限编一致")
    elif total or identity.serial_total:
        missing.append("限编")
    colors = {
        "red": "红",
        "blue": "蓝",
        "gold": "金",
        "green": "绿",
        "silver": "银",
        "purple": "紫",
        "black": "黑",
        "orange": "橙",
        "pink": "粉",
        "white": "白",
        "yellow": "黄",
        "bronze": "铜",
    }
    variant_title = _without_aliases(identity_title, insert_aliases) if insert_match else identity_title
    explicit_colors = {
        color
        for color, cn in colors.items()
        if _phrase_in_title(color, variant_title) or re.search(
            cn + r"(?:色)?(?:折|平行|波纹|碎冰|冰裂|熔岩|泡泡|镭射|脉冲|迷彩)", variant_title
        )
    }
    parallel = normalize_text(identity.parallel)
    if parallel in {"base", "base card", "基础版", "普卡", "普版"}:
        parallel = ""
    target_colors = {color for color, cn in colors.items() if color in parallel or cn in parallel}
    if target_colors and explicit_colors - target_colors:
        conflicts.append("平行颜色冲突")
    # Use the shared specificity gate: Gold must not absorb Gold Wave, and an
    # unspecified base must not absorb a named finish merely because its player
    # and product were returned by the same search.
    parallel_aliases = knowledge.aliases_for("parallel", identity.parallel or "")
    visual_parallel = requires_visual_confirmation('parallel', identity.parallel)
    if visual_parallel:
        parallel_aliases = tuple(dict.fromkeys((*parallel_aliases, *seller_aliases('parallel', identity.parallel))))
    if parallel:
        if parallel_title_has_conflicting_specificity(variant_title, parallel, parallel_aliases):
            conflicts.append("平行工艺冲突")
        elif is_explicit_base_listing(variant_title):
            conflicts.append("平行与 Base 冲突")
        elif parallel_matches_title(variant_title, parallel, parallel_aliases) and (
            explicit_colors
            or has_material_parallel_marker(variant_title, identity.product_name)
            or any(_phrase_in_title(a, variant_title) for a in parallel_aliases)
        ):
            score += 5
            reasons.append("平行一致")
        else:
            missing.append("平行完整名称")
        if visual_parallel and not visual_same:
            missing.append('平行俗称需要当前身份及图片绑定的同款复核')
        elif visual_parallel and visual_same:
            reasons.append('已复用当前身份版本与订单图片绑定的逐图复核（不确认官方平行名）')
    elif (explicit_colors or has_material_parallel_marker(variant_title, identity.product_name)
          or has_ambiguous_finish_marker(variant_title)):
        missing.append("库存未确认平行")
    for field, pattern, negative, label in [
        ("is_autograph", r"签字|签名|卡签|贴签|\bauto(?:graph)?s?\b|\brpa\b", r"无签|非签|no auto", "签字"),
        ("is_memorabilia", r"球衣|物料|\bjersey\b|\bpatch\b|\brelic\b|\brpa\b", r"无球衣|no relic", "球衣"),
    ]:
        attribute_title = without_artist_signature(title) if sketch and field == "is_autograph" else title
        attribute_title = re.sub(r'切割\s*(?:瑕疵|不齐|偏移|问题)|裁切\s*(?:瑕疵|不齐|偏移|问题)', '品相问题', attribute_title)
        present = bool(re.search(pattern, attribute_title)) and not re.search(negative, attribute_title)
        if field == "is_memorabilia":
            present = is_memorabilia_listing(attribute_title) and not re.search(negative, attribute_title)
        expected = getattr(identity, field) or (field == "is_memorabilia" and identity.is_patch)
        if present and not expected:
            conflicts.append(label + "属性冲突")
        elif expected and re.search(negative, title):
            conflicts.append(label + "属性冲突")
        elif expected and not present:
            missing.append(label)
    patch_negative = bool(re.search(
        r"(?:无|非|不是)\s*(?:patch|切割)|"
        r"(?<![a-z0-9])(?:no|non|not(?: a)?)\s+patch(?![a-z0-9])", title
    ))
    patch_title = re.sub(r'切割\s*(?:瑕疵|不齐|偏移|问题)', '品相问题', title)
    patch_present = not patch_negative and bool(re.search(
        r"切割|(?<![a-z0-9])(?:patch|rpa)(?![a-z0-9])", patch_title
    ))
    if identity.is_patch:
        if patch_negative:
            conflicts.append("Patch 切割属性冲突")
        elif not patch_present:
            missing.append("Patch 切割")
    elif patch_present and identity.is_memorabilia:
        conflicts.append("Patch 切割属性冲突")
    images = set(line.get("image_urls", []))
    image_match = any(card.get(key) in images for key in ("image_front_url", "image_back_url"))
    if image_match:
        reasons.append("来源图片 URL 一致（非视觉鉴定）")
    linked = _same_source_item(card, line)
    if linked:
        reasons.append("库存来源链接与商品编号一致")
    if sketch:
        if without_artist_signature(title) != title:
            reasons.append("画师署名与主体签字属性分开核对")
        if re.search(r"\breprints?\b|复刻|复制|印刷复制|非手绘", title):
            conflicts.append("手绘原作与复制品冲突")
        # A matching 1/1 fraction is not a physical-copy identifier for art.
        # Require an established source link/image, not just matching text.
        if not (linked or image_match):
            missing.append("手绘原作需核对实物并关联来源商品")
    serial_verified = bool(
        serial
        and serial[1]
        and identity.serial_number is not None
        and total == identity.serial_total
        and int(serial[1]) == identity.serial_number
    )
    if serial_verified:
        reasons.append("限编分子与总数一致")
    # Eligibility is semantic, not a score threshold or a physical-copy veto:
    # ordinary unnumbered cards can be identical purchases too. The service
    # resolves multiple eligible purchase units before writing a cost. An exact
    # source link/image can establish omitted title fields, but never overrides
    # an explicit conflict (including a different serial numerator).
    automatic = not conflicts and (not missing or linked or image_match) and not (visual_parallel and not visual_same) and not soft_differences
    return dict(
        confidence=Decimal(min(score, 99)) / 100 if not conflicts else Decimal(0),
        reasons=reasons,
        conflicts=conflicts,
        missing=missing,
        automatic=automatic,
        recalled=player_match or linked or image_match,
        match_method='exact', soft_differences=soft_differences,
        **({'visual_review': visual_evidence} if visual_evidence else {}),
    )
