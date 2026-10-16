"""Transcribed sale-back identity, authorized only by current reviewed originals."""
import json
import re

from card_platform.knowledge_card_catalog import text_key
from card_platform.ingestion.cardhobby_public_item import canonical_public_item_url, _image_url

SOURCE_BACK_CONTRACT = 'reviewed-source-back-identity-v1'
PARTIAL_BACK_CONTRACT = 'reviewed-source-back-fields-v1'
CORE_FIELDS = ('player_name', 'brand_name', 'product_name', 'season_start', 'season_end', 'card_number')
SOURCE_BACK_OMISSIONS = frozenset({
    '标题未提供可验证的目标产品信息',
    '标题未命中目标卡号或卡种',
    '标题未命中目标卡号，基础/原生闪卡需图像确认',
    '目标表面版本未知，需图像确认',
    '标题只命中球员和产品，缺少赛季、卡号、版本或限编锚点',
    '标题未命中目标卡种，需图像确认',
})


def source_back_observation_matches(observation, identity, source_url, front_url, current_sha):
    """Validate receipt structure and exact transcribed core; never infer a finish.

    The writer separately checks the archived gallery and its original bytes.
    Runtime callers supply a hash freshly read from the private image cache.
    """
    if not isinstance(observation, dict) or not current_sha:
        return False
    source = canonical_public_item_url(source_url)
    back_url = observation.get('image_url')
    if (observation.get('contract') not in {SOURCE_BACK_CONTRACT, PARTIAL_BACK_CONTRACT}
            or observation.get('side') != 'back' or observation.get('reviewed_by') != 'codex'
            or not source or canonical_public_item_url(observation.get('source_url')) != source
            or observation.get('candidate_front_url') != front_url
            or not back_url or _image_url(back_url) != back_url or back_url == front_url
            or observation.get('image_sha256') != current_sha
            or not re.fullmatch(r'[0-9a-f]{64}', current_sha)
            or not re.fullmatch(r'[0-9a-f]{64}', str(observation.get('gallery_html_sha256') or ''))):
        return False
    urls = observation.get('gallery_image_urls')
    if not isinstance(urls, list) or front_url not in urls or back_url not in urls:
        return False
    fields = observation.get('fields')
    if not isinstance(fields, dict):
        return False
    partial = observation['contract'] == PARTIAL_BACK_CONTRACT
    required = ('player_name', 'brand_name', 'card_number') if partial else CORE_FIELDS
    if partial and (not set(required) <= set(fields) or not set(fields) <= set(CORE_FIELDS) | {'serial_total', 'parallel_name'}):
        return False
    for key in (set(fields) & set(CORE_FIELDS) if partial else CORE_FIELDS):
        field = fields.get(key)
        if (not isinstance(field, dict) or not str(field.get('observed_text') or '').strip()
                or not identity.get(key) or not field.get('value')
                or text_key(field['value']) != text_key(identity[key])):
            return False
        if partial and text_key(field['value']) not in text_key(field['observed_text']):
            return False
        if partial and key == 'card_number':
            printed_number = re.sub(r'^(?:no\.?|number|卡号|#)\s*', '',
                                    str(field['observed_text']).strip(), flags=re.I)
            if text_key(printed_number) != text_key(field['value']):
                return False
    for key in ('serial_total', 'parallel_name'):
        if key not in fields:
            continue
        field = fields[key]
        expected = identity.get(key) or identity.get('print_run' if key == 'serial_total' else 'parallel')
        if (not isinstance(field, dict) or not expected
                or text_key(field.get('value')) != text_key(expected)):
            return False
        observed = str(field.get('observed_text') or '').strip()
        if key == 'serial_total':
            # This is a typed serial transcription from an independently
            # reviewed, byte-bound sale back. Generic title "x of y" remains
            # ambiguous with checklist ordinals and is not parsed as a run.
            stamp = re.fullmatch(r'(\d+)\s*(?:/|of)\s*(\d+)', observed, re.I)
            if not stamp or not (0 < int(stamp[1]) <= int(stamp[2]) == int(expected)):
                return False
            ordinal = re.fullmatch(r'#?\s*(\d+)\s*of\s*(\d+)',
                                   str(identity.get('card_number') or '').strip(), re.I)
            if ordinal and tuple(map(int, ordinal.groups())) == tuple(map(int, stamp.groups())):
                return False
        elif text_key(observed) != text_key(expected):
            return False
    return True


