"""Purchase-cost identity gates, isolated from valuation runtime upgrades.

These exact parallel/variant helpers are extracted from domain.comparables and
listing_title. Keeping the small dependency graph here lets purchase-cost roll
out on installations whose valuation modules predate those helpers, without
replacing their independent valuation changes.
"""

import re
from collections.abc import Sequence

from card_platform.domain.card_identity import normalize_text



_PARALLEL_COLORS = {
    "red": "红",
    "blue": "蓝",
    "gold": "金",
    "green": "绿",
    "purple": "紫",
    "black": "黑",
    "orange": "橙",
    "pink": "粉",
    "aqua": "青",
    "teal": "青",
    "bronze": "铜",
    "silver": "银",
}


_SPECIFIC_PARALLEL_TECHNOLOGIES = {
    "cracked ice": ("cracked ice", "碎冰", "冰裂"),
    "diamond": ("diamond", "钻石"),
    "laser": ("laser", "镭射"),
    "ray wave": ("ray wave", "射线", "光线"),
    "wave": ("wave", "波纹"),
    "lava": ("lava", "熔岩", "岩浆"),
    "disco": ("disco", "泡泡", "迪斯科"),
    "checkerboard": ("checkerboard", "棋盘", "格子"),
    "pulsar": ("pulsar", "脉冲"),
    "scope": ("scope",),
    "mojo": ("mojo",),
    "hyper": ("hyper",),
    "shimmer": ("shimmer", "闪烁"),
    "velocity": ("velocity",),
    "fast break": ("fast break",),
    "prizm": ("prizm",),
    "mosaic": ("mosaic", "马赛克"),
    "fluorescent": ("fluorescent", "荧光"),
    "fireworks": ("fireworks", "烟花"),
    "maze": ("maze", "迷宫"),
    "ice": ("ice", "冰"),
}


def _parallel_color(value: object) -> tuple[str, str] | None:
    normalized = normalize_text(value)
    for english, chinese in _PARALLEL_COLORS.items():
        if re.search(rf"(?<![0-9a-z]){english}(?![0-9a-z])", normalized):
            return english, chinese
        if chinese in str(value or ""):
            return english, chinese
    return None


def _colored_specific_parallel_technologies(
    value: object,
    color: tuple[str, str],
) -> set[str]:
    normalized = normalize_text(value)
    compact = re.sub(r"\s+", "", str(value or "").casefold())
    english, chinese = color
    matches: set[str] = set()
    for canonical, aliases in _SPECIFIC_PARALLEL_TECHNOLOGIES.items():
        for alias in aliases:
            if re.search(r"[a-z]", alias):
                normalized_alias = re.escape(normalize_text(alias))
                forward = re.search(
                    rf"(?<![0-9a-z]){english}\s+"
                    rf"(?:(?:prizm|parallel|refractor)\s+)?"
                    rf"{normalized_alias}(?![0-9a-z])",
                    normalized,
                )
                reverse = canonical not in {"mosaic", "prizm"} and re.search(
                    rf"(?<![0-9a-z]){normalized_alias}\s+"
                    rf"(?:(?:prizm|parallel|refractor)\s+)?"
                    rf"{english}(?![0-9a-z])",
                    normalized,
                )
                if forward or reverse:
                    matches.add(canonical)
                    break
            elif any(
                phrase in compact
                for phrase in (
                    f"{chinese}{alias}",
                    f"{chinese}色{alias}",
                    f"{alias}{chinese}",
                    f"{alias}{chinese}色",
                )
            ):
                matches.add(canonical)
                break
    # ``cracked ice`` is the specific name; do not also reduce it to ``ice``.
    if "cracked ice" in matches:
        matches.discard("ice")
    if "ray wave" in matches:
        matches.discard("wave")
    return matches


