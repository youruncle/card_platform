#!/usr/bin/env python3
"""Acquire reviewed official card references without changing database records."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api/src'))
from card_platform.knowledge_media import collect_media  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=ROOT / 'data/knowledge-three-seasons/inventory.json')
    parser.add_argument('--sources', type=Path, required=True,
                        help='Product-bound URL queue with optional per-image review locators')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/knowledge-three-seasons/media')
    parser.add_argument('--delay', type=float, default=0.5)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    inventory = json.loads(args.inventory.read_text())
    sources = json.loads(args.sources.read_text())
    report = collect_media(inventory['products'], sources, args.output, delay=args.delay,
                           retry_failed=args.retry_failed)
    print(json.dumps({k: report[k] for k in ('asset_count', 'product_count', 'products_with_images',
                                            'discovery_complete')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
