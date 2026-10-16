"""A temporary parallel-only text view; immutable listing facts stay unchanged."""
import re

from card_platform.domain.listing_title import explicit_print_runs
from card_platform.domain.product_identity import assess_identity_product_match, ProductMatchStatus
from card_platform.domain.season import explicit_seasons


def parallel_match_text(title: str, identity, *, resolver=None) -> str:
    if resolver is None:
        return title
    terms = resolver.card_catalog.matching_parallel_terms(identity)
    if not terms:
        return title
    start, end = identity.get('season_start'), identity.get('season_end')
    total = identity.get('serial_total') or identity.get('print_run')
    brand = str(identity.get('brand_name') or '').strip()
    seasons = explicit_seasons(title)
    years = {int(y) for y in re.findall(r'(?<![\d/])((?:19|20)\d{2})(?!\d|\s*[编遍])', title)}
    same_release = (seasons == {(start, end)} if seasons else start == end and years == {start})
    if (not start or not end or not same_release or not years.issubset({start, end})
            or not brand or not re.search(r'(?<![a-z0-9])' + re.escape(brand) + r'(?![a-z0-9])', title, re.I)
            or explicit_print_runs(title) != {int(total)}
            or assess_identity_product_match(title, identity, resolver=resolver) != ProductMatchStatus.MATCH):
        return title
    view = title
    for term, official in sorted(terms, key=lambda pair: -len(pair[0])):
        if re.search(r'(?:非|不是|不含|无|無|\bnot\s+|\bnon[ -])\s*' + re.escape(term), title, re.I):
            return title
        pattern = r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])'
        # Transform only the approved phrase actually present. Adding its
        # technology to global aliases would also erase a separately printed
        # English Cracked Ice/Wave claim, which must remain a conflict.
        view = re.sub(pattern, lambda _: ' ' + official + ' ', view, flags=re.I)
    return view
