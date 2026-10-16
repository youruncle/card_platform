"""Per-source product relationship corroboration; third-party facts stay third-party."""
import json
import re

from card_platform.domain.card_identity import CardIdentityResolver
from card_platform.domain.finish_evidence import VisualMatchFacts
from card_platform.domain.independent_finish import PrintedTextField, _specific_text
from card_platform.domain.product_identity import (
    EXPLICIT_SPORTS, MANUFACTURER_PREFIXES, ProductMatchStatus,
    _alias_occurs_at_token_boundary, _has_explicit_other_product_label,
    assess_identity_product_match, normalize_product_text,
)
from card_platform.domain.season import explicit_seasons
from card_platform.knowledge_card_catalog import text_key


def reviewed_reference_product_evidence(title, identity, visual_facts, *, resolver=None):
    """Combine an exact published reference row with a current sealed original review.

    Product/insert headings are transcribed on both fronts; source year/maker
    and at least the full insert or card number must be explicit in the title.
    No catalog, stored boolean, model verdict or photograph similarity alone
    supplies this authority. Explicit title conflicts remain the caller's gate.
    """
    if (resolver is None or not isinstance(visual_facts, VisualMatchFacts)
            or visual_facts._reviewed_proof is None
            or not visual_facts.valid_for_target(CardIdentityResolver().resolve(identity), title)):
        return None
    identity = {**identity, 'insert_name': identity.get('insert_name') or identity.get('insert')}
    required = ('brand_name', 'product_name', 'season_start', 'season_end',
                'player_name', 'card_number', 'insert_name')
    if (not all(identity.get(k) for k in required)
            or text_key(identity['insert_name']) in {'base', 'basecards', 'baseset', 'insert'}
            or assess_identity_product_match(title, identity, resolver=resolver) != ProductMatchStatus.UNKNOWN):
        return None
    candidates = resolver.reference_catalog.research_candidates(identity, limit=50)
    if len(candidates) >= 50:
        return None  # A truncated research result cannot rule out ambiguity.
    rows = [r for r in candidates if not r['differing_fields'] and not r['missing_fields']
            and r['subject_role'] == 'player_or_multi_player'
            and text_key(r['product_name']) == text_key(identity['product_name'])]
    # Different reviewed sources may corroborate exactly the same row. A
    # conflicting sibling subset sharing this brand/product/number is ambiguity.
    if not rows or any(r['differing_fields'] or r['missing_fields'] for r in candidates):
        return None
    season = (identity['season_start'], identity['season_end'])
    seasons = explicit_seasons(title)
    years = {int(y) for y in re.findall(r'(?<![\d/])((?:19|20)\d{2})(?!\d|\s*[编遍])', str(title))}
    brand = normalize_product_text(identity['brand_name'])
    if (not years or not years <= set(season) or (seasons and seasons != {season})
            or not _alias_occurs_at_token_boundary(title, brand)
            or any(m != brand and _alias_occurs_at_token_boundary(title, m) for m in MANUFACTURER_PREFIXES)
            or _has_explicit_other_product_label(title, identity['product_name'], strong_only=False,
                target_roles=(identity.get('set_name'), identity['insert_name'], identity.get('parallel_name') or identity.get('parallel')))):
        return None
    target_sports = {s for s in EXPLICIT_SPORTS if _alias_occurs_at_token_boundary(identity['product_name'], s)}
    source_sports = {s for s in EXPLICIT_SPORTS if _alias_occurs_at_token_boundary(title, s)}
    if source_sports and source_sports != target_sports:
        return None
    number = re.search(r'(?<![A-Za-z0-9])(?:#|no\.?\s*|卡号\s*)' + re.escape(str(identity['card_number']))
                       + r'(?![A-Za-z0-9/])', str(title), re.I)
    if not number and not _alias_occurs_at_token_boundary(title, identity['insert_name']):
        return None
    proof = visual_facts._reviewed_proof
    binding = json.loads(proof.binding_json)
    if not re.fullmatch(r'[a-f0-9]{64}', str(binding.get('target_back_sha256') or '')):
        return None
    labels = json.loads(proof.review_json).get('printed_front_reference_insert')
    if not isinstance(labels, dict) or labels.get('contract') != 'reviewed-reference-front-insert-v1':
        return None
    try:
        for side in ('target', 'candidate'):
            observation = labels[side]
            if observation.get('view') != 'front':
                return None
            for kind, expected in (('brand', brand), ('insert', identity['insert_name'])):
                field = PrintedTextField.model_validate(observation[kind])
                if (field.status != 'readable' or not _specific_text(field.value)
                        or text_key(field.value) != text_key(expected) or not _specific_text(field.location)
                        or re.search(r'背面|卡背|反面|\bback(?:side)?\b', field.location, re.I)):
                    return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return {k: rows[0][k] for k in ('authority', 'source_url', 'source_checksum', 'source_locator',
        'capture_kind', 'knowledge_version', 'entity_id', 'brand_name', 'product_name',
        'season_start', 'season_end', 'insert_name', 'card_number', 'subject')}
