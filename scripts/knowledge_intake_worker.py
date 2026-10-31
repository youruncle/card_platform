#!/usr/bin/env python3
"""Cloud-only, resumable public-card acquisition and reviewed media export.

Run in the cloud API environment. This does not confirm inventory, value cards,
publish knowledge or run a language model. Review/export retain explicit audit gates.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api/src'))
import card_platform  # noqa: E402
# Permit an immutable standalone worker bundle to reuse the installed API package.
worker_package = str(ROOT / 'apps/api/src/card_platform')
if worker_package not in card_platform.__path__:
    card_platform.__path__.insert(0, worker_package)

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from card_platform.config import get_settings  # noqa: E402
from card_platform.knowledge_card_catalog import ScopedCardCatalog  # noqa: E402
from card_platform.knowledge_intake_queue import IntakeQueue  # noqa: E402
from card_platform.knowledge_intake_sources import CardHobbyImageSource  # noqa: E402
from card_platform.knowledge_intake_worker import export_assets, plan_targets  # noqa: E402


def publication(connection):
    row = connection.execute('''SELECT kv.id,kv.version_number,kv.checksum,
        (SELECT jsonb_agg(e) FROM jsonb_array_elements(kv.snapshot::jsonb->'entities') e
         WHERE e->>'entity_type'='product') products
        FROM knowledge_workspace kw JOIN knowledge_version kv ON kv.id=kw.published_version_id
        WHERE kw.name='Card Platform' ''').fetchone()
    if not row:
        raise ValueError('cloud_published_knowledge_missing')
    products = row.pop('products')
    row['id'] = str(row['id'])
    return row, products


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'run', 'report', 'review', 'export'])
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--apply-schema', action='store_true')
    parser.add_argument('--limit', type=int, default=300, help='Pilot target count for init')
    parser.add_argument('--years', type=int, nargs='+', default=[2023, 2024, 2025])
    parser.add_argument('--max-tasks', type=int, default=10)
    parser.add_argument('--pages', type=int, default=1)
    parser.add_argument('--max-items', type=int, default=2)
    parser.add_argument('--max-images', type=int, default=4)
    parser.add_argument('--delay', type=float, default=1.5)
    parser.add_argument('--review-file', type=Path)
    parser.add_argument('--details', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}', args.batch_id):
        parser.error('batch-id must be a plain identifier')
    if args.delay < 1.5 or not 1 <= args.max_tasks <= 500:
        parser.error('delay must be >=1.5 seconds and max-tasks between 1 and 500')
    settings = get_settings()
    if urlsplit(settings.database_url).hostname in {None, 'localhost', '127.0.0.1', '::1'}:
        parser.error('run inside the cloud API environment; local runtime KB access is disabled')
    with psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row,
                         application_name='knowledge-intake-v1') as connection:
        if args.apply_schema:
            if args.command != 'init':
                parser.error('--apply-schema is valid only for init')
            connection.execute((ROOT / 'db/migrations/0087_knowledge_intake_queue.sql').read_text())
        queue = IntakeQueue(connection)
        if args.command == 'init':
            if args.data_root is None:
                parser.error('--data-root is required for init')
            artifact_root = ScopedCardCatalog().artifact_root.resolve()
            data_root = args.data_root.resolve()
            if not data_root.is_relative_to(artifact_root.parent.parent):
                parser.error('batch files must stay in the cloud data namespace')
            current, products = publication(connection)
            plan = plan_targets(products, artifact_root, limit=args.limit, years=args.years)
            if publication(connection)[0] != current:
                raise ValueError('cloud_baseline_changed_during_planning')
            data_root.mkdir(parents=True, exist_ok=True)
            queue.create_batch(args.batch_id, current, plan, data_root)
            (data_root / 'frozen-plan.json').write_text(json.dumps(
                dict(publication=current, **plan), ensure_ascii=False, indent=2))
        batch = queue.batch(args.batch_id)
        root = Path(batch['data_root'])
        if args.command in {'run', 'review', 'export'}:
            current, _ = publication(connection)
            if current != batch['publication']:
                raise ValueError('cloud_baseline_changed; create a fresh scope, preserving this batch')
        if args.command == 'run':
            locked = connection.execute("SELECT pg_try_advisory_lock(hashtext('knowledge-intake-cardhobby-v1')) AS ok").fetchone()
            if not locked['ok']:
                raise ValueError('cardhobby_intake_worker_already_running')
            source = CardHobbyImageSource(root, delay=args.delay, pages=args.pages,
                                         max_items=args.max_items, max_images=args.max_images)
            try:
                for _ in range(args.max_tasks):
                    task = queue.claim(args.batch_id)
                    if task is None:
                        break
                    try:
                        result = source.capture(task['target']['identity'])
                    except (ValueError, OSError, RuntimeError) as error:
                        result = dict(status='error', diagnostic=str(error), candidates=[], attempts=[])
                    if not queue.finish(task['id'], task['lease_token'], result):
                        raise ValueError('task_lease_expired; captured files retained for recovery')
                    print(json.dumps(dict(task_id=str(task['id']), status=result['status'],
                                          candidates=len(result.get('candidates', []))), ensure_ascii=False),
                          flush=True)
            finally:
                source.close()
        if args.command == 'review':
            if not args.review_file:
                parser.error('--review-file is required')
            reviews = json.loads(args.review_file.read_text())
            if not isinstance(reviews, list):
                reviews = [reviews]
            for record in reviews:
                queue.review(args.batch_id, record['candidate_id'], record['review'])
        report = queue.report(args.batch_id)
        if args.command == 'export':
            report['export'] = export_assets(queue.accepted(args.batch_id), root, root / 'export')
            report['export']['not_a_sealed_knowledge_batch'] = True
        if args.details:
            report['candidate_details'] = queue.candidates(args.batch_id)
        report['checked_at'] = datetime.now(timezone.utc).isoformat()
        (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, psycopg.Error) as error:
        # Never print the DSN/settings/SQL parameters containing environment secrets.
        print(json.dumps({'status': 'failed', 'reason': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
