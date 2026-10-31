"""Shared packet admission used by CLI and authenticated cloud research."""
import hashlib

from card_platform.domain.codex_sales_research import CONTRACT_VERSION, RESEARCH_INSTRUCTIONS, assess_sales_research


def validate_captures(data, root):
    """Local evidence is required before a result can be imported/promoted."""
    errors = []
    records = [*data.get('searches', []), *data.get('sales', [])]
    records += [image for sale in data.get('sales', []) for image in sale.get('card_images', [])]
    for record in records:
        reference = record.get('capture_ref')
        path = (root / str(reference or '')).resolve()
        if not reference or not path.is_relative_to(root.resolve()) or not path.is_file():
            errors.append('missing_or_outside_capture: ' + str(reference))
        elif record.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            errors.append('capture_hash_mismatch: ' + str(reference))
    return errors


def prepare_packet(identity, front, back, item_id):
    return dict(contract_version=CONTRACT_VERSION, collection_item_id=str(item_id), identity=identity,
        currency='CNY', front_sha256=hashlib.sha256(front).hexdigest(),
        back_sha256=hashlib.sha256(back).hexdigest(), instructions=RESEARCH_INSTRUCTIONS,
        searches=[], sales=[], fx=[])


def validate_packet(data, root):
    result = assess_sales_research(data)
    errors = validate_captures(data, root)
    if errors:
        result.update(status='research_incomplete', low_value=None, fair_value=None, high_value=None, confidence=0)
        result['reasons'].extend(errors)
    return result
