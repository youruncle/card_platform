"""Conservative public-title grouping. Search hits alone never prove a type."""

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal

import httpx

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
from card_platform.domain.player_aliases import query_player_aliases
from card_platform.ingestion.cardhobby import (
    CardHobbyProvider,
    assess_cardhobby_match,
    _insert_aliases,
    _contains_phrase,
)
from card_platform.player_prices.domain import day_end
from card_platform.public_source_catalog import observe_public_sale

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
    canonical = {}
    for key in fields:
        value = identity.get(key, False if key.startswith("is_") else None)
        canonical[key] = str(value).strip().casefold() if value is not None else None
    if canonical.get("grade_value"):
        canonical["grade_value"] = str(Decimal(canonical["grade_value"]).normalize())
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def identify_sale(sale, player_name: str, known_identities: list[dict]) -> dict | None:
    """Only accept a single, unambiguous, fully anchored deterministic match."""
    title = title_without_inventory_hash_tail(sale.title)
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
        insert_match = known.get("insert_name") and any(
            _contains_phrase(title, a) for a in _insert_aliases(known)
        )
        if not number_match and not insert_match:
            continue
        parallel = str(known.get("parallel_name") or "").casefold()
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
                    parallel_name="Base" if base else finishes[0],
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
        score, conflicts = assess_cardhobby_match(identity, sale)
        if not conflicts and score >= Decimal(".65"):
            matches[card_key(identity)] = identity
    return next(iter(matches.values())) if len(matches) == 1 else None


async def collect_player(
    player_name: str, basket: list[dict], report_date: date, *, provider=None, page_budget=12
) -> tuple[list[dict], dict]:
    """Budget is split among aliases and fixed cards; no early broad-query monopoly."""
    provider = provider or CardHobbyProvider(None, "", min_interval=1.0)
    aliases = list(dict.fromkeys([player_name, *query_player_aliases(player_name)]))[:2]
    precise = []
    for entry in basket:
        i = entry["identity"]
        precise.append(
            " ".join(
                str(v)
                for v in [
                    player_name,
                    i.get("season_start"),
                    i.get("product_name"),
                    i.get("parallel_name"),
                    i.get("card_number"),
                    i.get("grade_company"),
                    i.get("grade_value"),
                ]
                if v
            )
        )
    queries = list(dict.fromkeys([*precise, *aliases]))
    observations, errors, query_counts = [], [], {}
    cutoff, end = day_end(report_date) - timedelta(days=90), day_end(report_date)
    pages = 0
    # Match the existing public CardHobby search client. The endpoint closes
    # otherwise valid requests from the default httpx client without these headers.
    headers = {
        "Referer": "https://www.cardhobby.com.cn/",
        "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/139 Safari/537.36",
    }
    async with httpx.AsyncClient(timeout=provider.timeout, headers=headers) as client:
        for index, query in enumerate(queries):
            budget = page_budget // len(queries) + (index < page_budget % len(queries))
            for page in range(1, budget + 1):
                pages += 1
                try:
                    payload = await provider._get_search_payload(client, query, page)
                except (httpx.HTTPError, ValueError, TypeError):
                    errors.append({"query": query, "reason": "卡淘查询失败，稍后重试"})
                    break
                rows = payload["data"]["PagedMarketItemList"]
                query_counts[query] = query_counts.get(query, 0) + len(rows)
                dates = []
                for row in rows:
                    observation = observe_public_sale(row)
                    if observation:
                        occurred = provider.parse_market_item(row).occurred_at
                        dates.append(occurred)
                        if cutoff <= occurred < end:
                            observations.append(observation)
                if len(rows) < 60 or (dates and max(dates) < cutoff):
                    break
    coverage = dict(
        source="cardhobby",
        scope="bounded_sample",
        pages=pages,
        page_budget=page_budget,
        queries=query_counts,
        errors=errors,
        budget_exhausted=pages >= page_budget,
    )
    return observations, coverage
