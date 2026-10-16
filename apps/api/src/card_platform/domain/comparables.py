import re
from dataclasses import dataclass, field
from decimal import Decimal
from card_platform.domain.finish_evidence import VisualMatchFacts, finish_state
from card_platform.domain.scoped_parallel_names import parallel_match_text
from card_platform.domain.reviewed_catalog_identity import reviewed_unnumbered_card_anchor
from enum import StrEnum

from card_platform.domain.card_identity import (
    CardIdentity,
    INSERT_ALIASES,
    is_intrinsic_product_finish,
    normalize_text,
)
from card_platform.domain.listing_title import (
    explicit_print_runs,
    has_explicit_named_card_variant_claim,
    has_ambiguous_finish_marker,
    has_material_parallel_marker,
    has_named_card_variant_marker,
    is_explicit_base_listing,
    is_graded_listing,
    is_multi_card_listing,
    title_without_inventory_hash_tail,
)
from card_platform.domain.product_identity import (
    ProductMatchStatus,
    assess_identity_product_match,
    reviewed_parent_product_confirmation,
    product_matches_title,
    named_child_product_for_parent,
    has_ambiguous_child_product_shorthand,
)
from card_platform.domain.season import explicit_seasons as parse_explicit_seasons, matches_abbreviated_season
from card_platform.domain.redemption import redemption_conflict
from card_platform.domain.listing_attributes import autograph_claim, memorabilia_claim
from card_platform.knowledge import KnowledgeResolver
from card_platform.domain.entity_roles import identity_has_entity_role_collision
from card_platform.domain.collector_number import ordinal_collector_number_matches, ordinal_collector_number_conflicts, numeric_card_number_key
from card_platform.marketplace_aliases import seller_aliases
from card_platform.domain.subject_aliases import published_subject_aliases, published_subject_matches
from card_platform.domain.player_aliases import (
    PLAYER_MATCH_ALIAS_EXCLUSIONS,
    matching_player_aliases,
    player_roster_components,
)


class ComparableTier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ComparableInput:
    title: str
    grade_company: str | None = None
    grade_value: Decimal | None = None
    source_reliability: Decimal = Decimal("0.70")
    source_code: str | None = None
    visual_verdict: str | None = None
    visual_confidence: Decimal | None = None
    visual_reasons: tuple[str, ...] = ()
    visual_facts: VisualMatchFacts | None = None


@dataclass(frozen=True, slots=True)
class Classification:
    tier: ComparableTier
    similarity: Decimal
    source_reliability: Decimal
    field_matches: dict[str, bool | str | None] = field(default_factory=dict)
    hard_conflicts: list[str] = field(default_factory=list)


def _contains(text: str, value: object) -> bool:
    value_text = normalize_text(value)
    normalized_text = normalize_text(text)
    if not value_text:
        return False
    if value_text in normalized_text:
        return True
    # Chinese seller titles freely insert spaces inside transliterated names
    # (``魔术师 约翰逊`` vs ``魔术师约翰逊``). Whitespace is not an entity
    # boundary for Han text, so compare a compact form without weakening Latin
    # token boundaries.
    if re.search(r"[\u3400-\u9fff]", value_text):
        return value_text.replace(" ", "") in normalized_text.replace(" ", "")
    return False


def _contains_insert_name(text: str, value: object) -> bool:
    name = normalize_text(value)
    if not name:
        return False
    return bool(
        re.search(
            r"(?<![0-9a-z])" + re.escape(name) + r"(?![0-9a-z])",
            normalize_text(text),
        )
    )


def _weak_insert_alias(alias: object, canonical: object) -> bool:
    """Short discovery aliases are not exclusive checklist identifiers.

    Two-character descriptions and compact abbreviations can describe a
    player's achievement or the pictured scene on several distinct inserts.
    They must not contradict a full, explicitly printed target insert name;
    another complete checklist name remains stronger conflicting evidence.
    """
    alias_text = normalize_text(alias)
    canonical_text = normalize_text(canonical)
    return bool(
        alias_text != canonical_text
        and (
            re.fullmatch(r"[\u3400-\u9fff]{1,2}", alias_text)
            or (re.fullmatch(r"[a-z]{2,5}", alias_text) and len(canonical_text.split()) > 1)
        )
    )


def _contains_player(text: str, value: object, *, minimum_matches: int | None = None, resolver=None) -> bool:
    player = str(value or "").strip()
    parts = player_roster_components(player)
    aliases = {
        "aj dybantsa": ("aj迪班萨", "迪班萨"),
        "kevin garnett": ("凯文加内特", "加内特", "狼王"),
        "matas buzelis": ("马塔斯布泽利斯", "布泽利斯"),
        "tristan da silva": ("特里斯坦达席尔瓦", "达席尔瓦"),
        "victor wembanyama": ("维克托文班亚马", "维克多文班亚马", "文班亚马"),
        "tracy mcgrady": ("特雷西麦克格雷迪", "麦迪", "t mac"),
        "vj edge combe": ("vj edgecombe", "vj埃奇库姆", "埃奇库姆"),
        "vj edgecombe": ("vj edge combe", "vj埃奇库姆", "埃奇库姆"),
        "scoot henderson": ("斯库特亨德森", "亨德森"),
        "los angeles lakers": ("洛杉矶湖人", "湖人"),
        "stephen curry": ("斯蒂芬库里", "库里"),
        "kobe bryant": ("科比布莱恩特", "科比"),
        "jason kidd": ("杰森基德", "贾森基德"),
        "hakeem olajuwon": ("哈基姆奥拉朱旺", "奥拉朱旺", "大梦"),
        "andrew nembhard": ("安德鲁内姆哈德", "内姆哈德", "内姆布哈德"),
        "stephon marbury": ("斯蒂芬马布里", "马布里"),
        "alonzo mourning": ("阿朗佐莫宁", "莫宁"),
        "chauncey billups": ("昌西比卢普斯", "比卢普斯"),
        "elton brand": ("埃尔顿布兰德", "布兰德"),
        "joel embiid": ("乔尔恩比德", "恩比德"),
        "magic johnson": ("埃尔文约翰逊", "魔术师约翰逊"),
        "alex sarr": ("亚历克斯萨尔", "萨尔"),
        "derik queen": ("德里克奎因", "奎因"),
        "eddie robinson": ("埃迪罗宾逊",),
        "bryon russell": ("拜伦拉塞尔",),
        "bronny james jr.": ("布朗尼詹姆斯", "布朗尼", "小布朗尼詹姆斯"),
        "bronny james jr": ("布朗尼詹姆斯", "布朗尼", "小布朗尼詹姆斯"),
        "lebron james": ("勒布朗詹姆斯", "勒布朗", "詹姆斯", "小皇帝"),
        "anthony davis": ("安东尼戴维斯", "浓眉戴维斯", "浓眉"),
    }

    def contains_part(part: str) -> bool:
        normalized_part = normalize_text(part)
        published = {normalize_text(a) for a in published_subject_aliases(part, resolver)}
        # Initial punctuation varies across labels and seller titles (C.J./CJ).
        # Collapse only adjacent one-letter initials, not arbitrary name words.
        compact_part = re.sub(r"\b([a-z])\s+([a-z])\b", r"\1\2", normalized_part)
        compact_title = re.sub(r"\b([a-z])\s+([a-z])\b", r"\1\2", normalize_text(text))
        return (
            published_subject_matches(text, part, resolver)
            or (normalized_part not in published and _contains(text, part))
            or bool(
                normalized_part not in published and re.search(
                    r"(?<![0-9a-z])" + re.escape(compact_part) + r"(?![0-9a-z])",
                    compact_title,
                )
            )
            or any(
                _contains(text, alias)
                for alias in (
                    *aliases.get(normalized_part, ()),
                    *matching_player_aliases(normalized_part),
                )
                if alias not in PLAYER_MATCH_ALIAS_EXCLUSIONS.get(normalized_part, set())
                and normalize_text(alias) not in published
            )
        )

    if len(parts) > 1 and published_subject_matches(text, player, resolver):
        return True
    if len(parts) <= 1:
        return contains_part(player)
    required = len(parts) if minimum_matches is None else min(len(parts), minimum_matches)
    return sum(contains_part(part) for part in parts) >= required


