import re
from collections.abc import Sequence

from card_platform.domain.product_identity import (
    PRODUCT_MATCH_ALIASES,
    PRODUCT_SINGLE_CARD_SHORTHANDS,
    aliases_from_registry,
)


def title_without_inventory_hash_tail(title: str) -> str:
    """An isolated long code after seller brackets is not a card-number claim.

    Keep normal #12, #PA5 and explicit 'card #1036' declarations. Ambiguous
    trailing warehouse IDs are removed only from number parsing, never from
    immutable evidence or product/version matching.
    """
    return re.sub(r"(?<=[】\]\)）])\s*#\s*\d{4,}\s*$", "", title)

HARD_LOT_PATTERN = re.compile(
    r"(?:(?<![A-Za-z0-9])lots?(?:\s*[-:#]?\s*\d+)?(?![A-Za-z])|"
    r"打包|多卡|多张同出|(?:共\s*)?(?<!第)(?<!\d)\d{1,2}\s*张(?!总)(?:同出|通行品)?|"
    r"整套|全套|全套装|完整套装|full\s+set|complete\s+set|未拆|卡盒|盒卡|卡包|箱卡|"
    r"\bpack\b|\bbox\b)",
    re.IGNORECASE,
)
AMBIGUOUS_SET_CARD_PATTERN = re.compile(r"套卡", re.IGNORECASE)
MEMORABILIA_PATTERN = re.compile(r"球衣|物料|切割|patch|jersey|relic|\brpa\b", re.I)
GRADING_DETAIL_PATTERN = re.compile(
    r"(?<![A-Za-z])(PSA|BGS|SGC|CGC|CSG|AICCG|RFE|BEM|CBA|GMA|BGN|GBTC|"
    r"HGA|TAG|ISA|KSA|MNT|AP)(?![A-Za-z])[\s:_-]*"
    r"(?:(?:GEM|MINT|NM|MT|EX|VG|GOOD|FAIR|POOR|PRISTINE)[\s-]*){0,3}"
    r"(10(?:\.0)?|[1-9](?:\.\d+)?)(?![\d.])",
    re.IGNORECASE,
)
GRADING_COMPANY_PATTERN = re.compile(
    GRADING_DETAIL_PATTERN.pattern
    + r"|(?:公博|国评|华夏|保粹|爱藏)\s*(?:10|[1-9](?:\.\d+)?)(?![\d.])|评级",
    re.IGNORECASE,
)
STRONG_MEMORABILIA_PATTERN = re.compile(
    r"物料|切割|patch|jersey|relic|\brpa\b|球衣\s*(?:卡|窗|物料|切割)|"
    r"dressed\s+to\s+impress|时装走秀|走秀特卡",
    re.I,
)
UNIFORM_DESCRIPTION_PATTERN = re.compile(
    r"(?:主场|客场|公牛|湖人|球队|身穿).{0,12}(?:\d{1,2}\s*号)?球衣(?:选图)?",
    re.I,
)
MATERIAL_PARALLEL_PATTERN = re.compile(
    r"(?:\b(?:red|blue|gold|green|purple|black|orange|pink|aqua|teal|bronze|"
    r"white|yellow)\b(?:\s+(?:diamond|cracked\s*ice|ice|wave|laser|lava|"
    r"disco|checkerboard|pulsar|scope|mojo|hyper))?\s+"
    r"(?:refractor|parallel|prizm|wave|foil|holo|holofoil)\b|"
    r"\bsilver\s+(?:diamond|cracked\s*ice|wave|laser|lava|disco|checkerboard|"
    r"pulsar|scope|mojo|hyper)\b|"
    r"\b(?:cracked\s*ice|checkerboard|press\s*proof|pulsar|scope|mojo|hyper|"
    r"maze|fireworks|mcflurry|amoeba|dragon\s*scale|x-fractor|reptile|"
    r"zebra|tiger|elephant|peacock|kaleidoscope|gem|diamond|camo|"
    r"camouflage|explosion|logo|frostbite|holographic)\b|"
    r"(?<![A-Za-z0-9])(?:pmg|mojo|shimmer)(?![A-Za-z0-9])|"
    r"\bsilver\s+refractor\b|"
    r"(?:红|蓝|金|绿|紫|黑|橙|粉|青|白|黄|铜)"
    r"(?:(?:钻石|碎冰|冰裂|波纹|泡泡|熔岩|岩浆|闪电|棋盘|格子|"
    r"射线|光线|马赛克|碎点|点点|星光|镭射|脉冲|宝石|圈圈|"
    r"棱镜|三色|碎闪|迷彩|爆炸|logo|冰霜)折?|折)|"
    r"银(?:钻石|碎冰|冰裂|波纹|泡泡|熔岩|岩浆|闪电|棋盘|格子|"
    r"射线|光线|马赛克|碎点|点点|星光|镭射|脉冲|宝石|圈圈|"
    r"棱镜|三色|碎闪|迷彩|爆炸|logo|冰霜)折|"
    r"(?:钻石|碎冰|冰裂|波纹|泡泡|熔岩|岩浆|闪电|棋盘|格子|"
    r"射线|光线|马赛克|碎点|点点|星光|镭射|脉冲|宝石|圈圈|"
    r"棱镜|三色|碎闪|迷彩|爆炸|logo|冰霜)"
    r"(?:红|蓝|金|绿|紫|黑|橙|粉|青|白|黄|铜|银)?折|"
    r"\bparallel\b|平行(?:版|卡)?|"
    r"(?:红|蓝|金|绿|紫|黑|橙|粉|青|白|黄|铜|银)(?:色)?闪版|"
    r"月光(?:红|蓝|金|绿|紫|黑|橙|粉|青|白|黄|铜|银)|"
    r"\b(?:winter|holiday)\b|"
    r"(?:变形虫|龙鳞|龙纹|蛇皮|虎纹|虎皮|斑马|大象|孔雀|万花筒|霓虹|"
    r"涡旋|漩涡|旋涡|爆炸|球皮|宝石|圈圈|棱镜|三色|碎闪|"
    r"迷彩|logo|冰霜|冰雪嘉年华)(?:折(?:射)?)?|"
    r"全息折(?:射)?|"
    r"棋盘格(?:折(?:射)?)?|迷宫折|烟花折|"
    r"冬季(?:版|限定|特卡|雪花)?|雪花(?:版|限定|折)?)",
    re.IGNORECASE,
)
AMBIGUOUS_FINISH_PATTERN = re.compile(
    r"\b(?:silver\s+)?refractor\b|\b(?:holo|holofoil|foil)\b|"
    r"银折(?:射)?|折射(?:卡)?|闪卡",
    re.IGNORECASE,
)
NAMED_CARD_VARIANT_PATTERN = re.compile(
    r"(?:特卡|大比例|小比例|超短印|短印|变体(?:版)?|变化版|"
    r"星座|十二宫|联名|原型|\b(?:ssp|insert|variation(?:s)?|variant|zodiac|constellation)\b|"
    r"\b(?:case\s*hit|super\s+short\s+print|short\s+print)\b)",
    re.IGNORECASE,
)
HIGH_SPECIFICITY_NAMED_VARIANT_PATTERN = re.compile(
    r"(?:超短印|短印|变体(?:版)?|变化版|联名|原型|"
    r"大合照|全家福|多人卡|双人卡|三人卡|球员合影|"
    r"\b(?:ssp|variation(?:s)?|variant|zodiac|constellation|case\s*hit|"
    r"super\s+short\s+print)\b)",
    re.IGNORECASE,
)
ENGLISH_NAMED_VARIANT_PATTERN = re.compile(
    r"(?<![0-9A-Za-z])"
    r"([A-Za-z][A-Za-z0-9&'!.+-]*(?:\s+[A-Za-z][A-Za-z0-9&'!.+-]*){0,5})"
    r"\s*(?:[\u3400-\u9fff]{0,12}\s*)?(?:特卡|\binsert\b)",
    re.IGNORECASE,
)
CHINESE_NAMED_VARIANT_PATTERN = re.compile(
    r"(?<![\u3400-\u9fff])([\u3400-\u9fff]{2,10})\s*(?:特卡|插入卡)",
    re.IGNORECASE,
)
GENERIC_CHINESE_VARIANT_LABELS = {
    "大比例",
    "小比例",
    "普通",
    "经典",
    "经典老卡",
    "经典老特",
    "老卡",
    "老特",
    "新秀",
    "比例",
    "球星",
    "高端",
    "高端系列",
}
EXPLICIT_BASE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])base(?![A-Za-z0-9])|普卡|基础卡|正\s*base",
    re.IGNORECASE,
)
ENUMERATED_INSERTS_PATTERN = re.compile(
    r"[A-Za-z][A-Za-z'&-]*(?:\s+[A-Za-z][A-Za-z'&-]*){0,3}"
    r"\s*、\s*[A-Za-z][A-Za-z'&-]*(?:\s+[A-Za-z][A-Za-z'&-]*){0,3}"
    r"\s*(?:特卡|inserts?\b)", re.I,
)