def _target_parallel_technologies(value: object) -> set[str]:
    normalized = normalize_text(value)
    original = str(value or "").casefold()
    matches = {
        canonical
        for canonical, aliases in _SPECIFIC_PARALLEL_TECHNOLOGIES.items()
        if any(
            (
                bool(
                    re.search(
                        rf"(?<![0-9a-z]){re.escape(normalize_text(alias))}"
                        rf"(?![0-9a-z])",
                        normalized,
                    )
                )
                if re.search(r"[a-z]", alias)
                else alias in original
            )
            for alias in aliases
        )
    }
    if "cracked ice" in matches:
        matches.discard("ice")
    if "ray wave" in matches:
        matches.discard("wave")
    return matches


def parallel_title_has_conflicting_specificity(
    text: str,
    target: object,
    aliases: tuple[str, ...] = (),
) -> bool:
    """Detect a longer same-colour parallel that a short alias must not absorb.

    Marketplace titles routinely contain both a colour and a named technology.
    A plain ``Blue`` identity is therefore not enough to accept ``Blue Ice`` or
    ``Blue Laser``.  Chinese sellers also use ``蓝镭射`` as the ordinary
    translation of a checklist ``Blue Prizm``; that one audited spelling is a
    synonym of *Prizm*, while an English ``Blue Laser`` remains a distinct card.
    """
    target_color = _parallel_color(target)
    if target_color is None:
        return False
    english, chinese = target_color
    normalized_title = normalize_text(text)
    original_title = str(text or "").casefold()
    english_colored = bool(
        re.search(rf"(?<![0-9a-z]){english}(?![0-9a-z])", normalized_title)
    )
    chinese_colored = chinese in original_title
    if not english_colored and not chinese_colored:
        return False

    title_technologies = _colored_specific_parallel_technologies(text, target_color)
    target_technologies = set().union(
        *(
            _target_parallel_technologies(value)
            for value in (target, *aliases)
        )
    )
    if not title_technologies:
        return False

    target_normalized = normalize_text(target)
    if (
        target_normalized in {f"{english} prizm", f"{english} prizm parallel"}
        and title_technologies == {"laser"}
        and chinese_colored
        and f"{chinese}镭射" in original_title
        and not re.search(
            rf"(?<![0-9a-z]){english}\s+laser(?![0-9a-z])", normalized_title
        )
    ):
        return False
    return bool(title_technologies - target_technologies)


def parallel_matches_title(
    text: str,
    target: object,
    aliases: tuple[str, ...] = (),
) -> bool:
    """Match a parallel only after ruling out a more specific same-colour name."""
    if not normalize_text(target):
        return False
    if parallel_title_has_conflicting_specificity(text, target, aliases):
        return False

    target_normalized = normalize_text(target)
    color = _parallel_color(target)
    generated: tuple[str, ...] = ()
    if color is not None:
        english, chinese = color
        if target_normalized in {english, f"{english} parallel"}:
            generated = (english, f"{english} parallel", chinese, f"{chinese}折")
        elif target_normalized in {
            f"{english} prizm",
            f"{english} prizm parallel",
        }:
            generated = (
                f"{english} prizm",
                f"{english} parallel",
                english,
                chinese,
                f"{chinese}折",
                f"{chinese}镭射",
            )
        else:
            translated = []
            for technology in _target_parallel_technologies(target):
                for alias in _SPECIFIC_PARALLEL_TECHNOLOGIES[technology]:
                    if not re.search(r"[a-z]", alias):
                        translated.extend(
                            (f"{chinese}{alias}", f"{chinese}{alias}折")
                        )
            generated = tuple(translated)
    normalized_title = normalize_text(text)
    candidates = tuple(dict.fromkeys((*aliases, *generated, str(target or ""))))
    return any(
        normalize_text(alias) in normalized_title
        for alias in candidates
        if normalize_text(alias)
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


EXPLICIT_BASE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])base(?![A-Za-z0-9])|普卡|基础卡|正\s*base",
    re.IGNORECASE,
)


def is_explicit_base_listing(title: str) -> bool:
    """Recognize Base next to Chinese text without relying on Unicode ``\b``."""
    return bool(EXPLICIT_BASE_PATTERN.search(title))


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
