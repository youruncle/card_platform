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
