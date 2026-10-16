"""Distinguish copied product labels from independently known card subjects."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from card_platform.domain.card_identity import normalize_text

if TYPE_CHECKING:
    from card_platform.knowledge import KnowledgeResolver


def official_cover_work_subject(identity: dict, *, resolver: KnowledgeResolver | None = None) -> bool:
    """A scoped checklist can identify a numbered work, never a global person alias.

    Both a numbered work title and an official cover section are required.
    This is discovery evidence only: it says nothing about a sale's artwork,
    finish, condition, or release year.
    """
    if resolver is None or not all(identity.get(key) for key in (
        'player_name', 'brand_name', 'product_name', 'season_start', 'season_end',
        'card_number', 'insert_name',
    )):
        return False
    subject = str(identity['player_name'])
    if not re.fullmatch(r'.+[A-Za-z].*\s+#\s*\d+[A-Za-z]?', subject):
        return False
    insert = normalize_text(identity['insert_name'])
    if not re.search(r'\bcovers?\b', insert):
        return False
    for row in resolver.card_catalog.lookup(identity):
        attrs = row.get('attributes') or {}
        if (attrs.get('authority') == 'official_checklist'
                and str(attrs.get('source_url') or '').startswith('https://')
                and re.fullmatch(r'[0-9a-f]{64}', str(attrs.get('source_checksum') or ''))
                and normalize_text(attrs.get('player')) == normalize_text(subject)
                and normalize_text(attrs.get('insert')) == insert
                and re.search(r'\bcovers?\b', normalize_text(attrs.get('section')))):
            return True
    return False


def official_work_query_title(identity: dict, *, resolver=None) -> str | None:
    """Relax a verified cover subject for search only; never a matching alias."""
    if not official_cover_work_subject(identity, resolver=resolver):
        return None
    title = re.sub(r'\s+#\s*\d+[A-Za-z]?$', '', str(identity['player_name']).strip())
    # A trailing parenthesized comic year belongs to the work, not the card release.
    title = re.sub(r'\s*\((?:19|20)\d{2}\)$', '', title).strip()
    return title or None


def identity_has_entity_role_collision(
    identity: dict, *, resolver: KnowledgeResolver | None = None
) -> bool:
    """A shared name is suspicious only without trusted evidence of a person role.

    The resolver comes from published knowledge, never from listing claims or
    caller-supplied player aliases. A catalog row proves only the subject role;
    it does not validate a copied insert, parallel, or the comparable itself.
    """
    player = normalize_text(identity.get('player_name'))
    if not player:
        return False
    overlap = any(
        player == role
        or (len(player) >= 4 and re.search(
            rf'(?<![0-9a-z]){re.escape(player)}(?![0-9a-z])', role
        ))
        for value in (
            identity.get('brand_name'), identity.get('product_name'),
            identity.get('set_name'), identity.get('insert_name') or identity.get('insert'),
        )
        if (role := normalize_text(value))
    )
    if not overlap or resolver is None:
        return bool(overlap)
    if resolver.resolve('player', identity.get('player_name')).entity_id:
        return False
    # Keep every identifying scope anchor. Drop only variant fields because we
    # are asking whether this is a subject at all, not confirming its version.
    if all(identity.get(key) for key in ('brand_name', 'product_name', 'season_start', 'card_number')):
        subject_scope = {
            key: identity.get(key) for key in (
                'player_name', 'brand_name', 'product_name', 'season_start',
                'season_end', 'card_number',
            )
        }
        if resolver.card_catalog.lookup(subject_scope):
            return False
    return True


def _without_explicit_comic_work_numbers(title: str) -> str:
    """Mask only dates/issues grammatically attached to a labelled comic cover.

    Quotation plus the explicit comic-cover label distinguishes work metadata
    from independent release/card claims. Keep all other words (including grade,
    product, damage and parallel claims), and preserve source offsets.
    """
    book = r"《(?P<work>[^《》\r\n]{1,100})》"
    year = r"(?P<year>(?:19|20)\d{2})\s*年"
    patterns = (
        rf"(?<![\d/])(?:{year}\s*)?{book}\s*漫画封面",
        rf"漫画封面\s*{book}(?:\s*[（(]\s*{year}\s*[）)])?",
    )
    spans = []
    for pattern in patterns:
        for match in re.finditer(pattern, title):
            if match.group('year'):
                spans.append(match.span('year'))
            # Never consume an alphanumeric collector code, serial fraction,
            # grading value or a claim outside the work's quotation marks.
            for issue in re.finditer(r"(?<![\w/])#\s*\d+[A-Za-z]?(?![\w/])", match.group('work')):
                spans.append((match.start('work') + issue.start(), match.start('work') + issue.end()))
    chars = list(title)
    for start, end in spans:
        chars[start:end] = ' ' * (end - start)
    return ''.join(chars)


def title_without_catalog_subject(title: object, identity, *, resolver=None) -> str:
    """Separate labelled work metadata and official subjects from card fields.

    This view cannot supply player matches or mutate listing evidence. A comic
    issue number or character year belongs to the subject, not the card release.
    Extra product/year/collector-number claims outside the span remain intact.
    """
    text = _without_explicit_comic_work_numbers(str(title or ""))
    if resolver is None or not all(identity.get(key) for key in (
        'player_name', 'brand_name', 'product_name', 'season_start',
        'season_end', 'card_number',
    )):
        return text
    subject = str(identity['player_name']).strip()
    if len(subject) < 4 or not re.search(r'[A-Za-z\u3400-\u9fff]', subject):
        return text
    pattern = r'(?<![\w])' + re.escape(subject).replace(r'\ ', r'\s+') + r'(?![\w])'
    matches = list(re.finditer(pattern, text, re.I))
    if not matches:
        return text
    scope = {key: identity.get(key) for key in (
        'player_name', 'brand_name', 'product_name', 'season_start',
        'season_end', 'card_number', 'insert_name', 'insert',
    )}
    trusted = any(
        row.get('review_status', 'approved') == 'approved'
        and attrs.get('authority') == 'official_checklist'
        and str(attrs.get('source_url') or '').startswith('https://')
        and re.fullmatch(r'[0-9a-f]{64}', str(attrs.get('source_checksum') or ''))
        and normalize_text(attrs.get('player')) == normalize_text(subject)
        for row in resolver.card_catalog.lookup(scope)
        if (attrs := row.get('attributes') or {})
    )
    if not trusted:
        return text
    for match in reversed(matches):
        if re.search(r'(?:\bnot\s+|\bnon[ -]|不是|非)\s*$', text[:match.start()], re.I):
            continue
        text = text[:match.start()] + ' ' * (match.end() - match.start()) + text[match.end():]
    return text