def has_enumerated_bundle_claim(title: str) -> bool:
    """Independent player and insert enumerations describe a mixed lot.

    Multiple names alone can be a legitimate shared-roster card. Require a
    separate list of distinct insert names as well; do not mistake a roster,
    translations, or seller aliases alone for multiple physical cards.
    """
    match = ENUMERATED_INSERTS_PATTERN.search(title)
    if not match:
        return False
    remaining = title[:match.start()] + title[match.end():]
    return remaining.count("、") >= 2


def is_multi_card_listing(title: str, product_name: object = None) -> bool:
    """Detect lots while preserving audited single-card seller vocabulary.

    CardHobby sellers use ``银标套卡`` for individual cards from Upper Deck's
    MJ's Back set.  The phrase is ambiguous in isolation, so it is exempted only
    when both the catalog product and the listing title independently identify
    MJ's Back.  Explicit quantity, lot, sealed-product, or full-set language
    remains a hard rejection.
    """
    # ``第2张`` labels one listing in a seller's sequence; it is not a two-card
    # lot.  Remove ordinals before matching quantity language.  Long strings
    # such as ``260504张总`` are seller/account codes and are excluded by the
    # bounded quantity and ``张总`` guards in ``HARD_LOT_PATTERN``.
    quantity_title = re.sub(r"第\s*\d{1,2}\s*张", "", title)
    if HARD_LOT_PATTERN.search(quantity_title):
        return True
    if has_enumerated_bundle_claim(title):
        return True
    if not AMBIGUOUS_SET_CARD_PATTERN.search(title):
        return False
    # ``套卡`` also appears as seller shorthand immediately after a reviewed
    # product nickname.  In the absence of a quantity/full-set/packing marker,
    # that phrase is ambiguous rather than proof that multiple physical cards
    # were sold.  Keep it available as a low-confidence single-card candidate;
    # the normal player/card-kind checks still have to pass.
    for phrase in aliases_from_registry(
        product_name, PRODUCT_SINGLE_CARD_SHORTHANDS
    ):
        if phrase and re.search(re.escape(str(phrase)), title, re.I):
            return False
    return True