def _reviewed_back_observation(target, title, visual_facts):
    from card_platform.domain.finish_evidence import VisualMatchFacts
    if (not isinstance(visual_facts, VisualMatchFacts)
            or not visual_facts.valid_for_target(target, title)
            or visual_facts._reviewed_proof is None
            or not visual_facts._reviewed_proof.source_back_json):
        return None
    observation = json.loads(visual_facts._reviewed_proof.source_back_json)
    if source_back_observation_matches(observation, target.as_dict(),
            visual_facts.candidate_id, visual_facts.image_url, observation.get('image_sha256')):
        return observation
    return None


def reviewed_source_back_matches(target, title, visual_facts):
    observation = _reviewed_back_observation(target, title, visual_facts)
    return bool(observation and set(CORE_FIELDS) <= set(observation['fields']))


def reviewed_back_edition_matches(target, title, visual_facts):
    """Same transcribed reverse context; never a code-to-release-year lookup.

    Both current originals are bound by the independent review. A matching
    code alone cannot prove an edition: copyright and substantial body text
    must also agree. Product confirmation additionally needs an official row.
    """
    observation = _reviewed_back_observation(target, title, visual_facts)
    if not observation:
        return False
    context = observation.get('paired_edition_text')
    if not isinstance(context, dict):
        return False
    for key, minimum in (('production_code', 6), ('copyright', 20), ('body', 80)):
        pair = context.get(key)
        if (not isinstance(pair, dict) or not isinstance(pair.get('target'), str)
                or not isinstance(pair.get('candidate'), str)):
            return False
        left, right = text_key(pair['target']), text_key(pair['candidate'])
        if len(left) < minimum or left != right:
            return False
    return True


def reviewed_source_back_fields(target, title, visual_facts):
    """Only fields transcribed from the current, sealed sale-back original."""
    observation = _reviewed_back_observation(target, title, visual_facts)
    if not observation:
        return frozenset()
    fields = observation['fields']
    supported = set(fields)
    # A denominator shared by several finishes cannot identify the finish.
    if target.parallel and 'parallel_name' not in supported:
        supported.discard('serial_total')
    return frozenset(supported)


def reviewed_source_back_omissions(target, title, visual_facts):
    fields = reviewed_source_back_fields(target, title, visual_facts)
    if not fields:
        return frozenset()
    omissions = set(SOURCE_BACK_OMISSIONS) if set(CORE_FIELDS) <= fields else set()
    if 'card_number' in fields:
        omissions.update({'标题未命中目标卡号或卡种', '标题未命中目标卡号，基础/原生闪卡需图像确认'})
    if 'serial_total' in fields:
        omissions.add('目标为限编卡，但标题未命中目标总限编')
    if 'parallel_name' in fields:
        omissions.add('标题未命中目标平行版本')
    return frozenset(omissions)


def reviewed_front_serial_digest(target, title, visual_facts):
    """A front stamp supplements only the total, from a sealed paired review.

    The paired proof already verifies both current target originals and the
    current sale front. No source-back claim or inferred finish is created.
    """
    from card_platform.domain.finish_evidence import VisualMatchFacts
    from card_platform.valuation_trace import content_hash
    if (not isinstance(visual_facts, VisualMatchFacts)
            or not visual_facts.valid_for_target(target, title)
            or visual_facts._reviewed_proof is None):
        return None
    proof = visual_facts._reviewed_proof
    review = json.loads(proof.review_json)
    observation = review.get('source_front_serial')
    binding = json.loads(proof.binding_json)
    total = target.serial_total or target.print_run
    if (review.get('source') != 'codex_paired_image_review'
            or not isinstance(observation, dict) or not total
            or observation.get('contract') != 'reviewed-front-serial-v1'
            or observation.get('side') != 'front'
            or observation.get('image_sha256') != binding.get('candidate_sha256')
            or observation.get('value') != total):
        return None
    for key in ('observed_text', 'target_observed_text'):
        stamp = re.fullmatch(r'(\d+)\s*/\s*(\d+)', str(observation.get(key) or '').strip())
        if not stamp or not (0 < int(stamp[1]) <= int(stamp[2]) == total):
            return None
        if key == 'target_observed_text' and target.serial_number and int(stamp[1]) != target.serial_number:
            return None
    return content_hash(observation)
