"""Reviewed reference facts for research, separate from official enrichment."""
from collections import defaultdict
from copy import deepcopy
import re

from card_platform.knowledge import normalize_knowledge_text as key


class ReviewedReferenceCatalog:
    def __init__(self, entities=(), *, version=None):
        self.version = version
        self.index = defaultdict(list)
        for entity in entities:
            catalog = (entity.get('attributes') or {}).get('reference_catalog')
            if not isinstance(catalog, dict) or entity.get('entity_type') != 'product':
                continue
            if entity.get('review_status', 'approved') != 'approved':
                continue
            source = catalog.get('source_url')
            checksum = catalog.get('source_checksum')
            start, end = catalog.get('season_start'), catalog.get('season_end')
            labels, rows = catalog.get('product_labels'), catalog.get('rows')
            if (catalog.get('review_status') != 'approved'
                    or catalog.get('authority') != 'third_party_catalog'
                    or catalog.get('capture_kind') not in {
                        'web_reader_extraction', 'search_index_extraction', 'original_document'}
                    or not isinstance(source, str) or not source.startswith('https://')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(checksum or ''))
                    or type(start) is not int or type(end) is not int
                    or not 1850 <= start <= end <= start + 1
                    or not key(catalog.get('brand')) or not key(catalog.get('product'))
                    or not isinstance(labels, list) or not labels
                    or not all(isinstance(label, str) and key(label) for label in labels)
                    or not isinstance(rows, list) or not rows):
                continue
            if any(not isinstance(row, dict)
                   or not key(row.get('card_number')) or not key(row.get('subject'))
                   or row.get('subject_role') not in {'team', 'player_or_multi_player'}
                   or row.get('source_url') != source or row.get('source_checksum') != checksum
                   or not str(row.get('source_locator') or '').strip() for row in rows):
                continue
            if len({key(row['card_number']) for row in rows}) != len(rows):
                continue
            for row in rows:
                record = dict(
                    brand_name=catalog['brand'], product_name=catalog['product'],
                    season_start=start, season_end=end, insert_name=catalog.get('insert'),
                    card_number=row['card_number'], subject=row['subject'],
                    subject_role=row['subject_role'], authority=catalog['authority'],
                    source_url=source, source_checksum=checksum,
                    source_locator=row['source_locator'], capture_kind=catalog['capture_kind'],
                    knowledge_version=version, entity_id=str(entity['id']),
                    requires_physical_review=True,
                )
                for label in {key(label) for label in labels}:
                    self.index[(key(catalog['brand']), label, key(row['card_number']))].append(record)

    def research_candidates(self, identity, *, limit=12):
        """Return discrepancies to investigate; never alter identity or price."""
        scope = tuple(key(identity.get(field)) for field in (
            'brand_name', 'product_name', 'card_number'))
        if not all(scope):
            return []
        candidates = []
        for record in self.index.get(scope, ()):
            different, missing = [], []
            for field, value in (
                ('player_name', record['subject']), ('season_start', record['season_start']),
                ('season_end', record['season_end']), ('insert_name', record['insert_name']),
            ):
                if value is None:
                    continue
                current = identity.get(field)
                if current in (None, ''):
                    missing.append(field)
                elif key(current) != key(value):
                    different.append(field)
            candidates.append({**deepcopy(record), 'differing_fields': different,
                               'missing_fields': missing})
        candidates.sort(key=lambda row: (len(row['differing_fields']), len(row['missing_fields']),
                                         row['season_start'], row['source_url'], row['source_locator']))
        return candidates[:max(0, min(int(limit), 50))]
