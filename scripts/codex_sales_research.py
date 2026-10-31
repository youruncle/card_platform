"""Prepare a per-card research packet or validate captured Codex research.

This command never writes inventory, identity, knowledge or valuation tables.
"""
import argparse
import json
from pathlib import Path

from card_platform.domain.codex_sales_packet import prepare_packet, validate_packet, validate_captures  # noqa: F401




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
        data = prepare_packet(identity, args.front.read_bytes(), args.back.read_bytes(), args.item_id)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('x') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        print(args.out)
        return
    data = json.loads(args.research.read_text())
    result = validate_packet(data, args.research.parent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result['status'] == 'research_incomplete':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
