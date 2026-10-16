"""Scoped checklist membership and sealed original-image review, never title guesses."""
import json
import re

from card_platform.domain.finish_evidence import VisualMatchFacts
from card_platform.knowledge_card_catalog import text_key


def reviewed_unnumbered_card_anchor(target, title, visual_facts, *, resolver=None):
    """Support an omitted numeric ID on a base-set design, not its finish name.

    A current independent review must bind both held sides and the sale front.
    The caller still checks title conflicts, subject/year/product and finish.
    This does not assert that a sale back or its printed number was observed.
    """
    if (resolver is None or not target.card_number or target.insert
            or target.serial_total is not None or target.is_autograph or target.is_memorabilia
            or not isinstance(visual_facts, VisualMatchFacts)
            or not visual_facts.valid_for_target(target, title)
            or visual_facts._reviewed_proof is None):
        return False
    binding = json.loads(visual_facts._reviewed_proof.binding_json)
    if not all(re.fullmatch(r'[0-9a-f]{64}', str(binding.get(key) or ''))
               for key in ('target_front_sha256', 'target_back_sha256', 'candidate_sha256')):
        return False
    identity = target.as_dict()
    if not all(identity.get(key) for key in (
            'brand_name', 'product_name', 'season_start', 'season_end', 'player_name')):
        return False
    return any(
        attrs.get('authority') == 'official_checklist'
        and attrs.get('source_url') and attrs.get('source_locator')
        and re.fullmatch(r'[0-9a-f]{64}', str(attrs.get('source_checksum') or ''))
        and not attrs.get('insert')
        and (text_key(attrs.get('section')) in {'base', 'basecards', 'baseset'}
             or re.fullmatch(r'base\s+cards\s+(?:i{1,3}|iv|vi{0,3}|ix|x)',
                             str(attrs.get('section') or '').strip(), re.I))
        for attrs in (row.get('attributes') or {} for row in resolver.card_catalog.lookup(identity))
    )
