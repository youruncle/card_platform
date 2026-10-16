"""Preview, or --execute, the same Codex fallback used by the background worker."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path

from card_platform.codex_valuation_fallback import backfill_codex_valuations
from card_platform.config import Settings
from card_platform.db import Database


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--upload-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(_env_file=args.env_file)
    database = Database(settings)
    await database.open()
    try:
        results = await backfill_codex_valuations(database, args.upload_dir or settings.resolved_upload_dir,
                                                dry_run=not args.execute)
        report = {'execute': args.execute, 'counts': dict(Counter(r['status'] for r in results)), 'results': results}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report['counts'], ensure_ascii=False))
        if any(r['status'] == 'error' for r in results):
            raise SystemExit(1)
    finally:
        await database.close()


if __name__ == '__main__':
    asyncio.run(main())
