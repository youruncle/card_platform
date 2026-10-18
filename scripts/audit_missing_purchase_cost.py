"""Freeze private purchase-cost evidence without changing the application DB.

PYTHONPATH=apps/api/src .venv/bin/python scripts/audit_missing_purchase_cost.py \
  --output .runtime/cost-audit/frozen.json
Optional --candidates also invokes the read-only candidate service. No cost writes,
login POSTs, SMS, sync, inference, confirm or search-result endpoints are invoked.
"""
import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.purchase_cost_knowledge import load_purchase_cost_snapshot

SEED = 'cost-audit-20261017-v1'
STRATA = ('player_name', 'season_start', 'product_name', 'grade_company',
          'parallel_name', 'serial_total', 'transaction_state', 'search_state')


def freeze_sample(cards, limit=100):
    """Round-robin full identity strata, then SHA256 rank, without UUID/date bias."""
    groups = defaultdict(list)
    for card in cards:
        groups[tuple(str(card.get(k) or '') for k in STRATA)].append(card)
    def rank(value):
        return hashlib.sha256((SEED + str(value)).encode()).hexdigest()
    queues = [sorted(v, key=lambda c: rank(c['id']))
              for _, v in sorted(groups.items(), key=lambda kv: rank(kv[0]))]
    result = []
    while queues and len(result) < limit:
        result.extend(q.pop(0) for q in queues if q)
        queues = [q for q in queues if q]
    return result[:limit]


def save_private(path, result):
    """Never follow a pre-existing symlink or overwrite a frozen evidence file."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(result, stream, default=str, ensure_ascii=False, indent=2)
        stream.write('\n')


async def audit(args):
    db = Database(get_settings())
    await db.open()
    out = dict(seed=SEED, captured_at=datetime.now(timezone.utc).isoformat(),
               scope='active cards without any buy transaction or cost assignment', strata=STRATA)
    try:
        async with db.connection() as c:
            await c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            owners = await (await c.execute('SELECT owner_id,state,collect_shipping_default FROM cardhobby_account')).fetchall()
            if args.owner:
                owners = [r for r in owners if str(r['owner_id']) == args.owner]
            if len(owners) != 1:
                raise ValueError('Select exactly one account with --owner')
            owner = owners[0]['owner_id']
            out.update(owner_id=str(owner), account_state=owners[0]['state'],
                       collect_shipping_default=owners[0]['collect_shipping_default'])
            cards = await (await c.execute('''SELECT ci.*,p.full_name player_name,b.name brand_name,
                cs.season_start,cs.season_end,cs.product_name,cs.set_name,cd.card_number,
                cd.insert_name,cd.parallel_name,cd.is_rookie,cd.is_autograph,cd.is_memorabilia,
                cd.is_patch,cd.print_run,cd.autograph_type,cd.has_inscription,cd.inscription_text,
                cd.memorabilia_types,cd.memorabilia_usage,cd.memorabilia_player,u.base_currency,
                EXISTS(SELECT 1 FROM card_transaction t WHERE t.collection_item_id=ci.id) transaction_state,
                COALESCE((SELECT r->>'status' FROM cardhobby_cost_job j,
                  LATERAL jsonb_array_elements(j.results) r WHERE j.owner_id=ci.owner_id
                  AND r->>'item_id'=ci.id::text ORDER BY j.created_at DESC LIMIT 1),'unsearched') search_state
                FROM collection_item ci JOIN card_definition cd ON cd.id=ci.card_definition_id
                JOIN card_set cs ON cs.id=cd.card_set_id JOIN player p ON p.id=cd.player_id
                JOIN brand b ON b.id=cs.brand_id JOIN app_user u ON u.id=ci.owner_id
                WHERE ci.owner_id=%s AND ci.deleted_at IS NULL
                AND NOT EXISTS(SELECT 1 FROM card_transaction t WHERE t.collection_item_id=ci.id AND t.transaction_type='buy')
                AND NOT EXISTS(SELECT 1 FROM cardhobby_cost_assignment a WHERE a.collection_item_id=ci.id)
                ORDER BY ci.id''', (owner,))).fetchall()
            out['eligible_count'] = len(cards)
            selected = freeze_sample(cards, args.limit)
            if args.manifest:
                manifest = json.loads(Path(args.manifest).read_text())
                if manifest['owner_id'] != str(owner):
                    raise ValueError('Manifest belongs to a different account')
                by_id = {str(r['id']): r for r in cards}
                selected = [by_id[i] for i in manifest['sample_ids']]
            out['samples'] = selected
            out['sample_ids'] = [str(r['id']) for r in selected]
            out['manifest_sha256'] = hashlib.sha256('\n'.join(out['sample_ids']).encode()).hexdigest()
            for name, sql in (
                ('orders', 'SELECT * FROM cardhobby_purchase_order WHERE owner_id=%s'),
                ('units', '''SELECT u.*,a.collection_item_id assigned_item FROM cardhobby_purchase_unit u
                    LEFT JOIN cardhobby_cost_assignment a ON a.unit_id=u.id WHERE u.owner_id=%s'''),
                ('inferences', 'SELECT * FROM cardhobby_cost_visual_inference WHERE owner_id=%s'),
                ('jobs', 'SELECT * FROM cardhobby_cost_job WHERE owner_id=%s'),
            ):
                out[name] = await (await c.execute(sql, (owner,))).fetchall()
            from card_platform.purchase_cost_service import PurchaseCostService
            service = PurchaseCostService()
            out['effective_cards'] = [await service.card(c, owner, card['id']) for card in selected]
            out['knowledge'] = await load_purchase_cost_snapshot(c, out['effective_cards'])
            if args.candidates:
                out['candidate_previews'] = []
                for index, card in enumerate(selected):
                    out['candidate_previews'].append(dict(id=str(card['id']),
                        card=await service.card(c, owner, card['id']),
                        result=await service.candidates(c, owner, card['id'])))
                    if index % 10 == 0:
                        print(f'preview {index+1}/{len(selected)}', file=sys.stderr, flush=True)
            await c.rollback()
    finally:
        await db.close()
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--manifest')
    parser.add_argument('--candidates', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.limit <= 2000:
        parser.error('--limit must be between 1 and 2000')
    result = asyncio.run(audit(args))
    save_private(args.output, result)
    print(json.dumps(dict(eligible_count=result['eligible_count'], frozen=len(result['samples']),
                          manifest_sha256=result['manifest_sha256'])))