def explicit_print_runs(title: str) -> set[int]:
    """Extract print-run totals without mistaking seller inventory paths.

    A real serial claim is normally a bounded ``12/99`` fraction, a bounded
    denominator-only ``/99``, or an explicit ``99编`` label.  Inventory tails
    such as ``7075cea/46`` are intentionally excluded because the slash is
    attached to an alphanumeric seller code.
    """
    totals = {
        int(value)
        for value in re.findall(
            r"(?<![0-9A-Za-z])\d{1,6}\s*/\s*(\d{1,6})(?![0-9A-Za-z])",
            title,
        )
    }
    totals.update(
        int(value)
        for value in re.findall(
            r"(?<![0-9A-Za-z/])/\s*(\d{1,6})(?![0-9A-Za-z])",
            title,
        )
    )
    totals.update(
        int(value)
        for value in re.findall(
            # ``2021编年史`` names the Chronicles product; it is not a
            # 2021-copy print run.  Preserve a genuine standalone ``2021编``
            # and numbered forms such as ``17/2021编``.
            r"(?<!\d)(\d{1,6})\s*[编遍](?!\s*年\s*史)",
            title,
        )
    )
    return totals


def is_explicit_base_listing(title: str) -> bool:
    """Recognize Base next to Chinese text without relying on Unicode ``\b``."""
    return bool(EXPLICIT_BASE_PATTERN.search(title))


def is_memorabilia_listing(title: str) -> bool:
    """Separate a depicted uniform from an actual memorabilia card.

    Chinese sellers often describe the uniform visible in the photograph with
    phrases such as ``公牛客场23号球衣`` or ``主场球衣选图``.  Those phrases do
    not imply that the card contains jersey material.  Strong material/window
    vocabulary remains authoritative.
    """
    if STRONG_MEMORABILIA_PATTERN.search(title):
        return True
    if UNIFORM_DESCRIPTION_PATTERN.search(title):
        return False
    return bool(MEMORABILIA_PATTERN.search(title))


def is_graded_listing(title: str) -> bool:
    """Recognize major international and domestic grading labels uniformly."""
    return bool(GRADING_COMPANY_PATTERN.search(title))


