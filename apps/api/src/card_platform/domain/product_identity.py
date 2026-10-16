"""Shared marketplace normalization for product-family labels."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from enum import StrEnum
from functools import lru_cache

from card_platform.domain.season import explicit_seasons


MANUFACTURER_PREFIXES = (
    "panini",
    "topps",
    "upper deck",
    "fleer",
    "skybox",
)
EXPLICIT_SPORTS = ("basketball", "football", "baseball", "hockey", "soccer")
# A category word may be absent from a seller's product name. Explicit sport
# conflicts are checked on the original labels before this comparison view.
OPTIONAL_SPORT_SUFFIXES = EXPLICIT_SPORTS

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
        "chronicles classics",
        "chronicles absolute",
        "chronicles essentials",
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
    *EXPLICIT_SPORTS,
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
    # The 2023 anniversary release is distinct from subsequent Disney Chrome.
    # Official name: Topps Chrome Disney 100; collection labels may reorder it.
    "disney 100 chrome": (
        "disney 100 chrome", "chrome disney 100", "chrome disney100",
        "chrome disney 迪士尼100周年", "chrome 迪士尼100周年",
        "chrome 迪士尼 100周年",
    ),
    "marvel chrome": ("marvel chrome", "topps chrome marvel"),
    "topps chrome marvel": ("marvel chrome", "topps chrome marvel"),
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
    "spectra": ("panini spectra", "spectra basketball", "spectra"),
    "national treasures basketball": ("national treasures", "panini 国宝", "国宝"),
    "crown royale basketball": ("crown royale basketball", "crown royale", "皇冠"),
    "topps chrome basketball": ("topps chrome",),
    "topps chrome updates basketball": ("topps chrome update", "topps chrome updates", "tcu", "tc update", "tc updates"),
    "topps chrome update": ("topps chrome update", "topps chrome updates", "tcu", "tc update", "tc updates"),
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
    folded = re.sub(r"(?<![a-z0-9])topps3(?![a-z0-9])", "topps 3", folded)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", folded).strip()


def _product_comparison_text(value: object) -> str:
    """Ignore an optional sport word between product and subtype tokens.

    Sellers write both ``Chrome Update Basketball`` and ``Chrome Basketball
    Update``. Sport category tokens may move or disappear; the
    Update/Sapphire/Optic qualifiers remain significant.
    Explicit sport conflicts are checked against the original text before
    product matching.
    """
    tokens = normalize_product_text(value).split()
    product_tokens = [token for token in tokens if token not in OPTIONAL_SPORT_SUFFIXES]
    # A separated TC update label is the same explicit child as TCU.
    return re.sub(r"(?<![a-z0-9])(?:tc updates?|tcu)(?![a-z0-9])", "topps chrome updates", " ".join(product_tokens or tokens))


def product_label_variants(value: object) -> tuple[str, ...]:
    """Return catalog-equivalent labels with optional maker/sport wrappers.

    Marketplace titles routinely omit ``Panini`` and ``Basketball`` while the
    catalog keeps one or both. Internal family qualifiers such as ``Optic`` or
    ``Elite`` are never removed.
    """
    # Catalog labels recur thousands of times in a purchase-order batch. Cache
    # immutable variants, not match decisions or account/card-specific evidence.
    return _product_label_variants(str(value or ""))


@lru_cache(maxsize=4096)
def _product_label_variants(value: str) -> tuple[str, ...]:
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
        if not removed_sport and not (set(original.split()) & (set(EXPLICIT_SPORTS) - {"basketball"})):
            values.append(f"{candidate} basketball")
    return tuple(dict.fromkeys(value for value in values if value))


def _distinctive_product_labels(labels: Sequence[str]) -> set[str]:
    # Maker/sport wrappers shared by unrelated releases are not alias evidence.
    return {
        label for label in labels
        if label not in LOW_INFORMATION_PRODUCT_LABELS
        and label not in MANUFACTURER_PREFIXES
        and not set(label.split()) <= set(OPTIONAL_SPORT_SUFFIXES)
    }


def aliases_from_registry(
    value: object,
    registry: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    """Resolve aliases despite optional catalog maker/sport wrappers."""
    target_variants = _distinctive_product_labels(product_label_variants(value))
    if not target_variants:
        return ()
    for key, aliases in registry.items():
        if target_variants & _distinctive_product_labels(product_label_variants(key)):
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
    return bool(_distinctive_product_labels(tuple(left_labels)) & _distinctive_product_labels(tuple(right_labels)))


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
    scoped_release_words: frozenset[str] = frozenset(),
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
            # Only a fully anchored, publisher-reviewed release may supply
            # these words. A parent label composed entirely of its words is
            # not a competing product. Other callers retain the strict rule.
            label_words = frozenset(_product_comparison_text(label).split())
            if label_words and label_words <= scoped_release_words:
                continue
            # Most registry labels never occur in this title. Only resolve the
            # product family for an actual hit; the conflict rules are unchanged.
            if _same_product_family(canonical, target_product):
                continue
            if _is_parent_label_for_target(canonical, target_product):
                continue
            if _is_parent_shorthand_for_target(label, target_product):
                continue
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
    normalized_title = normalize_product_text(title)
    if normalized_alias not in normalized_title:
        normalized_alias = _product_comparison_text(alias)
    if not normalized_alias or len(normalized_alias.split()) != 1:
        return False
    occurrences = list(re.finditer(
        rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])", normalized_title
    ))
    colors = r"(?:red|blue|gold|green|purple|black|orange|pink|silver|aqua|teal|white|yellow|红|蓝|金|绿|紫|黑|橙|粉|银|青|白|黄)\s*$"
    def finish_use(match):
        colored = re.search(colors, normalized_title[:match.start()], re.I)
        suffix = re.match(r"\s*(?:折|refractor|parallel|prizm|foil|holo)",
                          normalized_title[match.end():], re.I)
        # Prizm and Mosaic are finish names in their own right. A suffix such
        # as "refractor" is not required for "Select ... Red Prizm".
        return bool(colored and (suffix or normalized_alias in {'prizm', 'mosaic'}))
    # A separate occurrence naming the product remains independent evidence.
    return bool(occurrences) and all(finish_use(match) for match in occurrences)


SCOPED_EDITION_TERMS = {"sapphire": ("sapphire", "蓝宝石", "藍寶石")}


def _official_release_components(title, identity, resolver):
    """Resolve separated release words only through approved scoped evidence."""
    for attrs in resolver.card_catalog.release_labels_for(identity):
        sports = {s for s in EXPLICIT_SPORTS
                  if re.search(r'(?<![a-z0-9])' + s + r'(?![a-z0-9])', str(title), re.I)}
        if sports and sports != {str(attrs.get('sport') or '').casefold()}:
            continue
        for label in attrs['release_labels']:
            if not isinstance(label, str) or len(label) > 150:
                continue
            words = frozenset(normalize_product_text(label).split())
            brand_words = frozenset(normalize_product_text(attrs['brand']).split())
            # A maker or an edition word alone is not a product identity.
            # Retain franchise and every other qualifier in the verified name.
            if len(words - brand_words) < 3 or not words.intersection(SCOPED_EDITION_TERMS):
                continue
            groups = [SCOPED_EDITION_TERMS.get(word, (word,)) for word in words]
            if not all(any(_alias_occurs_at_token_boundary(title, term) for term in group)
                       for group in groups):
                continue
            terms = [term for group in groups for term in group]
            if any(re.search(r'(?:非|不是|不含|无|無|\bnot\s+|\bnon[ -])\s*'
                             + re.escape(term), str(title), re.I) for term in terms):
                continue
            if any(_alias_occurs_at_token_boundary(title, term) and term not in words
                   for term in ('update', 'updates', 'tcu', 'cosmic', 'logofractor', 'sonic', 'bowman')):
                continue
            if _has_explicit_other_product_label(title, identity.get('product_name'), strong_only=True,
                    target_roles=(identity.get('set_name'), identity.get('insert_name'), identity.get('parallel_name')),
                    scoped_release_words=words | brand_words):
                continue
            return True
    return False


def _official_distributed_edition(title, identity, resolver):
    """Require every named component and an exact official row, never appearance.

    The edition vocabulary is deliberately separate from parallel colors.
    A source may separate the parent/edition with a player name or seller prose.
    """
    product = _product_comparison_text(identity.get('product_name'))
    for edition, terms in SCOPED_EDITION_TERMS.items():
        if not _alias_occurs_at_token_boundary(product, edition):
            continue
        parent = re.sub(r'\b' + re.escape(edition) + r'\b', '', product).strip()
        if (not parent or parent in LOW_INFORMATION_PRODUCT_LABELS
                or not _alias_occurs_at_token_boundary(title, parent)
                or not any(_alias_occurs_at_token_boundary(title, term) for term in terms)):
            continue
        if re.search(r'(?:非|不是|不含|无|無|\bnot\s+|\bnon[ -])(?:sapphire|蓝宝石|藍寶石)', str(title), re.I):
            continue
        # Updates is another release, even when separated from Chrome. Color
        # words such as Black remain parallel vocabulary unless fully named.
        if any(_alias_occurs_at_token_boundary(title, term)
               and not _alias_occurs_at_token_boundary(product, term)
               for term in ('update', 'updates', 'tcu', 'cosmic', 'logofractor', 'sonic')):
            continue
        if _has_explicit_other_product_label(title, identity.get('product_name'), strong_only=True,
                target_roles=(identity.get('set_name'), identity.get('insert_name'), identity.get('parallel_name'))):
            continue
        sports = {sport for sport in EXPLICIT_SPORTS if _alias_occurs_at_token_boundary(title, sport)}
        for row in resolver.card_catalog.lookup(identity):
            attrs = row.get('attributes') or {}
            if (attrs.get('authority') == 'official_checklist'
                    and attrs.get('source_url') and attrs.get('source_locator')
                    and re.fullmatch(r'[0-9a-f]{64}', str(attrs.get('source_checksum') or ''))
                    and (not sports or sports == {str(attrs.get('sport') or '').casefold()})):
                return True
    return False


def assess_identity_product_match(title: object, identity: Mapping, *, resolver=None) -> ProductMatchStatus:
    """Consume source-scoped product equivalents without widening global aliases."""
    from card_platform.domain.entity_roles import title_without_catalog_subject
    title = title_without_catalog_subject(title, identity, resolver=resolver)
    roles = {
        "target_set": identity.get("set_name"),
        "target_insert": identity.get("insert_name") or identity.get("insert"),
        "target_parallel": identity.get("parallel_name") or identity.get("parallel"),
    }
    original = assess_product_match(title, identity.get("product_name"), **roles)
    if original == ProductMatchStatus.MATCH or resolver is None:
        return original
    aliases = resolver.card_catalog.matching_product_aliases(identity)
    release_components = _official_release_components(title, identity, resolver)
    distributed = (original == ProductMatchStatus.UNKNOWN
                   and _official_distributed_edition(title, identity, resolver))
    if not aliases and not distributed and not release_components:
        return original
    # A scoped alias is not a global spelling correction. Require the source
    # itself to state the matching release and manufacturer before using it.
    start, end = identity.get("season_start"), identity.get("season_end")
    years = {int(year) for year in re.findall(r"(?<![\d/])((?:19|20)\d{2})(?!\d|\s*[编遍])", str(title))}
    seasons = explicit_seasons(title)
    if (not years or not start or not end or not years.issubset({start, end})
            or (seasons and seasons != {(start, end)})):
        return original
    brand = normalize_product_text(identity.get("brand_name"))
    if not brand or not _alias_occurs_at_token_boundary(title, brand):
        return original
    if any(maker != brand and _alias_occurs_at_token_boundary(title, maker)
           for maker in MANUFACTURER_PREFIXES):
        return original
    # These labels distinguish edition/media scopes. A parent alias must not
    # erase an explicit qualifier absent from the target product/set scope.
    qualifiers = r"\b(?:sapphire|studios|mcu|cinematic|movie|film)\b|蓝宝石|藍寶石|影视|影視|真人"
    target_scope = f"{identity.get('product_name') or ''} {identity.get('set_name') or ''}"
    if any(not re.search(re.escape('sapphire' if marker in {'蓝宝石', '藍寶石'} else marker), target_scope, re.I)
           for marker in re.findall(qualifiers, str(title), re.I)):
        return original
    if distributed or release_components:
        return ProductMatchStatus.MATCH
    if any(assess_product_match(title, alias, **roles) == ProductMatchStatus.MATCH for alias in aliases):
        return ProductMatchStatus.MATCH
    return original


def assess_product_match(
    title: object,
    target_product: object,
    *,
    target_set: object = None,
    target_insert: object = None,
    target_parallel: object = None,
) -> ProductMatchStatus:
    """Distinguish a match, omitted/unknown product, and explicit conflict."""
    # Some listings reorder the Bowman product words around a player name.
    # Require a product-position Bowman or the labelled college product; a
    # surname (even translated 鲍曼) alone is not product evidence. Preserve
    # explicit Chrome child products before considering this word-order alias.
    if ("鲍曼" in str(title)
            and (re.search(r"\btopps\s+chrome\s+bowman\b", str(title), re.I)
                 or re.search(r"\bbowman\s+鲍曼\s*大学\s+1st\b", str(title), re.I))
            and not re.search(r"\bbowman\s+chrome\b", str(title), re.I)
            and not has_product_child_conflict(title, "Topps Chrome")):
        title = re.sub(r"\btopps\s+chrome\b", "Topps Bowman Chrome", str(title), flags=re.I)
    normalized_target = normalize_product_text(target_product)
    normalized_title = normalize_product_text(title)
    if not normalized_target:
        return ProductMatchStatus.UNKNOWN
    if re.fullmatch(r"(?:19|20)\d{2} (?:(?:19|20)\d{2}|\d{2}) upper deck basketball", normalized_target):
        # The maker also published a same-named flagship. Only an explicitly
        # scoped entity and a full same-season source label establish it; UD
        # alone still identifies the manufacturer of many distinct products.
        other_ud_product = re.search(
            r"(?<![a-z0-9])(?:upper deck|ud)(?: basketball)? "
            r"(?:first edition|sp(?: authentic| game used| signature)?|spx|mvp|"
            r"artifacts|ultimate|hardcourt|draft edition)(?![a-z0-9])",
            normalized_title,
        )
        named_ud_child = re.search(
            r"(?<![a-z0-9])(?:first edition|sp authentic|sp game used|sp signature|"
            r"spx|artifacts|draft edition)(?![a-z0-9])", normalized_title,
        )
        if other_ud_product or named_ud_child or _has_explicit_other_product_label(
            title, target_product, strong_only=True,
            target_roles=(target_set, target_insert, target_parallel),
        ):
            return ProductMatchStatus.EXPLICIT_CONFLICT
        target_seasons = explicit_seasons(target_product)
        source_seasons = explicit_seasons(title)
        if source_seasons and source_seasons != target_seasons:
            return ProductMatchStatus.EXPLICIT_CONFLICT
        return ProductMatchStatus.MATCH if (
            target_seasons and source_seasons == target_seasons
            and re.search(r"(?<![a-z0-9])upper deck basketball(?![a-z0-9])", normalized_title)
        ) else ProductMatchStatus.UNKNOWN
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
    # Sellers may name the Deadpool movie set in Chinese, with Marvel between
    # Chrome and the film title. Require both anchors; Marvel alone is broader.
    if normalized_target in {"marvel chrome", "topps chrome marvel"} and (
        "死侍与金刚狼" in normalized_title
        or re.search(r"\bdeadpool (?:and )?wolverine\b", normalized_title)
        or "deadpool chrome" in normalized_title
    ):
        return ProductMatchStatus.EXPLICIT_CONFLICT
    if normalized_target == "deadpool chrome" and (
        "chrome" in normalized_title.split() and "死侍与金刚狼" in normalized_title
    ):
        return ProductMatchStatus.MATCH
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
    if _product_comparison_text(target_product) in PRODUCT_CHILD_FAMILIES['topps chrome']:
        # Separated edition words still identify competing Chrome children.
        # Black is excluded because it also denotes an ordinary parallel color.
        # A known insert/parallel role keeps its own qualifier interpretation.
        for term in ('update', 'updates', 'tcu', 'cosmic', 'sapphire', 'logofractor', 'sonic'):
            family_term = 'update' if term in {'updates', 'tcu'} else term
            target_terms = normalize_product_text(target_product).replace('updates', 'update')
            if (_alias_occurs_at_token_boundary(title, term)
                    and not _alias_occurs_at_token_boundary(target_terms, family_term)
                    and not _label_is_target_role(term, (target_set, target_insert, target_parallel))):
                return ProductMatchStatus.EXPLICIT_CONFLICT
        # An explicit competing child is stronger than every target alias,
        # including TCU and even a second fully written Updates label.
        if (re.search(r'(?<![a-z0-9])bowman chrome(?![a-z0-9])', normalized_title)
                or _has_explicit_other_product_label(
                    title, target_product, strong_only=True,
                    target_roles=(target_set, target_insert, target_parallel),
                )):
            return ProductMatchStatus.EXPLICIT_CONFLICT
        if (_product_comparison_text(target_product) in {'topps chrome update', 'topps chrome updates'}
                and re.search(r'(?<![a-z0-9])tc updates?(?![a-z0-9])', normalized_title)
                and not re.search(r'(?<![a-z0-9])topps chrome(?![a-z0-9])', normalized_title)):
            return ProductMatchStatus.UNKNOWN
    if normalized_target in {"disney 100 chrome", "topps disney 100 chrome"}:
        # Sellers distribute the licensed subject and anniversary around names.
        # Only the 2023 release plus all four independent product anchors proves
        # this scope; a card number, serial /100 or later Disney Chrome does not.
        without_disney = normalize_product_text(re.sub(
            r"(?<![a-z])disney(?![a-z])|迪士尼", " ", normalized_title
        ))
        if (re.search(r"(?<![a-z0-9])(?:sapphire|bowman)(?![a-z0-9])", normalized_title)
                or "platinum anniversary" in normalized_title
                or has_product_child_conflict(without_disney, "Topps Chrome")):
            return ProductMatchStatus.EXPLICIT_CONFLICT
        years = set(re.findall(r"(?<!\d)20\d{2}(?!\d)", str(title)))
        if years - {"2023"} or re.search(r"2023\s*[-/]\s*(?:20)?\d{2}", str(title)):
            return ProductMatchStatus.EXPLICIT_CONFLICT
        anniversary = bool(re.search(
            r"100\s*周年|100th\s+anniversary|(?<![a-z])disney\s*100(?:\b|周年)(?!\s*[/元$￥¥])",
            str(title), re.I,
        ))
        return ProductMatchStatus.MATCH if (
            years == {"2023"} and anniversary
            and re.search(r"(?<![a-z])topps(?![a-z])", normalized_title)
            and re.search(r"(?<![a-z])chrome(?![a-z])", normalized_title)
            and (re.search(r"(?<![a-z])disney(?![a-z])", normalized_title)
                 or "迪士尼" in normalized_title)
        ) else ProductMatchStatus.UNKNOWN
    aliases = aliases_from_registry(target_product, PRODUCT_MATCH_ALIASES)
    generic_wrappers = {*EXPLICIT_SPORTS, *MANUFACTURER_PREFIXES}
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


def has_ambiguous_child_product_shorthand(
    title: object,
    target_product: object,
    *,
    target_set: object = None,
    target_insert: object = None,
    target_parallel: object = None,
) -> bool:
    """Recognize audited child-only wording for image review, never a match.

    Prestige also exists as a standalone product. Its name cannot establish
    Chronicles lineage without independent image evidence. The caller must
    check player, maker, season, number/serial and finish before promotion.
    Context roles cannot substitute for the stored child product.
    """
    if "chronicles prestige" not in product_label_variants(target_product):
        return False
    if not _alias_occurs_at_token_boundary(title, "prestige"):
        return False
    if any(
        _alias_occurs_at_token_boundary(title, label)
        for label in (
            "chronicles", "编年史", "draft", "选秀",
            *(maker for maker in MANUFACTURER_PREFIXES if maker != "panini"),
            "ud", "bowman",
            *(sport for sport in EXPLICIT_SPORTS if sport != "basketball"),
            *(child.removeprefix("chronicles ")
              for child in PRODUCT_CHILD_FAMILIES["chronicles"]),
        )
    ):
        return False
    return (
        assess_product_match(
            title, target_product, target_set=target_set,
            target_insert=target_insert, target_parallel=target_parallel,
        ) == ProductMatchStatus.UNKNOWN
        and not _has_explicit_other_product_label(title, target_product, strong_only=False)
    )


def _reviewed_front_product_insert_anchor(title, identity, visual_facts) -> bool:
    """Read explicit printed labels from a sealed review, never its free-form notes.

    This replaces an omitted sale card number only for a named insert whose
    full child product is readable on both fronts. The enclosing caller checks
    current proof, parent-only title, release, maker and official membership.
    A source back or a canonical parallel name is not inferred.
    """
    import json
    from card_platform.domain.independent_finish import PrintedTextField, _specific_text
    from card_platform.knowledge_card_catalog import text_key

    insert = identity.get('insert_name') or identity.get('insert')
    if (not insert or text_key(insert) in {'base', 'basecards', 'baseset', 'insert'}
            or not _alias_occurs_at_token_boundary(title, insert)
            or re.search(r'\b(?:premium\s+box\s+set|factory\s+set)\b', str(title), re.I)):
        return False
    proof = visual_facts._reviewed_proof
    binding = json.loads(proof.binding_json)
    if not re.fullmatch(r'[0-9a-f]{64}', str(binding.get('target_back_sha256') or '')):
        return False
    review = json.loads(proof.review_json)
    labels = review.get('printed_front_identity')
    if (not isinstance(labels, dict)
            or labels.get('contract') != 'reviewed-printed-front-product-insert-v1'):
        return False
    try:
        for side in ('target', 'candidate'):
            observation = labels[side]
            if observation.get('view') != 'front':
                return False
            for kind in ('product', 'insert'):
                field = PrintedTextField.model_validate(observation[kind])
                if (field.status != 'readable' or not _specific_text(field.value)
                        or not _specific_text(field.location)
                        or re.search(r'背面|卡背|反面|\bback(?:side)?\b', field.location, re.I)):
                    return False
                if kind == 'product':
                    if assess_product_match(field.value, identity.get('product_name')) != ProductMatchStatus.MATCH:
                        return False
                elif text_key(field.value) != text_key(insert):
                    return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return True


def reviewed_parent_product_confirmation(title, identity, visual_facts, *, resolver=None) -> bool:
    """Fill an omitted child label only through reviewed images and an official card row.

    This is per-comparable evidence, never a parent/child product alias. Models,
    unbound booleans and checklist membership alone cannot provide this proof.
    """
    from card_platform.domain.card_identity import CardIdentityResolver
    from card_platform.domain.finish_evidence import VisualMatchFacts
    if (resolver is None or not isinstance(visual_facts, VisualMatchFacts)
            or visual_facts._reviewed_proof is None
            or not visual_facts.valid_for_target(CardIdentityResolver().resolve(identity), title)):
        return False
    if assess_identity_product_match(title, identity, resolver=resolver) != ProductMatchStatus.UNKNOWN:
        return False
    target_product = identity.get('product_name')
    parents = [parent for parent in PRODUCT_CHILD_FAMILIES
               if _is_parent_label_for_target(parent, target_product)
               and _alias_occurs_at_token_boundary(title, parent)]
    if not parents or any(has_product_child_conflict(title, parent) for parent in parents):
        return False
    # Seller word order may separate the parent from a competing child token
    # ("Topps Chrome [player] Sapphire"). Such a title is not an omission.
    child_qualifiers = {
        normalize_product_text(child).replace(normalize_product_text(parent), "").strip()
        for parent in parents for child in PRODUCT_CHILD_FAMILIES[parent]
    }
    if any(qualifier and _alias_occurs_at_token_boundary(title, qualifier)
           for qualifier in child_qualifiers):
        return False
    brand = normalize_product_text(identity.get('brand_name'))
    if (not brand or not _alias_occurs_at_token_boundary(title, brand)
            or any(maker != brand and _alias_occurs_at_token_boundary(title, maker)
                   for maker in MANUFACTURER_PREFIXES)):
        return False
    season = (identity.get('season_start'), identity.get('season_end'))
    from card_platform.domain.reviewed_source_back import reviewed_back_edition_matches, reviewed_source_back_fields
    target = CardIdentityResolver().resolve(identity)
    paired_back = reviewed_back_edition_matches(target, title, visual_facts)
    full_seasons = explicit_seasons(title)
    single_years = {int(y) for y in re.findall(r'(?<![\d/])((?:19|20)\d{2})(?!\d)', str(title))}
    compatible_short_year = (paired_back and not full_seasons and len(single_years) == 1
                             and single_years <= set(season))
    if not all(season) or (full_seasons != {season} and not compatible_short_year):
        return False
    number = str(identity.get('card_number') or '').strip()
    number_in_title = re.search(r'(?<![A-Za-z0-9])(?:#|no\.?\s*|卡号\s*)'
        + re.escape(number) + r'(?![A-Za-z0-9/])', str(title), re.I)
    number_on_back = paired_back and 'card_number' in reviewed_source_back_fields(target, title, visual_facts)
    printed_insert_anchor = _reviewed_front_product_insert_anchor(title, identity, visual_facts)
    if not number or not (number_in_title or number_on_back or printed_insert_anchor):
        return False
    rows = resolver.card_catalog.lookup(identity)
    return any(attrs.get('authority') == 'official_checklist'
               and attrs.get('source_url') and attrs.get('source_locator')
               and re.fullmatch(r'[0-9a-f]{64}', str(attrs.get('source_checksum') or ''))
               for attrs in (row.get('attributes') or {} for row in rows))
