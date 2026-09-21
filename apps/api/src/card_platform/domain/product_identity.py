"""Shared marketplace normalization for product-family labels."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from enum import StrEnum


MANUFACTURER_PREFIXES = (
    "panini",
    "topps",
    "upper deck",
    "fleer",
    "skybox",
)
# This service currently prices basketball cards.  Seller titles routinely omit
# that catalog suffix, but an explicitly different sport is identity evidence
# and must never be normalized away.
OPTIONAL_SPORT_SUFFIXES = ("basketball",)
EXPLICIT_SPORTS = ("basketball", "football", "baseball", "hockey", "soccer")

# A parent product's short seller name is useful only while it does not cross
# into a separately checklisted child product. These are product-taxonomy
# rules, not card/player exceptions.
PRODUCT_CHILD_FAMILIES: dict[str, tuple[str, ...]] = {
    "donruss": (
        "donruss optic",
        "donruss elite",
        "donruss draft picks",
        "chronicles donruss",
    ),
    "hoops": (
        "hoops draft picks",
        "chronicles hoops",
        "hoops winter",
        "nba hoops winter",
        "hoops holiday",
        "nba hoops holiday",
        "premium stock",
        "nba hoops premium stock",
        "hoops premium stock",
    ),
    "mosaic": ("mosaic draft picks", "chronicles mosaic"),
    "prizm": (
        "prizm draft picks",
        "prizm deca",
    ),
    "contenders": ("contenders optic", "contenders draft picks"),
    "select": ("select draft picks",),
    "chronicles": (
        "chronicles draft picks",
        "chronicles flux draft picks",
        "chronicles hoops",
        "chronicles donruss",
        "chronicles mosaic",
    ),
    "topps chrome": (
        "topps chrome black",
        "topps chrome update",
        "topps chrome updates",
        "topps cosmic chrome",
        "topps chrome cosmic",
        "topps chrome sapphire",
        "topps chrome logofractor",
        "topps chrome sonic",
    ),
    "topps basketball": (
        "topps chrome",
        "topps signature class",
        "topps finest",
        "topps cosmic chrome",
        "topps three",
        "topps 3",
        "topps bowman",
    ),
}

# A child product may be shortened to its parent family in seller titles.  The
# shorthand is useful recall evidence, but it cannot prove the exact child.  In
# particular, Chinese sellers commonly write only ``编年史`` for one of the
# separately checklisted Chronicles sub-products.
PRODUCT_PARENT_SHORTHANDS: dict[str, tuple[str, ...]] = {
    "chronicles": ("chronicles", "panini chronicles", "编年史"),
}

# Labels this broad identify the sport/category, not a product family.  They
# are useful discovery terms, but accepting them as deterministic product
# evidence lets every product in the sport cross-match every other product.
LOW_INFORMATION_PRODUCT_LABELS = {
    "basketball",
    "basketball cards",
    "nba",
    "nba basketball",
    "sports cards",
    "trading cards",
    "panini",
    "panini basketball",
    "topps basketball cards",
    "upper deck basketball",
}


class ProductMatchStatus(StrEnum):
    MATCH = "match"
    UNKNOWN = "unknown"
    EXPLICIT_CONFLICT = "explicit_conflict"

# Deterministic match aliases are shared by ingestion and valuation. Discovery
# may use broader terms, but a row that passes this table must receive the same
# product decision in every downstream stage.
PRODUCT_MATCH_ALIASES: dict[str, tuple[str, ...]] = {
    "marvel spider man": ("Marvel Spider-Man", "Marvel (MCU) Spider-Man"),
    "goodwin champions": ("Goodwin Champions", "古德温"),
    "nba hoops basketball": ("nba hoops", "hoops"),
    "nba hoops": ("nba hoops", "hoops"),
    "topps three": ("topps three", "topps 3"),
    "topps 3": ("topps three", "topps 3"),
    "topps basketball": ("topps basketball", "topps flagship", "flagship"),
    "flagship": ("topps basketball", "topps flagship", "flagship"),
    "mosaic basketball": (
        "panini mosaic",
        "mosaic basketball",
        "mosaic",
        "马赛克",
    ),
    "mosaic": ("panini mosaic", "mosaic basketball", "mosaic", "马赛克"),
    "prizm basketball": (
        "panini prizm",
        "prizm basketball",
        "prizm",
        "prizm系列",
    ),
    "prizm": ("panini prizm", "prizm basketball", "prizm", "prizm系列"),
    "optic basketball": ("panini donruss optic", "donruss optic", "杜蕾斯 op"),
    "donruss optic basketball": (
        "panini donruss optic",
        "donruss optic",
        "杜蕾斯 op",
    ),
    "select basketball": ("panini select", "select basketball", "select系列"),
    "national treasures basketball": ("national treasures", "panini 国宝", "国宝"),
    "crown royale basketball": ("crown royale basketball", "crown royale", "皇冠"),
    "topps chrome basketball": ("topps chrome",),
    "topps chrome updates basketball": ("topps chrome update", "topps chrome updates", "tcu"),
    "topps chrome update": ("topps chrome update", "topps chrome updates", "tcu"),
    "topps signature class basketball": ("topps signature class", "signature class"),
    "topps signature class": ("topps signature class", "signature class"),
    "upper deck draft edition": ("upper deck draft edition", "ud draft edition", "draft edition"),
    "2009 10 draft edition": ("upper deck draft edition", "ud draft edition", "draft edition"),
    "ud choice": ("ud choice", "upper deck choice"),
    "upper deck reserve": ("upper deck reserve", "ud reserve"),
    "hardcourt": ("upper deck hardcourt", "hardcourt"),
    "revolution basketball": ("panini revolution", "revolution", "革命"),
    "preferred basketball": ("panini preferred", "preferred"),
    "origins basketball": ("panini origins", "origins basketball", "origins", "起源"),
    "cosmic chrome basketball": ("topps cosmic chrome", "topps chrome cosmic", "cosmic chrome"),
    "signature series basketball": ("panini signature series", "signature series", "签字系列"),
    "immaculate collection basketball": ("panini immaculate", "immaculate collection", "immaculate", "爱咪咪"),
    "donruss basketball": (
        "panini donruss",
        "donruss basketball",
        "donruss",
        "杜蕾斯",
    ),
    "one and one basketball": ("one and one", "one one", "oao", "壹和壹"),
    "one one basketball": ("one and one", "one one", "oao", "壹和壹"),
    "court kings basketball": ("panini court kings", "court kings", "油画"),
    "chronicles draft picks": ("panini chronicles draft picks", "chronicles draft picks", "panini chronicles", "编年史"),
    "chronicles flux draft picks": ("panini chronicles flux draft picks", "chronicles flux draft picks", "chronicles flux", "flux draft picks"),
    "fleer showcase": ("fleer showcase", "flair showcase", "showcase"),
    "flair showcase": ("fleer showcase", "flair showcase", "showcase"),
    "mj s back": ("mj s back", "mjs back", "银标"),
    "premium stock basketball": (
        "nba hoops premium stock",
        "hoops premium stock",
        "premium stock basketball",
        "premium stock",
    ),
    "photogenic basketball": (
        "panini photogenic",
        "photogenic basketball",
        "photogenic",
        "上镜",
    ),
    "complete basketball": (
        "panini complete",
        "complete basketball",
        "complete",
    ),
}

# Some marketplaces use a phrase containing ``套卡`` as the conventional name
# of one card from a product. Keep those audited phrases in product taxonomy so
# the generic lot detector does not need card/player exceptions. Unlisted
# ``...套卡`` phrases remain multi-card listings.
PRODUCT_SINGLE_CARD_SHORTHANDS: dict[str, tuple[str, ...]] = {
    "mj s back": ("银标套卡",),
}


def normalize_product_text(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", folded).strip()


def _product_comparison_text(value: object) -> str:
    """Ignore an optional sport word between product and subtype tokens.

    Sellers write both ``Chrome Update Basketball`` and ``Chrome Basketball
    Update``. Only the optional basketball token can move or disappear; the
    Update/Sapphire/Optic qualifier and any other sport remain significant.
    Explicit sport conflicts are checked against the original text before
    product matching.
    """
    tokens = normalize_product_text(value).split()
    product_tokens = [token for token in tokens if token not in OPTIONAL_SPORT_SUFFIXES]
    return " ".join(product_tokens or tokens)


def product_label_variants(value: object) -> tuple[str, ...]:
    """Return catalog-equivalent labels with optional maker/sport wrappers.

    Marketplace titles routinely omit ``Panini`` and ``Basketball`` while the
    catalog keeps one or both. Internal family qualifiers such as ``Optic`` or
    ``Elite`` are never removed.
    """
    original = normalize_product_text(value)
    if not original:
        return ()
    values = [original]
    without_release_year = re.sub(
        r"^(?:19|20)\d{2}(?:\s+(?:(?:19|20)\d{2}|\d{2}))?\s+",
        "",
        original,
    )
    if without_release_year and without_release_year != original:
        values.append(without_release_year)
    for candidate in list(values):
        for maker in MANUFACTURER_PREFIXES:
            if candidate.startswith(f"{maker} "):
                values.append(candidate[len(maker) + 1 :])
                break
    values.extend(_product_comparison_text(candidate) for candidate in list(values))
    bases = list(values)
    for candidate in bases:
        removed_sport = False
        for sport in OPTIONAL_SPORT_SUFFIXES:
            if candidate.endswith(f" {sport}"):
                values.append(candidate[: -(len(sport) + 1)])
                removed_sport = True
                break
        if not removed_sport:
            values.append(f"{candidate} basketball")
    return tuple(dict.fromkeys(value for value in values if value))


def aliases_from_registry(
    value: object,
    registry: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    """Resolve aliases despite optional catalog maker/sport wrappers."""
    target_variants = set(product_label_variants(value))
    if not target_variants:
        return ()
    for key, aliases in registry.items():
        if target_variants & set(product_label_variants(key)):
            return tuple(aliases)
    return ()


def _alias_occurs_at_token_boundary(text: object, alias: object) -> bool:
    """Match a normalized alias without requiring whitespace at script changes.

    CardHobby titles commonly concatenate Latin product names with Chinese
    descriptions (``Showcase折射`` or ``Prizm艾弗森``).  A Latin/Han
    transition is a real token boundary, while two adjacent Latin letters or
    digits are not: ``Prizm`` must never match ``Prizmatic``.
    """
    normalized_text = _product_comparison_text(text)
    normalized_alias = _product_comparison_text(alias)
    if normalized_alias in MANUFACTURER_PREFIXES:
        # "Topps Basketball" is a flagship label. Removing the sport must
        # not let a maker-only title assert that specific product family.
        normalized_text = normalize_product_text(text)
        normalized_alias = normalize_product_text(alias)
    if not normalized_text or not normalized_alias:
        return False
    compact_alias = normalized_alias.replace(" ", "")
    if (
        len(compact_alias) >= 2
        and re.fullmatch(r"[\u3400-\u9fff]+", compact_alias)
    ):
        # Reviewed Chinese product aliases are routinely glued to card-type
        # prose (国宝卡签、油画特卡、马赛克折). Han has no whitespace token
        # boundary, so requiring one systematically breaks marketplace recall.
        return compact_alias in normalized_text.replace(" ", "")

    def character_class(character: str) -> str:
        if character.isascii() and character.isalnum():
            return "ascii_word"
        if "\u3400" <= character <= "\u9fff":
            return "han"
        return "separator"

    start = 0
    while (index := normalized_text.find(normalized_alias, start)) >= 0:
        end = index + len(normalized_alias)
        left_class = character_class(normalized_alias[0])
        right_class = character_class(normalized_alias[-1])
        left_ok = index == 0 or character_class(normalized_text[index - 1]) not in {
            left_class,
        }
        right_ok = end == len(normalized_text) or character_class(
            normalized_text[end]
        ) not in {right_class}
        if left_ok and right_ok:
            return True
        start = index + 1
    return False


def _context_confirms_child_product(
    parent: str,
    child: str,
    target_context: Sequence[object],
) -> bool:
    """Require an explicit child-family qualifier in the target identity."""
    parent_tokens = set(normalize_product_text(parent).split())
    child_tokens = normalize_product_text(child).split()
    qualifier = " ".join(token for token in child_tokens if token not in parent_tokens)
    terms = tuple(
        dict.fromkeys(
            term
            for term in (normalize_product_text(child), qualifier)
            if term and term not in {"base", "basketball"}
        )
    )
    return any(
        _alias_occurs_at_token_boundary(context, term)
        for context in target_context
        if normalize_product_text(context)
        for term in terms
    )


def has_product_child_conflict(
    title: object,
    target_product: object,
    *,
    target_set: object = None,
    target_insert: object = None,
    target_parallel: object = None,
) -> bool:
    """Prevent a parent-product alias from accepting a distinct child set."""
    normalized_title = _product_comparison_text(title)
    target_variants = set(product_label_variants(target_product))
    # Only an explicit set hierarchy may confirm a child product. Insert and
    # parallel words can coincidentally contain names such as Elite or Optic;
    # allowing those roles to override product lineage cross-contaminates sets.
    target_context = (target_set,)
    for parent, children in PRODUCT_CHILD_FAMILIES.items():
        if parent not in target_variants:
            continue
        for child in children:
            # Child detection stays deliberately conservative. Marketplace
            # sellers sometimes concatenate two Latin tokens (for example
            # ``Chrome UpdatesShaedon``); failing to notice ``Chrome Updates``
            # would let a parent ``Topps Chrome`` target cross product lines.
            if child in normalized_title and not (
                _context_confirms_child_product(parent, child, target_context)
            ):
                return True
        if parent == "donruss" and re.search(r"杜蕾斯\s*op\b", normalized_title, re.I):
            if not _context_confirms_child_product(
                parent, "donruss optic", target_context
            ):
                return True
        if parent == "hoops" and re.search(
            r"(?:\bwinter\b|\bholiday\b|冬季|雪花)", normalized_title, re.I
        ):
            child = "hoops holiday" if re.search(
                r"(?:\bholiday\b|雪花)", normalized_title, re.I
            ) else "hoops winter"
            if not _context_confirms_child_product(parent, child, target_context):
                return True
    return False


def named_child_product_for_parent(title: object, target_product: object) -> str | None:
    """Return a named child for discovery only; never equate product families."""
    target_variants = set(product_label_variants(target_product))
    normalized_title = _product_comparison_text(title)
    for parent, children in PRODUCT_CHILD_FAMILIES.items():
        if parent not in target_variants or any(child in target_variants for child in children):
            continue
        matches = sorted((child for child in children if child in normalized_title), key=len, reverse=True)
        if matches and all(other in matches[0] for other in matches):
            return matches[0]
    return None


def _same_product_family(left: object, right: object) -> bool:
    left_labels = {
        *product_label_variants(left),
        *(
            normalize_product_text(alias)
            for alias in aliases_from_registry(left, PRODUCT_MATCH_ALIASES)
        ),
    }
    right_labels = {
        *product_label_variants(right),
        *(
            normalize_product_text(alias)
            for alias in aliases_from_registry(right, PRODUCT_MATCH_ALIASES)
        ),
    }
    return bool(left_labels & right_labels)


def _is_parent_label_for_target(parent_label: object, target_product: object) -> bool:
    parent_variants = set(product_label_variants(parent_label))
    target_variants = set(product_label_variants(target_product))
    return any(
        parent in parent_variants
        and any(child in target_variants for child in children)
        for parent, children in PRODUCT_CHILD_FAMILIES.items()
    )


def _is_parent_shorthand_for_target(label: object, target_product: object) -> bool:
    """Return broad seller shorthand for a separately checklisted child."""
    normalized_label = normalize_product_text(label)
    target_variants = set(product_label_variants(target_product))
    if not normalized_label or not target_variants:
        return False
    return any(
        any(child in target_variants for child in children)
        and normalized_label
        in {
            normalize_product_text(alias)
            for alias in PRODUCT_PARENT_SHORTHANDS.get(parent, (parent,))
        }
        for parent, children in PRODUCT_CHILD_FAMILIES.items()
    )


def _label_is_target_role(label: object, target_roles: Sequence[object]) -> bool:
    """Keep a product-like insert/parallel token ambiguous across entity roles.

    ``Prizm`` can be a product or, for example, the named parallel of a Flux
    card.  The bare token therefore cannot prove another product when the
    target identity already assigns it to a different role.  A fully qualified
    label such as ``Panini Prizm`` does not occur in the target role and remains
    explicit product-conflict evidence.
    """
    normalized_label = normalize_product_text(label)
    if not normalized_label:
        return False

    def is_unqualified(alias: object) -> bool:
        normalized = normalize_product_text(alias)
        return (
            bool(normalized)
            and not any(
                normalized.startswith(f"{maker} ")
                for maker in MANUFACTURER_PREFIXES
            )
            and not normalized.endswith(" basketball")
            and not normalized.endswith("系列")
        )

    for role in target_roles:
        normalized_role = normalize_product_text(role)
        if not normalized_role:
            continue
        role_aliases = {normalized_role}
        # A target role may use the English taxonomy label while a Chinese
        # seller uses its reviewed shorthand (Mosaic/马赛克).  Expand only
        # bare aliases: manufacturer- or sport-qualified phrases still make an
        # explicit product assertion and must remain hard conflicts.
        for canonical, aliases in PRODUCT_MATCH_ALIASES.items():
            bare_aliases = {
                normalize_product_text(alias)
                for alias in (canonical, *aliases)
                if is_unqualified(alias)
            }
            if any(
                _alias_occurs_at_token_boundary(normalized_role, alias)
                for alias in bare_aliases
            ):
                role_aliases.update(bare_aliases)
        if normalized_label in role_aliases:
            return True
    return False


def _has_explicit_other_product_label(
    title: object,
    target_product: object,
    *,
    strong_only: bool,
    target_roles: Sequence[object] = (),
) -> bool:
    """Find another reviewed product label without treating finish words as sets."""
    labels: list[tuple[str, object]] = []
    for canonical, aliases in PRODUCT_MATCH_ALIASES.items():
        labels.extend((canonical, alias) for alias in (canonical, *aliases))
    for children in PRODUCT_CHILD_FAMILIES.values():
        labels.extend((child, child) for child in children)
    for canonical, label in labels:
        normalized_label = normalize_product_text(label)
        if not normalized_label or normalized_label in LOW_INFORMATION_PRODUCT_LABELS:
            continue
        if _same_product_family(canonical, target_product):
            continue
        if _is_parent_label_for_target(canonical, target_product):
            continue
        if _is_parent_shorthand_for_target(label, target_product):
            continue
        ascii_tokens = re.findall(r"[a-z0-9]+", normalized_label)
        han = "".join(re.findall(r"[\u3400-\u9fff]", normalized_label))
        # Short Chinese marketplace nicknames are useful in the *target*
        # direction (油画特卡, 上镜新秀), but the same two characters are also
        # ordinary prose (油画风格, 上镜效果).  They therefore cannot, by
        # themselves, prove that a listing belongs to a different product.
        # A reviewed canonical/multi-token Latin label or a more specific Han
        # alias remains hard conflict evidence.
        if han and not ascii_tokens and len(han) <= 2:
            continue
        if strong_only and len(ascii_tokens) < 2 and len(han) < 3:
            continue
        if _alias_occurs_at_token_boundary(title, label):
            # If an optional sport word was omitted by the seller, a registry
            # alias cannot supply that missing product specificity. Preserve
            # bare target-role ambiguity, while an actually printed maker or
            # sport qualifier still establishes a different product.
            matched_label = (
                label
                if normalized_label in normalize_product_text(title)
                else _product_comparison_text(label)
            )
            if _label_is_target_role(matched_label, target_roles):
                continue
            return True
    return False


def _target_alias_occurs(title: object, alias: object) -> bool:
    """Match a reviewed target alias while filtering descriptive Han prose."""
    if not _alias_occurs_at_token_boundary(title, alias):
        return False
    normalized_alias = normalize_product_text(alias).replace(" ", "")
    if not re.fullmatch(r"[\u3400-\u9fff]{2}", normalized_alias):
        return True
    original_title = str(title or "")
    allowed_glued_roles = (
        "卡签",
        "特卡",
        "新秀",
        "签字",
        "签名",
        "切割",
        "套卡",
        "系列",
    )
    for match in re.finditer(re.escape(normalized_alias), original_title):
        left = original_title[match.start() - 1 : match.start()]
        suffix = original_title[match.end() :]
        # A two-Han alias embedded in longer prose is descriptive, not a
        # product assertion (实卡很上镜、革命性设计、油画般风格).
        if left and re.fullmatch(r"[\u3400-\u9fff]", left):
            continue
        if suffix and re.match(r"[\u3400-\u9fff]", suffix):
            if not suffix.startswith(allowed_glued_roles):
                continue
            # 银标 is also the ordinary description of a silver parallel. Its
            # audited product phrase is 银标套卡; bare 银标特卡 remains unknown.
            if normalized_alias == "银标" and not suffix.startswith("套卡"):
                continue
        return True
    return False


def _alias_is_used_as_parallel_finish(title: object, alias: object) -> bool:
    normalized_alias = normalize_product_text(alias)
    if not normalized_alias or len(normalized_alias.split()) != 1:
        return False
    escaped = re.escape(normalized_alias)
    normalized_title = normalize_product_text(title)
    return bool(
        re.search(
            rf"(?:red|blue|gold|green|purple|black|orange|pink|silver|"
            rf"红|蓝|金|绿|紫|黑|橙|粉|银)\s*{escaped}\s*"
            rf"(?:折|refractor|parallel|prizm|foil|holo)",
            normalized_title,
            re.I,
        )
    )


def assess_product_match(
    title: object,
    target_product: object,
    *,
    target_set: object = None,
    target_insert: object = None,
    target_parallel: object = None,
) -> ProductMatchStatus:
    """Distinguish a match, omitted/unknown product, and explicit conflict."""
    normalized_target = normalize_product_text(target_product)
    normalized_title = normalize_product_text(title)
    if not normalized_target:
        return ProductMatchStatus.UNKNOWN
    if normalized_target in LOW_INFORMATION_PRODUCT_LABELS:
        return ProductMatchStatus.UNKNOWN
    target_sports = {
        sport for sport in EXPLICIT_SPORTS if sport in normalized_target.split()
    }
    title_sports = {
        sport for sport in EXPLICIT_SPORTS if sport in normalized_title.split()
    }
    if target_sports and title_sports and target_sports.isdisjoint(title_sports):
        return ProductMatchStatus.EXPLICIT_CONFLICT
    if has_product_child_conflict(
        title,
        target_product,
        target_set=target_set,
        target_insert=target_insert,
        target_parallel=target_parallel,
    ):
        return ProductMatchStatus.EXPLICIT_CONFLICT
    if "topps chrome" in normalized_target and any(
        token in normalized_target for token in ("mcdonald", "all american", "麦当劳", "全美")
    ):
        return ProductMatchStatus.MATCH if (
            "topps chrome" in normalized_title and any(
            token in normalized_title for token in ("mcdonald", "all american", "麦当劳", "全美")
            )
        ) else ProductMatchStatus.UNKNOWN
    if normalized_target in {"bowman rookies and stars", "bowman rookies stars"}:
        children = (
            "bowman s best", "bowman best", "bowman chrome", "bowman draft",
            "bowman signature", "bowman sterling",
        )
        return ProductMatchStatus.MATCH if (
            "bowman" in normalized_title
            and not any(child in normalized_title for child in children)
        ) else ProductMatchStatus.EXPLICIT_CONFLICT if any(
            child in normalized_title for child in children
        ) else ProductMatchStatus.UNKNOWN
    if normalized_target in {"bowman basketball", "2025 26 bowman basketball"}:
        bowman_children = ("bowman s best", "bowman best", "bowman sapphire")
        return ProductMatchStatus.MATCH if (
            "bowman" in normalized_title
            and not any(child in normalized_title for child in bowman_children)
        ) else ProductMatchStatus.EXPLICIT_CONFLICT if any(
            child in normalized_title for child in bowman_children
        ) else ProductMatchStatus.UNKNOWN
    aliases = aliases_from_registry(target_product, PRODUCT_MATCH_ALIASES)
    generic_wrappers = {"basketball", "panini", "topps", "fleer", "skybox"}
    candidates = aliases or tuple(
        dict.fromkeys(
            [
                normalized_target,
                *(
                    variant
                    for variant in product_label_variants(target_product)
                    if variant not in generic_wrappers
                ),
            ]
        )
    )
    matched_candidates = [
        candidate
        for candidate in candidates
        if normalize_product_text(candidate)
        and _target_alias_occurs(title, candidate)
    ]
    if matched_candidates:
        # A fully named other family outranks a target word only when every
        # target hit is being used as a finish (``Topps Finest ... 金马赛克折``
        # is not a Mosaic-product listing).  When the title independently names
        # the target product, child/set vocabulary such as Elite, Premium Stock,
        # or Cosmic Chrome must not turn that positive match into a conflict.
        if all(
            _alias_is_used_as_parallel_finish(title, candidate)
            for candidate in matched_candidates
        ) and _has_explicit_other_product_label(
            title,
            target_product,
            strong_only=True,
            target_roles=(target_set, target_insert, target_parallel),
        ):
            return ProductMatchStatus.EXPLICIT_CONFLICT
        return ProductMatchStatus.MATCH

    # A title that simply omits the product (or uses an unreviewed nickname) is
    # uncertainty, not proof of a different product. Only another reviewed
    # canonical product label creates a hard conflict. Parent shorthand for a
    # known child target remains UNKNOWN because sellers routinely omit the
    # child qualifier.
    if _has_explicit_other_product_label(
        normalized_title,
        target_product,
        strong_only=False,
        target_roles=(target_set, target_insert, target_parallel),
    ):
        return ProductMatchStatus.EXPLICIT_CONFLICT
    return ProductMatchStatus.UNKNOWN


def product_matches_title(
    title: object,
    target_product: object,
    *,
    target_set: object = None,
    target_insert: object = None,
    target_parallel: object = None,
) -> bool:
    """Backward-compatible boolean view of the shared tri-state decision."""
    return assess_product_match(
        title,
        target_product,
        target_set=target_set,
        target_insert=target_insert,
        target_parallel=target_parallel,
    ) == ProductMatchStatus.MATCH
