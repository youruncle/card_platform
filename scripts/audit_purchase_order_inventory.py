"""Read all purchase-list pages using only the account's existing session.

No login, SMS, DB write, cost sync or ledger mutation. Each result is private.
--resume retries failed/missing details in a prior file, without discarding successes.
The result distinguishes list coverage from details coverage; neither proves that
an absent title is an absent physical card. Sessions/credentials are never exported.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from audit_missing_purchase_cost import save_private
from card_platform.config import get_settings
from card_platform.db import Database
from card_platform.cardhobby_account import CardHobbySession, new_client, restore_cookies
from card_platform.cardhobby_account_crypto import AccountCipher
from card_platform.cardhobby_order_html import (
    Document, parse_orders, safe_url, recognized_list, detail_amounts,
)


def next_page(doc, current):
    for a in doc.all('a'):
        if a.text() not in {'»', '下一页', '›'} or not a.attrs.get('href'):
            continue
        if any('disabled' in n.attrs or n.attrs.get('aria-disabled') == 'true'
               or 'disabled' in n.attrs.get('class', '').split() for n in (a, a.parent) if n):
            continue
        url = safe_url(urljoin(current, a.attrs['href']), 'list')
        old, new = parse_qs(urlsplit(current).query), parse_qs(urlsplit(url).query)
        for key in ('status', 'beginDate', 'endDate', 'searchTxt'):
            if old.get(key, []) != new.get(key, []):
                raise ValueError('pagination_scope_changed')
        return url
    return None


async def read_inventory(session, scope, max_pages=50, previous=None):
    out = previous or dict(pages=[], orders=[], list_complete=False, scope=scope)
    out['at'] = datetime.now(timezone.utc).isoformat()
    if not previous:
        url = safe_url('/order/myorders?' + urlencode(scope), 'list')
        seen, orders = set(), {}
        try:
            while url:
                if url in seen or len(seen) >= max_pages:
                    raise ValueError('pagination_bound')
                seen.add(url)
                response = await session.request(url, stage='order_search')
                safe_url(str(response.url), 'list')
                doc = Document(response.text)
                if not recognized_list(doc):
                    raise ValueError('unrecognized_list')
                parsed = parse_orders(doc)
                out['pages'].append(dict(url=url, orders=len(parsed)))
                for order in parsed:
                    orders[order.external_order_id] = order.model_dump(mode='json')
                print(f'inventory page {len(seen)}, orders {len(orders)}', file=sys.stderr, flush=True)
                url = next_page(doc, url)
                await asyncio.sleep(.5)
            out['list_complete'] = True
        except Exception as error:
            out['list_error'] = getattr(error, 'code', type(error).__name__)
        out['orders'] = list(orders.values())
    for index, order in enumerate(out['orders']):
        if order.get('paid_total') is not None and not order.get('detail_error'):
            continue
        try:
            response = await session.request(safe_url(order['source_url'], 'detail'), stage='order_detail')
            safe_url(str(response.url), 'detail')
            order.update(detail_amounts(Document(response.text)))
            if order.get('paid_total') is None:
                raise ValueError('paid_total_missing')
            order.pop('detail_error', None)
        except Exception as error:
            code = getattr(error, 'code', type(error).__name__)
            order['detail_error'] = code
            if code in {'LOGIN_REQUIRED', 'RATE_LIMITED', 'VERIFICATION_REQUIRED'}:
                break
        if index % 20 == 0:
            print(f'details {index+1}/{len(out["orders"])}', file=sys.stderr, flush=True)
        await asyncio.sleep(.6)
    out['details_complete'] = all(o.get('paid_total') is not None and not o.get('detail_error')
                                  for o in out['orders'])
    out['complete'] = out['list_complete'] and out['details_complete']
    return out


async def audit(args):
    db = Database(get_settings())
    await db.open()
    try:
        async with db.connection() as c:
            await c.execute('SET TRANSACTION READ ONLY')
            rows = await (await c.execute('SELECT owner_id,session_enc FROM cardhobby_account')).fetchall()
            if args.owner:
                rows = [r for r in rows if str(r['owner_id']) == args.owner]
            if len(rows) != 1:
                raise ValueError('Select exactly one account with --owner')
            row = rows[0]
            previous = json.loads(Path(args.resume).read_text()) if args.resume else None
            if previous and previous['owner_id'] != str(row['owner_id']):
                raise ValueError('Resume file belongs to another account')
            scope = dict(status=0, searchTxt='', beginDate='2000-01-01 00:00:00',
                         endDate=f'{datetime.now(ZoneInfo('Asia/Shanghai')).year}-12-31 23:59:59')
            if not row['session_enc']:
                raise ValueError('Existing session required; log in through the product')
            key_file = Path(get_settings().cardhobby_account_key_file)
            if not key_file.is_file():
                raise ValueError('Existing account encryption key required; audit will not create one')
            cipher = AccountCipher(str(key_file))
            async with new_client() as client:
                restore_cookies(client, cipher.decrypt(row['owner_id'], 'session', row['session_enc']))
                session = CardHobbySession(client)
                if not await session.check():
                    raise ValueError('Existing session expired; log in through the product')
                out = await read_inventory(session, scope, args.max_pages, previous)
                out['owner_id'] = str(row['owner_id'])
            await c.rollback()
            return out
    finally:
        await db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner')
    parser.add_argument('--resume')
    parser.add_argument('--max-pages', type=int, default=50)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = asyncio.run(audit(args))
    save_private(args.output, result)
    print(json.dumps(dict(pages=len(result['pages']), orders=len(result['orders']),
                          list_complete=result['list_complete'], details_complete=result['details_complete'])))
