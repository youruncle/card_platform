"""Resolve observed catalogue subjects/year roles from four reviewed originals.

No year alias is created. The source title, target release and copyright
transcription retain their distinct meanings in both storage and audit.
"""
import re

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.entity_roles import official_cover_work_subject, title_without_catalog_subject
from card_platform.domain.product_identity import assess_identity_product_match, ProductMatchStatus
from card_platform.domain.reviewed_source_back import _reviewed_back_observation
from card_platform.domain.season import explicit_seasons
from card_platform.knowledge_card_catalog import text_key


def _claims_catalog_child(identity, title, resolver):
    def words(value):
        value = re.sub(r'\b(?:19|20)\d{2}(?:[-/]\d{2,4})?\b', '', str(value or '').casefold())
        return set(re.findall(r'[a-z]+|[\u3400-\u9fff]+', value)) - {'upper', 'deck', 'ud', 'ucc', 'the', 'checklist'}
    target, claimed = words(identity.get('product_name')), words(title)
    products = [e.attributes for e in resolver.entities.values() if e.entity_type == 'product']
    products.extend(resolver.card_catalog.artifacts)
    return bool(target and any(target < other <= claimed for a in products
        if (other := words(a.get('product') or a.get('product_name')))))


def reviewed_catalog_back_context(identity, title, visual_facts, *, resolver=None):
    if resolver is None:
        return None
    target = CardIdentityResolver().resolve(identity)
    observation = _reviewed_back_observation(target, title, visual_facts)
    if (not observation or not {'player_name', 'brand_name', 'card_number'} <= set(observation['fields'])
            or assess_identity_product_match(title, identity, resolver=resolver) != ProductMatchStatus.MATCH
            or _claims_catalog_child(identity, title, resolver)):
        return None
    context = observation.get('paired_catalog_text')
    if not isinstance(context, dict):
        return None
    texts = {}
    for key, minimum in (('heading', 15), ('copyright', 18), ('body', 80)):
        pair = context.get(key)
        if (not isinstance(pair, dict) or not isinstance(pair.get('target'), str)
                or not isinstance(pair.get('candidate'), str)):
            return None
        left, right = text_key(pair['target']), text_key(pair['candidate'])
        if len(left) < minimum or left != right:
            return None
        texts[key] = pair['target']
    subject, insert = text_key(target.player_name), text_key(target.insert)
    if not subject or not insert or not all(v in text_key(texts['heading']) for v in (subject, insert)):
        return None
    rows = [r for r in resolver.card_catalog.lookup(identity)
        if (a := r.get('attributes') or {}).get('authority') == 'official_checklist'
        and str(a.get('source_url') or '').startswith('https://')
        and re.fullmatch(r'[a-f0-9]{64}', str(a.get('source_checksum') or ''))
        and a.get('source_locator') and text_key(a.get('player')) == subject
        and text_key(a.get('insert')) == insert and text_key(a.get('section')) == insert]
    if not rows:
        return None
    return dict(rows=rows, texts=texts, observation=observation)


def reviewed_cover_subject(identity, title, visual_facts, *, resolver=None):
    """Fill an omitted cover work only; explicit other issue numbers still conflict."""
    identity = {**identity, 'insert_name': identity.get('insert_name') or identity.get('insert')}
    if not official_cover_work_subject(identity, resolver=resolver):
        return False
    context = reviewed_catalog_back_context(identity, title, visual_facts, resolver=resolver)
    if not context or text_key(identity.get('insert_name') or identity.get('insert')) not in text_key(title):
        return False
    expected = re.search(r'#\s*(\d+[A-Za-z]?)$', str(identity.get('player_name')))
    issues = set(re.findall(r'#\s*(\d+[A-Za-z]?)(?![A-Za-z0-9])', str(title)))
    return bool(expected and (not issues or issues == {expected[1]}))


def reviewed_copyright_year_label(identity, title, visual_facts, *, resolver=None):
    context = reviewed_catalog_back_context(identity, title, visual_facts, resolver=resolver)
    if not context:
        return None
    text = title_without_catalog_subject(title, identity, resolver=resolver)
    years = {int(y) for y in re.findall(r'(?<![\d/])((?:19|20)\d{2})(?!\d|\s*[编遍])', text)}
    if len(years) != 1:
        return None
    year = next(iter(years))
    if explicit_seasons(text):
        return None
    copyright_text = context['texts']['copyright']
    if not re.search(r'(?:©|\bcopyright\b)\s*' + str(year) + r'\b', copyright_text, re.I):
        return None
    for label in getattr(resolver.card_catalog, 'copyright_year_labels', ()):
        if (label.get('contract') != 'product-copyright-year-label-v1'
                or type(label.get('copyright_year')) is not int or label['copyright_year'] != year
                or (label.get('season_start'), label.get('season_end')) !=
                    (identity.get('season_start'), identity.get('season_end'))
                or any(text_key(label.get(a)) != text_key(identity.get(b)) for a, b in
                    [('brand', 'brand_name'), ('product', 'product_name')])
                or text_key(label.get('section')) != text_key(identity.get('insert_name') or identity.get('insert'))):
            continue
        official = next((r for r in context['rows'] if
            r['attributes']['source_url'] == label.get('official_source_url')
            and r['attributes']['source_checksum'] == label.get('official_source_checksum')), None)
        observations = label.get('observations')
        if not official or not isinstance(observations, list):
            continue
        from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url, _image_url
        valid = [o for o in observations if isinstance(o, dict)
            and o.get('role') == 'printed_copyright_not_release_year'
            and canonical_public_item_url(o.get('source_url'))
            and o.get('image_url') and _image_url(o['image_url']) == o['image_url']
            and re.fullmatch(r'[a-f0-9]{64}', str(o.get('image_sha256') or ''))
            and text_key(o.get('observed_text')) == text_key(copyright_text)]
        if valid:
            return dict(copyright_year=year, role='printed_copyright_not_release_year',
                official_card_id=official['id'], official_source_url=label['official_source_url'],
                official_source_checksum=label['official_source_checksum'], knowledge_version=resolver.version,
                source_back_sha256=context['observation']['image_sha256'])
    return None
