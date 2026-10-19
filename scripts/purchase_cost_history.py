#!/usr/bin/env python3
"""Prepare owned purchase history and execute a frozen sample through normal cost gates.

Run with the API environment. Credentials are read only by the existing encrypted
account service; arguments and progress output contain no credentials or amounts.
"""
import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


def load_targets(path):
    value = json.loads(Path(path).read_text())
    owner = UUID(value['owner_id'])
    cases = value['cases']
    ids = [UUID(case['card_id']) for case in cases]
    if not cases or len(ids) != len(set(ids)) or len({c['case_id'] for c in cases}) != len(cases):
        raise ValueError('empty or duplicate sample cards')
    return owner, cases


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.pending')
    with temporary.open('w') as file:
        os.chmod(temporary, 0o600)
        json.dump(value, file, ensure_ascii=False, default=str, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


async def ledger(conn, owner, cases):
    ids = [UUID(c['card_id']) for c in cases]
    rows = await (await conn.execute('''SELECT t.id,t.collection_item_id,t.transaction_type,
        t.unit_price,t.fee_amount,t.created_at FROM card_transaction t
        JOIN collection_item ci ON ci.id=t.collection_item_id
        WHERE ci.owner_id=%s AND ci.id=ANY(%s)''', (owner, ids))).fetchall()
    return dict(captured_at=datetime.now(timezone.utc).isoformat(), transactions=rows)


async def execute(args):
    from card_platform.config import get_settings
    from card_platform.db import Database
    from card_platform.cardhobby_account_api import account_service
    from card_platform.purchase_cost_history import PurchaseHistoryService
    from card_platform.purchase_cost_service import PurchaseCostService, owner_lock
    from card_platform.purchase_cost_knowledge import purchase_cost_knowledge_scope
    settings = get_settings()
    costs, account = PurchaseCostService(), account_service()
    history = PurchaseHistoryService(settings, account, costs)
    owner, cases = load_targets(args.manifest)
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = Database(settings)
    await db.open()
    try:
        if args.phase == 'sync':
            for index in range(args.max_steps):
                async with db.connection() as conn:
                    await owner_lock(conn, owner)
                    result = await history.sync_page(conn, owner, restart=args.restart and index == 0)
                save(out/'sync.json', result)
                emit(dict(phase='sync', **result))
                if result['complete']:
                    return
            raise RuntimeError('history sync step budget reached; checkpoint retained')
        if args.phase == 'prepare':
            for index in range(args.max_steps):
                async with db.connection() as conn:
                    # Preparation reads account metadata and writes only private
                    # files. Do not hold a financial owner lock during OCR.
                    # Keep the service's two image/model slots busy when one
                    # listing is slow; each product still checkpoints separately.
                    result = await history.prepare(conn, owner, limit=10,
                        retry_failed=args.retry_failed and index == 0)
                save(out/'preparation.json', result)
                emit(dict(phase='prepare', **result))
                if result['remaining'] == 0:
                    return
            raise RuntimeError('product preparation step budget reached; checkpoint retained')
        if args.phase == 'match':
            async with db.connection() as conn:
                if await account.archive_orders(conn, owner) is None:
                    raise RuntimeError('current account complete fresh history is required before matching')
                if not (out/'ledger-before.json').exists():
                    save(out/'ledger-before.json', await ledger(conn, owner, cases))
            path = out/'results.json'
            results = [] if args.rerun or not path.exists() else json.loads(path.read_text())
            done = {r['case_id'] for r in results}
            expected = {case['case_id'] for case in cases}
            if not expected <= done:
                # An interrupted/new bounded run is not a completed sample.
                for name in ('ledger-after.json', 'summary.json'):
                    (out/name).unlink(missing_ok=True)
            processed = 0
            for case in cases:
                if case['case_id'] in done:
                    continue
                result = None
                failures = 0
                async with db.connection() as conn:
                    with purchase_cost_knowledge_scope():
                        for step in range(args.max_rounds):
                            try:
                                # Existing ledger entries remain untouched. The
                                # same resolve/confirm path owns every new write.
                                async with conn.transaction():
                                    plan = await costs.search_plan(conn, owner, UUID(case['card_id']), refresh=False)
                                    if plan['status'] == 'skipped':
                                        result = dict(status='existing_cost', reason=plan['reason'])
                                    else:
                                        result = await costs.resolve_search(conn, owner, UUID(case['card_id']), [], True)
                                await conn.commit()
                                pending = any(i.get('code') == 'multi_photo_pending'
                                              for i in result.get('verification_issues', []))
                                if not pending:
                                    break
                                verification = result.get('trace', {}).get('verification', {})
                                if not (verification.get('new_observations') or verification.get('untried_candidates')):
                                    failures += 1
                                    if failures >= 3:
                                        break
                            except Exception as error:
                                await conn.rollback()
                                result = dict(status='error', error_type=type(error).__name__)
                                break
                            emit(dict(phase='match_progress', case_id=case['case_id'], step=step+1,
                                      status=result['status'], pending=pending))
                entry = dict(case_id=case['case_id'], card_id=case['card_id'],
                             checked_at=datetime.now(timezone.utc).isoformat(), result=result,
                             rounds=step+1)
                results.append(entry)
                save(path, results)
                emit(dict(phase='match', case_id=case['case_id'], status=result['status'],
                          completed=len(results), total=len(cases)))
                done.add(case['case_id'])
                processed += 1
                budget = getattr(args, 'max_cases', None)
                if budget is not None and processed >= budget and not expected <= done:
                    emit(dict(phase='checkpoint', complete=False, attempted=len(results),
                              total=len(cases), reason='process_case_budget'))
                    return
            async with db.connection() as conn:
                after = await ledger(conn, owner, cases)
            save(out/'ledger-after.json', after)
            before = json.loads((out/'ledger-before.json').read_text())
            previous = {str(t['id']) for t in before['transactions']}
            added = [t for t in after['transactions'] if str(t['id']) not in previous and t['transaction_type']=='buy']
            summary = dict(total=len(cases), attempted=len(results),
                           statuses=dict(Counter(r['result']['status'] for r in results)),
                           new_cost_cards=len({str(t['collection_item_id']) for t in added}),
                           total_cards_with_cost=len({str(t['collection_item_id']) for t in after['transactions']
                                                     if t['transaction_type']=='buy'}))
            save(out/'summary.json', summary)
            emit(dict(phase='summary', **summary))
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['sync','prepare','match'], required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--max-steps', type=int, default=2000)
    parser.add_argument('--max-rounds', type=int, default=50)
    parser.add_argument('--max-cases', type=int,
                        help='checkpoint after this many new cases; resume in a fresh process')
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--rerun', action='store_true')
    args = parser.parse_args()
    if (not 1 <= args.max_steps <= 10000 or not 1 <= args.max_rounds <= 500
            or (args.max_cases is not None and not 1 <= args.max_cases <= 10000)):
        parser.error('invalid run budget')
    os.umask(0o077)
    asyncio.run(execute(args))


if __name__ == '__main__':
    main()
