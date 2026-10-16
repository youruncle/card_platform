"""Build bounded, read-only CardHobby order searches from an existing card identity."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.player_aliases import NBA_PREFERRED_QUERY_ALIASES, player_roster_components
from card_platform.domain.product_identity import MANUFACTURER_PREFIXES, LOW_INFORMATION_PRODUCT_LABELS
from card_platform.knowledge import KnowledgeResolver, normalize_knowledge_text
from card_platform.purchase_cost_bundle import purchase_player_aliases


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
    "无法识别球员",
    "未识别球员",
    "未知球员",
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


def _specific_product(value: object) -> str:
    product = _clean_name(value)
    key = _name_key(product)
    # This is the actual flagship name; generic brand/sport/card combinations
    # otherwise identify no specific product and cause noisy discovery.
    if key == 'topps basketball':
        return product
    generic_tokens = {'topps', 'panini', 'nba', 'nfl', 'mlb', 'nhl', 'basketball',
                      'baseball', 'football', 'soccer', 'hockey', 'sport', 'sports',
                      'trading', 'collectible', 'card', 'cards', 'product', 'unknown',
                      '品牌', '产品', '產品', '体育', '體育', '篮球', '籃球', '球星卡', '卡片'}
    generic_tokens.update(token for manufacturer in (*MANUFACTURER_PREFIXES, 'leaf')
                          for token in _name_key(manufacturer).split())
    generic_tokens.add('品牌产品')
    if (re.fullmatch(r'(?:未知|未识别|未識別|无法识别|無法識別)(?:品牌)?(?:产品|產品)', product)
            or re.fullmatch(r'(?:体育|體育|篮球|籃球|球星卡|卡片)+', product)
            or key in LOW_INFORMATION_PRODUCT_LABELS or set(key.split()) <= generic_tokens):
        return ''
    return product if key not in {
        '', 'base', 'basketball', 'nba', 'unknown', 'panini', 'topps', '无法识别产品',
        'sports', 'sport', 'card', 'cards', '球星卡', '篮球',
    } else ''


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
    resolution = knowledge.resolve('player', seed)
    entity = knowledge.entities.get(resolution.entity_id)
    preferred = NBA_PREFERRED_QUERY_ALIASES.get(re.sub(r'[^\w]+', ' ', canonical.casefold()).strip(), ())
    authoritative = list(explicit)
    if entity and 'nba_id' not in entity.attributes:
        authoritative.extend(knowledge.aliases_for('player', seed))
        authoritative.extend(knowledge.query_aliases_for('player', seed))
    authoritative_keys = {_name_key(v) for raw in authoritative if (v := _clean_name(raw))}
    preferred_keys = {_name_key(v) for v in preferred}
    folded = ''.join(c for c in unicodedata.normalize('NFKD', canonical) if not unicodedata.combining(c))
    spelling_keys = {_name_key(folded), _name_key(re.sub(r"['’.]", '', canonical))} - {_name_key(canonical)}
    candidates = [(canonical, "canonical_player")]
    seen = {_name_key(canonical)}
    for raw in (original, *authoritative, *preferred, *purchase_player_aliases(seed, knowledge, discovery=True)):
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
            0 if _name_key(entry[0]) in authoritative_keys else 1 if _name_key(entry[0]) in preferred_keys or _name_key(entry[0]) in spelling_keys else 2,
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
    roster = player_roster_components(_clean_name(identity.player_name))
    if len(roster) > 1:
        # Round-robin by subject before spending a limited query budget on
        # one person's aliases. These terms discover, never verify, a roster.
        per_subject = [_player_candidates({}, subject, knowledge) for subject in roster]
        players = [(group[index][0], 'roster_subject')
                   for index in range(max(map(len, per_subject), default=0))
                   for group in per_subject if index < len(group)]
    product = _specific_product(identity.product_name)
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
    if not players and card.get('image_front_url'):
        # A scene/vehicle card can have a printed work title without a person.
        # These are discovery terms only; matching still needs a valid identity.
        work_title = _clean_name(identity.insert or '')
        non_sport = re.search(r'\bstar wars\b|\bstar trek\b|\bmarvel\b|\bdisney\b|星球大战|星球大戰|星战|星戰|漫威|迪士尼', product, re.I)
        if (non_sport and len(work_title) >= 4
                and _name_key(work_title) not in {'base','refractor','parallel','unknown','特卡','折射'}):
            candidates.extend(((work_title, 'non_player_title'), (product, 'product')))
        elif product:
            candidates.append((product, 'product'))

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
        # Seller names can be wrong. This is discovery only, never identity proof.
        "fallback_keywords": [product] if players and card.get("image_front_url") and product else [],
        "status": "ready" if keywords else "skipped",
        "reason": "" if keywords else "现有卡片信息不足以自动定位订单，已跳过",
    }
