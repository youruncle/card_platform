"""Explicit checklist ordinals are whole identifiers, never print-run fractions."""
import re


ORDINAL_COLLECTOR_PATTERN = r"[0-9]+\s*of\s*[0-9]+"


def ordinal_collector_number_matches(text: str, value: object) -> bool | None:
    """Handle only a complete target ``x of y``; None leaves other formats alone.

    Requiring the literal 'of' on both sides prevents a bare number, serial
    fraction or print-run denominator from becoming a collector-number match.
    """
    target = re.fullmatch(r"#?\s*([0-9]+)\s*of\s*([0-9]+)", str(value or "").strip(), re.I)
    if not target:
        return None
    number, total = target.groups()
    return bool(re.search(
        rf"(?<![0-9a-z]){re.escape(number)}\s*of\s*{re.escape(total)}(?![0-9a-z])",
        text, re.I,
    ))


def ordinal_collector_number_conflicts(text: str, value: object) -> bool:
    """A different explicit ordinal is a mismatch, not an omitted number."""
    return ordinal_collector_number_matches(text, value) is False and bool(re.search(
        rf"(?<![0-9a-z]){ORDINAL_COLLECTOR_PATTERN}(?![0-9a-z])", text, re.I,
    ))


def numeric_card_number_key(value: str) -> str:
    """Ignore padding only in wholly numeric checklist IDs, for comparison.

    Alphanumeric and ordinal identifiers keep their exact normalized spelling.
    Callers retain original values and separately distinguish serial fractions.
    """
    return (value.lstrip('0') or '0') if re.fullmatch(r'[0-9]+', value) else value
