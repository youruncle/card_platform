"""Conservative public-title grouping. Search hits alone never prove a type."""

import hashlib
import json
import re
from decimal import Decimal

from card_platform.domain.listing_title import (
    GRADING_DETAIL_PATTERN,
    GRADING_COMPANIES,
    is_graded_listing,
    is_explicit_base_listing,
    is_multi_card_listing,
    explicit_print_runs,
    has_material_parallel_marker,
    has_ambiguous_finish_marker,
    title_without_inventory_hash_tail,
)
from card_platform.ingestion.cardhobby import (
    assess_cardhobby_match,
    _insert_aliases,
    _contains_phrase,
)
from card_platform.player_prices.collection import collect_player as collect_player
from card_platform.player_prices.subjects import canonical_subject_name

# Narrow initial automatic coverage. Existing verified catalog identities extend it.
PRODUCTS = {
    "Prizm": ("prizm", "帕尼尼prizm"),
    "Donruss Optic": ("donruss optic", "optic", "杜蕾斯光学"),
    "Select": ("select", "精选"),
    "Hoops": ("hoops", "海普"),
    "Donruss": ("donruss", "杜蕾斯"),
    "Topps Chrome": ("topps chrome", "托普斯chrome"),
}
FINISHES = {
    "Silver": r"\bsilver\s+(?:prizm|refractor)\b|银折(?:射)?",
    "Red": r"\bred\s+(?:prizm|refractor)\b|红折(?:射)?",
    "Blue": r"\bblue\s+(?:prizm|refractor)\b|蓝折(?:射)?",
    "Gold": r"\bgold\s+(?:prizm|refractor)\b|金折(?:射)?",
}


def identity_title(title: str) -> str:
    """Ignore a seller's closed hash-code chain, keeping earlier card claims.

    Real captures contain tails such as #132B#AIR#XBW10#. None of those
    adjacent warehouse/consignor tokens establishes a catalog number. The
    immutable sale and displayed source title are never changed.
    """
    title = re.sub(r"(?:#[^#\s]+){2,}#\s*$", "", title_without_inventory_hash_tail(title))
    # A seller-bracketed sequence before the release year is equally ambiguous
    # (one real capture series numbers the same LeBron card #5, #6, ... #17).
    return re.sub(r"^(\s*[【\[].*?[】\]]\s*)#[A-Za-z0-9-]+\s+(?=(?:19|20)\d{2})",
                  r"\1", title)


