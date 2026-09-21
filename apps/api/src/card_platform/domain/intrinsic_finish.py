"""Evidence-scoped print finishes that do not identify another parallel.

Normalization is a local matching view only. Callers must retain the original
listing title as source evidence and enforce ``explicit_variant_conflict``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from card_platform.domain.listing_title import explicit_print_runs
from card_platform.domain.product_identity import (
    normalize_product_text,
    product_matches_title,
)


@dataclass(frozen=True, slots=True)
class IntrinsicFinishRule:
    rule_id: str
    products: frozenset[str]
    season: tuple[int, int]
    insert: str
    finish_pattern: re.Pattern[str]
    sources: tuple[str, ...]


INTRINSIC_FINISH_RULES = (
    IntrinsicFinishRule(
        rule_id="contenders-2023-24-supernatural-base-dotted-foil",
        products=frozenset({
            "contenders", "contenders basketball",
            "panini contenders", "panini contenders basketball",
        }),
        season=(2023, 2024),
        insert="supernatural",
        # The ordinary Supernatural insert has dotted foil in independent
        # photographs and is cataloged by PSA as Base. "Disco" is seller
        # vocabulary, not a separate checklist parallel. Do not extrapolate
        # this finding to 碎点折, other inserts/seasons, or Contenders Optic.
        finish_pattern=re.compile(r"点点折|(?<![a-z0-9])disco(?:\s*折)?(?![a-z0-9])", re.I),
        sources=(
            "https://www.psacard.com/spec/psa/11311070",
            "https://blog.paniniamerica.net/wrap-up-the-2023-24-nba-season-with-contenders-basketball/",
            # Manufacturer sell sheet, page 4: numbered Supernatural parallels.
            "https://www.blowoutcards.net/images/ss/23pncontendersBK.pdf",
            # Detailed checklist: Playoff /99, Cracked Ice /25, Championship 1/1.
            "https://www.checklistcenter.com/2023-24-panini-contenders-basketball-card-checklist/",
            # Ordinary #17 photograph independently showing the dotted foil.
            "https://www.ebay.com/itm/227347252139",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class IntrinsicFinishTitle:
    title: str
    explicit_variant_conflict: bool = False
    rule_id: str | None = None


_EXPLICIT_VARIANT = re.compile(
    r"\b(?:cracked[\s-]*ice|playoff(?:\s+edition)?|championship(?:\s+edition)?|"
    r"gold|silver|red|blue|green|purple|black|white|orange|pink|yellow|bronze|"
    r"aqua|teal|parallel)\b|"
    r"碎冰|季后赛|冠军版|总冠军|"
    r"(?:红|蓝|绿|金|银|紫|黑|白|橙|粉|黄)(?:色)?\s*(?:disco|折|平行|版)",
    re.I,
)


def assess_intrinsic_finish_title(
    title: str, identity: Mapping[str, object]
) -> IntrinsicFinishTitle:
    """Apply only a verified base-insert finish rule; never alter raw evidence.

    Explicit variants are detected even without a serial (e.g. Cracked Ice).
    They must not inherit the ordinary insert's eligibility merely because an
    older caller's generic parallel regex does not recognize the variant.
    """
    product = normalize_product_text(identity.get("product_name"))
    insert = normalize_product_text(identity.get("insert_name") or identity.get("insert"))
    parallel = normalize_product_text(identity.get("parallel_name") or identity.get("parallel"))
    if parallel not in {"", "base"} or any(
        identity.get(key) is not None for key in ("serial_total", "print_run", "serial_number")
    ):
        return IntrinsicFinishTitle(title)
    for rule in INTRINSIC_FINISH_RULES:
        if (
            product not in rule.products
            or (identity.get("season_start"), identity.get("season_end")) != rule.season
            or insert != rule.insert
        ):
            continue
        # The rule cannot help an unrelated or product-omitted candidate.
        if not product_matches_title(title, identity.get("product_name")) or not re.search(
            rf"(?<![a-z0-9]){re.escape(rule.insert)}(?![a-z0-9])", title, re.I
        ):
            return IntrinsicFinishTitle(title)
        # Color words inside the card's own player name are not parallel claims.
        variant_text = title
        player = str(identity.get("player_name") or "").strip()
        if player:
            variant_text = re.sub(re.escape(player), " ", variant_text, flags=re.I)
        # Calendar labels and seller stock paths are not print-run claims.
        # This copy is used only for serial detection, never as listing evidence.
        serial_text = re.sub(
            r"(?<!\d)(?:19|20)\d{2}\s*[/-]\s*(?:0?[1-9]|1[0-2])"
            r"\s*[/-]\s*(?:0?[1-9]|[12]\d|3[01])(?!\d)",
            " ", variant_text,
        )
        def remove_season_or_month(match: re.Match[str]) -> str:
            start, end = int(match.group(1)), int(match.group(2))
            if len(match.group(2)) == 2 and end > 12:
                end += (start // 100) * 100
            return " " if 1 <= end <= 12 or end == start + 1 else match.group(0)
        serial_text = re.sub(
            r"(?<!\d)((?:19|20)\d{2})\s*/\s*((?:19|20)\d{2}|\d{1,2})(?!\d)",
            remove_season_or_month, serial_text,
        )
        if _EXPLICIT_VARIANT.search(variant_text) or explicit_print_runs(serial_text):
            return IntrinsicFinishTitle(title, True, rule.rule_id)
        return IntrinsicFinishTitle(rule.finish_pattern.sub(" ", title), False, rule.rule_id)
    return IntrinsicFinishTitle(title)