def _normalize_card_number(value: object) -> str:
    normalized = normalize_text(value)
    return numeric_card_number_key(re.sub(r"^no\s*", "", normalized).strip())


def has_partial_multi_player_match(title: str, player_name: str, *, resolver=None) -> bool:
    """Two distinct subjects on a 3+ subject card justify image review only."""
    return bool(
        len(player_roster_components(player_name)) >= 3
        and _contains_player(title, player_name, minimum_matches=2, resolver=resolver)
    )


def _contains_card_number(text: str, value: object) -> bool:
    ordinal_match = ordinal_collector_number_matches(text, value)
    if ordinal_match is not None:
        return ordinal_match
    target = _normalize_card_number(value)
    if not target:
        return False
    if target.isdigit():
        raw_target = re.sub(r"^no\s*", "", normalize_text(value)).strip()
        if (
            len(raw_target) > 1
            and raw_target.startswith("0")
            and re.search(rf"(?<!\d){re.escape(raw_target)}(?!\d)", text)
        ):
            return True
        return bool(
            re.search(
                rf"(?:#|\bno\.?\s*|\bcard\s*#?\s*)0*{re.escape(target)}(?!\d)",
                text,
                re.IGNORECASE,
            )
        )
    return bool(
        re.search(
            rf"(?<![0-9a-z])(?:#|no\.?\s*)?{re.escape(target)}(?![0-9a-z])",
            normalize_text(text),
            re.IGNORECASE,
        )
    )


