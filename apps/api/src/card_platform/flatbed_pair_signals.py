"""Conservative, offline observations for flatbed pair proposals.

These are printed-text observations, not a conclusion that two physical cards
are the same. In particular, dates and copyright years are not release seasons.
"""

from __future__ import annotations

from functools import lru_cache
import re
import unicodedata


PRODUCTS = (
    "Bowman Chrome",
    "Topps Chrome",
    "Donruss Optic",
    "Court Kings",
    "SP Authentic",
    "Century Legends",
    "Topps Now",
    "National Treasures",
    "Goodwin Champions",
    "Prizm",
    "Select",
    "Instant",
    "Status",
    "Mosaic",
    "Origins",
    "Flux",
    "Donruss",
    "Hoops",
    "Impulse",
    "Inception",
)
INSERT_PRODUCTS = {
    "Deep Space": "Prizm",
    "Express Lane": "Donruss Optic",
    "Concourse": "Select",
    "Trophy Club": "Status",
    "Hobby Stars": "Bowman Chrome",
    "Generation Now": None,
    "Generations": None,
    "Rookie+Veteran": None,
}
RELEASE = re.compile(
    r"^\s*((?:19|20)\d{2})\s*[-–—/]\s*(\d{2,4})\s+"
    r"(?:PANINI|TOPPS|BOWMAN|UPPER\s+DECK|COLLECTOR['’]?S?\s+EDGE)"
    r"\s*[-–—]?\s*(.{1,60}?)\s+(?:BASKETBALL|BASEBALL|FOOTBALL|CARDS)\b",
    re.I,
)


def normalized_text(value: object) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        re.sub(r"[^\w]+", " ", "".join(c for c in folded if not unicodedata.combining(c))).split()
    )


def canonical_product(value: object) -> str:
    normalized = normalized_text(value)
    aliases = {normalized_text(product): product for product in PRODUCTS}
    for prefix in ("panini ", "upper deck ", "collectors edge "):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    return aliases.get(normalized, str(value).strip())


@lru_cache(maxsize=1)
def _player_labels() -> dict[str, str]:
    from card_platform.domain.nba_player_catalog import nba_player_manifest

    # Use full, pinned player names only. A surname, team or player mentioned
    # inside a paragraph is not evidence of the card's named subject.
    return {
        normalized_text(entity["canonical_name"]): entity["canonical_name"]
        for entity in nba_player_manifest()["entities"]
    }


@lru_cache(maxsize=512)
def _extract(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized = normalized_text(text)
    signals: dict = {
        "ocr_text": text,
        "ocr_tokens": sorted(set(re.findall(r"[A-Za-z0-9#/-]{2,}", text.casefold()))),
    }
    evidence: dict[str, str] = {}
    brands = {name for pattern, name in ((r"\bupper\s*deck\b", "Upper Deck"), (r"\bpanini\b", "Panini"), (r"\btopps\b|\bbowman\b", "Topps")) if re.search(pattern, normalized)}
    if len(brands) == 1:
        signals["brand_name"] = brands.pop()
        evidence["brand_name"] = signals["brand_name"]
    releases = set()
    for line in lines:
        match = RELEASE.search(line)
        if not match:
            continue
        start, end = int(match[1]), int(match[2])
        if len(match[2]) == 2:
            end += (start // 100) * 100
            if end < start:
                end += 100
        if end != start + 1:
            continue
        releases.add((start, end, canonical_product(match[3])))
        evidence["release"] = line
    if len(releases) == 1:
        start, end, product = releases.pop()
        signals.update(season_start=start, season_end=end, product_name=product)
        evidence["product_name"] = evidence["release"]
    elif releases:
        signals["identity_signal_issues"] = ["conflicting_release_lines"]
    else:
        found = [
            product
            for product in PRODUCTS
            if re.search(r"\b" + re.escape(normalized_text(product)) + r"\b", normalized)
        ]
        # A Prizm foil mark also appears on Select/Mosaic/Optic. Prefer a
        # specific product name to the foil mark; do not pick competing names.
        found = [
            p
            for p in found
            if not any(
                p != other and normalized_text(p) in normalized_text(other) for other in found
            )
        ]
        specific = [p for p in found if p != "Prizm"]
        if len(specific) == 1:
            found = specific
        if len(found) == 1:
            if found[0] != "Prizm" or "panini prizm" in normalized:
                signals["product_name"] = found[0]
                evidence["product_name"] = found[0]

    inserts = [
        name
        for name in INSERT_PRODUCTS
        if re.search(r"\b" + re.escape(normalized_text(name)) + r"\b", normalized)
    ]
    if len(inserts) == 1:
        signals["insert_name"] = inserts[0]
        evidence["insert_name"] = inserts[0]
        parent = INSERT_PRODUCTS[inserts[0]]
        if parent and "product_name" not in signals and not signals.get("identity_signal_issues"):
            signals["product_name"] = parent
            evidence["product_name"] = f"printed insert: {inserts[0]}"

    names = sorted(
        {
            _player_labels()[normalized_text(line)]
            for line in lines
            if normalized_text(line) in _player_labels()
        }
    )
    # Scene OCR may split a printed name over two adjacent lines or collapse
    # spaces. Match complete pinned names only; never infer from a surname.
    compact_labels = {name.replace(" ", ""): canonical for name, canonical in _player_labels().items()}
    observed = [*lines]
    for left, right in zip(lines, lines[1:]):
        if len(normalized_text(left).split()) == len(normalized_text(right).split()) == 1:
            observed.extend((left+" "+right, right+" "+left))
    names = sorted(set(names) | {compact_labels[normalized_text(line).replace(" ", "")] for line in observed if normalized_text(line).replace(" ", "") in compact_labels})
    if len(names) == 1:
        signals["player_name"] = names[0]
        evidence["player_name"] = names[0]
    elif names:
        signals["player_names"] = names

    numbers = set()
    for line in lines:
        if match := re.fullmatch(r"(?:NO\.?\s*|N[º°]\s*|#\s*)([A-Z0-9-]{1,24})", line, re.I):
            if re.search(r"\d", match[1]) and not match[1].upper().startswith("CMP"):
                numbers.add(match[1].upper())
    if len(numbers) == 1:
        signals["card_number"] = numbers.pop()
    serials = set()
    for line in lines:
        # A standalone stamp (or an explicit serial label) is different from
        # three-part dates, shooting fractions or narrative/statistics text.
        match = re.fullmatch(
            r"\s*(?:(?:S/N|SERIAL|NUMBERED)\s*[:#]?\s*)?"
            r"(\d{1,4})\s*/\s*(\d{1,4})\s*",
            line,
            re.I,
        )
        if match and 0 < int(match[1]) <= int(match[2]) and not re.match(r"(?:19|20)\d{2}", line):
            serials.add((int(match[1]), int(match[2])))
    if len(serials) == 1:
        signals["serial_number"], signals["serial_total"] = serials.pop()
    if evidence:
        signals["identity_signal_evidence"] = evidence
    return signals


def extract_pair_signals(text: str) -> dict:
    # Cache immutable observations internally, but callers may add geometry or
    # diagnostics to the returned dict/list without polluting another region.
    import copy

    return copy.deepcopy(_extract(text))
