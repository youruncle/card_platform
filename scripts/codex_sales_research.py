"""Prepare a per-card research packet or validate captured Codex research.

This command never writes inventory, identity, knowledge or valuation tables.
"""
import argparse
import hashlib
import json
from pathlib import Path

from card_platform.domain.codex_sales_research import (
    CONTRACT_VERSION, RESEARCH_INSTRUCTIONS, assess_sales_research,
)


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--identity', type=Path, required=True)
    prepare.add_argument('--front', type=Path, required=True)
    prepare.add_argument('--back', type=Path, required=True)
    prepare.add_argument('--item-id', required=True)
    prepare.add_argument('--out', type=Path, required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('research', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        identity = json.loads(args.identity.read_text())
        data = dict(contract_version=CONTRACT_VERSION, collection_item_id=args.item_id,
                    identity=identity, currency='CNY',
                    front_sha256=hashlib.sha256(args.front.read_bytes()).hexdigest(),
                    back_sha256=hashlib.sha256(args.back.read_bytes()).hexdigest(),
                    instructions=RESEARCH_INSTRUCTIONS, searches=[], sales=[], fx=[])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('x') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        print(args.out)
        return
    data = json.loads(args.research.read_text())
    result = assess_sales_research(data)
    errors = validate_captures(data, args.research.parent)
    if errors:
        result.update(status='research_incomplete', low_value=None, fair_value=None, high_value=None, confidence=0)
        result['reasons'].extend(errors)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status'] == 'research_incomplete':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