def _contains_product(
    text: str,
    value: object,
    *,
    target_set: object = None,
    target_insert: object = None,
    target_parallel: object = None,
) -> bool:
    return product_matches_title(
        text,
        value,
        target_set=target_set,
        target_insert=target_insert,
        target_parallel=target_parallel,
    )


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
    "geometric": ("geometric", "几何", "幾何"),
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
    target_normalized = normalize_text(target)
    if target_normalized in {'refractor', 'chrome refractor', 'silver refractor', 'silver'}:
        # A generic Refractor alias occurs inside Red/Lava Refractor too.
        # Require an explicit finish phrase so player/team colour words alone
        # do not turn into version conflicts.
        colors = '|'.join(re.escape(c) for c in _PARALLEL_COLORS if c != 'silver')
        technologies = '|'.join(re.escape(alias) for aliases_ in
            _SPECIFIC_PARALLEL_TECHNOLOGIES.values() for alias in aliases_
            if re.search(r'[a-z]', alias))
        if re.search(rf'(?<![a-z0-9])(?:(?:{colors})\s+(?:(?:{technologies})\s+)?'
                     rf'|(?:{technologies})\s+)(?:refractor|parallel)(?![a-z0-9])',
                     normalize_text(text)):
            return True
    target_color = _parallel_color(target)
    if target_color is None:
        return False
    english, chinese = target_color
    normalized_title = normalize_text(text)
    original_title = str(text or "").casefold()
    english_colored = bool(re.search(rf"(?<![0-9a-z]){english}(?![0-9a-z])", normalized_title))
    chinese_colored = chinese in original_title
    if not english_colored and not chinese_colored:
        return False

    title_technologies = _colored_specific_parallel_technologies(text, target_color)
    target_technologies = set().union(
        *(_target_parallel_technologies(value) for value in (target, *aliases))
    )
    if not title_technologies:
        return False

    target_normalized = normalize_text(target)
    if (
        target_normalized in {f"{english} prizm", f"{english} prizm parallel"}
        and title_technologies == {"laser"}
        and chinese_colored
        and f"{chinese}镭射" in original_title
        and not re.search(rf"(?<![0-9a-z]){english}\s+laser(?![0-9a-z])", normalized_title)
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
                        translated.extend((f"{chinese}{alias}", f"{chinese}{alias}折"))
            generated = tuple(translated)
    normalized_title = normalize_text(text)
    candidates = tuple(dict.fromkeys((*aliases, *generated, str(target or ""))))
    return any(
        normalize_text(alias) in normalized_title for alias in candidates if normalize_text(alias)
    )


def shortened_refractor_color_claim(text: str, target: object) -> bool:
    """A matching Chinese colour alone omits finish detail; it proves no version."""
    normalized = normalize_text(target)
    color = _parallel_color(target)
    if color is None or normalized != f'{color[0]} refractor':
        return False
    phrase = color[1] + '折'
    if phrase not in text:
        return False
    if re.search(r'(?:非|无|無|不是|并非|並非|不含|\bnot\s+|\bwithout\s+|\bnon[-\s]*)\s*'
                 + re.escape(phrase), text, re.I):
        return False
    # Specific technologies elsewhere in the same title still contradict a
    # plain finish. Geometric is included even when the older colour parser
    # does not recognize the Chinese spelling.
    if _target_parallel_technologies(text) or re.search(r'\bgeometric\b|几何|幾何', text, re.I):
        return False
    # Conflict vocabulary is wider than the supported target shorthand. Match
    # colour-plus-finish phrases, not a player's surname such as Brown.
    conflict_colors = dict(_PARALLEL_COLORS, white='白', yellow='黄', brown='棕')
    chinese_claims = set(re.findall('([' + ''.join(set(conflict_colors.values()))
                                    + '])(?:色)?(?:折|平行)', text))
    english_claims = set(re.findall(r'\b(' + '|'.join(conflict_colors)
                                   + r')\s+(?:parallel|refractor)\b', text, re.I))
    return chinese_claims == {color[1]} and all(c.casefold() == color[0] for c in english_claims)


def _contains_parallel(text: str, value: object) -> bool:
    target = normalize_text(value)
    aliases = {
        "hyper red and green": ("hyper red and green", "hyper red & green", "红绿折", "红绿双折"),
        "silver": ("silver", "silver parallel", "银平行", "银折"),
        "silver parallel": ("silver", "silver parallel", "银平行", "银折"),
        "red": ("red", "red parallel", "红平行", "红折"),
        "red parallel": ("red", "red parallel", "红平行", "红折"),
        "blue": ("blue", "blue parallel", "蓝平行", "蓝折"),
        "blue parallel": ("blue", "blue parallel", "蓝平行", "蓝折"),
        "gold": ("gold", "gold parallel", "金平行", "金折"),
        "gold parallel": ("gold", "gold parallel", "金平行", "金折"),
        "fusion red and yellow": (
            "fusion red and yellow",
            "fusion red yellow",
            "red yellow mosaic",
            "红黄马赛克",
            "红金马赛克",
            "黄马赛克",
        ),
        "red white and blue refractor": (
            "red white and blue refractor",
            "red white blue refractor",
            "rwb refractor",
            "红白蓝三色平行折射",
            "红白蓝",
        ),
        "x fractor": ("x fractor", "xfractor", "x折", "x碎冰"),
        "plaid": ("plaid", "格子折", "格子"),
        "fast break purple mosaic": (
            "fast break purple mosaic",
            "mosaic fast break purple",
            "fast break purple prizm",
            "purple fast break mosaic",
            "purple disco",
            "紫 disco",
            "紫迪斯科",
        ),
        "red lava refractor": (
            "red lava refractor",
            "red lava",
            "lava red",
            "红熔岩折",
            "红熔岩",
            "红岩浆折",
        ),
        "chrome refractor": (
            "chrome refractor",
            "refractor",
            "silver refractor",
            "银折",
            "银折射",
        ),
        "refractor": (
            "refractor",
            "silver refractor",
            "silver parallel",
            "银平行",
            "银折",
            "银折射",
        ),
    }
    reviewed = seller_aliases("parallel", value)
    candidates = tuple(dict.fromkeys((*reviewed, *aliases.get(target, (target,)))))
    return parallel_matches_title(text, value, candidates)


def _contains_other_parallel(text: str, target: object) -> bool:
    target_text = normalize_text(target)
    normalized = normalize_text(text)
    # Named foil families have no colour token; colour-only checks miss an
    # explicit different version even when the back supplies a valid stamp.
    named_foils = {'astro', 'cosmic', 'fractal', 'galactic', 'impact', 'lava', 'cubic', 'sunburst'}
    if target_text in named_foils and any(
        re.search(rf'\b{re.escape(name)}\b', normalized)
        for name in named_foils - {target_text}
    ):
        return True
    if parallel_title_has_conflicting_specificity(text, target):
        return True
    parallel_aliases = {
        "refractor": (
            "refractor",
            "silver refractor",
            "silver parallel",
            "银平行",
            "银折",
            "银折射",
        ),
        "silver": ("silver", "silver parallel", "银平行", "银折"),
        "red": ("red", "red parallel", "红平行", "红折"),
        "blue": ("blue", "blue parallel", "蓝平行", "蓝折"),
        "gold": ("gold", "gold parallel", "金平行", "金折"),
        "green": ("green", "green parallel", "绿平行", "绿折"),
        "purple": ("purple", "purple parallel", "紫平行", "紫折"),
        "black": ("black", "black parallel", "黑平行", "黑折"),
        "bronze": ("bronze", "bronze parallel", "铜平行", "铜折"),
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
    target_aliases = set(parallel_aliases.get(target_text, (target_text,)))
    for canonical, aliases in parallel_aliases.items():
        if (
            canonical == target_text
            or canonical in target_text.split()
            or set(aliases) & target_aliases
        ):
            continue
        if any(alias in normalized for alias in aliases if alias):
            return True
    return False


def strong_geometry_can_resolve_visual_disagreement(reasons: tuple[str, ...]) -> bool:
    """Weak local matches can be a frame or one card inside a multi-card photo.

    The override is reserved for strong measured geometry without an explicit
    contradictory player/insert/version claim. Normal visually compatible T2
    proxy candidates do not need this override or these stronger thresholds.
    """
    semantic_conflict = re.compile(
        r"(?:球员(?:为)?不同|球员不匹配|不同球员|卡型明确标注为)|"
        r"(?:different|mismatched)\s+player|"
        r"(?:组合|多张卡|多卡|multiple\s+cards)",
        re.I,
    )
    if any(semantic_conflict.search(reason) for reason in reasons):
        return False
    for reason in reasons:
        match = re.fullmatch(
            r"geometry_same_template:matches=(\d+),inliers=(\d+),ratio=([0-9.]+)",
            reason,
        )
        if match and int(match[2]) >= 50 and Decimal(match[3]) >= Decimal("0.65"):
            return True
    return False


class ComparableClassifier:
    def __init__(self, resolver: KnowledgeResolver | None = None) -> None:
        self.resolver = resolver

    def classify(self, target: CardIdentity, comp: ComparableInput) -> Classification:
        text = comp.title
        normalized = normalize_text(text)
        conflicts: list[str] = []
        voucher_conflict = redemption_conflict(target.as_dict(), text)
        if voucher_conflict:
            return Classification(ComparableTier.REJECTED, Decimal("0"),
                                  comp.source_reliability, hard_conflicts=[voucher_conflict])
        visual_confidence = comp.visual_confidence or Decimal("0")
        geometry_template_evidence = bool(
            any(
                reason == "local_geometry_verified_same_printed_template"
                or reason.startswith("geometry_same_template:")
                for reason in comp.visual_reasons
            )
        )
        geometry_same_template = bool(
            not target.is_redemption
            and
            comp.visual_verdict in {"same", "compatible"}
            and visual_confidence >= Decimal("0.90")
            and geometry_template_evidence
        )
        visual_claims_different = bool(
            comp.visual_verdict == "different" and visual_confidence >= Decimal("0.80")
        )
        if is_multi_card_listing(text, target.product_name):
            conflicts.append("lot_or_multi_card")
        target_raw = target.grade_company is None
        comp_graded = bool(comp.grade_company or is_graded_listing(text))
        if target_raw != (not comp_graded):
            conflicts.append("grade_state_conflict")
        elif not target_raw and (
            normalize_text(comp.grade_company) != normalize_text(target.grade_company)
            or comp.grade_value is None
            or Decimal(comp.grade_value) != Decimal(str(target.grade_value))
        ):
            conflicts.append("grade_conflict")
        target_material = target.is_memorabilia or target.is_patch
        material_state = memorabilia_claim(text)
        comp_material = material_state is True
        attribute_omissions = []
        if target_material and material_state is None:
            attribute_omissions.append('material')
        elif material_state is not None and target_material != material_state:
            conflicts.append("material_conflict")
        target_autograph = target.is_autograph
        from card_platform.domain.sketch_identity import is_sketch_card, without_artist_signature, SKETCH_ALIASES
        autograph_state = autograph_claim(without_artist_signature(text)
            if is_sketch_card(target.insert) else text)
        if target_autograph and autograph_state is None:
            attribute_omissions.append('autograph')
        elif autograph_state is not None and target_autograph != autograph_state:
            conflicts.append("autograph_conflict")

        player = _contains_player(text, target.player_name, resolver=self.resolver)
        player_role_collision = identity_has_entity_role_collision(
            target.as_dict(), resolver=self.resolver
        )
        if player_role_collision:
            player = False
            conflicts.append("identity_role_conflict")
        product_status = assess_identity_product_match(text, target.as_dict(), resolver=self.resolver)
        from card_platform.domain.reviewed_source_back import reviewed_source_back_matches, reviewed_source_back_fields, reviewed_front_serial_digest
        reviewed_back = reviewed_source_back_matches(target, text, comp.visual_facts)
        reviewed_back_fields = reviewed_source_back_fields(target, text, comp.visual_facts)
        from card_platform.domain.reviewed_catalog_back import reviewed_cover_subject, reviewed_copyright_year_label
        reviewed_work = reviewed_cover_subject(target.as_dict(), text, comp.visual_facts, resolver=self.resolver)
        player = player or (reviewed_work and not player_role_collision)
        front_serial_digest = reviewed_front_serial_digest(target, text, comp.visual_facts)
        reviewed_product = reviewed_parent_product_confirmation(
            text, target.as_dict(), comp.visual_facts, resolver=self.resolver)
        from card_platform.domain.reviewed_reference_product import reviewed_reference_product_evidence
        reference_product = reviewed_reference_product_evidence(
            text, target.as_dict(), comp.visual_facts, resolver=self.resolver)
        product = product_status == ProductMatchStatus.MATCH or reviewed_product or reviewed_back or bool(reference_product)
        from card_platform.domain.entity_roles import title_without_catalog_subject
        attribute_text = title_without_catalog_subject(text, target.as_dict(), resolver=self.resolver)
        season_tokens = (
            {
                str(target.season_start),
                str(target.season_end),
                f"{target.season_start}-{str(target.season_end)[-2:]}",
            }
            if target.season_start and target.season_end
            else {str(target.season_start)}
            if target.season_start
            else set()
        )
        explicit_seasons = parse_explicit_seasons(attribute_text)
        target_season = (
            (int(target.season_start), int(target.season_end))
            if target.season_start and target.season_end
            else None
        )
        season = (
            target_season in explicit_seasons
            if explicit_seasons and target_season
            else not season_tokens
            or any(token in attribute_text for token in season_tokens)
            or matches_abbreviated_season(attribute_text, target.season_start, target.season_end)
        )
        normalized_target_number = _normalize_card_number(target.card_number)
        if not season and reviewed_copyright_year_label(target.as_dict(), text, comp.visual_facts, resolver=self.resolver):
            season = True
        if (reviewed_back and not explicit_seasons
                and not re.search(r'(?<![\d/])(?:19|20)\d{2}(?!\d)', attribute_text)):
            season = True
        card_number = _contains_card_number(attribute_text, target.card_number)
        card_number = card_number or 'card_number' in reviewed_back_fields
        if ordinal_collector_number_conflicts(attribute_text, target.card_number):
            conflicts.append("card_number_conflict")
        target_card_number = normalized_target_number
        raw_target_number = str(target.card_number or "").strip()
        prefixed_number = re.fullmatch(r"([A-Za-z]+)[#\s-]*(\d+)", raw_target_number)
        if prefixed_number:
            prefix, target_suffix = prefixed_number.groups()
            explicit_same_family = set(
                re.findall(
                    rf"(?<![0-9A-Za-z])#?{re.escape(prefix)}[\s-]*(\d+)(?!\d)",
                    attribute_text,
                    re.IGNORECASE,
                )
            )
            if explicit_same_family and target_suffix not in explicit_same_family:
                conflicts.append("card_number_conflict")
        elif raw_target_number.isdigit():
            explicit_numeric_numbers = set(
                re.findall(
                    r"(?:#|\bno\.?\s*)(\d+)(?!\d)", title_without_inventory_hash_tail(attribute_text), re.I
                )
            )
            if (
                target_card_number
                and explicit_numeric_numbers
                and target_card_number
                not in {_normalize_card_number(value) for value in explicit_numeric_numbers}
            ):
                conflicts.append("card_number_conflict")
        knowledge_insert_resolution = (
            self.resolver.resolve("insert", target.insert)
            if self.resolver and self.resolver.entities
            else None
        )
        knowledge_insert_aliases = (
            self.resolver.aliases_for("insert", target.insert)
            if knowledge_insert_resolution and knowledge_insert_resolution.entity_id is not None
            else ()
        )
        insert_aliases = tuple(
            dict.fromkeys(
                (
                    *seller_aliases("insert", target.insert),
                    *(SKETCH_ALIASES if is_sketch_card(target.insert) else ()),
                    *(
                        knowledge_insert_aliases
                        or INSERT_ALIASES.get(normalize_text(target.insert), (target.insert or "",))
                    ),
                )
            )
        )
        insert = any(normalize_text(alias) in normalized for alias in insert_aliases if alias)
        explicit_insert_name = _contains_insert_name(text, target.insert)
        if normalize_text(target.insert) == "rookie patch autographs" and re.search(
            r"\brpa\b", text, re.I
        ):
            insert = True
        if (
            normalize_text(target.insert) == "rookie patch autographs"
            and comp_material
            and bool(re.search(r"(?:签字|卡签|贴签|签名|\bauto(?:graph)?\b)", text, re.I))
        ):
            insert = True
        material_parallel_marker = has_material_parallel_marker(
            text,
            target.product_name,
            insert_aliases if insert else (),
        )
        # Visual similarity is supporting evidence, not a substitute for an
        # explicit card number/insert/parallel anchor. Closely related base and
        # insert cards often share the same player photo and layout.
        target_parallel_value = (
            None
            if is_intrinsic_product_finish(target.parallel, target.product_name)
            else target.parallel
        )
        target_parallel = normalize_text(target_parallel_value)
        unknown_finish = finish_state(target) == 'unknown'
        paired_finish = bool(comp.visual_facts and comp.visual_facts.valid_for_target(target, text))
        attributes_image_confirmed = bool(
            paired_finish and comp.visual_verdict == 'same' and visual_confidence >= Decimal('.95'))
        if not attributes_image_confirmed:
            conflicts.extend(f'{attribute}_confirmation_required' for attribute in attribute_omissions)
        parallel_text = parallel_match_text(text, target.as_dict(), resolver=self.resolver)
        parallel = _contains_parallel(parallel_text, target_parallel_value)
        explicitly_described_base = is_explicit_base_listing(text)
        totals = explicit_print_runs(text)
        explicit_base_ambiguous_finish = bool(
            explicitly_described_base
            and not target.insert
            and target_parallel in {"", "base"}
            and not (target.serial_total or target.print_run)
            and has_ambiguous_finish_marker(text)
            and not material_parallel_marker
            and not has_named_card_variant_marker(text, target.product_name)
            and not re.search(
                r"\b(?:red|blue|gold|green|purple|black|orange|pink|aqua|"
                r"teal|bronze|silver|white|yellow)\b|"
                r"(?:红|蓝|金|绿|紫|黑|橙|粉|青|铜|银|白|黄)(?:平行|折)",
                text,
                re.I,
            )
            and not totals
        )
        visual_confirms_ambiguous_base_finish = bool(
            (target.card_number or explicitly_described_base)
            and not target.insert
            and target_parallel in {"", "base"}
            and comp.visual_verdict == "same"
            and visual_confidence >= Decimal("0.95")
            and has_ambiguous_finish_marker(text)
            and not material_parallel_marker
        )
        unresolved_ambiguous_finish = bool(
            target_parallel in {"", "base"}
            and not (target.serial_total or target.print_run)
            and has_ambiguous_finish_marker(text)
            and not material_parallel_marker
            and not totals
            and (not target.insert or not insert)
            and (
                insert
                or card_number
                or not has_named_card_variant_marker(text, target.product_name)
            )
        )
        ambiguous_finish_softened = bool(
            unresolved_ambiguous_finish
            and not explicitly_described_base
            and not geometry_template_evidence
            and not (comp.visual_verdict == "same" and visual_confidence >= Decimal("0.95"))
        )
        target_total = target.serial_total or target.print_run
        shortened_parallel = bool(
            target_total and totals == {int(target_total)}
            and (target.card_number or target.insert)
            and shortened_refractor_color_claim(parallel_text, target_parallel_value)
        )
        intrinsic_holofoil_base = False
        if normalize_text(target.insert) == "starquest":
            star_tiers = {
                "blue": r"(?:一星|1\s*星|one\s*star)",
                "green": r"(?:二星|2\s*星|two\s*stars?)",
                "red": r"(?:三星|3\s*星|three\s*stars?)",
                "gold": r"(?:四星|4\s*星|four\s*stars?)",
            }
            expected_tier = star_tiers.get(target_parallel)
            if expected_tier and re.search(expected_tier, text, re.I):
                parallel = True
            elif expected_tier and any(
                re.search(pattern, text, re.I)
                for name, pattern in star_tiers.items()
                if name != target_parallel
            ):
                conflicts.append("parallel_conflict")
        # Base and unknown share product-intrinsic stock vocabulary, but only
        # an explicit Base identity can conflict with a named source finish.
        if target_parallel in {"", "base"} and not (target.serial_total or target.print_run):
            optic_base_insert = bool(
                "contenders optic" in normalize_text(target.product_name) and target.insert
            )
            intrinsic_refractor_insert = normalize_text(target.insert) == "retrofractor variations"
            named_parallel = bool(
                material_parallel_marker
                or re.search(
                    r"(?:\b(?:refractor|ray\s*wave|mcflurry|lava|parallel)\b|"
                    r"(?:麦旋风|熔岩|岩浆|银折|红(?:碎冰)?折|蓝(?:碎冰)?折|"
                    r"金折|绿折|紫折|"
                    r"黑折|粉折|橙折|射线折|光线折|彩虹折|"
                    r"碎闪折?|碎点折?|点点折|波纹折|棋盘折|折射))",
                    text,
                    re.IGNORECASE,
                )
                or totals
            )
            intrinsic_holofoil_base = bool(
                (
                    normalize_text(target.product_name) == "fleer showcase"
                    and target.season_start == 2000
                    and target.season_end == 2001
                    or normalize_text(target.product_name) in {"encore", "upper deck encore"}
                    and target.season_start == 1998
                    and target.season_end == 1999
                )
                and not (
                    totals
                    or re.search(
                        r"(?:\brow\s*[0-3]\b|\blegacy\b|\bmasterpiece\b|"
                        r"蓝字|红字|绿字|紫字|黑字)",
                        text,
                        re.IGNORECASE,
                    )
                )
            )
            if intrinsic_holofoil_base:
                # Every 2000-01 Fleer Showcase base card has the reflective
                # Holofoil/gold-finish face. CardHobby sellers commonly call
                # that stock “折射/金版 SP/Holofoil”; those words describe the
                # base print technology, not a separate parallel. Explicit
                # Row/Legacy/Masterpiece/colour/serial variants stay blocked.
                named_parallel = False
                ambiguous_finish_softened = False
            if visual_confirms_ambiguous_base_finish or explicit_base_ambiguous_finish:
                # Seller vocabulary such as “银折/折射” often describes
                # the base stock. An explicit Base label or a same-image result
                # can resolve that ambiguity; explicit colours, technologies,
                # named card kinds and serial totals remain hard conflicts.
                named_parallel = False
            elif named_parallel and unresolved_ambiguous_finish:
                named_parallel = False
            if insert and named_parallel:
                # Sellers often describe the chromium finish of an insert as
                # “折射/折射卡”. That generic print-finish wording is not itself
                # a distinct parallel. Explicit colours, named technologies or
                # serial numbering remain hard parallel conflicts.
                explicit_parallel = re.search(
                    r"(?:\b(?:red|blue|gold|green|purple|black|silver|bronze|"
                    r"red\s*cracked\s*ice|blue\s*cracked\s*ice|"
                    r"ray\s*wave|mcflurry|lava|parallel)\b|麦旋风|熔岩|岩浆|"
                    r"银折|红(?:碎冰)?折|蓝(?:碎冰)?折|金折|绿折|紫折|"
                    r"黑折|粉折|橙折|射线折|光线折|彩虹折|"
                    r"碎闪折?|碎点折?|点点折|波纹折|棋盘折)",
                    text,
                    re.IGNORECASE,
                )
                named_parallel = bool(explicit_parallel or material_parallel_marker or totals)
                if (
                    optic_base_insert
                    and explicit_parallel
                    and normalize_text(explicit_parallel.group(0)) in {"silver", "银折"}
                ):
                    # Contenders Optic base inserts use a silver/chrome finish;
                    # sellers commonly call that finish 银折. Actual checklist
                    # parallels are explicitly named Cracked Ice/Gold variants.
                    named_parallel = False
                if (
                    intrinsic_refractor_insert
                    and explicit_parallel
                    and normalize_text(explicit_parallel.group(0))
                    in {"silver", "silver refractor", "refractor", "银折", "银折射"}
                ):
                    # Retrofractor is itself a refractor-finish variation. Seller
                    # wording such as 银折 describes the target card, not a
                    # separate parallel; explicit colours/serials remain strict.
                    named_parallel = False
                if (
                    target.is_autograph
                    and "chrome" in normalize_text(target.product_name)
                    and explicit_parallel
                    and normalize_text(explicit_parallel.group(0))
                    in {"silver", "silver refractor", "refractor", "银折", "银折射"}
                    and not totals
                ):
                    # Chrome autograph sellers routinely describe the normal
                    # reflective stock as 银折/折射 even when the card back does
                    # not name a numbered or coloured parallel. Keep explicit
                    # colour technologies and serial totals strict.
                    named_parallel = False
            elif (
                target.insert
                and (
                    geometry_same_template
                    or (comp.visual_verdict == "same" and visual_confidence >= Decimal("0.95"))
                )
                and named_parallel
                and not material_parallel_marker
                and not (
                    totals
                    or re.search(
                        r"(?:\b(?:red|blue|gold|green|purple|black|silver|bronze|"
                        r"ray\s*wave|mcflurry|lava|parallel)\b|麦旋风|熔岩|岩浆|"
                        r"银折|红(?:碎冰)?折|蓝(?:碎冰)?折|金折|绿折|紫折|"
                        r"黑折|粉折|橙折|射线折|光线折|彩虹折|"
                        r"碎闪折?|碎点折?|点点折|波纹折|棋盘折)",
                        text,
                        re.I,
                    )
                )
            ):
                # Older foil inserts are often titled only “折射/老卡”.  A
                # high-confidence same-image verdict may treat that generic
                # finish word as the target insert's stock, but explicit
                # colours, technologies and serial totals remain conflicts.
                named_parallel = False
            # A named source finish is not a contradiction of an unknown
            # target finish. It remains pending paired-front evidence.
            if named_parallel and not unknown_finish:
                conflicts.append("parallel_conflict")
        elif target_parallel and not parallel and not shortened_parallel:
            if (
                (explicitly_described_base and not (target_total and int(target_total) in totals))
                or _contains_other_parallel(parallel_text, target_parallel_value)
                or material_parallel_marker
            ):
                conflicts.append("parallel_conflict")
        if target_parallel == 'base' and target_total and material_parallel_marker:
            conflicts.append('parallel_conflict')
        serial_total = bool(target_total and int(target_total) in totals)
        if (
            serial_total
            and target_parallel
            and target_parallel != "base"
            and not shortened_parallel
            and not _contains_other_parallel(parallel_text, target_parallel_value)
        ):
            parallel = True
        if totals and target_total and int(target_total) not in totals:
            conflicts.append("serial_total_conflict")
        original_art_singleton = (is_sketch_card(target.insert) and is_sketch_card(text) and totals == {1})
        if (unknown_finish or target_parallel == 'base') and totals and not target_total and not original_art_singleton:
            conflicts.append('target_serial_total_unknown')
        # Preserve all explicit title conflicts before supplying omitted fields.
        serial_total = serial_total or 'serial_total' in reviewed_back_fields or bool(front_serial_digest)
        parallel = parallel or 'parallel_name' in reviewed_back_fields
        child_product_shorthand_visual_confirmation = bool(
            product_status == ProductMatchStatus.UNKNOWN
            and comp.source_code == "cardhobby"
            and has_ambiguous_child_product_shorthand(text, target.product_name)
            and normalize_text(target.brand_name) == "panini"
            and re.search(r"\bpanini\b", text, re.I)
            and player
            and target_season
            and explicit_seasons == {target_season}
            and (card_number or serial_total)
            and (not target_total or serial_total)
            and (target_parallel in {"", "base"} or parallel)
            and comp.visual_verdict == "same"
            and visual_confidence >= Decimal("0.95")
            and geometry_same_template
        )
        if child_product_shorthand_visual_confirmation:
            product = True
        partial_multi_player_verified = bool(
            not player
            and has_partial_multi_player_match(text, target.player_name, resolver=self.resolver)
            and product
            and season
            and (
                geometry_same_template
                or (comp.visual_verdict == "same" and visual_confidence >= Decimal("0.95"))
            )
        )
        if partial_multi_player_verified:
            # Multi-player titles often omit one subject. Two named subjects
            # plus an independently verified printed template may establish the
            # roster; a one-name listing or merely compatible image cannot.
            player = True
        partial_roster_image_proxy = bool(
            not player
            and comp.source_code == "cardhobby"
            and len(player_roster_components(target.player_name)) >= 2
            and _contains_player(text, target.player_name, minimum_matches=1, resolver=self.resolver)
            and re.search(
                r"双人|三人|四人|多人|\bduals?\b|\bduos?\b|\btriple\b", text, re.I
            )
            and product
            and insert
            and season
            and comp.visual_verdict == "same"
            and visual_confidence >= Decimal(".95")
        )
        if partial_roster_image_proxy:
            player = True
        if not player:
            conflicts.append("player_conflict")
        parent_child_image_proxy = bool(
            product_status == ProductMatchStatus.EXPLICIT_CONFLICT
            and comp.source_code == "cardhobby"
            and named_child_product_for_parent(text, target.product_name)
            and player
            and card_number
            and insert
            and season
            and comp.visual_verdict == "same"
            and visual_confidence >= Decimal(".95")
        )
        if product_status == ProductMatchStatus.EXPLICIT_CONFLICT and not parent_child_image_proxy:
            conflicts.append("product_conflict")
        strong_visual_text_anchor = bool(
            player
            and product
            and (card_number or insert)
            and (
                comp.visual_verdict == "same"
                and visual_confidence >= Decimal("0.95")
                or geometry_template_evidence
            )
        )
        season_visual_override = bool(not season and strong_visual_text_anchor)
        visual_geometry_override = bool(
            visual_claims_different
            and strong_geometry_can_resolve_visual_disagreement(comp.visual_reasons)
            and player
            and product
            and (card_number or insert)
        )
        if visual_claims_different and not visual_geometry_override:
            conflicts.append("visual_identity_conflict")
        if not season and not season_visual_override:
            conflicts.append("season_conflict")

        player_parts = [
            part.strip()
            for part in re.split(
                r",|\s+&\s+|\s+and\s+|\s+/\s+",
                target.player_name,
                flags=re.I,
            )
            if part.strip()
        ]
        target_player_aliases = {
            normalize_text(alias)
            for part in player_parts
            for alias in (part, *seller_aliases("player", part))
            if normalize_text(alias)
        }
        target_component_names = {
            normalize_text(value)
            for value in (
                target.brand_name,
                target.product_name,
                target.set_name,
                target.insert,
            )
            if normalize_text(value)
        }
        known_other_insert = False
        if self.resolver and self.resolver.entities:
            target_resolution = self.resolver.resolve("insert", target.insert)
            # Knowledge aliases are intentionally broad and may contain names
            # that are also legitimate product/set labels (for example
            # "UD Choice").  Seeing the target's own product family in a
            # seller title must not be interpreted as evidence of a different
            # insert.  Only insert entities outside every target identity
            # component are contradictory.
            known_other_insert = any(
                match.entity_id != target_resolution.entity_id
                and _contains_insert_name(text, match.matched_alias or match.canonical_value)
                and normalize_text(match.canonical_value) != "gu jersey"
                and normalize_text(match.canonical_value) not in target_component_names
                and normalize_text(match.matched_alias or "") not in target_player_aliases
                and normalize_text(match.matched_alias or "") not in normalize_text(target.insert)
                and not (
                    explicit_insert_name
                    and _weak_insert_alias(match.matched_alias, match.canonical_value)
                    and not _contains_insert_name(text, match.canonical_value)
                )
                # A short seller-language alias can be embedded in an
                # unrelated operational phrase (e.g. 大头 in 大头预).  Once
                # the target insert is explicitly present, an alias-only
                # two-Han-character substring is not strong enough to negate
                # it.  The other insert's canonical name remains a conflict.
                and not (
                    (insert or geometry_same_template or card_number)
                    and len(str(match.matched_alias or "")) <= 2
                    and bool(re.fullmatch(r"[\u3400-\u9fff]+", str(match.matched_alias or "")))
                    and normalize_text(match.canonical_value) not in normalized
                )
                for match in self.resolver.find_in_text("insert", text)
            )
        target_legacy_insert_aliases = {
            normalize_text(alias)
            for canonical, aliases in INSERT_ALIASES.items()
            if normalize_text(canonical) == normalize_text(target.insert)
            for alias in (canonical, *aliases)
            if normalize_text(alias)
        } | {
            normalize_text(alias)
            for alias in seller_aliases("insert", target.insert)
            if normalize_text(alias)
        }
        legacy_other_insert = any(
            any(
                _contains_insert_name(text, alias)
                and normalize_text(alias) not in target_player_aliases
                and normalize_text(alias) not in target_legacy_insert_aliases
                and normalize_text(alias) not in target_component_names
                and not (
                    explicit_insert_name
                    and _weak_insert_alias(alias, canonical)
                    and not _contains_insert_name(text, canonical)
                )
                and not (
                    (insert or geometry_same_template or card_number)
                    and len(alias) <= 2
                    and bool(re.fullmatch(r"[\u3400-\u9fff]+", alias))
                    and normalize_text(canonical) not in normalized
                )
                for alias in (canonical, *aliases)
            )
            for canonical, aliases in INSERT_ALIASES.items()
            if canonical not in {"gu jersey", "treasured threads", "topps chrome autographs"}
            and normalize_text(canonical) != normalize_text(target.insert)
            and normalize_text(canonical) not in target_component_names
        )
        known_other_insert = known_other_insert or legacy_other_insert
        if normalize_text(target.insert) == "first steps" and re.search(
            r"(?:胶片|一级|二级|三级|炫彩怒吼|定装照)", text, re.I
        ):
            known_other_insert = True
        if (
            target.insert
            and not insert
            and has_explicit_named_card_variant_claim(text, target.product_name, target.player_name)
        ):
            # The global insert dictionary will never be exhaustive. A proper
            # named Chinese/English ``... 特卡`` claim is still an explicit
            # different card kind when the requested insert itself is absent.
            known_other_insert = True
        target_is_unidentified_base_kind = bool(
            not target.insert
            and target_parallel in {"", "base"}
            and not (target.serial_total or target.print_run)
        )
        if (
            target_is_unidentified_base_kind
            and (target.card_number or target_parallel == "base")
            and has_explicit_named_card_variant_claim(text, target.product_name, target.player_name)
        ):
            # A title can identify a named insert even when that insert has not
            # reached the catalog/recognition payload.  Player + season + product
            # is therefore not enough to treat it as the unspecified base card.
            known_other_insert = True
        if known_other_insert:
            conflicts.append("insert_conflict")
        if conflicts:
            return Classification(
                ComparableTier.REJECTED,
                Decimal("0"),
                comp.source_reliability,
                hard_conflicts=conflicts,
            )

        fields = {
            "finish_evidence_status": ('paired_front_verified' if paired_finish else 'pending')
                if unknown_finish else 'named_or_base',
            "player": player,
            "partial_multi_player_visual_confirmation": partial_multi_player_verified,
            "season": season,
            "product": product if product else None,
            "reviewed_parent_product_confirmation": reviewed_product,
            "reviewed_source_back_identity": reviewed_back,
            "child_product_shorthand_visual_confirmation": child_product_shorthand_visual_confirmation,
            "card_number": card_number,
            "insert": insert,
            "parallel": parallel,
            "serial_total": serial_total,
            "grade": True,
            "visual": (
                comp.visual_verdict in {"same", "compatible"}
                if comp.visual_verdict is not None
                else None
            ),
            "season_visual_override": season_visual_override,
            "visual_geometry_override": visual_geometry_override,
            "ambiguous_finish_softened": ambiguous_finish_softened,
        }
        serial_requirement_met = target_total is None or serial_total
        if front_serial_digest:
            fields['reviewed_front_serial_sha256'] = front_serial_digest
        if reference_product:
            fields['reviewed_reference_product_confirmation'] = True
            fields['reviewed_reference_product_evidence'] = reference_product
        if reviewed_back_fields:
            from card_platform.valuation_trace import content_hash
            # Freeze the exact sealed supplement into stored classifications
            # and publication drafts. Serialized hashes confer no authority.
            fields['reviewed_source_back_identity_sha256'] = content_hash(
                comp.visual_facts._reviewed_proof.source_back_json.encode())
        visual_chrome_auto_base_finish = bool(
            target_parallel in {"silver", "silver refractor", "refractor"}
            and target.is_autograph
            and "chrome" in normalize_text(target.product_name)
            and not totals
            and not _contains_other_parallel(parallel_text, target_parallel_value)
            and comp.visual_verdict == "same"
            and visual_confidence >= Decimal("0.90")
        )
        parallel_requirement_met = (
            target_parallel in {"", "base"} or parallel or visual_chrome_auto_base_finish
        )
        if parallel_text != text:
            # Marketplace terminology is a scoped naming clue, not proof of
            # the source's physical surface. Require the same current paired
            # image authority used for unknown finishes before granting T0/T1.
            parallel_requirement_met = parallel_requirement_met and paired_finish
            fields['scoped_parallel_term_used'] = True
        # A current independent paired-original review can establish the same
        # finish when its name is omitted. All explicit conflicts above still
        # reject; neither model confidence nor geometry alone grants authority.
        reviewed_named_finish = bool(
            target_parallel not in {"", "base"} and not unknown_finish and not parallel
            and paired_finish and comp.visual_facts._reviewed_proof is not None
            and comp.visual_verdict == "same" and visual_confidence >= Decimal(".95")
            and player and season and product and (card_number or insert)
            and serial_requirement_met
        )
        if reviewed_named_finish:
            from card_platform.valuation_trace import content_hash
            parallel_requirement_met = True
            fields["reviewed_named_finish_omission"] = True
            fields["reviewed_named_finish_sha256"] = content_hash(
                comp.visual_facts._reviewed_proof.review_json.encode())
        if unknown_finish:
            parallel_requirement_met = paired_finish
        identity_anchor = insert if target.insert else card_number or serial_total
        visual_exact_template = geometry_same_template
        explicit_base_title = is_explicit_base_listing(text)
        if target.insert and explicit_base_title and not insert:
            conflicts.append("insert_conflict")
            return Classification(
                ComparableTier.REJECTED,
                Decimal("0"),
                comp.source_reliability,
                hard_conflicts=conflicts,
            )
        visual_base_anchor = bool(
            target.card_number
            and not target.insert
            and target_parallel in {"", "base"}
            and target_total is None
            and comp.visual_verdict in {"same", "compatible"}
            and visual_confidence >= Decimal("0.90")
        )
        reviewed_number_anchor = reviewed_unnumbered_card_anchor(
            target, text, comp.visual_facts, resolver=self.resolver,
        )
        fields["reviewed_catalog_number_anchor"] = reviewed_number_anchor
        visual_explicit_base_anchor = bool(
            visual_base_anchor
            # Numeric IDs are often omitted even without the word "base".
            # Measured geometry plus a same-version image decision can supply
            # that missing anchor; text alone and explicit variants cannot.
            and (explicit_base_title or intrinsic_holofoil_base or geometry_same_template
                 or reviewed_number_anchor)
            and player
            and season
            and product
            and comp.visual_verdict == "same"
            and visual_confidence >= Decimal("0.95")
        )
        visual_numbered_anchor = bool(
            target_total
            and serial_total
            and player
            and season
            and product
            and parallel_requirement_met
            and (comp.visual_verdict == "same" or visual_exact_template)
            and visual_confidence >= Decimal("0.90")
        )
        visual_insert_anchor = bool(
            target.insert
            and player
            and season
            and product
            and parallel_requirement_met
            and serial_requirement_met
            and (
                (visual_exact_template and (target.is_autograph or target.is_memorabilia))
                or (
                    comp.visual_verdict == "same"
                    and (
                        target.is_autograph
                        or target.is_memorabilia
                        or visual_confidence >= Decimal("0.95")
                    )
                    and not (
                        explicit_base_title
                        and not target.is_autograph
                        and not target.is_memorabilia
                    )
                )
            )
            and visual_confidence >= Decimal("0.90")
        )
        visual_typed_card_anchor = bool(
            target.card_number
            and not target.insert
            and (target.is_autograph or target.is_memorabilia)
            and player
            and season
            and product
            and parallel_requirement_met
            and serial_requirement_met
            and comp.visual_verdict == "same"
            and visual_confidence >= Decimal("0.90")
        )
        # Seller titles often omit numeric checklist IDs on unnumbered
        # parallels. Require both an explicit matching finish and independent
        # high-confidence same-version imagery + measured template geometry.
        # Keep this T1: it is not a photographed candidate-back card number.
        visual_parallel_anchor = bool(
            target.card_number and not target.insert and target_total is None
            and target_parallel not in {"", "base"} and parallel
            and player and season and product
            and comp.visual_verdict == "same" and visual_confidence >= Decimal("0.95")
            and geometry_same_template
        )
        fields["visual_same_template_anchor"] = (not unknown_finish or paired_finish) and bool(
            visual_numbered_anchor
            or visual_insert_anchor
            or visual_typed_card_anchor
            or visual_explicit_base_anchor
            or visual_chrome_auto_base_finish
            or visual_parallel_anchor
            or reviewed_named_finish
            or ((unknown_finish or reviewed_product or reviewed_back or reference_product) and paired_finish and player and product and season
                and (card_number or insert) and serial_requirement_met)
        )
        if (
            product
            and (
                identity_anchor
                or visual_numbered_anchor
                or visual_insert_anchor
                or visual_typed_card_anchor
                or visual_explicit_base_anchor
            )
            and parallel_requirement_met
            and serial_requirement_met
        ):
            tier, similarity = ComparableTier.T0, Decimal("1.00")
        elif product and (visual_base_anchor or visual_parallel_anchor):
            # A high-confidence same-template image can identify an unnumbered
            # seller title for a plain base card, but remains below textual T0:
            # front imagery alone cannot prove every catalog attribute.
            tier, similarity = ComparableTier.T1, Decimal("0.86")
        elif product and insert and (card_number or target.card_number is None):
            tier, similarity = ComparableTier.T1, Decimal("0.82")
        elif player and season and product_status != ProductMatchStatus.EXPLICIT_CONFLICT:
            tier, similarity = (
                ComparableTier.T2,
                Decimal("0.60") if product else Decimal("0.50"),
            )
        else:
            tier, similarity = ComparableTier.T3, Decimal("0.42")
        if parallel_text != text and not paired_finish and tier in {ComparableTier.T0, ComparableTier.T1}:
            tier, similarity = ComparableTier.T2, min(similarity, Decimal('.55'))
            fields['scoped_parallel_visual_pending'] = True
        if ambiguous_finish_softened and tier in {
            ComparableTier.T0,
            ComparableTier.T1,
        }:
            tier, similarity = ComparableTier.T2, Decimal("0.55")
        if (
            comp.visual_verdict == "different"
            and visual_confidence >= Decimal("0.55")
            and tier == ComparableTier.T0
        ):
            # A low-resolution reference can still reveal large layout/pose
            # differences, but it is not strong enough to hard-reject a textually
            # exact sale. Demote it from exact-card evidence instead.
            tier, similarity = ComparableTier.T1, Decimal("0.78")
        if tier == ComparableTier.T0 and (season_visual_override or visual_geometry_override):
            tier, similarity = ComparableTier.T1, Decimal("0.78")
        if tier == ComparableTier.T0 and (
            comp.visual_verdict == "uncertain"
            or (
                comp.visual_verdict == "compatible"
                and (bool(target_parallel) or visual_confidence < Decimal("0.90"))
            )
            or (comp.visual_verdict == "same" and visual_confidence < Decimal("0.75"))
        ):
            # An attempted image check that cannot establish the exact card must
            # not silently inherit text-only T0 status. This is essential for
            # visually dense products such as Mosaic and Prizm.
            tier, similarity = ComparableTier.T1, Decimal("0.78")
        if partial_roster_image_proxy:
            fields["partial_roster_image_verified_proxy"] = True
            tier, similarity = ComparableTier.T2, Decimal(".55")
        if parent_child_image_proxy:
            fields["parent_child_image_verified_proxy"] = True
            fields["product_lineage_unverified"] = True
            tier, similarity = ComparableTier.T2, Decimal(".55")
        if attribute_omissions and attributes_image_confirmed:
            fields['image_confirmed_attribute_omissions'] = attribute_omissions
            if tier == ComparableTier.T0:
                tier, similarity = ComparableTier.T1, min(similarity, Decimal('.86'))
        if (child_product_shorthand_visual_confirmation or reviewed_product or reference_product) and tier == ComparableTier.T0:
            # Image-confirmed lineage remains below an explicitly named child.
            tier, similarity = ComparableTier.T1, Decimal(".86")
        if (tier == ComparableTier.T0 and visual_explicit_base_anchor and not card_number
                and (reviewed_number_anchor or (not explicit_base_title and not intrinsic_holofoil_base))):
            # Geometry supplies the omitted numeric anchor, not a photographed
            # candidate-back number or complete reverse-side identity.
            fields["card_number_image_supported_only"] = True
            tier, similarity = ComparableTier.T1, Decimal(".86")
        if unknown_finish and not paired_finish:
            tier, similarity = ComparableTier.T2, min(similarity, Decimal('.45'))
        if (reviewed_back or reviewed_work or front_serial_digest or reviewed_named_finish) and tier == ComparableTier.T0:
            tier, similarity = ComparableTier.T1, min(similarity, Decimal('.86'))
        return Classification(tier, similarity, comp.source_reliability, fields)
