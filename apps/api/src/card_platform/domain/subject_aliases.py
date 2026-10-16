"""Published subject labels shared by discovery and valuation identity checks."""
import re

from card_platform.domain.player_aliases import PLAYER_MATCH_ALIAS_EXCLUSIONS
from card_platform.knowledge import normalize_knowledge_text


def published_subject_aliases(name, resolver=None):
    if resolver is None or not callable(getattr(resolver, 'resolve', None)):
        return ()
    if resolver.resolve('player', name).entity_id is None:
        return ()
    aliases = resolver.aliases_for('player', name)
    if not aliases:
        return ()
    excluded = {normalize_knowledge_text(a) for a in
                PLAYER_MATCH_ALIAS_EXCLUSIONS.get(normalize_knowledge_text(aliases[0]), ())}
    return tuple(a for a in aliases if normalize_knowledge_text(a) not in excluded)


def _subject_text(value):
    text = normalize_knowledge_text(value)
    return re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])', '', text)


def _subject_pattern(token):
    # Latin names need word boundaries; Chinese labels often adjoin a year,
    # card number or Latin product name without whitespace.
    left = r'(?<![a-z0-9])' if re.match(r'[a-z0-9]', token[0]) else ''
    right = r'(?![a-z0-9])' if re.match(r'[a-z0-9]', token[-1]) else ''
    return left + re.escape(token) + right


def published_subject_matches(title, name, resolver=None):
    aliases = published_subject_aliases(name, resolver)
    if not aliases:
        return False
    # Book-title brackets explicitly mark a work, not its eponymous character.
    text = _subject_text(re.sub(r'《[^》]*》', ' ', str(title)))
    hits = []
    for alias in aliases:
        token = _subject_text(alias)
        if not token:
            continue
        for match in re.finditer(_subject_pattern(token), text):
            # A character mentioned as another character's relative/associate
            # is not the listed subject (e.g. 灭霸之女 or 毒液后代).
            if re.match(r'(?:之女|之子|后代|後代|爷爷|爺爺|好友|(?:经典|經典)?(?:宿敌|宿敵|女主)|的(?:女儿|女兒|儿子|兒子|父亲|父親|母亲|母親|好友))', text[match.end():].lstrip()):
                continue
            if re.search(r'(?:寄生|对抗|對抗|击败|擊敗)\s*$', text[:match.start()]):
                continue
            hits.append(match.span())
    if not hits:
        return False
    resolution = resolver.resolve('player', name)
    blockers = []
    for entity in resolver.entities.values():
        if entity.entity_type != 'player' or entity.id == resolution.entity_id:
            continue
        for other in (entity.canonical_name, *entity.aliases):
            token = _subject_text(other)
            if not token or token not in text or not any(len(token) > end-start for start, end in hits):
                continue
            for match in re.finditer(_subject_pattern(token), text):
                blockers.append(match.span())
    # A translated name embedded in a longer published subject is not a hit:
    # 蜘蛛侠 inside 钢铁蜘蛛侠 must not collapse two checklist subjects.
    return any(not any(a <= start and end <= b and b-a > end-start for a, b in blockers)
               for start, end in hits)
