"""Offline text/amount/allocation replay, never a ledger-write authorization.

Requires frozen evidence and an inventory from the same account. Raw evidence,
keywords, identities and order IDs remain in private --output. stdout is aggregate.
--scope-knowledge emulates the DB projection on the exact frozen snapshot for a
before/after comparison. It retains official checklist and artifact semantics.
This replay does not perform new paired-image inference or prove physical ownership
when titles are missing or wrong. Candidate counts are not recovered-cost counts.
"""
import argparse
import asyncio
from collections import Counter
import gzip
import hashlib
import json
import re
import time
import unicodedata

from audit_missing_purchase_cost import save_private
from card_platform.knowledge import KnowledgeResolver
from card_platform.knowledge_card_catalog import text_key
from card_platform.purchase_cost import OrderCapture, order_errors, prepare_purchase_recall, score_match
from card_platform.purchase_cost_search import build_purchase_cost_search_plan


def read_json(path):
    with gzip.open(path, 'rt') if str(path).endswith('.gz') else open(path) as stream:
        return json.load(stream)


def scope_snapshot(snapshot, cards):
    numbers = {text_key(c.get('card_number')) for c in cards} - {''}
    keep_all = any(not n.isascii() for n in numbers)
    def keep(entity):
        attrs = entity.get('attributes') or {}
        number = re.sub('[^a-z0-9]', '', unicodedata.normalize('NFKD', str(attrs.get('card_number') or '')).lower())
        return (entity['entity_type'] != 'card' or keep_all or number in numbers
                or any(ord(c) < 32 or ord(c) > 126 for c in str(attrs.get('card_number') or ''))
                or any(key in attrs for key in ('catalog_mappings', 'parallel_options')))
    return {**snapshot, 'entities': [e for e in snapshot.get('entities', []) if keep(e)]}


def classify(rows):
    good = [r for r in rows if r['match']['automatic']]
    usable = [r for r in good if not r['errors'] and not r.get('detail_error')
              and len(r['assigned']) < r['quantity']]
    count = sum(r['quantity'] - len(r['assigned']) for r in usable)
    if count:
        return 'text_candidate_ready' if count == 1 else 'allocation_ambiguous'
    if good and all(len(r['assigned']) == r['quantity'] for r in good):
        return 'purchase_already_allocated'
    if good:
        return 'amount_or_order_unverified'
    if any(not r['match']['conflicts'] for r in rows):
        return 'identity_incomplete'
    return 'identity_conflict_only' if rows else 'no_identity_anchor_in_scope'