def has_material_parallel_marker(
    title: str,
    product_name: object = None,
    ignored_card_kind_terms: Sequence[object] = (),
) -> bool:
    """Detect an explicit value-bearing parallel, not generic shiny stock.

    Bare ``银折``/``refractor`` wording is deliberately excluded because sellers
    frequently use it for a product's normal chromium finish.  A colour,
    technology, or named production variant is strong enough to remain a hard
    version boundary.
    """
    candidate = title
    for term in ignored_card_kind_terms:
        if str(term or "").strip():
            candidate = re.sub(re.escape(str(term)), " ", candidate, flags=re.I)
    normalized_product = re.sub(
        r"[^0-9a-z一-鿿]+", " ", str(product_name or "").casefold()
    ).strip()
    if "mosaic" in normalized_product or "马赛克" in normalized_product:
        # Sellers use “马赛克折” for Mosaic's normal chromium stock. A
        # colour immediately before it is a named parallel and must survive.
        candidate = re.sub(
            r"(?<![红蓝金绿紫黑橙粉青白黄铜银])马赛克折",
            " ",
            candidate,
            flags=re.I,
        )
    if "winter" in normalized_product or "holiday" in normalized_product:
        # Winter/Holiday releases are distinct products in their own right.
        # Their product word is not evidence of a parallel inside that product.
        candidate = re.sub(
            r"\b(?:winter|holiday)\b|冬季(?:版|限定)?|雪花(?:版|限定)?",
            " ",
            candidate,
            flags=re.I,
        )
    return bool(MATERIAL_PARALLEL_PATTERN.search(candidate))


def has_ambiguous_finish_marker(title: str) -> bool:
    """Return seller finish words that need image context, not hard rejection."""
    return bool(AMBIGUOUS_FINISH_PATTERN.search(title))


def has_named_card_variant_marker(title: str, product_name: object = None) -> bool:
    """Return explicit insert/variation vocabulary, independent of its name.

    A catalog may not yet contain the insert name printed in a marketplace
    title.  High-precision type words such as ``SSP``, ``特卡`` and
    ``variation`` still prove that the row is a named card kind rather than an
    unspecified base sale.  Bare ``SP`` is intentionally excluded: it is also
    a product-family acronym and generic seller shorthand for older foil stock.
    """
    candidate = title
    product_label = str(product_name or "").strip()
    if product_label:
        # Do not classify a product whose official name itself contains a type
        # word (for example "Variations") as a child insert of that product.
        candidate = re.sub(re.escape(product_label), " ", candidate, flags=re.I)
    return bool(NAMED_CARD_VARIANT_PATTERN.search(candidate))


def has_explicit_named_card_variant_claim(
    title: str,
    product_name: object = None,
    player_name: object = None,
) -> bool:
    """Distinguish an explicit named variant from a seller's bare ``特卡``.

    Missing identity text is soft: a seller may call the requested insert only
    ``特卡``.  A proper label such as ``Serenity 特卡`` or ``Power Players 特卡``,
    and high-specificity words such as ``variation`` or ``联名``, are concrete
    version claims and must be checked before a sale can become a comparable.
    """
    candidate = title
    product_terms = tuple(
        dict.fromkeys(
            (
                str(product_name or ""),
                *aliases_from_registry(product_name, PRODUCT_MATCH_ALIASES),
            )
        )
    )
    for term in product_terms:
        if term.strip():
            # Keep a non-whitespace boundary where identity text was removed.
            # Replacing with a blank would join a preceding player nickname to
            # a following bare ``特卡`` and invent a named variant claim.
            candidate = re.sub(re.escape(term), " § ", candidate, flags=re.I)
    player_label = str(player_name or "").strip()
    if player_label:
        candidate = re.sub(re.escape(player_label), " § ", candidate, flags=re.I)

    if HIGH_SPECIFICITY_NAMED_VARIANT_PATTERN.search(candidate):
        return True
    identity_words = {
        word
        for value in (product_name, player_name)
        for word in re.findall(r"[a-z0-9]+", str(value or "").casefold())
    }
    generic_words = {
        "base",
        "basketball",
        "card",
        "cards",
        "classic",
        "fleer",
        "nba",
        "panini",
        "rc",
        "rookie",
        "sp",
        "skybox",
        "topps",
        "upper",
        "deck",
    }
    for match in ENGLISH_NAMED_VARIANT_PATTERN.finditer(candidate):
        words = re.findall(r"[a-z0-9]+", match.group(1).casefold())
        if any(word not in identity_words | generic_words for word in words):
            return True
    for match in CHINESE_NAMED_VARIANT_PATTERN.finditer(candidate):
        label = match.group(1)
        reviewed_short_labels = {
            compact
            for aliases in PRODUCT_MATCH_ALIASES.values()
            for alias in aliases
            if (
                (compact := re.sub(r"\s+", "", str(alias)))
                and re.fullmatch(r"[\u3400-\u9fff]{2}", compact)
            )
        }
        if label not in GENERIC_CHINESE_VARIANT_LABELS and (
            len(label) >= 3 or label in reviewed_short_labels
        ):
            return True
    return False
