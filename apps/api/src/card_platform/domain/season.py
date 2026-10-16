"""Season notation shared by marketplace matching and comparable classification."""

import re
import unicodedata


_EXPLICIT_SEASON = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*[-‐‑‒–—−/]\s*((?:(?:19|20)\d{2})|\d{2})(?!\d)"
)


def explicit_seasons(value: object) -> set[tuple[int, int]]:
    """Read full-start-year seasons, including a two-digit century rollover.

    An abbreviated end year belongs to the first matching year at or after
    the start: ``1999-00`` means 1999–2000, never 1999–1900. Fully specified
    end years are preserved so a conflicting title is not silently corrected.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    seasons: set[tuple[int, int]] = set()
    for start_text, end_text in _EXPLICIT_SEASON.findall(text):
        start = int(start_text)
        end = int(end_text)
        if len(end_text) == 2:
            end += (start // 100) * 100
            if end < start:
                end += 100
        seasons.add((start, end))
    return seasons


_ABBREVIATED_SEASON = re.compile(
    r"(?<![\dA-Za-z/#‐‑‒–—−-])(\d{2})\s*([-‐‑‒–—−/])\s*(\d{2})(?![\dA-Za-z/‐‑‒–—−-])"
)
_NON_SEASON_PREFIX = re.compile(
    r"(?:卡号|卡號|编号|編號|货号|貨號|库存|庫存|限编|限編|\bsku|\binventory|\blot|\bno\.?|#)\s*[:：#-]?\s*[([]?\s*$", re.I
)


def matches_abbreviated_season(value: object, start: int | None, end: int | None) -> bool:
    """Match a bounded two-digit season against an already known target.

    This does not infer a century or enrich identity. Slash notation needs an
    explicit season label because an unlabelled 24/25 is a serial stamp. Full
    start-year evidence wins over a coincidental abbreviated token.
    """
    if start is None or end is None or int(end) != int(start) + 1:
        return False
    text = unicodedata.normalize("NFKC", str(value or ""))
    full = explicit_seasons(text)
    if full:
        return False  # Existing full-year callers handle these, including conflicts.
    pairs: set[tuple[int, int]] = set()
    for match in _ABBREVIATED_SEASON.finditer(text):
        before, after = text[:match.start()], text[match.end():]
        if re.search(r"[/‐‑‒–—−-]\s*$", before) or re.match(r"\s*[/‐‑‒–—−-]", after):
            continue
        if _NON_SEASON_PREFIX.search(before) or re.match(r"\s*(?:编|編|号|號|张|張|件)", after):
            continue
        if match[2] == '/' and not (
            re.search(r"(?:赛季|賽季|\bseason)\s*[:：]?\s*$", before, re.I)
            or re.match(r"\s*(?:赛季|賽季|season\b)", after, re.I)
        ):
            continue
        pairs.add((int(match[1]), int(match[3])))
    return pairs == {(int(start) % 100, int(end) % 100)}