def replay(frozen, inventory, scope_knowledge=False):
    if frozen['owner_id'] != inventory['owner_id']:
        raise ValueError('Inventory and manifest belong to different accounts')
    ids = [str(c['id']) for c in frozen['samples']]
    digest = hashlib.sha256('\n'.join(ids).encode()).hexdigest()
    if ids != frozen['sample_ids'] or digest != frozen['manifest_sha256']:
        raise ValueError('Frozen sample IDs or ordering changed')
    cards = frozen.get('effective_cards', frozen['samples'])
    if [str(c['id']) for c in cards] != ids:
        raise ValueError('Effective identities do not match frozen sample ordering')
    knowledge = frozen['knowledge']
    if scope_knowledge:
        knowledge = scope_snapshot(knowledge, cards)
    resolver = KnowledgeResolver(knowledge)
    captures = [OrderCapture.model_validate({k: v for k, v in o.items() if k != 'detail_error'})
                for o in inventory['orders']]
    if 'collect_shipping_default' in frozen:
        from card_platform.purchase_cost_shipping import apply_collect_shipping_policy
        previous = {o['external_order_id']: o['capture'] for o in frozen['orders']}
        class EvidenceCursor:
            def __init__(self, row):
                self.row = row
            async def fetchone(self):
                return self.row
        class EvidenceConnection:
            async def execute(self, sql, params):
                if 'collect_shipping_default FROM' in sql:
                    return EvidenceCursor(dict(collect_shipping_default=frozen['collect_shipping_default']))
                capture = previous.get(params[1])
                return EvidenceCursor(dict(capture=capture) if capture else None)
        captures = asyncio.run(apply_collect_shipping_policy(EvidenceConnection(), frozen['owner_id'], captures))
    raw = {o['external_order_id']: o for o in inventory['orders']}
    orders = {o.external_order_id: {**o.model_dump(mode='json'),
              **({'detail_error': raw[o.external_order_id]['detail_error']} if raw[o.external_order_id].get('detail_error') else {})}
              for o in captures}
    stored = {o['id']: o['external_order_id'] for o in frozen['orders']}
    assigned = {(stored[u['order_id']], u['item_id'], u['unit_index']): str(u['assigned_item'])
                for u in frozen['units'] if u.get('assigned_item')}
    samples = []
    for index, card in enumerate(cards):
        recall, rows = prepare_purchase_recall(card, resolver), []
        for order in orders.values():
            capture = OrderCapture.model_validate({k: v for k, v in order.items() if k != 'detail_error'})
            for line in order['lines']:
                if not recall(line):
                    continue
                rows.append(dict(order_id=order['external_order_id'], item_id=line['item_id'],
                    title=line['title'], quantity=line['quantity'], match=score_match(card, line, resolver),
                    errors=order_errors(capture), detail_error=order.get('detail_error'),
                    assigned=[assigned[(order['external_order_id'], line['item_id'], n)]
                              for n in range(line['quantity']) if (order['external_order_id'], line['item_id'], n) in assigned]))
        samples.append(dict(index=index+1, id=str(card['id']), state=classify(rows),
                            plan=build_purchase_cost_search_plan(card, resolver), rows=rows))
    return dict(manifest_sha256=digest, samples=samples,
                inventory_complete=bool(inventory.get('complete')),
                scope=inventory['scope'], artifact_errors=len(resolver.card_catalog.artifact_errors),
                limitation='Text/amount/allocation replay; no new visual inference, ledger writes or physical-copy uniqueness proof')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', required=True)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--scope-knowledge', action='store_true')
    parser.add_argument('--shipping-policy', help='Legacy freeze sidecar; same owner account preference, never market guess')
    parser.add_argument('--reference', help='Compare every sample result with a prior replay')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    started = time.monotonic()
    frozen = read_json(args.frozen)
    if args.shipping_policy:
        policy = read_json(args.shipping_policy)
        if len(policy) != 1 or policy[0]['owner_id'] != frozen['owner_id']:
            raise ValueError('Shipping policy belongs to a different account')
        from decimal import Decimal
        value = policy[0]['collect_shipping_default']
        frozen['collect_shipping_default'] = Decimal(value) if value is not None else None
    elif frozen.get('collect_shipping_default') is not None:
        from decimal import Decimal
        frozen['collect_shipping_default'] = Decimal(str(frozen['collect_shipping_default']))
    result = replay(frozen, read_json(args.inventory), args.scope_knowledge)
    # Compare the serialized evidence contract (Decimal amounts/confidences are strings).
    result = json.loads(json.dumps(result, default=str))
    if args.reference:
        reference = read_json(args.reference)
        if reference['manifest_sha256'] != result['manifest_sha256']:
            raise ValueError('Reference manifest differs')
        result['changed_samples'] = [i+1 for i, (a, b) in enumerate(zip(reference['samples'], result['samples'])) if a != b]
        if len(reference['samples']) != len(result['samples']):
            raise ValueError('Reference sample count differs')
    save_private(args.output, result)
    print(json.dumps(dict(states=dict(Counter(s['state'] for s in result['samples'])),
        changed_samples=result.get('changed_samples'), elapsed_seconds=round(time.monotonic()-started, 3),
        artifact_errors=result['artifact_errors']), ensure_ascii=False))
