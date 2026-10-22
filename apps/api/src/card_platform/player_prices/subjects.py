"""Canonical public market subjects without modifying private holdings."""
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
import unicodedata

from card_platform.domain.nba_player_catalog import nba_player_manifest


def name_key(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')).casefold()
                   if c.isalnum() and not unicodedata.combining(c))


@lru_cache(maxsize=1)
def _names():
    claims, query_only, by_nba_id = defaultdict(dict), set(), {}
    for entity in nba_player_manifest()['entities']:
        if entity.get('review_status') != 'approved':
            continue
        nba_id = str(entity['attributes']['nba_id'])
        name = entity['canonical_name']
        by_nba_id[nba_id] = name
        for alias in [name, *(a['value'] for a in entity.get('aliases', []))]:
            claims[name_key(alias)][nba_id] = name
        query_only.update(name_key(a) for a in entity['attributes'].get('query_aliases', []))
    return ({key: next(iter(values.items())) for key, values in claims.items() if len(values) == 1},
            {key for key, values in claims.items() if len(values) > 1} | (query_only - claims.keys()),
            by_nba_id)


def canonical_subject_name(value):
    return _names()[0].get(name_key(value), (None, str(value or '').strip()))[1]


class SubjectIndex:
    def __init__(self, players):
        names, ambiguous, by_nba_id = _names()
        groups = defaultdict(list)
        for row in players:
            key = name_key(row['full_name'])
            claimed, _ = names.get(key, (None, None))
            explicit = str(row['nba_person_id']) if row.get('nba_person_id') is not None else None
            if key in ambiguous or (claimed and explicit and claimed != explicit):
                group = f"unresolved:{row['id']}"
            else:
                group = f"nba:{explicit or claimed}" if explicit or claimed else f'name:{key}'
            groups[group].append(row)
        self.by_id = {}
        for key, rows in groups.items():
            births = {str(r['birth_date']) for r in rows if r.get('birth_date') is not None}
            partitions = defaultdict(list)
            for row in rows:
                # Conflicting biographical facts prevent automatic merging;
                # an undated row cannot pick either of two different people.
                suffix = str(row.get('birth_date') or row['id']) if len(births) > 1 else ''
                partitions[suffix].append(row)
            for suffix, members in partitions.items():
                members.sort(key=lambda r: (r.get('created_at') or datetime.min.replace(tzinfo=timezone.utc),
                                            str(r['id'])))
                first = members[0]
                name = (by_nba_id.get(key.removeprefix('nba:')) if key.startswith('nba:') else None)
                subject = dict(id=first['id'], full_name=name or canonical_subject_name(first['full_name']),
                               subject_key=f'{key}:{suffix}', member_ids=[r['id'] for r in members])
                for row in members:
                    self.by_id[str(row['id'])] = subject

    def get(self, player_id):
        return self.by_id.get(str(player_id))

    def for_ids(self, ids):
        subjects = {}
        for player_id in ids:
            subject = self.get(player_id)
            if subject:
                subjects[str(subject['id'])] = subject
        return sorted(subjects.values(), key=lambda r: r['full_name'])
