"""Offline source-scoped atlas view over existing official checklist IDs.

No fetch, database, schema, canonical cross-source merge or publication. Full
official section names are identity atoms; missing pictures are a separate state.
The caller verifies the frozen official artifact before passing its rows, and
uses the existing media validator before passing image supplements.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from card_platform.knowledge_card_catalog import product_key

READ_ADAPTER_FINGERPRINT = sha256(Path(__file__).read_bytes()).hexdigest()


IDENTITY_FIELDS = ('source_key', 'season_start', 'season_end', 'product', 'section',
                   'player', 'card_number', 'source_variant_label')


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _identity(attrs):
    return {field: deepcopy(attrs.get(field)) for field in IDENTITY_FIELDS}


def _serial_status(attrs):
    if type(attrs.get('serial_total')) is int and attrs['serial_total'] > 0:
        return 'known'
    evidence = attrs.get('serial_status_evidence') or {}
    quote = evidence.get('quote')
    if (attrs.get('serial_status') == 'confirmed_unnumbered' and isinstance(quote, str)
            and quote and quote in str(attrs.get('raw_text') or '')
            and re.search(r'\b(?:unnumbered|not serial numbered)\b', quote, re.I)
            and evidence.get('source_url') == attrs.get('source_url')
            and evidence.get('source_checksum') == attrs.get('source_checksum')
            and evidence.get('locator')):
        return 'not_applicable'
    return 'unknown'


def build_projection(rows, scope):
    """Establish only explicit source identities; do not expand parallel rules."""
    result = {'mode': 'offline_source_scoped_projection', 'scope': deepcopy(scope),
              'atlases': {}, 'pending': []}
    counts = Counter(row.get('id') for row in rows)
    groups = defaultdict(list)
    for row in rows:
        attrs = row.get('attributes') or {}
        required = ('product', 'section', 'player', 'card_number', 'source_url',
                    'source_checksum', 'source_locator', 'raw_text')
        valid = (row.get('entity_type') == 'card'
                 and str(row.get('id') or '').startswith('official:' + str(scope.get('source_key') or '') + ':')
                 and attrs.get('sport') == 'basketball'
                 and type(attrs.get('season_start')) is int and type(attrs.get('season_end')) is int
                 and all(isinstance(attrs.get(k), str) and attrs[k].strip() for k in required)
                 and all(attrs.get(k) == v for k, v in scope.items())
                 and re.fullmatch(r'[a-f0-9]{64}', str(attrs.get('source_checksum') or ''))
                 and str(attrs.get('source_url') or '').startswith('https://')
                 and (attrs.get('serial_total') is None or
                      type(attrs['serial_total']) is int and attrs['serial_total'] > 0))
        if not valid or counts[row.get('id')] != 1:
            result['pending'].append({'row_id': row.get('id'), 'reason': 'invalid_scope_identity_or_duplicate_id'})
            continue
        groups[_digest(_identity(attrs))].append(row)
    for group in groups.values():
        if len(group) != 1:
            result['pending'].extend({'row_id': r['id'], 'reason': 'conflicting_source_identity'} for r in group)
            continue
        row = group[0]
        attrs = deepcopy(row['attributes'])
        positive_auto = bool(re.search(r'\b(?:autographs?|signatures)\b', attrs['section'], re.I)
                             and not re.search(r'\bfacsimile\b', attrs['section'], re.I))
        result['atlases'][row['id']] = {
            'atlas_id': row['id'], 'identity': _identity(attrs), 'attributes': attrs,
            'identity_status': 'confirmed_within_official_source',
            'serial_status': _serial_status(attrs),
            'autograph_status': 'manufacturer_named_autograph_family' if positive_auto else 'unknown',
            'physical_autograph_status': None, 'claims': [], 'conflicts': [],
            'derived_fields': {}, 'image_status': 'missing',
        }
    return result


def lookup_exact(projection, query):
    """Exact full source identity only; abbreviations are not version evidence."""
    matches = [key for key, entry in projection['atlases'].items()
               if entry['identity'] == query]
    return matches[0] if len(matches) == 1 else None


def _update_image_status(entry):
    images = [c for c in entry['claims'] if c['field'] == 'reference_image']
    sides = {c['value']['side'] for c in images}
    pairs = defaultdict(set)
    for claim in images:
        image = claim['value']
        if image.get('pair_id'):
            pairs[(image['pair_id'], claim['source']['url'])].add(image['side'])
    entry['image_status'] = ('paired' if any(s == {'front', 'back'} for s in pairs.values()) else
                             'front_and_back_unpaired' if sides == {'front', 'back'} else
                             'front_only' if sides == {'front'} else 'back_only' if sides else 'missing')


def apply_supplement(projection, atlas_id, supplement):
    """Append provenance to an exact existing identity; never create or rekey one.

    A seller's specimen claim stays a source-qualified claim, not a manufacturer
    assertion or a new canonical physical signature classification.
    """
    if atlas_id not in projection['atlases']:
        raise ValueError('unknown atlas identity')
    entry = projection['atlases'][atlas_id]
    if supplement.get('identity') != entry['identity']:
        raise ValueError('supplement identity differs from atlas')
    source = supplement.get('source') or {}
    if (not str(source.get('url') or '').startswith('https://')
            or not re.fullmatch(r'[a-f0-9]{64}', str(source.get('sha256') or ''))
            or not all(source.get(k) for k in ('locator', 'authority', 'usage_scope'))):
        raise ValueError('supplement lacks source provenance')
    field, value = supplement.get('field'), supplement.get('value')
    if not isinstance(field, str) or not field:
        raise ValueError('supplement lacks field')
    if field in IDENTITY_FIELDS:
        raise ValueError('identity fields require separate review')
    claim = deepcopy(supplement)
    if claim in entry['claims'] or claim in entry['conflicts']:
        return 'duplicate'
    if field == 'reference_image':
        if (not isinstance(value, dict) or value.get('verified_official_card_id') != atlas_id
                or value.get('side') not in {'front', 'back'}
                or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('sha256') or ''))):
            raise ValueError('image lacks exact validated identity')
        if any(c['field'] == field and c['value']['sha256'] == value['sha256']
               and c['value']['side'] != value['side'] for c in entry['claims']):
            raise ValueError('same image cannot establish both faces')
    elif field.startswith('specimen_') or re.search(r'autograph|signature|authentic', field, re.I):
        # A declaration about one pictured/listed copy is never a catalogue-
        # wide value. Contradictory declarations on that same source stay queued.
        if any(c['field'] == field and c['source']['url'] == source['url']
               and (type(c['value']) is not type(value) or c['value'] != value)
               for c in entry['claims']):
            entry['conflicts'].append(claim)
            return 'conflict'
    else:
        existing = entry['attributes'].get(field)
        if existing is None:
            existing = entry['derived_fields'].get(field)
        if existing is not None and (type(value) is not type(existing) or value != existing):
            entry['conflicts'].append(claim)
            return 'conflict'
        if value is not None:
            entry['derived_fields'][field] = deepcopy(value)
    entry['claims'].append(claim)
    _update_image_status(entry)
    return 'added'


def atlas_metadata(entity, entry=None, *, pending=False):
    """Small read descriptor; never a new catalogue or a global canonical ID."""
    attrs = entity.get('attributes') or {}
    if entry is None and not pending and attrs.get('source_key'):
        scope = {k: attrs.get(k) for k in ('source_key', 'season_start', 'season_end', 'product')}
        entry = build_projection([entity], scope)['atlases'].get(entity.get('id'))
    return {
        'atlas_id': entry['atlas_id'] if entry else None,
        'identity_status': entry['identity_status'] if entry else 'needs_review',
        'identity_scope': 'official_source_record', 'global_canonical_id': None,
        'serial_status': entry['serial_status'] if entry else 'unknown',
        'autograph_status': entry['autograph_status'] if entry else 'unknown',
    }


def reviewed_external_mapping(card, ref):
    """Consume a published, exact reviewed binding; never guess from a title.

    Source assertions remain attributed claims. Hashes bind to prior intake
    review; this reader does not replace original-byte/review admission checks.
    """
    proof = ref.get('identity_verification')
    proof = proof if isinstance(proof, dict) else {}
    record = ref.get('pair_evidence')
    record = record if isinstance(record, dict) else {}
    observed = ref.get('source_card_identity')
    observed = observed if isinstance(observed, dict) else {}
    record_id = record.get('record_id') or observed.get('record_id') or observed.get('certification_number') or observed.get('cert')
    record_type = record.get('record_type') or observed.get('record_type')
    def public_url(value):
        try:
            parts = urlsplit(value) if isinstance(value, str) else None
            return value if parts and parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password else None
        except ValueError:
            return None

    source_url = public_url(ref.get('source_url'))
    normal = lambda value: ' '.join(str(value or '').casefold().split())
    source_player = observed.get('player') or observed.get('player_as_source')
    source_number = observed.get('card_number') or observed.get('card_number_as_source')
    source_product = observed.get('product') or observed.get('product_as_source')
    season = str(observed.get('season') or observed.get('season_from_visible_back') or observed.get('year_as_source') or '')
    year = re.match(r'^(\d{4})(?:[-/](\d{2,4}))?$', season)
    year_ok = bool(year and int(year[1]) == card.get('season_start'))
    if year_ok and year[2]:
        end = int(year[2]) if len(year[2]) == 4 else int(year[1]) // 100 * 100 + int(year[2])
        year_ok = end == card.get('season_end')
    elif year_ok:
        year_ok = card.get('season_start') == card.get('season_end')
    hash_ok = lambda value: bool(re.fullmatch(r'[a-f0-9]{64}', str(value or '')))
    reason = []
    if card.get('atlas', {}).get('identity_status') != 'confirmed_within_official_source':
        reason.append('target_identity_pending')
    if (proof.get('status') != 'verified' or proof.get('card_id') != card['id']
            or proof.get('candidate_card_ids') != [card['id']]
            or not hash_ok(card.get('official_artifact_sha256'))
            or proof.get('artifact_sha256') != card.get('official_artifact_sha256')):
        reason.append('no_unique_reviewed_official_target')
    if (not hash_ok(ref.get('source_artifact_sha256')) or not hash_ok(ref.get('review_artifact_sha256'))
            or proof.get('source_sha256') != ref.get('source_artifact_sha256')
            or proof.get('source_url') != source_url or not proof.get('source_locator')):
        reason.append('missing_or_conflicting_review_provenance')
    if (not record_id or record_type not in {'certification', 'certification_record', 'public_specimen_listing'}
            or record.get('record_url', source_url) != source_url):
        reason.append('source_record_unknown')
    # Only captured provider record formats supported by the existing intake
    # are recognized. A different hostname/CDN does not establish independence.
    provider = None
    if source_url:
        parts = urlsplit(source_url)
        host = parts.hostname or ''
        if record_type == 'public_specimen_listing' and (host == 'ebay.com' or host.endswith('.ebay.com')):
            match = re.fullmatch(r'/itm/(?:[^/]+/)?(\d+)/?', parts.path)
            if match and match[1] == str(record_id):
                provider = 'ebay_listing'
        elif record_type in {'certification', 'certification_record'} and (host == 'psacard.com' or host.endswith('.psacard.com')):
            match = re.fullmatch(r'/cert/(\d+)(?:/psa)?/?', parts.path, re.I)
            if match and match[1] == str(record_id):
                provider = 'psa_certification'
    if provider is None:
        reason.append('unsupported_or_conflicting_source_record_profile')
    observed_record = observed.get('record_id') or observed.get('certification_number') or observed.get('cert')
    if observed_record is not None and str(observed_record) != str(record_id):
        reason.append('captured_record_id_conflict')
    if (ref.get('source_key') != card.get('source_key')
            or not ref.get('variant') or normal(ref['variant']) != normal(card.get('section'))
            or not source_player or normal(source_player) != normal(card.get('player'))
            or not source_number or normal(source_number) != normal(card.get('card_number'))
            or normal(ref.get('player')) != normal(card.get('player'))
            or normal(ref.get('card_number')) != normal(card.get('card_number'))
            or not source_product or product_key(source_product) != product_key(card.get('source_product'))
            or not year_ok):
        reason.append('unknown_or_conflicting_identity_fields')
    source_maker = observed.get('manufacturer')
    source_variant = observed.get('variant') or observed.get('parallel_as_source') or observed.get('variant_as_source')
    if source_maker and normal(source_maker) != normal(card.get('manufacturer')):
        reason.append('source_manufacturer_conflict')
    # This validates a prior exact review, never selects a target by suffix.
    if not source_variant or normal(source_variant) not in normal(ref.get('variant')):
        reason.append('source_variant_unknown_or_conflicting')
    if observed.get('official_card_set') is not None and normal(observed['official_card_set']) != normal(card.get('section')):
        reason.append('captured_official_section_conflict')
    totals = [v for v in (ref.get('serial_total'), observed.get('serial_total'), observed.get('print_run')) if v is not None]
    if any(type(v) is not int or v != card.get('serial_total') for v in totals):
        reason.append('serial_total_conflict')
    if card.get('serial_total') is not None and not totals:
        reason.append('source_serial_total_unknown')
    numbers = [v for v in (ref.get('serial_number'), observed.get('serial_number')) if v is not None]
    if numbers and (type(card.get('serial_total')) is not int
                    or any(type(n) is not int or not 0 < n <= card['serial_total'] for n in numbers)
                    or len(set(numbers)) != 1):
        reason.append('conflicting_or_invalid_specimen_serial')
    official_hosts = {urlsplit(url).hostname for s in card.get('sources', []) if (url := public_url(s.get('url')))}
    if not source_url or not official_hosts or urlsplit(source_url).hostname in official_hosts:
        reason.append('source_independence_not_established')
    assertions = {}
    for field, value in {
        'player': source_player, 'card_number': source_number, 'product': source_product,
        'season': observed.get('season') or observed.get('year_as_source'),
        'variant': observed.get('variant') or observed.get('parallel_as_source') or observed.get('variant_as_source'),
        'serial_total': observed.get('serial_total'), 'serial_number': observed.get('serial_number'),
    }.items():
        if value is not None and isinstance(value, (str, int, bool)):
            assertions[field] = {'value': value, 'scope': 'source_record_claim'}
    return {
        'role': 'reviewed_external_record', 'source_id': f'{record_type}:{record_id}' if record_id else source_url,
        'source_system': provider,
        'source_record_id': str(record_id) if record_id else None, 'source_record_type': record_type,
        'source_url': source_url, 'source_sha256': ref.get('source_artifact_sha256'),
        'review_sha256': ref.get('review_artifact_sha256'),
        'source_asset_ids': [ref['asset_id']] if ref.get('asset_id') else [],
        'atlas_id': card['id'] if not reason else None,
        'mapping_status': 'confirmed_by_existing_review' if not reason else 'needs_review',
        'reasons': reason, 'assertions': assertions,
        'reviewed_target_section': ref.get('variant'),
        'usage_scope': ref.get('usage_scope'), 'rights': ref.get('rights'),
        'observations': [{'source_sha256': ref.get('source_artifact_sha256'),
                          'review_sha256': ref.get('review_artifact_sha256'),
                          'source_asset_id': ref.get('asset_id'), 'assertions': assertions,
                          'usage_scope': ref.get('usage_scope'), 'rights': ref.get('rights')}],
    }


def combine_source_mappings(card, images):
    mappings = [{
        'role': 'official_backbone' if card.get('atlas', {}).get('atlas_id') else 'backbone_record_needs_review',
        'source_id': card['id'], 'source_key': card.get('source_key'),
        'atlas_id': card.get('atlas', {}).get('atlas_id'),
        'mapping_status': card.get('atlas', {}).get('identity_status', 'needs_review'),
        'sources': card.get('sources', []), 'source_sha256': card.get('source_checksum'),
    }]
    grouped = {}
    for image in images:
        mapping = image.get('source_mapping')
        if not isinstance(mapping, dict):
            continue
        key = ((mapping.get('source_system'), mapping.get('source_record_id'))
               if mapping.get('source_system') else
               (mapping.get('source_id'), mapping.get('source_url')))
        if key not in grouped:
            grouped[key] = deepcopy(mapping)
            continue
        previous = grouped[key]
        previous['source_asset_ids'] = sorted(set(previous['source_asset_ids'] + mapping['source_asset_ids']))
        previous['observations'].extend(o for o in mapping['observations'] if o not in previous['observations'])
        if (previous['atlas_id'] != mapping['atlas_id'] or previous['assertions'] != mapping['assertions']
                or previous['source_sha256'] != mapping['source_sha256']
                or previous['review_sha256'] != mapping['review_sha256']):
            previous.update(atlas_id=None, mapping_status='needs_review')
            previous['reasons'] = sorted(set(previous['reasons'] + mapping['reasons'] + ['conflicting_same_record_faces']))
    return mappings + list(grouped.values())
