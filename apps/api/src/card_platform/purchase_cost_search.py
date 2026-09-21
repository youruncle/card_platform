"""Build bounded, read-only CardHobby order searches from an existing card identity."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.knowledge import KnowledgeResolver, normalize_knowledge_text


MAX_KEYWORD_LENGTH = 120
_PUBLIC_ITEM_NUMBER = re.compile(r"商品编号\s*[:：#]?\s*([0-9]{1,40})(?![0-9])")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# Serialization and OCR placeholders must never be sent as player searches.
# Keep this feature-local: deployed CardIdentityResolver versions may not
# expose the newer generic placeholder helper.
_PLAYER_PLACEHOLDERS = {
    "none",
    "null",
    "unknown",
    "unspecified",
    "not applicable",
    "not identified",
    "n a",
    "na",
    "未知",
    "未识别",
    "无法识别",
}


def _clean_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = "".join(char for char in value if unicodedata.category(char) != "Cf")
    value = re.sub(r"\s+", " ", value).strip()
    # Ignore OCR decoration around a name, while retaining punctuation inside
    # names such as D'Angelo Russell and Shai Gilgeous-Alexander.
    while value and not value[0].isalnum():
        value = value[1:]
    while value and not value[-1].isalnum():
        value = value[:-1]
    if (
        not value
        or len(value) > MAX_KEYWORD_LENGTH
        or normalize_knowledge_text(value) in _PLAYER_PLACEHOLDERS
        or sum(char.isalpha() for char in value) < 2
    ):
        return ""
    return value


def _name_key(value: str) -> str:
    return normalize_knowledge_text(value)


def _player_candidates(card: dict[str, Any], player: str, knowledge: KnowledgeResolver):
    explicit = card.get("player_name_aliases") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    if not isinstance(explicit, (list, tuple)):
        explicit = []

    original = _clean_name(player)
    # Explicit aliases can recover a missing OCR name without requiring the
    # user to supply a keyword. Never derive a player from arbitrary notes.
    seed = original or next((name for value in explicit if (name := _clean_name(value))), "")
    if not seed:
        return []
    canonical = _clean_name(knowledge.resolve("player", seed).canonical_value) or seed
    candidates = [(canonical, "canonical_player")]
    seen = {_name_key(canonical)}
    for raw in (original, *knowledge.aliases_for("player", seed), *explicit):
        alias = _clean_name(raw)
        key = _name_key(alias)
        if alias and key not in seen:
            seen.add(key)
            candidates.append((alias, "player_alias"))

    # MyOrders searchTxt is a single plain string. Full card descriptions are
    # not guaranteed to behave as multi-field AND queries, so search names and
    # let the existing identity matcher examine year/product/parallel later.
    # Reserve the next slot for the other script before English nicknames can
    # exhaust the budget. Short Chinese aliases also cover longer full names.
    primary_is_han = bool(_HAN.search(canonical))
    alternatives = candidates[1:]
    alternatives.sort(
        key=lambda entry: (
            not bool(_HAN.search(entry[0])),
            len(entry[0]) if _HAN.search(entry[0]) else 0,
        )
    )
    opposite = next(
        (entry for entry in alternatives if bool(_HAN.search(entry[0])) != primary_is_han),
        None,
    )
    if opposite:
        alternatives.remove(opposite)
    return [candidates[0], *([opposite] if opposite else []), *alternatives]


def build_purchase_cost_search_plan(
    card: dict[str, Any],
    knowledge: KnowledgeResolver | None = None,
    *,
    max_keywords: int = 3,
) -> dict[str, Any]:
    """Return a deterministic search plan; no browser, credentials, or I/O.

    Each keyword is a separate plain ``searchTxt`` request. The default three
    requests leave room for the public item number and bilingual player names.
    Identity matching remains responsible for deciding which results are this
    card; a broad search result alone never authorizes recording a cost.
    """
    if not 1 <= max_keywords <= 6:
        raise ValueError("max_keywords must be between 1 and 6")
    knowledge = knowledge or KnowledgeResolver()
    identity = CardIdentityResolver().resolve(card)
    players = _player_candidates(card, identity.player_name, knowledge)
    label = (
        " ".join(
            str(value)
            for value in (
                players[0][0] if players else "",
                identity.season_start,
                identity.product_name,
            )
            if value
        )[:240]
        or "未识别卡片"
    )

    candidates: list[tuple[str, str]] = []
    for field in ("notes", "purchase_context"):
        context = unicodedata.normalize("NFKC", str(card.get(field) or ""))
        candidates.extend(
            (number, "public_item_number") for number in _PUBLIC_ITEM_NUMBER.findall(context)
        )
    candidates.extend(players)

    keywords: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()
    for keyword, source in candidates:
        key = _name_key(keyword)
        if key in seen:
            continue
        # A previously selected short Chinese alias already covers its longer
        # full-name spelling in a literal keyword search. Spend the remaining
        # slot on another actual alias instead of redundant wording.
        if (
            source == "player_alias"
            and _HAN.search(keyword)
            and any(_HAN.search(previous) and previous in keyword for previous in keywords)
        ):
            continue
        seen.add(key)
        keywords.append(keyword)
        sources.append(source)
        if len(keywords) == max_keywords:
            break

    return {
        "label": label,
        "keywords": keywords,
        "keyword_sources": sources,
        "status": "ready" if keywords else "skipped",
        "reason": "" if keywords else "现有卡片信息不足以自动定位订单，已跳过",
    }