def card_key(identity: dict) -> str:
    fields = (
        "player_name",
        "season_start",
        "season_end",
        "brand_name",
        "product_name",
        "set_name",
        "card_number",
        "insert_name",
        "parallel_name",
        "print_run",
        "grade_company",
        "grade_value",
        "is_autograph",
        "is_memorabilia",
        "is_patch",
        "is_redemption",
    )
    if (
        identity.get("serial_total")
        and identity.get("print_run")
        and int(identity["serial_total"]) != int(identity["print_run"])
    ):
        raise ValueError("conflicting print run")
    identity = {**identity, "print_run": identity.get("serial_total") or identity.get("print_run")}
    identity["player_name"] = canonical_subject_name(identity.get("player_name")) or None
    canonical = {}
    for key in fields:
        value = identity.get(key, False if key.startswith("is_") else None)
        canonical[key] = str(value).strip().casefold() if value is not None else None
    if canonical.get("grade_value"):
        canonical["grade_value"] = str(Decimal(canonical["grade_value"]).normalize())
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def identify_sale(sale, player_name: str, known_identities: list[dict], *, catalog=None) -> dict | None:
    """Only accept a single, unambiguous, fully anchored deterministic match."""
    title = identity_title(sale.title)
    sale = sale.model_copy(update={"title": title})
    if is_multi_card_listing(title):
        return None
    grade = GRADING_DETAIL_PATTERN.search(title)
    if (
        is_graded_listing(title)
        or re.search(rf"(?<![A-Za-z])(?:{GRADING_COMPANIES})(?![A-Za-z])", title, re.I)
    ) and grade is None:
        return None
    if grade:
        sale = sale.model_copy(
            update={"grade_company": grade.group(1).upper(), "grade_value": Decimal(grade.group(2))}
        )
    season = re.findall(r"\b(20\d{2}|19\d{2})(?:[-/](20\d{2}|19\d{2}|\d{2}))?\b", title)
    numbers = re.findall(
        r"(?:#|卡号\s*[:：]?|\bcard\s*#?)([A-Za-z]*-?\d{1,3})(?![\w-])", title, re.I
    )
    if len(season) != 1:
        return None
    base = is_explicit_base_listing(title)
    print_runs = explicit_print_runs(title)
    if base and print_runs:
        return None  # a numbered base needs a separately verified finish, unsupported in v1
    identities = []
    seen = set()
    for known in known_identities:
        if not known.get("season_start"):
            continue
        if (
            known.get("serial_total")
            and known.get("print_run")
            and int(known["serial_total"]) != int(known["print_run"])
        ):
            continue
        number_match = known.get("card_number") and str(known["card_number"]).casefold() in {
            n.casefold() for n in numbers
        }
        insert = str(known.get("insert_name") or "").strip()
        product = str(known.get("product_name") or "").strip()
        insert_match = insert and not _contains_phrase(product, insert) and any(
            _contains_phrase(title, a) for a in _insert_aliases(known)
        )
        if not number_match and not insert_match:
            continue
        parallel = str(known.get("parallel_name") or "").casefold()
        if parallel and _contains_phrase(product, parallel):
            continue  # A product word cannot independently prove its version.
        if parallel in {"base", "普卡", "基础卡"}:
            if not base:
                continue
        elif not parallel or not (
            has_material_parallel_marker(title, known.get("product_name"))
            or has_ambiguous_finish_marker(title)
            or _contains_phrase(title, parallel)
        ):
            continue
        # Clear title anchors prune impossible identities before the full gate.
        if (
            len(season) == 1
            and known.get("season_start")
            and int(known["season_start"]) != int(season[0][0])
        ):
            continue
        if (
            len(set(numbers)) == 1
            and known.get("card_number")
            and str(known["card_number"]).casefold() != numbers[0].casefold()
        ):
            continue
        candidate = {
            **known,
            "player_name": player_name,
            "grade_company": sale.grade_company,
            "grade_value": str(sale.grade_value) if sale.grade_value else None,
        }
        key = card_key(candidate)
        if key not in seen:
            identities.append(candidate)
            seen.add(key)
    products = [
        name
        for name, aliases in PRODUCTS.items()
        if any(re.search(r"(?<![a-z])" + re.escape(a) + r"(?![a-z])", title, re.I) for a in aliases)
    ]
    if "Donruss Optic" in products:
        products = [p for p in products if p != "Donruss"]
    finishes = [name for name, pattern in FINISHES.items() if re.search(pattern, title, re.I)]
    explicit_finish = re.search(r"\b(silver|red|blue|gold)\s+(prizm|refractor)\b", title, re.I)
    base = is_explicit_base_listing(title)
    print_runs = explicit_print_runs(title)
    if (
        not re.search(r"特卡|插入|\binsert\b", title, re.I)
        and len(season) == len(products) == 1
        and len(set(numbers)) == 1
        and ((base and not finishes) or (not base and len(finishes) == 1))
        and len(print_runs) <= 1
    ):
        start, end = season[0]
        year = int(start)
        end_year = int(end) if len(end) == 4 else (year // 100 * 100 + int(end)) if end else None
        if end_year is None or year <= end_year <= year + 1:
            identities.append(
                dict(
                    player_name=player_name,
                    season_start=year,
                    season_end=end_year,
                    brand_name="Topps" if products[0].startswith("Topps") else "Panini",
                    product_name=products[0],
                    set_name=None,
                    card_number=numbers[0],
                    insert_name=None,
                    parallel_name="Base" if base else (
                        explicit_finish.group().title() if explicit_finish else finishes[0]),
                    print_run=next(iter(print_runs), None),
                    is_autograph=False,
                    is_memorabilia=False,
                    is_patch=False,
                    is_redemption=False,
                    grade_company=sale.grade_company,
                    grade_value=str(sale.grade_value) if sale.grade_value else None,
                )
            )
    matches = {}
    for identity in identities:
        if catalog is not None:
            identity = catalog.confirm_title(identity, sale)
            if identity is None:
                continue
        score, conflicts = assess_cardhobby_match(identity, sale)
        if not conflicts and score >= Decimal(".65"):
            matches[card_key(identity)] = identity
    return next(iter(matches.values())) if len(matches) == 1 else None


def rejection_reason(sale) -> str:
    """Report observable missing anchors; do not invent an identity or reason."""
    title = identity_title(sale.title)
    if is_multi_card_listing(title):
        return "multi_card"
    if (is_graded_listing(title) or re.search(rf"(?<![A-Za-z])(?:{GRADING_COMPANIES})(?![A-Za-z])", title, re.I)) and not GRADING_DETAIL_PATTERN.search(title):
        return "grade_unclear"
    if len(re.findall(r"\b(20\d{2}|19\d{2})(?:[-/](20\d{2}|19\d{2}|\d{2}))?\b", title)) != 1:
        return "season_unclear"
    return "identity_unresolved"
